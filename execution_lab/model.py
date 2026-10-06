"""Frozen ridge forecast with a label-maturity purge and train-only normalization."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class RidgeForecast:
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float
    label_end_max: int
    train_rows: int

    def predict(self, x: np.ndarray) -> float:
        return float((x - self.mean) / self.scale @ self.coef + self.intercept)

    def record(self):
        return {"mean": self.mean.tolist(), "scale": self.scale.tolist(),
                "coef": self.coef.tolist(), "intercept": self.intercept,
                "label_end_max": self.label_end_max, "train_rows": self.train_rows}


def fit_past(X: np.ndarray, mids: np.ndarray, start: int, horizon: int,
             lookback: int, alpha=10.0, embargo=1) -> RidgeForecast:
    if horizon < 1 or embargo < 1 or alpha <= 0 or lookback < 20:
        raise ValueError("invalid model configuration")
    # A label uses mid[i+horizon]. Its end must be STRICTLY before start-embargo.
    stop = start - embargo - horizon
    indices = np.arange(max(0, stop - lookback), stop)
    if len(indices) < 20:
        raise ValueError("fewer than 20 fully matured training labels")
    train = X[indices]
    y = (mids[indices+horizon] / mids[indices] - 1) * 1e4
    mean, scale = train.mean(axis=0), np.maximum(train.std(axis=0), 1e-8)
    z = (train - mean) / scale
    center = float(y.mean())
    coef = np.linalg.solve(z.T @ z + alpha * np.eye(z.shape[1]), z.T @ (y-center))
    return RidgeForecast(mean, scale, coef, center, int(indices[-1]+horizon), len(indices))
