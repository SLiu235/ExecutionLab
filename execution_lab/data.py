"""Validated snapshots and an append-only public REST recorder.

Replay uses receive time, never the earlier exchange timestamp, as availability.
Sequence gaps are expected for independently polled snapshots. Regressions and
duplicates are rejected; they are not fabricated into incremental order events.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def new_directory(path: Path) -> None:
    # An existing directory is an error, even if empty: never replace evidence.
    path.mkdir(parents=True, exist_ok=False)


@dataclass(frozen=True)
class Book:
    product: str
    received_ns: int
    exchange_ns: int
    sequence: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    source: str

    def __post_init__(self):
        if not self.product or self.source not in {"coinbase_rest", "synthetic"}:
            raise ValueError("product and supported source are required")
        if self.received_ns <= 0 or self.exchange_ns <= 0 or self.sequence < 0:
            raise ValueError("invalid timestamps or sequence")
        # Clock skew is observable and rejected rather than silently repaired.
        if self.exchange_ns > self.received_ns + 5_000_000_000:
            raise ValueError("exchange clock exceeds receive clock by >5 seconds")
        for levels, reverse in ((self.bids, True), (self.asks, False)):
            if not levels:
                raise ValueError("both book sides must have depth")
            for p, q in levels:
                if not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0:
                    raise ValueError("nonpositive or nonfinite book level")
            prices = [p for p, _ in levels]
            if prices != sorted(set(prices), reverse=reverse):
                raise ValueError("book prices must be unique and ordered")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("locked/crossed or auction book cannot be replayed")

    @property
    def mid(self) -> float:
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def spread_bps(self) -> float:
        return (self.asks[0][0] - self.bids[0][0]) / self.mid * 1e4

    def row(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict) -> Book:
        fields = {**row, "bids": tuple(tuple(x) for x in row["bids"]),
                  "asks": tuple(tuple(x) for x in row["asks"])}
        return cls(**fields)


def normalize(payload: dict, product: str, received_ns: int, depth: int) -> Book:
    if payload.get("auction_mode", False):
        raise ValueError("auction snapshot")
    if not payload.get("time"):
        raise ValueError("exchange time missing; cannot assess data age")
    dt = datetime.fromisoformat(payload["time"].replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("exchange timestamp needs timezone")
    def side(key, reverse):
        # Coinbase level 2 size is already aggregated. Never multiply by order count.
        return tuple(sorted(((float(x[0]), float(x[1])) for x in payload[key]),
                            reverse=reverse)[:depth])
    return Book(product, received_ns, int(dt.timestamp() * 1e9), int(payload["sequence"]),
                side("bids", True), side("asks", False), "coinbase_rest")


def load_books(path: Path) -> list[Book]:
    books = []
    with path.open() as f:
        for line_no, line in enumerate(f, 1):
            try:
                b = Book.from_row(json.loads(line))
                if books:
                    prev = books[-1]
                    if b.product != prev.product or b.source != prev.source:
                        raise ValueError("mixed products or source types")
                    if b.received_ns <= prev.received_ns or b.sequence <= prev.sequence:
                        raise ValueError("non-increasing receive time or sequence")
                books.append(b)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
    if len(books) < 2:
        raise ValueError("need at least two validated snapshots")
    return books


def capture(output: Path, product="BTC-USD", samples=600, interval=1.0,
            depth=50, timeout=15.0) -> dict:
    if not re.fullmatch(r"[A-Z0-9]{2,16}-[A-Z0-9]{2,16}", product):
        raise ValueError("invalid Coinbase product identifier")
    if samples < 2 or interval < 0.5 or depth < 1 or timeout <= 0:
        raise ValueError("samples>=2, interval>=0.5s, depth>=1 and timeout>0 required")
    new_directory(output)
    endpoint = f"https://api.exchange.coinbase.com/products/{product}/book?level=2"
    manifest = {"schema_version": 1, "source": "coinbase_rest", "product": product,
                "endpoint": endpoint, "started_at": stamp(), "requested_attempts": samples,
                "interval_seconds": interval, "retained_levels": depth,
                "accepted": 0, "errors": [], "status": "recording",
                "semantics": "independent full snapshots, not an event-complete feed"}
    write_json(output / "manifest.json", manifest)
    last_sequence = -1
    last_receive = 0
    try:
        with (output / "raw.jsonl").open("x") as raw, (output / "snapshots.jsonl").open("x") as clean:
            for attempt in range(samples):
                started = time.monotonic()
                request_ns = time.time_ns()
                try:
                    req = Request(endpoint, headers={"User-Agent": "ExecutionLab/0.1 public research",
                                                    "Cache-Control": "no-cache"})
                    with urlopen(req, timeout=timeout) as response:
                        body = response.read().decode("utf-8")
                        received_ns = time.time_ns()
                        headers = {k: response.headers.get(k) for k in ("Date", "Age", "Cache-Control")}
                    raw.write(json.dumps({"attempt": attempt, "request_ns": request_ns,
                                          "received_ns": received_ns, "headers": headers,
                                          "body": body}) + "\n")
                    raw.flush()
                    b = normalize(json.loads(body), product, received_ns, depth)
                    if b.sequence <= last_sequence or b.received_ns <= last_receive:
                        raise ValueError("duplicate/regressed snapshot")
                    if (b.received_ns - b.exchange_ns) / 1e9 > 10:
                        raise ValueError("snapshot exchange age exceeds 10 seconds")
                    clean.write(json.dumps(b.row(), allow_nan=False) + "\n")
                    clean.flush()
                    manifest["accepted"] += 1
                    last_sequence, last_receive = b.sequence, b.received_ns
                except Exception as exc:
                    manifest["errors"].append({"attempt": attempt, "at": stamp(),
                                               "type": type(exc).__name__, "message": str(exc)[:300]})
                if (attempt + 1) % 10 == 0:
                    print(f"capture {attempt+1}/{samples}: {manifest['accepted']} valid", flush=True)
                    write_json(output / "manifest.json", manifest)
                if attempt + 1 < samples:
                    time.sleep(max(0, interval - (time.monotonic() - started)))
        manifest["status"] = "complete" if manifest["accepted"] >= 2 else "failed"
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
    finally:
        manifest["finished_at"] = stamp()
        manifest["hashes"] = {p.name: sha256(p) for p in output.glob("*.jsonl")}
        write_json(output / "manifest.json", manifest)
    if manifest["accepted"] < 2:
        raise RuntimeError(f"Capture failed; raw responses and errors preserved in {output}")
    return manifest
