"""Experimental spectral transforms, with explicit validity and diagonal variance.

Coaddition assumes independent arm noise. Bin averaging propagates the diagonal
variance exactly under that assumption; neighboring output bins can still share
native pixels, so the returned ivar is not a complete covariance description.
Observed flux-density units are retained when moving the wavelength coordinate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

WAVE_MIN = 3600.0
WAVE_MAX = 9799.2001953125
OVERLAPS = ((5760.0, 5800.0), (7520.0, 7620.0))


@dataclass(frozen=True)
class MergedSpectrum:
    wavelength: np.ndarray
    flux: np.ndarray
    ivar: np.ndarray
    mask_bits: np.ndarray
    contributors: np.ndarray

    @property
    def valid(self) -> np.ndarray:
        return self.ivar > 0


@dataclass(frozen=True)
class RebinnedSpectrum:
    wavelength: np.ndarray
    flux: np.ndarray
    ivar: np.ndarray
    coverage: np.ndarray
    overlap: np.ndarray

    @property
    def valid(self) -> np.ndarray:
        return self.ivar > 0


def _arrays(wave, flux, ivar, mask) -> tuple[np.ndarray, ...]:
    w, f, v = (np.asarray(x, dtype=float) for x in (wave, flux, ivar))
    m = np.asarray(mask)
    if any(x.ndim != 1 for x in (w, f, v, m)) or not (len(w) == len(f) == len(v) == len(m)) or not len(w):
        raise ValueError("spectral arrays must be nonempty aligned one-dimensional arrays")
    if not np.isfinite(w).all() or (w <= 0).any():
        raise ValueError("wavelength must be finite and positive")
    if not np.isfinite(v).all() or (v < 0).any():
        raise ValueError("ivar must be finite and nonnegative")
    if not np.issubdtype(m.dtype, np.integer) or (m < 0).any():
        raise ValueError("mask must be nonnegative integer bits")
    return w, f, v, m.astype(np.int64)


def validate_audit_contract(row: dict) -> None:
    """Reject structural changes to the sampled Stage 0 corpus contract."""
    w, _, _, _ = _arrays(*(row[k] for k in ("wavelength", "flux", "ivar", "mask_array")))
    unique, counts = np.unique(w, return_counts=True)
    duplicated = unique[counts > 1]
    correct = (
        len(w) == 7927
        and len(unique) == 7750
        and np.array_equal(np.flatnonzero(np.diff(w) < 0), [2750, 5076])
        and w.min() == WAVE_MIN
        and w.max() == WAVE_MAX
        and np.all(counts <= 2)
        and len(duplicated) == 177
        and sum((duplicated >= 5760) & (duplicated <= 5800)) == 51
        and sum((duplicated >= 7520) & (duplicated <= 7620)) == 126
    )
    if not correct:
        raise ValueError("live spectrum violates Stage 0 wavelength contract")


def merge_pixels(wave, flux, ivar, mask) -> MergedSpectrum:
    """Stable-sort and coadd exact equals, excluding invalid contributors.

    Source bitwise masks are retained separately from output validity, so a
    flagged arm cannot invalidate the other arm's usable measurement.
    """
    w, f, v, m = _arrays(wave, flux, ivar, mask)
    order = np.argsort(w, kind="stable")
    w, f, v, m = (x[order] for x in (w, f, v, m))
    start = np.r_[0, np.flatnonzero(np.diff(w) != 0) + 1]
    usable = (m == 0) & (v > 0) & np.isfinite(f)
    weights = np.where(usable, v, 0.0)
    total = np.add.reduceat(weights, start)
    numerator = np.add.reduceat(weights * np.where(usable, f, 0.0), start)
    mean = np.divide(numerator, total, out=np.zeros_like(total), where=total > 0)
    return MergedSpectrum(w[start], mean, total, np.bitwise_or.reduceat(m, start), np.diff(np.r_[start, len(w)]))


def common_grid(z_low: float, z_high: float, step: float = 4.0) -> np.ndarray:
    """Return bin edges fully inside the conservative common rest interval."""
    if not np.isfinite([z_low, z_high, step]).all() or not (0 <= z_low < z_high) or step <= 0:
        raise ValueError("invalid redshift window or grid step")
    low, high = WAVE_MIN / (1 + z_low), WAVE_MAX / (1 + z_high)
    first, last = np.ceil(low / step), np.floor(high / step)
    if last <= first:
        raise ValueError("redshift window has no usable common rest interval")
    return np.arange(first, last + 1) * step


def rebin_rest(merged: MergedSpectrum, z: float, edges: np.ndarray) -> RebinnedSpectrum:
    """Average piecewise-constant native pixels; require complete valid support.

    For overlaps l_i and output width D, variance is sum((l_i/D)^2/ivar_i).
    No bad pixel is interpolated, and there is no extrapolation. Input flux
    amplitudes are unchanged by the rest-coordinate transform.
    """
    edges = np.asarray(edges, dtype=float)
    if not np.isfinite(z) or z < 0:
        raise ValueError("redshift must be finite and nonnegative")
    if edges.ndim != 1 or len(edges) < 2 or not np.isfinite(edges).all() or (np.diff(edges) <= 0).any():
        raise ValueError("output edges must be finite and strictly increasing")
    w = merged.wavelength / (1 + z)
    if len(w) < 2 or (np.diff(w) <= 0).any():
        raise ValueError("at least two strictly increasing merged wavelengths are required")
    native = np.r_[w[0] - (w[1] - w[0]) / 2, (w[1:] + w[:-1]) / 2, w[-1] + (w[-1] - w[-2]) / 2]
    start = np.clip(np.searchsorted(native, edges[:-1], side="right") - 1, 0, len(w) - 1)
    stop = np.clip(np.searchsorted(native, edges[1:], side="left"), 0, len(w))
    span = max(1, int(np.max(stop - start)))
    indices = start[:, None] + np.arange(span)
    in_range = indices < len(w)
    indices = np.minimum(indices, len(w) - 1)
    length = np.maximum(
        0.0, np.minimum(native[indices + 1], edges[1:, None]) - np.maximum(native[indices], edges[:-1, None])
    )
    length = np.where(in_range, length, 0.0)
    width = np.diff(edges)
    good_length = np.where(merged.valid[indices], length, 0.0)
    coverage = good_length.sum(axis=1) / width
    coefficients = good_length / width[:, None]
    native_variance = np.divide(1.0, merged.ivar, out=np.zeros_like(merged.ivar), where=merged.valid)
    variance = np.sum(coefficients**2 * native_variance[indices], axis=1)
    valid = (coverage >= 1 - 1e-8) & (variance > 0)
    flux = np.where(valid, np.sum(coefficients * merged.flux[indices], axis=1), 0.0)
    ivar = np.divide(1.0, variance, out=np.zeros_like(variance), where=valid)
    overlap = np.any((length > 0) & (merged.contributors[indices] > 1), axis=1)
    return RebinnedSpectrum((edges[1:] + edges[:-1]) / 2, flux, ivar, np.clip(coverage, 0, 1), overlap)


def load_rest_spectrum(row: dict, z_low: float, z_high: float, step: float = 4.0) -> RebinnedSpectrum:
    """Validate one corpus row, merge arms, and place it in the chosen window."""
    z = float(row["z"])
    edges = common_grid(z_low, z_high, step)
    if not z_low <= z < z_high:
        raise ValueError("object redshift is outside the selected window")
    validate_audit_contract(row)
    merged = merge_pixels(*(row[k] for k in ("wavelength", "flux", "ivar", "mask_array")))
    return rebin_rest(merged, z, edges)
