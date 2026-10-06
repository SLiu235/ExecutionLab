"""Paired execution experiments, purged forecasting, manifests, and stress scenarios."""
from __future__ import annotations

import bisect
import csv
import json
import math
import platform
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from .data import load_books, new_directory, sha256, stamp, write_json
from .features import FEATURES, matrix
from .model import fit_past
from .simulator import POLICIES, ExecutionConfig, replay


@dataclass(frozen=True)
class Config:
    warmup_snapshots: int = 300
    train_window: int = 240
    horizon_snapshots: int = 3
    embargo_snapshots: int = 1
    episode_seconds: float = 30
    ridge_alpha: float = 10
    seed: int = 42
    bootstrap_repetitions: int = 1000
    bootstrap_block_episodes: int = 4
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    def __post_init__(self):
        if self.warmup_snapshots < self.horizon_snapshots+self.embargo_snapshots+20:
            raise ValueError("warmup must allow 20 matured labels plus horizon and embargo")
        if (self.train_window < 20 or self.horizon_snapshots < 1 or self.embargo_snapshots < 1
                or self.episode_seconds <= 0 or self.ridge_alpha <= 0
                or self.bootstrap_repetitions < 100 or self.bootstrap_block_episodes < 1):
            raise ValueError("invalid research configuration")
        if not math.isfinite(self.episode_seconds) or not math.isfinite(self.ridge_alpha):
            raise ValueError("nonfinite research configuration")

    @classmethod
    def from_file(cls, path):
        with Path(path).open("rb") as f:
            values = tomllib.load(f)
        exe = ExecutionConfig(**values.pop("execution", {}))
        return cls(**values, execution=exe)


def write_csv(path: Path, rows: list[dict]):
    with path.open("x", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def paired_interval(values: list[float], cfg: Config) -> dict:
    n = len(values)
    out = {"time_blocks": n, "mean_delta_bps": float(np.mean(values)) if n else None,
           "ci95_bps": None, "interpretation": "insufficient independent time coverage"}
    if n < 8:
        return out
    rng = np.random.default_rng(cfg.seed)
    v = np.asarray(values)
    block = min(cfg.bootstrap_block_episodes, n)
    starts = rng.integers(0, n-block+1, size=(cfg.bootstrap_repetitions, math.ceil(n/block)))
    indices = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(len(starts), -1)[:, :n]
    means = v[indices].mean(axis=1)
    out["ci95_bps"] = np.quantile(means, [.025, .975]).tolist()
    out["interpretation"] = "exploratory paired moving-block bootstrap; one session is not market validation"
    return out


def aggregate(rows, forecast_rows, cfg):
    summaries = []
    for policy in POLICIES:
        subset = [r for r in rows if r["policy"] == policy]
        costs = [r["total_marked_shortfall_bps"] for r in subset]
        summaries.append({"policy": policy, "parents": len(subset),
                          "completion_rate": float(np.mean([r["completed"] for r in subset])),
                          "mean_fill_rate": float(np.mean([r["fill_rate"] for r in subset])),
                          "mean_marked_shortfall_bps": float(np.mean(costs)),
                          "p95_marked_shortfall_bps": float(np.quantile(costs, .95)),
                          "mean_depth_bps": float(np.mean([r["depth_bps"] for r in subset])),
                          "mean_fees_bps": float(np.mean([r["fee_bps"] for r in subset])),
                          "guard_rejections": sum(r["guard_rejections"] for r in subset)})
    pairs = {(r["episode"], r["side"], r["policy"]): r for r in rows}
    # Buy and sell parents at the same start are correlated. Average them BEFORE bootstrap.
    paired, excluded = [], 0
    for ep in sorted({r["episode"] for r in rows}):
        deltas = []
        for side in ("buy", "sell"):
            a, t = pairs[ep, side, "adaptive"], pairs[ep, side, "twap"]
            if not (a["completed"] and t["completed"]):
                break
            deltas.append(a["realized_shortfall_bps"]-t["realized_shortfall_bps"])
        if len(deltas) == 2:
            paired.append(float(np.mean(deltas)))
        else:
            excluded += 1
    comparison = paired_interval(paired, cfg)
    comparison["excluded_incomplete_time_blocks"] = excluded
    comparison["delta_definition"] = "adaptive minus TWAP; negative favors adaptive; both sides completed"
    actual = np.array([r["actual_return_bps"] for r in forecast_rows])
    pred = np.array([r["predicted_return_bps"] for r in forecast_rows])
    squared_zero = float(np.sum(actual**2))
    forecast = {"samples": len(actual), "ridge_rmse_bps": float(np.sqrt(np.mean((actual-pred)**2))),
                "zero_rmse_bps": float(np.sqrt(np.mean(actual**2))),
                "r2_vs_zero": 1-float(np.sum((actual-pred)**2))/squared_zero if squared_zero > 0 else None}
    return {"policies": summaries, "adaptive_vs_twap": comparison, "forecast": forecast}


def report_text(result, cfg):
    data = result["data"]
    lines = ["# ExecutionLab — experiment report", "",
             f"Data: **{data['source']}** · {data['product']} · {data['snapshots']} validated snapshots · "
             f"{data['span_seconds']:.1f} seconds observed.", "",
             f"{result['episodes']} non-overlapping parent time windows; independent buy and sell replay "
             "on each window. Same parent size, deadline, fee and visible-depth limits for every policy.", "",
             f"Parent: {cfg.execution.quantity} base units; deadline: {cfg.episode_seconds}s; "
             f"assumed fee: {cfg.execution.fee_bps} bps; latency floor: {cfg.execution.latency_ms}ms; "
             f"available depth haircut: {100*cfg.execution.depth_fraction:.0f}%.", "",
             "| Policy | Completion | Average filled | Mean marked cost (bps) | P95 cost (bps) |",
             "|---|---:|---:|---:|---:|"]
    for row in result["policies"]:
        lines.append(f"| {row['policy']} | {row['completion_rate']:.1%} | {row['mean_fill_rate']:.1%} | "
                     f"{row['mean_marked_shortfall_bps']:.4f} | {row['p95_marked_shortfall_bps']:.4f} |")
    c = result["adaptive_vs_twap"]
    delta = "undefined" if c["mean_delta_bps"] is None else f"{c['mean_delta_bps']:.4f} bps"
    lines += ["", "Lower cost is better. Marked cost includes terminal-mid opportunity cost on unfilled "
              "quantity; it is not a forced-completion price. Completion must be assessed alongside cost.", "",
              f"Adaptive minus TWAP, on fully completed paired time blocks: **{delta}**. "
              f"Included blocks: {c['time_blocks']}; excluded for noncompletion: "
              f"{c['excluded_incomplete_time_blocks']}.", "",
              f"Paired 95% interval: {c['ci95_bps']}. {c['interpretation']}.", ""]
    f = result["forecast"]
    lines += [f"Frozen ridge forecast RMSE: {f['ridge_rmse_bps']:.4f} bps; zero-change baseline: "
              f"{f['zero_rmse_bps']:.4f} bps over {f['samples']} future-return labels.", "",
              "## Interpretation", "",
              "The synthetic fixture tests accounting and temporal mechanics. A short real capture "
              "tests the adapter and replay path. Neither establishes execution improvement across market regimes.", "",
              "Replay assumes aggressive orders against recorded depth, exogenous future books, and no "
              "permanent/transient own-order impact. It provides no queue position, passive fill, "
              "cross-venue routing, or event-complete OFI claim. Repeated snapshots can replenish simulated "
              "depth independently of the hypothetical parent; the depth haircut and size stress expose "
              "sensitivity but cannot repair that counterfactual assumption.", "",
              "Latency is a minimum: orders resolve against the first later available snapshot. Sampling "
              "delays and guards are in fills.csv and decisions.csv. Sparse capture is unsuitable for "
              "submillisecond latency claims.", "",
              "## Audit artifacts", "",
              "- parents.csv: fills, unfilled quantity, implementation shortfall and additive cost components.",
              "- fills.csv: every simulated child, observed depth price, timing, fees and five-second markout.",
              "- decisions.csv: frozen-model forecast, requested quantity, label cutoff and guard outcomes.",
              "- models.json: each fold's coefficients and training-only scaling parameters.",
              "- forecasts.csv: start-of-episode forecasts and future labels, compared with zero change.",
              "- manifest.json: configuration, input hash, source hashes, environment and artifact hashes.", ""]
    return "\n".join(lines)


def run(input_path: Path, output: Path, cfg: Config) -> dict:
    books = load_books(input_path)
    capture_manifest = input_path.parent / "manifest.json"
    provenance = "synthetic_fixture" if books[0].source == "synthetic" else "external_unverified"
    if books[0].source == "coinbase_rest" and capture_manifest.exists():
        captured = json.loads(capture_manifest.read_text())
        raw = input_path.parent / "raw.jsonl"
        hashes = captured.get("hashes", {})
        if (captured.get("source") != "coinbase_rest" or not raw.exists()
                or hashes.get("snapshots.jsonl") != sha256(input_path)
                or hashes.get("raw.jsonl") != sha256(raw)):
            raise ValueError("capture evidence/hash mismatch; cannot label this a verified capture")
        provenance = "local_capture_hashes_verified"
    if len(books) <= cfg.warmup_snapshots + cfg.horizon_snapshots + 2:
        raise ValueError("capture too short for this config; collect longer or use smoke.toml")
    X = matrix(books)
    mids = np.array([b.mid for b in books])
    times = [b.received_ns for b in books]
    rows, fills, decisions, models, forecasts = [], [], [], [], []
    start, episode = cfg.warmup_snapshots, 0
    while start + cfg.horizon_snapshots < len(books):
        deadline = times[start] + int(cfg.episode_seconds*1e9)
        # Discard tail windows that do not have the complete observation horizon.
        if deadline > times[-1]:
            break
        end = bisect.bisect_right(times, deadline)-1
        if end <= start + cfg.horizon_snapshots:
            start = end+1
            continue
        model = fit_past(X, mids, start, cfg.horizon_snapshots, cfg.train_window,
                         cfg.ridge_alpha, cfg.embargo_snapshots)
        models.append({"episode": episode, "train_before_index": start, **model.record()})
        forecasts.append({"episode": episode, "start": start, "label_end": start+cfg.horizon_snapshots,
                          "predicted_return_bps": model.predict(X[start]),
                          "actual_return_bps": (mids[start+cfg.horizon_snapshots]/mids[start]-1)*1e4})
        for side in ("buy", "sell"):
            for policy in POLICIES:
                row, child, dec = replay(books, X, model, start, end, side, policy, cfg.execution,
                                         cfg.episode_seconds)
                rows.append({"episode": episode, **row})
                fills.extend({"episode": episode, **r} for r in child)
                decisions.extend({"episode": episode, **r} for r in dec)
        episode += 1
        start = bisect.bisect_right(times, deadline)
    if not rows:
        raise ValueError("no complete parent windows; record more data or shorten episode_seconds")
    new_directory(output)
    write_csv(output / "parents.csv", rows)
    write_csv(output / "fills.csv", fills)
    write_csv(output / "decisions.csv", decisions)
    write_csv(output / "forecasts.csv", forecasts)
    write_json(output / "models.json", {"features": FEATURES, "folds": models})
    result = aggregate(rows, forecasts, cfg)
    gaps = np.diff(np.asarray(times, dtype=np.int64))/1e9
    result["data"] = {"source": books[0].source, "provenance": provenance, "product": books[0].product,
                      "snapshots": len(books), "span_seconds": (times[-1]-times[0])/1e9,
                      "median_interval_seconds": float(np.median(gaps)),
                      "max_interval_seconds": float(np.max(gaps)),
                      "input_sha256": sha256(input_path)}
    result["episodes"] = episode
    write_json(output / "summary.json", result)
    (output / "REPORT.md").write_text(report_text(result, cfg))
    root = Path(__file__).resolve().parent.parent
    manifest = {"created_at": stamp(), "config": asdict(cfg), "source": books[0].source,
                "provenance": provenance,
                "input_path": str(input_path.resolve()), "input_sha256": sha256(input_path),
                "capture_manifest_sha256": sha256(capture_manifest) if capture_manifest.exists() else None,
                "provenance_note": "source tags are declarations; inspect raw capture and its manifest",
                "python": platform.python_version(), "numpy": np.__version__,
                "source_hashes": {str(p.relative_to(root)): sha256(p)
                                  for p in sorted((root / "execution_lab").glob("*.py"))},
                "artifact_hashes": {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()}}
    write_json(output / "manifest.json", manifest)
    return result


def stress(input_path: Path, output: Path, cfg: Config):
    new_directory(output)
    scenarios = {"base": cfg.execution,
                 "latency_1000ms": replace(cfg.execution, latency_ms=1000),
                 "fee_20bps": replace(cfg.execution, fee_bps=20),
                 "depth_2pct": replace(cfg.execution, depth_fraction=.02),
                 "size_5x": replace(cfg.execution, quantity=cfg.execution.quantity*5)}
    rows = []
    for name, exe in scenarios.items():
        result = run(input_path, output/name, replace(cfg, execution=exe))
        rows.extend({"scenario": name, **r} for r in result["policies"])
    write_csv(output / "stress.csv", rows)
    (output / "README.md").write_text("# Prespecified stress scenarios\n\n"
        "All scenarios retained: baseline, 1-second latency floor, 20-bp fee, 2% visible depth, "
        "and 5x parent size. These are assumed stress values, not venue fee tiers or impact estimates. "
        "Compare completion and cost jointly. See stress.csv and each scenario's REPORT.md.\n")
