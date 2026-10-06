"""Causal snapshot features. Snapshot OFI is a sampled proxy, not full event OFI."""
from __future__ import annotations

import numpy as np

from .data import Book

FEATURES = ("spread_bps", "depth_imbalance", "microprice_offset_bps", "sampled_ofi",
            "last_return_bps", "rolling_vol_bps", "log_depth", "elapsed_seconds")


def vector(book: Book, previous: Book | None, past_returns: list[float]) -> list[float]:
    bid, bq = book.bids[0]
    ask, aq = book.asks[0]
    bd = sum(q for _, q in book.bids[:5])
    ad = sum(q for _, q in book.asks[:5])
    micro = (ask * bq + bid * aq) / (bq + aq)
    ofi, ret, elapsed = 0.0, 0.0, 0.0
    if previous:
        pb, pbq = previous.bids[0]
        pa, paq = previous.asks[0]
        ofi = ((bq if bid >= pb else 0) - (pbq if bid <= pb else 0)
               - (aq if ask <= pa else 0) + (paq if ask >= pa else 0)) / (bq + aq + pbq + paq)
        ret = (book.mid / previous.mid - 1) * 1e4
        elapsed = (book.received_ns - previous.received_ns) / 1e9
    return [book.spread_bps, (bd - ad) / (bd + ad), (micro / book.mid - 1) * 1e4,
            ofi, ret, float(np.std(past_returns[-30:])) if past_returns else 0.0,
            float(np.log1p(bd + ad)), elapsed]


def matrix(books: list[Book]) -> np.ndarray:
    rows, returns = [], []
    for i, book in enumerate(books):
        row = vector(book, books[i-1] if i else None, returns)
        rows.append(row)
        returns.append(row[4])
    return np.asarray(rows)
