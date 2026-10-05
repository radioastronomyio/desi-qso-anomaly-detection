"""Tests for split independence, uncertainty scaling and residual ranking."""

import numpy as np
import pandas as pd
import pytest

from dqad_audit.experiment import fit_baseline, normalize, nuisance_diagnostics, score_baseline, select_candidates


def test_normalization_scales_ivar_and_rejects_nonpositive_or_sparse_rows():
    result = normalize(
        np.array([[2.0, 4.0, 6.0], [-1.0, -2.0, -3.0], [1.0, 2.0, 3.0]]),
        np.array([[1.0, 2.0, 3.0], [1.0, 1.0, 1.0], [0.0, 0.0, 1.0]]),
    )
    np.testing.assert_allclose(result.flux[0], [0.5, 1, 1.5])
    np.testing.assert_allclose(result.ivar[0], [16, 32, 48])
    np.testing.assert_array_equal(result.included, [True, False, False])


def test_injected_shape_outlier_outranks_normals_and_holdout_cannot_change_model():
    rng = np.random.default_rng(7)
    x = np.linspace(0, 1, 30)
    train = 1 + rng.normal(size=(40, 1)) * 0.1 * x + rng.normal(0, 0.005, (40, 30))
    model = fit_baseline(train, np.ones_like(train) * 100, components=2)
    center = model.center.copy()
    basis = model.components.copy()
    holdout = np.ones((4, 30))
    holdout[-1, 10:13] += 3
    result = score_baseline(model, holdout, np.ones_like(holdout) * 100, np.zeros_like(holdout, dtype=bool))
    assert result["score"][-1] > 20 * max(result["score"][:-1])
    changed = holdout.copy()
    changed[:] = 99
    score_baseline(model, changed, np.ones_like(changed), np.zeros_like(changed, dtype=bool))
    np.testing.assert_array_equal(model.center, center)
    np.testing.assert_array_equal(model.components, basis)
    again = score_baseline(model, holdout, np.ones_like(holdout) * 100, np.zeros_like(holdout, dtype=bool))
    np.testing.assert_array_equal(result["score"], again["score"])


def test_masked_outlier_has_no_score_contribution():
    rng = np.random.default_rng(9)
    train = rng.normal(1, 0.01, (40, 30))
    model = fit_baseline(train, np.ones_like(train), components=2)
    flux = np.ones((2, 30))
    flux[1, 3] = 1e10
    ivar = np.ones_like(flux)
    ivar[:, 3] = 0
    result = score_baseline(model, flux, ivar, np.zeros_like(flux, dtype=bool))
    assert result["score"][0] == pytest.approx(result["score"][1])


def test_training_only_support_removes_missing_columns():
    flux = np.arange(120, dtype=float).reshape(12, 10) + 1
    ivar = np.ones_like(flux)
    ivar[:4, 0] = 0
    model = fit_baseline(flux, ivar, components=2)
    assert not model.active[0]
    assert model.active[1:].all()


def test_seeded_selection_is_tile_disjoint_across_overlapping_windows():
    rows = []
    for i in range(60):
        for j, z in enumerate([0.6, 1.6, 2.2]):
            rows.append(dict(tile_id=str(i), row_uid=f"{i}:{j}", target_id=3 * i + j, z=z))
    objects = pd.DataFrame(rows)
    a = select_candidates(objects, seed=20261004, n_train=10, n_holdout=5)
    b = select_candidates(objects, seed=20261004, n_train=10, n_holdout=5)
    train_tiles, holdout_tiles = set(), set()
    for key in a:
        pd.testing.assert_frame_equal(a[key], b[key])
        frame = a[key]
        assert frame.split.value_counts().to_dict() == {"train": 10, "holdout": 5}
        train_tiles.update(frame[frame.split == "train"].tile_id)
        holdout_tiles.update(frame[frame.split == "holdout"].tile_id)
        assert frame.target_id.is_unique
    assert train_tiles.isdisjoint(holdout_tiles)


def test_missing_nuisance_is_reported_not_replaced_with_zero():
    frame = pd.DataFrame(
        dict(
            score=[1.0, 2.0, 3.0, 4.0],
            snr_proxy=[np.nan] * 4,
            masked_fraction=[0.0] * 4,
            zero_ivar_fraction=[0.1, 0.2, 0.3, 0.4],
            z=[1.0, 2.0, 3.0, 4.0],
            valid_fraction=[1.0] * 4,
            tile_id=["a", "b", "c", "d"],
            score_without_overlap=[1.0, 2.0, 3.0, 4.0],
            overlap_residual_fraction=[0.0] * 4,
            overlap_pixel_fraction=[0.0] * 4,
            row_uid=["a", "b", "c", "d"],
        )
    )
    d = nuisance_diagnostics(frame, seed=1)
    assert d["correlations"]["snr_proxy"]["n"] == 0
    assert d["correlations"]["snr_proxy"]["rho"] is None
    assert d["correlations"]["masked_fraction"]["status"] == "constant_or_insufficient"
    assert d["correlations"]["zero_ivar_fraction"]["rho"] == pytest.approx(1)
    assert d["tile"]["status"] == "insufficient_repeat_tiles"


def test_report_renders_insufficient_overlap_and_preserves_large_target_ids(tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "loader_runner", Path(__file__).parents[1] / "scripts/06_loader_experiment.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    frame = pd.DataFrame(
        dict(
            score=[1.0, 1.0, 1.0],
            snr_proxy=[1.0, 2.0, 3.0],
            masked_fraction=[0.0] * 3,
            zero_ivar_fraction=[0.0] * 3,
            z=[0.6] * 3,
            valid_fraction=[1.0] * 3,
            tile_id=["a", "b", "c"],
            score_without_overlap=[1.0] * 3,
            overlap_residual_fraction=[0.0] * 3,
            overlap_pixel_fraction=[0.0] * 3,
            row_uid=["a:0", "b:0", "c:0"],
            target_id=[39627953495081494, 39627953495081495, 39627953495081496],
            rank=[1, 2, 3],
        )
    )
    frame.to_csv(tmp_path / "W1-scores.csv", index=False)
    (tmp_path / "W1-diagnostics.json").write_text(json.dumps(nuisance_diagnostics(frame)))
    summary = dict(
        window=[0.5, 0.75],
        population=43633,
        rest_edges=[2400.0, 5596.0],
        train_included=576,
        holdout_included=3,
        scored=3,
        modeled_pixels=799,
        median_score=1.0,
        selected=579,
        exclusions={},
        median_valid_fraction=1.0,
    )
    runner.write_report(tmp_path, {"W1": summary}, {"cross_candidate_shared_targets": {}})
    report = (tmp_path / "report.md").read_text()
    assert "insufficient" in report
    assert "39627953495081495" in report


@pytest.mark.parametrize("window_order", [("W2",), ("W3", "W1", "W2")])
def test_window_sample_is_independent_of_candidate_order_and_subset(monkeypatch, window_order):
    from dqad_audit import experiment

    objects = pd.DataFrame(
        [
            dict(tile_id=str(i), row_uid=f"{i}:{j}", target_id=3 * i + j, z=z)
            for i in range(60)
            for j, z in enumerate([0.6, 1.6, 2.2])
        ]
    )
    reference = select_candidates(objects, seed=20261004, n_train=10, n_holdout=5)["W2"]
    candidates = experiment.CANDIDATES.copy()
    monkeypatch.setattr(experiment, "CANDIDATES", {key: candidates[key] for key in window_order})
    changed = select_candidates(objects, seed=20261004, n_train=10, n_holdout=5)["W2"]
    pd.testing.assert_frame_equal(reference, changed)


@pytest.mark.parametrize("window_id", ["W1", "W2", "W3"])
def test_explicit_window_subset_retains_its_sample_and_global_split(window_id):
    objects = pd.DataFrame(
        [
            dict(tile_id=str(i), row_uid=f"{i}:{j}", target_id=3 * i + j, z=z)
            for i in range(60)
            for j, z in enumerate([0.6, 1.6, 2.2])
        ]
    )
    all_windows = select_candidates(objects, n_train=10, n_holdout=5)
    single = select_candidates(objects, n_train=10, n_holdout=5, window_ids=(window_id,))
    assert list(single) == [window_id]
    pd.testing.assert_frame_equal(all_windows[window_id], single[window_id])
