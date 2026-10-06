"""Seeded mechanical fixture; deliberately labeled synthetic at every boundary."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .data import Book


def generate(path: Path, count=1800, seed=42) -> None:
    if count < 100:
        raise ValueError("fixture requires >=100 snapshots")
    rng = np.random.default_rng(seed)
    mid, pressure = 60000.0, 0.0
    epoch = 1_700_000_000_000_000_000
    with path.open("x") as f:
        for i in range(count):
            # A controlled predictive relationship tests plumbing, not real alpha.
            mid *= 1 + (0.15*pressure + rng.normal(0, 0.35)) / 1e4
            pressure = float(0.85*pressure + rng.normal(0, 0.35))
            half = mid*(0.12 + rng.uniform(0, 0.15))/1e4
            step = mid*0.2/1e4
            liquidity = 0.3 if 500 <= i % 900 < 530 else 1.0
            bsize = liquidity*0.4*np.exp(pressure)
            asize = liquidity*0.4*np.exp(-pressure)
            bids = tuple((mid-half-j*step, float(bsize*rng.uniform(.5, 1.5))) for j in range(10))
            asks = tuple((mid+half+j*step, float(asize*rng.uniform(.5, 1.5))) for j in range(10))
            t = epoch + i*1_000_000_000
            b = Book("BTC-USD", t+20_000_000, t, i, bids, asks, "synthetic")
            f.write(json.dumps(b.row(), allow_nan=False)+"\n")
