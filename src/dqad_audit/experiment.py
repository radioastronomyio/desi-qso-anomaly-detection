"""Bounded PCA baseline and descriptive nuisance diagnostics for window review.

Only training rows enter imputation, centering and the PCA basis. Held-out
coefficients are fitted on each object's valid pixels using its diagonal ivar.
The residual score is deliberately descriptive, not a calibrated probability.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

CANDIDATES = {
    "W1": (0.5, 0.75, 43633),
    "W2": (1.5, 1.75, 144523),
    "W3": (1.0, 2.5, 733045),
}
SEED = 20261004


@dataclass(frozen=True)
class Normalized:
    flux: np.ndarray
    ivar: np.ndarray
    scale: np.ndarray
    included: np.ndarray
    valid_fraction: np.ndarray


@dataclass(frozen=True)
class Baseline:
    active: np.ndarray
    center: np.ndarray
    components: np.ndarray
    fill: np.ndarray
    singular_values: np.ndarray
    n_train: int


def select_candidates(
    objects: pd.DataFrame, seed: int = SEED, n_train: int = 576, n_holdout: int = 192
) -> dict[str, pd.DataFrame]:
    """Uniform row samples within a globally shared, seeded tile split."""
    if objects.target_id.duplicated().any() or objects.row_uid.duplicated().any():
        raise ValueError("selection requires unique objects")
    tiles = np.array(sorted(objects.tile_id.unique()))
    rng = np.random.default_rng(seed)
    holdout = set(rng.permutation(tiles)[: max(1, len(tiles) // 4)])
    result = {}
    for key, (lo, hi, _) in CANDIDATES.items():
        eligible = objects[(objects.z >= lo) & (objects.z < hi)].sort_values("row_uid")
        parts = []
        for split, count in [("train", n_train), ("holdout", n_holdout)]:
            pool = eligible[eligible.tile_id.isin(holdout) == (split == "holdout")]
            if len(pool) < count:
                raise ValueError(f"insufficient eligible objects: {key} {split}")
            chosen = pool.iloc[rng.choice(len(pool), count, replace=False)].copy()
            chosen["split"] = split
            parts.append(chosen)
        result[key] = pd.concat(parts).sort_values("row_uid").reset_index(drop=True)
    return result


def normalize(flux: np.ndarray, ivar: np.ndarray) -> Normalized:
    """Median-normalize each row, propagating inverse variance by scale squared."""
    flux, ivar = np.asarray(flux, float), np.asarray(ivar, float)
    if flux.ndim != 2 or flux.shape != ivar.shape:
        raise ValueError("normalization requires aligned matrices")
    valid = np.isfinite(flux) & np.isfinite(ivar) & (ivar > 0)
    fractions = valid.mean(axis=1)
    scales = np.array([np.median(f[v]) if v.any() else np.nan for f, v in zip(flux, valid, strict=True)])
    include = np.isfinite(scales) & (scales > 0) & (fractions >= 0.8)
    safe = np.where(include, scales, 1.0)
    usable = valid & include[:, None]
    return Normalized(
        np.where(usable, flux / safe[:, None], 0.0),
        np.where(usable, ivar * safe[:, None] ** 2, 0.0),
        scales,
        include,
        fractions,
    )


def fit_baseline(flux: np.ndarray, ivar: np.ndarray, components: int = 8) -> Baseline:
    """Fit ordinary PCA after training-only support selection and median fill."""
    flux, ivar = np.asarray(flux, float), np.asarray(ivar, float)
    if flux.ndim != 2 or flux.shape != ivar.shape or not len(flux):
        raise ValueError("training requires nonempty aligned matrices")
    valid = (ivar > 0) & np.isfinite(ivar) & np.isfinite(flux)
    active = valid.mean(axis=0) >= 0.8
    if min(len(flux) - 1, int(active.sum())) < components or components < 1:
        raise ValueError("insufficient training rows or supported pixels for PCA")
    x = flux[:, active]
    good = valid[:, active]
    fill = np.array([np.median(x[good[:, j], j]) for j in range(x.shape[1])])
    x = np.where(good, x, fill)
    center = x.mean(axis=0)
    _, singular, basis = np.linalg.svd(x - center, full_matrices=False)
    return Baseline(active, center, basis[:components], fill, singular[:components], len(flux))


def score_baseline(model: Baseline, flux: np.ndarray, ivar: np.ndarray, overlap: np.ndarray) -> dict[str, np.ndarray]:
    """Fit held-out coefficients; return residuals and overlap sensitivity."""
    if flux.shape != ivar.shape or flux.shape != overlap.shape or flux.shape[1] != len(model.active):
        raise ValueError("scoring arrays must align with the fitted model")
    n = len(flux)
    values = {
        key: np.full(n, np.nan)
        for key in ["score", "mse", "score_without_overlap", "overlap_residual_fraction", "overlap_pixel_fraction"]
    }
    recon = np.full(flux.shape, np.nan)
    coefficients = np.full((n, len(model.components)), np.nan)
    for i in range(n):
        f, v, joint = flux[i, model.active], ivar[i, model.active], overlap[i, model.active]
        good = (v > 0) & np.isfinite(v) & np.isfinite(f)
        if good.sum() <= len(model.components):
            continue
        weights = np.sqrt(v[good])
        design = model.components[:, good].T * weights[:, None]
        coeff, _, rank, _ = np.linalg.lstsq(design, (f[good] - model.center[good]) * weights, rcond=None)
        if rank < len(model.components):
            continue
        prediction = model.center + coeff @ model.components
        residual = np.where(good, f - prediction, 0.0)
        squared = residual**2 * np.where(good, v, 0.0)
        values["score"][i] = squared.sum() / good.sum()
        values["mse"][i] = (residual**2).sum() / good.sum()
        outside = good & ~joint
        if outside.any():
            values["score_without_overlap"][i] = squared[outside].mean()
        values["overlap_residual_fraction"][i] = squared[joint].sum() / squared.sum() if squared.sum() > 0 else 0.0
        values["overlap_pixel_fraction"][i] = (good & joint).sum() / good.sum()
        recon[i, model.active] = prediction
        coefficients[i] = coeff
    values.update(reconstruction=recon, coefficients=coefficients)
    return values


def _correlation(a: np.ndarray, b: np.ndarray) -> dict:
    valid = np.isfinite(a) & np.isfinite(b)
    x, y = np.asarray(a)[valid], np.asarray(b)[valid]
    result = {"n": len(x), "rho": None, "p_descriptive": None, "status": "constant_or_insufficient"}
    if len(x) >= 3 and len(np.unique(x)) > 1 and len(np.unique(y)) > 1:
        rho, p = spearmanr(x, y)
        result.update(rho=float(rho), p_descriptive=float(p), status="measured")
    return result


def nuisance_diagnostics(frame: pd.DataFrame, seed: int = SEED) -> dict:
    """Quantify descriptive nuisance associations without imputing missing values."""
    frame = frame[np.isfinite(frame.score)].copy()
    columns = ["snr_proxy", "masked_fraction", "zero_ivar_fraction", "z", "valid_fraction"]
    correlations = {key: _correlation(frame.score.to_numpy(), frame[key].to_numpy()) for key in columns}
    deciles = []
    ordered = frame.sort_values(["score", "row_uid"])
    for i, indices in enumerate(np.array_split(np.arange(len(ordered)), 10)):
        subset = ordered.iloc[indices]
        deciles.append(
            {
                "score_decile": i + 1,
                "n": len(subset),
                "score_median": _finite_median(subset.score),
                **{key: _finite_median(subset[key]) for key in columns},
            }
        )
    counts = frame.tile_id.value_counts()
    repeated = frame[frame.tile_id.isin(counts[counts >= 2].index)]
    tile = {
        "n_tiles": len(counts),
        "singleton_tiles": int((counts == 1).sum()),
        "repeat_tiles": int((counts >= 2).sum()),
        "repeat_rows": len(repeated),
        "status": "insufficient_repeat_tiles",
        "eta_squared": None,
        "p_permutation": None,
    }
    if tile["repeat_tiles"] >= 5 and len(repeated) >= 15 and repeated.score.var() > 0:
        codes, groups = pd.factorize(repeated.tile_id)
        group_counts = np.bincount(codes)
        scores = repeated.score.to_numpy()
        overall = scores.mean()
        total = ((scores - overall) ** 2).sum()

        def eta(y):
            means = np.bincount(codes, weights=y) / group_counts
            return float(np.sum(group_counts * (means - overall) ** 2) / total)

        observed = eta(scores)
        rng = np.random.default_rng(seed)
        permutation = np.array([eta(rng.permutation(scores)) for _ in range(499)])
        tile.update(
            status="measured_on_repeated_tiles_only",
            eta_squared=observed,
            p_permutation=float((1 + (permutation >= observed).sum()) / 500),
            permutations=499,
        )
    count = min(10, len(frame))
    highest = set(frame.nlargest(count, "score").row_uid)
    without = set(frame.nlargest(count, "score_without_overlap").row_uid)
    overlap = {
        "score_correlation_without_overlap": _correlation(
            frame.score.to_numpy(), frame.score_without_overlap.to_numpy()
        ),
        "top_count": count,
        "top_retained_without_overlap": len(highest & without),
        "median_residual_fraction": _finite_median(frame.overlap_residual_fraction),
        "median_pixel_fraction": _finite_median(frame.overlap_pixel_fraction),
        "residual_fraction_correlation": _correlation(
            frame.score.to_numpy(), frame.overlap_residual_fraction.to_numpy()
        ),
        "pixel_fraction_correlation": _correlation(frame.score.to_numpy(), frame.overlap_pixel_fraction.to_numpy()),
    }
    return {"correlations": correlations, "score_deciles": deciles, "tile": tile, "overlap": overlap}


def _finite_median(values) -> float | None:
    a = np.asarray(values, float)
    return float(np.median(a[np.isfinite(a)])) if np.isfinite(a).any() else None
