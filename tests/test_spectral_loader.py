"""Independent arithmetic and measured-contract tests for the experimental loader."""

import numpy as np
import pytest

from dqad_audit.spectral_loader import common_grid, merge_pixels, rebin_rest, validate_audit_contract


def audit_row():
    wave = (
        np.concatenate([np.linspace(3600, 5800, 2751), np.linspace(5760, 7620, 2326), np.linspace(7520, 9799.2, 2850)])
        .astype(np.float32)
        .astype(float)
    )
    return dict(wavelength=wave, flux=np.ones(7927), ivar=np.ones(7927), mask_array=np.zeros(7927, dtype=int))


def test_exact_coadd_uses_valid_inverse_variances_and_preserves_flags():
    # Keeping the first occurrence or OR-masking the good duplicate must fail.
    wave = np.array([3.0, 1.0, 2.0, 2.0, 1.0, 3.0])
    original = wave.copy()
    merged = merge_pixels(wave, [9, 2, 10, 14, 100, 50], [0, 4, 1, 3, 100, 5], [0, 0, 0, 0, 1, 1])
    np.testing.assert_array_equal(wave, original)
    np.testing.assert_array_equal(merged.wavelength, [1, 2, 3])
    np.testing.assert_allclose(merged.flux, [2, 13, 0])
    np.testing.assert_allclose(merged.ivar, [4, 4, 0])
    np.testing.assert_array_equal(merged.mask_bits, [1, 0, 1])
    np.testing.assert_array_equal(merged.valid, [True, True, False])
    np.testing.assert_array_equal(merged.contributors, [2, 2, 2])


def test_near_equal_wavelengths_remain_distinct_and_nonfinite_flux_is_ignored():
    m = merge_pixels([1, 1.001, 2, 2], [4, 5, np.nan, 8], [1, 1, 2, 3], [0, 0, 0, 0])
    np.testing.assert_allclose(m.wavelength, [1, 1.001, 2])
    np.testing.assert_allclose(m.flux, [4, 5, 8])
    np.testing.assert_allclose(m.ivar, [1, 1, 3])


def test_audit_shape_and_breaks_are_enforced():
    row = audit_row()
    validate_audit_contract(row)
    m = merge_pixels(row["wavelength"], row["flux"], row["ivar"], row["mask_array"])
    assert len(m.wavelength) == 7750
    assert np.all(np.diff(m.wavelength) > 0)
    assert (m.contributors > 1).sum() == 177
    row["wavelength"] = np.sort(row["wavelength"])
    with pytest.raises(ValueError, match="contract"):
        validate_audit_contract(row)


@pytest.mark.parametrize(
    "wave,flux,ivar,mask",
    [
        ([1, 2], [1], [1, 1], [0, 0]),
        ([1, np.nan], [1, 1], [1, 1], [0, 0]),
        ([1, 2], [1, 1], [-1, 1], [0, 0]),
        ([1, 2], [1, 1], [np.inf, 1], [0, 0]),
        ([1, 2], [1, 1], [1, 1], [0, 0.5]),
        ([1, 2], [1, 1], [1, 1], [0, -1]),
    ],
)
def test_invalid_input_fails(wave, flux, ivar, mask):
    with pytest.raises(ValueError):
        merge_pixels(wave, flux, ivar, mask)


def test_bin_mean_and_variance_use_squared_overlap_coefficients():
    m = merge_pixels([1, 2, 3], [2, 6, 10], [1, 4, 1], [0, 0, 0])
    b = rebin_rest(m, 0.0, np.array([0.5, 2.0, 3.5]))
    # First bin weights are 2/3,1/3: var=4/9 + (1/9)/4 = 17/36.
    np.testing.assert_allclose(b.flux, [10 / 3, 26 / 3])
    np.testing.assert_allclose(b.ivar, [36 / 17, 36 / 17])
    np.testing.assert_array_equal(b.valid, [True, True])
    np.testing.assert_allclose(b.coverage, [1, 1])


def test_masks_zero_ivar_and_extrapolation_cannot_become_valid():
    m = merge_pixels([1, 2, 3], [2, 6, 10], [1, 0, 1], [0, 0, 1])
    b = rebin_rest(m, 0.0, np.array([0.0, 1.0, 2.0, 3.0, 4.0]))
    assert not b.valid.any()
    np.testing.assert_array_equal(b.flux, np.zeros(4))
    np.testing.assert_array_equal(b.ivar, np.zeros(4))
    np.testing.assert_allclose(b.coverage, [0.5, 0.5, 0, 0])


def test_rest_coordinates_keep_amplitude_and_report_overlap():
    m = merge_pixels([2, 4, 4, 6], [2, 6, 6, 10], [1, 2, 2, 1], [0, 0, 0, 0])
    b = rebin_rest(m, 1.0, np.array([0.5, 2.0, 3.5]))
    np.testing.assert_allclose(b.flux, [10 / 3, 26 / 3])
    np.testing.assert_allclose(b.ivar, [36 / 17, 36 / 17])
    assert b.overlap.all()


@pytest.mark.parametrize("lo,hi", [(0.5, 0.75), (1.5, 1.75), (1.0, 2.5)])
def test_grid_is_inside_every_objects_rest_support(lo, hi):
    edges = common_grid(lo, hi)
    assert edges[0] >= 3600 / (1 + lo)
    assert edges[-1] <= 9799.2001953125 / (1 + hi)
    assert np.all(np.diff(edges) == 4)
    for z in [lo, (lo + hi) / 2, np.nextafter(hi, lo)]:
        assert edges[0] >= 3600 / (1 + z)
        assert edges[-1] <= 9799.2001953125 / (1 + z)


@pytest.mark.parametrize("lo,hi", [(0.05123, 6.802568), (2, 1), (-1, 1), (1, np.nan)])
def test_bad_or_disjoint_window_rejected(lo, hi):
    with pytest.raises(ValueError):
        common_grid(lo, hi)


def test_selected_window_rejects_upper_endpoint_and_accepts_lower():
    from dqad_audit.spectral_loader import load_rest_spectrum

    row = audit_row()
    row["z"] = 1.5
    result = load_rest_spectrum(row, 1.5, 1.75)
    assert result.valid.all()
    row["z"] = 1.75
    with pytest.raises(ValueError, match="outside"):
        load_rest_spectrum(row, 1.5, 1.75)
