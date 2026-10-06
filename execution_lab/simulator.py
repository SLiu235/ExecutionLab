"""Latency-aware, aggressive-only snapshot replay with explicit cost accounting.

This is an exogenous replay: it does not predict replenishment or own-order impact.
Each child can consume only a configurable fraction of the *observed* depth.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import asdict, dataclass

import numpy as np

from .data import Book
from .model import RidgeForecast

POLICIES = ("immediate", "twap", "ac_schedule", "adaptive")


@dataclass(frozen=True)
class ExecutionConfig:
    quantity: float = 0.10
    fee_bps: float = 5.0  # Assumed scenario; never infer a customer's fee tier.
    latency_ms: float = 250.0
    depth_fraction: float = 0.10
    max_spread_bps: float = 30.0
    max_slippage_bps: float = 20.0
    max_gap_seconds: float = 5.0
    max_age_seconds: float = 10.0
    ac_urgency: float = 2.0
    alpha_scale_bps: float = 2.0
    adaptive_strength: float = 0.5

    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("execution parameters must be finite")
        if self.quantity <= 0 or self.fee_bps < 0 or self.latency_ms < 0:
            raise ValueError("invalid quantity, fee, or latency")
        if not 0 < self.depth_fraction <= 1 or not 0 <= self.ac_urgency <= 10:
            raise ValueError("invalid depth fraction or urgency")
        if min(self.max_spread_bps, self.max_slippage_bps, self.max_gap_seconds, self.max_age_seconds,
               self.alpha_scale_bps) <= 0 or not 0 <= self.adaptive_strength <= 1:
            raise ValueError("invalid execution guard or adaptive strength")


def cumulative_fraction(policy: str, elapsed: float, urgency: float) -> float:
    f = min(1.0, max(0.0, elapsed))
    if policy == "immediate":
        return 1.0
    if policy in {"twap", "adaptive"} or urgency < 1e-8:
        return f
    if policy == "ac_schedule":
        return 1 - math.sinh(urgency * (1-f)) / math.sinh(urgency)
    raise ValueError(f"unknown policy {policy}")


def sweep(book: Book, side: str, quantity: float, fraction: float,
          limit_price: float | None = None) -> tuple[float, float]:
    """Return filled base quantity and quote notional; never fabricate deep liquidity."""
    if (side not in {"buy", "sell"} or not math.isfinite(quantity) or quantity < 0
            or not 0 < fraction <= 1):
        raise ValueError("invalid sweep arguments")
    remaining, filled, notional = quantity, 0.0, 0.0
    for price, depth in book.asks if side == "buy" else book.bids:
        if limit_price is not None and ((side == "buy" and price > limit_price)
                                        or (side == "sell" and price < limit_price)):
            break
        take = min(remaining, fraction * depth)
        filled += take
        notional += take * price
        remaining -= take
        if remaining < 1e-12:
            break
    return filled, notional


def replay(books: list[Book], X: np.ndarray, model: RidgeForecast, start: int, end: int,
           side: str, policy: str, cfg: ExecutionConfig,
           duration_seconds: float = 30.0) -> tuple[dict, list[dict], list[dict]]:
    if side not in {"buy", "sell"} or policy not in POLICIES or not 0 <= start < end < len(books):
        raise ValueError("invalid episode")
    if model.label_end_max >= start:
        raise ValueError("forecast trained on labels unavailable at episode start")
    sign = 1 if side == "buy" else -1
    times = [b.received_ns for b in books]
    arrival = books[start].mid
    # Deadline is an instruction known at arrival, not a future snapshot timestamp.
    deadline_ns = times[start] + int(duration_seconds * 1e9)
    if duration_seconds <= 0 or times[end] > deadline_ns:
        raise ValueError("episode end exceeds the predeclared parent deadline")
    latency_ns = int(cfg.latency_ms * 1e6)
    # Freeze the observed sampling cadence at arrival. Reserve a trailing 95th-
    # percentile interval plus latency to give the final child time to execute.
    # Without a reserve, sparse polling can make a perfectly sized final child late.
    past_intervals = np.diff(times[max(0, start-30):start+1])
    interval_ns = float(np.median(past_intervals)) if len(past_intervals) else 1_000_000_000
    reserve_ns = (float(np.quantile(past_intervals, .95)) if len(past_intervals) else interval_ns) + latency_ns
    plan_ns = max(interval_ns, deadline_ns - times[start] - reserve_ns)
    filled, cash, fees = 0.0, 0.0, 0.0
    delay, spread, depth_cost = 0.0, 0.0, 0.0
    fills, decisions = [], []
    i = start
    rejected = 0
    while i < end and filled < cfg.quantity - 1e-10:
        b = books[i]
        forecast = model.predict(X[i]) if policy == "adaptive" else 0.0
        elapsed = (times[i] - times[start] + interval_ns) / plan_ns
        target = cfg.quantity * cumulative_fraction(policy, elapsed, cfg.ac_urgency)
        wanted = max(0.0, target - filled)
        if policy == "adaptive":
            # Buy faster before predicted rises; sell faster before predicted falls.
            multiplier = 1 + cfg.adaptive_strength * math.tanh(sign*forecast / cfg.alpha_scale_bps)
            wanted *= multiplier
            if elapsed >= 1:
                wanted = cfg.quantity - filled
        wanted = min(cfg.quantity - filled, wanted)
        j = bisect.bisect_left(times, times[i] + latency_ns, lo=i+1, hi=end+1)
        record = {"decision_index": i, "decision_ns": times[i], "policy": policy,
                  "side": side, "forecast_bps": forecast, "target_quantity": target,
                  "requested_quantity": wanted, "filled_before": filled,
                  "label_end_max": model.label_end_max, "execution_index": j if j <= end else None}
        limit_price = b.mid * (1 + sign*cfg.max_slippage_bps/1e4)
        record["limit_price"] = limit_price
        decisions.append(record)
        if (b.spread_bps > cfg.max_spread_bps
                or (b.received_ns-b.exchange_ns)/1e9 > cfg.max_age_seconds):
            record["status"] = "decision_guard_rejected"
            rejected += 1
            i += 1
            continue
        if j > end or times[j] > deadline_ns:
            record["status"] = "missed_deadline"
            break
        if wanted < 1e-12:
            record["status"] = "wait"
            i += 1
            continue
        execution = books[j]
        data_gap = (times[j] - times[i] - latency_ns) / 1e9
        age = (execution.received_ns - execution.exchange_ns) / 1e9
        if data_gap > cfg.max_gap_seconds or age > cfg.max_age_seconds:
            record["status"] = "unobservable_execution"
            rejected += 1
            i = j
            continue
        qty, notional = sweep(execution, side, wanted, cfg.depth_fraction, limit_price)
        if qty <= 1e-12:
            record["status"] = "limit_not_marketable"
            i = j
            continue
        fee = notional * cfg.fee_bps / 1e4
        price = notional / qty
        touch = execution.asks[0][0] if side == "buy" else execution.bids[0][0]
        d = sign * qty * (execution.mid - arrival)
        s = sign * qty * (touch - execution.mid)
        c = sign * (notional - qty * touch)
        filled += qty
        cash += notional
        fees += fee
        delay += d
        spread += s
        depth_cost += c
        # Markout is computed only for reporting and never passed to the policy.
        mark_idx = bisect.bisect_left(times, times[j] + 5_000_000_000)
        markout = (sign * (books[mark_idx].mid-price)/price*1e4
                   if mark_idx < len(books) and times[mark_idx]-times[j] <= 10_000_000_000 else None)
        record["status"] = "filled" if qty >= wanted-1e-10 else "partial"
        fills.append({"decision_index": i, "execution_index": j, "decision_ns": times[i],
                      "execution_ns": times[j], "side": side, "policy": policy,
                      "quantity": qty, "price": price, "notional": notional,
                      "fee_quote": fee, "delay_quote": d, "spread_quote": s,
                      "depth_quote": c, "execution_mid": execution.mid,
                      "markout_5s_bps": markout, "data_gap_seconds": data_gap})
        i = j  # At most one child per snapshot; prior child has finished.
    remaining = max(0.0, cfg.quantity-filled)
    opportunity = sign * remaining * (books[end].mid-arrival)
    actual = sign*(cash - filled*arrival) + fees
    denominator = cfg.quantity*arrival
    if not math.isclose(actual, delay+spread+depth_cost+fees, abs_tol=1e-7):
        raise AssertionError("execution accounting identity failed")
    summary = {"policy": policy, "side": side, "start": start, "end": end,
               "deadline_ns": deadline_ns, "completion_reserve_ms": reserve_ns/1e6,
               "arrival_mid": arrival, "terminal_mid": books[end].mid,
               "requested_quantity": cfg.quantity, "filled_quantity": filled,
               "unfilled_quantity": remaining, "fill_rate": filled/cfg.quantity,
               "completed": remaining <= 1e-8, "vwap": cash/filled if filled else None,
               "realized_shortfall_bps": actual/denominator*1e4,
               "opportunity_bps": opportunity/denominator*1e4,
               "total_marked_shortfall_bps": (actual+opportunity)/denominator*1e4,
               "delay_bps": delay/denominator*1e4, "spread_bps": spread/denominator*1e4,
               "depth_bps": depth_cost/denominator*1e4, "fee_bps": fees/denominator*1e4,
               "children": len(fills), "guard_rejections": rejected,
               "label_end_max": model.label_end_max}
    return summary, fills, decisions
