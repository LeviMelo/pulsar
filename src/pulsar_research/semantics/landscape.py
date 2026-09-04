"""Landscape geometry: 2-D maps that report how much they distort.

A scatter plot of research projects is the single most persuasive object in the
dashboard, and persuasion without fidelity is a bug. PCoA (classical MDS) is
fast, deterministic and closed-form, but it optimizes an inner-product criterion
and can leave large distance error. SMACOF refines the *actual* metric-MDS
stress by majorization, initialized from PCoA so it stays deterministic.

Every map is persisted together with its stress and rank correlations, and the
dashboard shows them. A map with stress 0.4 is a sketch, not a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse


def cosine_distance_matrix(vectors) -> np.ndarray:
    """Angular-style distance ``1 - cos`` from L2-normalized row vectors."""
    x = np.asarray(vectors.todense()) if sparse.issparse(vectors) else np.asarray(vectors, dtype=float)
    k = np.clip(x @ x.T, -1.0, 1.0)
    d = np.clip(1.0 - k, 0.0, 2.0)
    d = (d + d.T) / 2.0
    np.fill_diagonal(d, 0.0)
    return d


def pcoa(distances: np.ndarray, n_components: int = 2) -> np.ndarray:
    """Classical multidimensional scaling (Torgerson/Gower)."""
    d = np.asarray(distances, dtype=float)
    n = d.shape[0]
    if n == 0:
        return np.empty((0, n_components))
    j = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * j @ (d ** 2) @ j
    b = (b + b.T) / 2.0
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    positive = vals > 1e-10
    vals = vals[positive][:n_components]
    vecs = vecs[:, positive][:, :n_components]
    coords = vecs * np.sqrt(vals)[None, :] if len(vals) else np.zeros((n, 0))
    if coords.shape[1] < n_components:
        coords = np.column_stack([coords, np.zeros((n, n_components - coords.shape[1]))])
    return _orient(coords)


def _orient(coords: np.ndarray) -> np.ndarray:
    """Pin the arbitrary reflection of each axis so reruns look identical."""
    coords = np.array(coords, dtype=float)
    for col in range(coords.shape[1]):
        idx = int(np.argmax(np.abs(coords[:, col])))
        if coords[idx, col] < 0:
            coords[:, col] *= -1
    return coords


def kruskal_stress(distances: np.ndarray, coords: np.ndarray) -> float:
    """Kruskal stress-1 after optimal isotropic rescaling. Lower is better."""
    d = np.asarray(distances, dtype=float)
    lo = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=-1)
    iu = np.triu_indices(d.shape[0], k=1)
    a, b = d[iu], lo[iu]
    if not len(a) or np.dot(b, b) <= 0:
        return 0.0
    scale = float(np.dot(a, b) / np.dot(b, b))
    return float(np.sqrt(np.sum((a - b * scale) ** 2) / max(np.sum(a ** 2), 1e-12)))


def smacof(distances: np.ndarray, init: np.ndarray, *, max_iter: int = 600, tol: float = 1e-7) -> np.ndarray:
    """Metric MDS by SMACOF majorization from a fixed initialization.

    Deterministic by construction: no random restarts, no seed to remember. The
    only inputs are the distance matrix and the PCoA initialization.
    """
    d = np.asarray(distances, dtype=float)
    n = d.shape[0]
    if n < 3:
        return np.asarray(init, dtype=float)
    x = np.asarray(init, dtype=float).copy()
    # Match the initialization scale to the target distances first, otherwise
    # the first majorization step wastes iterations on a pure rescale.
    lo = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=-1)
    iu = np.triu_indices(n, k=1)
    if np.dot(lo[iu], lo[iu]) > 0:
        x *= float(np.dot(d[iu], lo[iu]) / np.dot(lo[iu], lo[iu]))

    previous = np.inf
    for _ in range(max_iter):
        lo = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=-1)
        stress = float(np.sum((d[iu] - lo[iu]) ** 2))
        ratio = np.divide(d, lo, out=np.zeros_like(d), where=lo > 1e-12)
        b = -ratio
        np.fill_diagonal(b, 0.0)
        np.fill_diagonal(b, -b.sum(axis=1))
        x = (b @ x) / n
        if previous - stress <= tol * max(previous, 1e-12):
            break
        previous = stress
    return _orient(x)


@dataclass(slots=True)
class LandscapeMap:
    coords: np.ndarray
    diagnostics: dict[str, Any]


def build_map(vectors, *, n_components: int = 2, refine: bool = True) -> LandscapeMap:
    """PCoA, optionally refined by SMACOF, with fidelity diagnostics attached."""
    from scipy.stats import pearsonr, spearmanr

    d = cosine_distance_matrix(vectors)
    n = d.shape[0]
    if n < 3:
        return LandscapeMap(np.zeros((n, n_components)), {"method": "degenerate", "points": n})

    base = pcoa(d, n_components)
    diagnostics: dict[str, Any] = {"points": int(n), "pcoa_stress": kruskal_stress(d, base)}
    coords = base
    method = "pcoa"
    if refine:
        refined = smacof(d, base)
        refined_stress = kruskal_stress(d, refined)
        diagnostics["smacof_stress"] = refined_stress
        if refined_stress < diagnostics["pcoa_stress"]:
            coords, method = refined, "pcoa+smacof"

    lo = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=-1)
    iu = np.triu_indices(n, k=1)
    a, b = d[iu], lo[iu]
    diagnostics.update({
        "method": method,
        "stress": kruskal_stress(d, coords),
        "pearson": float(pearsonr(a, b)[0]) if np.std(b) > 0 else 0.0,
        "spearman": float(spearmanr(a, b)[0]) if np.std(b) > 0 else 0.0,
    })
    return LandscapeMap(coords, diagnostics)
