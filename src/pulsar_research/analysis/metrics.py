from __future__ import annotations

import numpy as np


def gini(values) -> float:
    x = np.asarray(list(values), dtype=float)
    if x.size == 0 or np.allclose(x.sum(), 0):
        return 0.0
    x = np.sort(np.clip(x, 0, None))
    n = x.size
    idx = np.arange(1, n + 1)
    return float(np.sum((2 * idx - n - 1) * x) / (n * x.sum()))


def hhi(values) -> float:
    x = np.asarray(list(values), dtype=float)
    total = x.sum()
    if total <= 0:
        return 0.0
    shares = x / total
    return float(np.sum(shares ** 2))
