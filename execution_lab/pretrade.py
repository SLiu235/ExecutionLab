"""Observable spread/depth costs for a proposed parent; never routes an order."""
from __future__ import annotations

import math

from .data import Book
from .simulator import sweep


def quote(book: Book, side: str, quantities: list[float], fee_bps: float,
          depth_fraction: float) -> dict:
    if (not math.isfinite(fee_bps) or fee_bps < 0
            or any(not math.isfinite(q) or q <= 0 for q in quantities)):
        raise ValueError("nonnegative fee and positive quantities required")
    sign = 1 if side == "buy" else -1
    rows = []
    for q in quantities:
        filled, notional = sweep(book, side, q, depth_fraction)
        price = notional/filled
        fee = notional*fee_bps/1e4
        cost = sign*(price/book.mid-1)*1e4 + fee/(filled*book.mid)*1e4
        rows.append({"requested_quantity": q, "available_quantity": filled,
                     "unfilled_quantity": max(0.0, q-filled), "vwap": price,
                     "one_way_cost_bps_on_filled": cost,
                     "fee_quote": fee,
                     "estimated_same_cost_roundtrip_hurdle_bps": 2*cost})
    return {"source": book.source, "product": book.product, "side": side,
            "received_ns": book.received_ns, "mid": book.mid, "spread_bps": book.spread_bps,
            "assumed_fee_bps": fee_bps, "depth_fraction": depth_fraction, "quotes": rows,
            "interpretation": "as-of-snapshot estimate; fees are assumed; future exit cost and own impact unknown; "
                              "doubling entry cost is only an illustrative round-trip hurdle"}
