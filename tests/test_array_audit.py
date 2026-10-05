#!/usr/bin/env python3
"""
Script Name  : test_array_audit.py
Description  : Unit and script tests for the D3 array contract audit.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers the sampled array contract audit: the six per-row checks over
hand-built rows with known defects (alignment break, mono breaks at known
indices, exact 40-Angstrom arm overlap, duplicate and near-duplicate
wavelengths, negative/zero/non-finite ivar, specific mask values),
deterministic systematic tile/row sampling (same seed twice, different
seed differs), the run-config fence over the array_audit parts family
(different sampling parameters invalidate checkpoints), the merge-policy
recommendation derived from measured stats, summary assembly, and the full
script over a synthetic corpus including resume and fence pruning. All
fixtures are synthetic files under tmp_path; the real corpus is never
touched.

Usage
-----
    pytest tests/test_array_audit.py

Examples
--------
    pytest tests/test_array_audit.py -k overlap
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import write_qso_table
from dqad_audit import (
    ARRAY_AUDIT_FILENAME,
    ARRAY_AUDIT_PART_KIND,
    ARRAY_AUDIT_SCHEMA,
    ARRAY_AUDIT_SUMMARY_FILENAME,
    array_audit_part_table,
    audit_arrays,
    audit_chunk,
    build_array_audit_run_config,
    build_array_audit_summary,
    child_seed_for_tile,
    project_array_audit_table,
    read_kind_manifest,
    recommend_merge_policy,
    sampled_tile_selection,
    select_row_indices,
    select_sampled_tiles,
    write_kind_manifest,
)

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "03_array_contract_audit.py"


def three_arm_wave(*, rz_gap: float = 0.0, offset: float = 0.0) -> np.ndarray:
    """Build a concatenated three-arm wavelength grid with known overlaps.

    Segment 1 covers 100..149, segment 2 restarts at 109 (a 40-Angstrom
    B/R-style overlap), and segment 3 restarts at 118 (another 40-Angstrom
    R/Z-style overlap). ``offset`` shifts segments 2 and 3 progressively
    (offset and 2*offset) to break exact equality; ``rz_gap`` additionally
    offsets only segment 3.
    """
    seg1 = 100.0 + np.arange(50, dtype=np.float64)
    seg2 = 109.0 + offset + np.arange(50, dtype=np.float64)
    seg3 = 118.0 + 2.0 * offset + rz_gap + np.arange(50, dtype=np.float64)
    return np.concatenate([seg1, seg2, seg3])


def make_row_table(rows: list[dict[str, object]]) -> pa.Table:
    """Build a synthetic corpus-shaped table from per-row array specs."""
    columns: dict[str, pa.Array] = {
        "target_id": pa.array(list(range(len(rows))), pa.int64()),
        "ra": pa.array([150.0] * len(rows), pa.float64()),
        "dec": pa.array([2.2] * len(rows), pa.float64()),
        "z": pa.array([2.5] * len(rows), pa.float64()),
        "wavelength": pa.array([row["wavelength"] for row in rows], pa.list_(pa.float64())),
        "flux": pa.array([row["flux"] for row in rows], pa.list_(pa.float64())),
        "ivar": pa.array([row["ivar"] for row in rows], pa.list_(pa.float64())),
        "mask_array": pa.array([row["mask_array"] for row in rows], pa.list_(pa.int64())),
    }
    return pa.table(columns)


# =============================================================================
# Functions
# =============================================================================


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("array_contract_audit_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_audit_arrays_clean_monotonic_row() -> None:
    wave = np.arange(4000.0, 4050.0)
    flux = np.ones(50)
    ivar = np.ones(50)
    mask = np.zeros(50, dtype=np.int64)
    record = audit_arrays(wave, flux, ivar, mask)
    assert record["n_pixels"] == 50
    assert record["align_ok"] is True
    assert record["n_mono_breaks"] == 0
    assert record["first_break_idx"] == -1
    assert record["overlap_br_angstrom"] != record["overlap_br_angstrom"]
    assert record["overlap_rz_angstrom"] != record["overlap_rz_angstrom"]
    assert record["n_dup_wave"] == 0
    assert record["n_neardup_wave"] == 0
    assert record["ivar_zero_count"] == 0
    assert record["wave_min"] == 4000.0
    assert record["wave_max"] == 4049.0
    assert json.loads(record["mask_value_counts"]) == {"0": 50}


def test_audit_arrays_alignment_break_detected() -> None:
    wave = np.arange(10.0)
    record = audit_arrays(wave, np.ones(10), np.ones(9), np.zeros(10, dtype=np.int64))
    assert record["align_ok"] is False


def test_audit_arrays_overlap_measures_exactly_40_angstrom() -> None:
    wave = three_arm_wave()
    record = audit_arrays(wave, np.ones(len(wave)), np.ones(len(wave)), np.zeros(len(wave), dtype=np.int64))
    assert record["overlap_br_angstrom"] == 40.0
    assert record["overlap_rz_angstrom"] == 40.0
    assert record["n_mono_breaks"] == 2
    assert record["first_break_idx"] == 49
    assert record["second_break_idx"] == 99
    assert record["br_wave_before"] == 149.0
    assert record["br_wave_after"] == 109.0


def test_audit_arrays_counts_duplicate_and_near_duplicate_values() -> None:
    wave = three_arm_wave()
    flux = np.ones(len(wave))
    ivar = np.ones(len(wave))
    mask = np.zeros(len(wave), dtype=np.int64)
    exact = audit_arrays(wave, flux, ivar, mask)
    expected_unique = len(np.unique(wave))
    assert exact["n_dup_wave"] == len(wave) - expected_unique
    assert exact["n_neardup_wave"] == 0
    shifted = audit_arrays(three_arm_wave(offset=0.05), flux, ivar, mask)
    shifted_wave = three_arm_wave(offset=0.05)
    expected_neardup = int(np.count_nonzero(np.diff(np.unique(shifted_wave)) <= 0.1))
    assert shifted["n_dup_wave"] == 0
    assert shifted["n_neardup_wave"] == expected_neardup > 0


def test_audit_arrays_ivar_and_mask_inventories() -> None:
    wave = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    ivar = np.array([-1.0, 0.0, np.nan, np.inf, 2.0])
    mask = np.array([0, 0, 1, 4, 1], dtype=np.int64)
    record = audit_arrays(wave, np.ones(5), ivar, mask)
    assert record["ivar_neg_count"] == 1
    assert record["ivar_nonfinite_count"] == 2
    assert record["ivar_zero_count"] == 1
    assert record["ivar_zero_frac"] == pytest.approx(0.2)
    assert json.loads(record["mask_value_counts"]) == {"0": 2, "1": 2, "4": 1}


def test_sampled_tile_selection_stride_and_bounds() -> None:
    start, stride, indices = sampled_tile_selection(10793, 512, 20260815)
    assert stride == 21
    assert 0 <= start < 21
    assert indices == list(range(start, 10793, 21))
    assert len(indices) in (513, 514)
    with pytest.raises(ValueError):
        sampled_tile_selection(5, 10, 1)


def test_select_sampled_tiles_deterministic_and_seed_sensitive(tmp_path: Path) -> None:
    names = [f"tile_{10000 + index}/qso_data_TILE{10000 + index}_5_qsos.parquet" for index in range(30)]
    files = [tmp_path / name for name in names]
    first, recipe_first = select_sampled_tiles(files, target_tiles=10, seed=20260815)
    again, recipe_again = select_sampled_tiles(list(reversed(files)), target_tiles=10, seed=20260815)
    assert first == again
    assert recipe_first == recipe_again
    assert recipe_first["tiles_selected"] == len(first)
    other_start = None
    for candidate_seed in range(20260815 + 1, 20260815 + 100):
        other_start, _, _ = sampled_tile_selection(30, 10, candidate_seed)
        if other_start != recipe_first["start"]:
            break
    assert other_start is not None and other_start != recipe_first["start"]
    other, _ = select_sampled_tiles(files, target_tiles=10, seed=candidate_seed)
    assert other != first


def test_select_row_indices_systematic_exact_and_deterministic() -> None:
    child = child_seed_for_tile(20260815, 0, 1)
    indices = select_row_indices(100, 8, child)
    again = select_row_indices(100, 8, child_seed_for_tile(20260815, 0, 1))
    assert indices == again
    assert len(indices) == 8
    assert indices == sorted(set(indices))
    stride = 100 // 8
    assert indices[1] - indices[0] == stride
    assert indices[-1] < 100
    assert select_row_indices(3, 64, child) == [0, 1, 2]
    assert select_row_indices(64, 64, child) == list(range(64))
    other = select_row_indices(100, 8, child_seed_for_tile(20260815, 1, 2))
    assert other != indices


def test_scan_task_roundtrip_over_synthetic_file(tmp_path: Path) -> None:
    wave = three_arm_wave()
    rows = [
        {
            "wavelength": wave.tolist(),
            "flux": np.ones(len(wave)).tolist(),
            "ivar": (np.arange(len(wave)) % 3 == 0).astype(float).tolist(),
            "mask_array": (np.arange(len(wave)) % 7 == 0).astype(np.int64).tolist(),
        }
        for _ in range(2)
    ]
    path = write_qso_table(tmp_path / "tile_10000/qso_data_TILE10000_2_qsos.parquet", make_row_table(rows))
    from dqad_audit import scan_file_arrays

    result = scan_file_arrays(path, "10000", seed=20260815, tile_index=0, n_tiles=1, rows_per_tile=1)
    assert result["n_rows"] == 2
    assert len(result["rows"]) == 1
    record = result["rows"][0]
    expected_idx = select_row_indices(2, 1, child_seed_for_tile(20260815, 0, 1))[0]
    assert record["row_uid"] == f"10000:{expected_idx}"
    assert record["overlap_br_angstrom"] == 40.0
    assert record["ivar_zero_frac"] == pytest.approx(np.count_nonzero(np.arange(150) % 3 != 0) / 150)
    expected_mask = int(np.count_nonzero(np.arange(150) % 7 == 0))
    assert json.loads(record["mask_value_counts"]) == {"0": 150 - expected_mask, "1": expected_mask}


def test_audit_chunk_records_open_failure_without_raising(tmp_path: Path) -> None:
    tasks = [
        {
            "path": str(tmp_path / "missing.parquet"),
            "tile_id": "10000",
            "seed": 20260815,
            "tile_index": 0,
            "n_tiles": 1,
            "rows_per_tile": 2,
            "neardup_tolerance": 0.1,
        },
    ]
    result = audit_chunk(tasks)
    assert result["rows"] == []
    assert len(result["errors"]) == 1
    assert "missing.parquet" in result["errors"][0]["path"]


def test_recommend_merge_policy_from_measured_stats() -> None:
    measured = dict(
        rows_sampled=100,
        rows_with_breaks=100,
        rows_with_dups=100,
        rows_with_neardups=0,
        mean_n_pixels=150.0,
        mean_dup_per_row=20.0,
        mean_neardup_per_row=0.0,
        median_overlap_br=40.0,
        median_overlap_rz=100.0,
        misaligned_rows=0,
        neardup_tolerance=0.1,
    )
    policy = recommend_merge_policy(**measured)
    assert policy["required"] is True
    assert policy["policy"] == "exact_wavelength_dedup"
    assert policy["expected_pixel_reduction_per_row"]["mean_pixels_after_dedup"] == pytest.approx(130.0)
    assert any("sort" in directive for directive in policy["method"])
    clean = recommend_merge_policy(**{**measured, "rows_with_breaks": 0, "rows_with_dups": 0})
    assert clean["required"] is False
    assert clean["policy"] == "none_pass_through"
    near = recommend_merge_policy(
        **{**measured, "rows_with_dups": 0, "rows_with_neardups": 50, "mean_neardup_per_row": 3.0}
    )
    assert near["policy"] == "tolerance_wavelength_dedup"
    misaligned = recommend_merge_policy(**{**measured, "misaligned_rows": 2})
    assert any("validate length alignment" in directive for directive in misaligned["method"])


def test_part_schema_projection_drops_auxiliary_columns() -> None:
    wave = np.arange(5.0)
    record = audit_arrays(wave, np.ones(5), np.ones(5), np.zeros(5, dtype=np.int64))
    record.update({"row_uid": "10000:0", "tile_id": "10000", "row_idx": 0})
    part_table = array_audit_part_table([record])
    final_table = project_array_audit_table(part_table)
    assert final_table.schema.names == ARRAY_AUDIT_SCHEMA.names
    assert "br_wave_before" in part_table.schema.names
    assert "br_wave_before" not in final_table.schema.names


def test_build_array_audit_summary_blocks_and_int_counts() -> None:
    wave = three_arm_wave()
    clean = audit_arrays(np.arange(50.0), np.ones(50), np.ones(50), np.zeros(50, dtype=np.int64))
    broken = audit_arrays(wave, np.ones(150), np.ones(150), np.zeros(150, dtype=np.int64))
    rows = []
    for tile_id, row_idx, record in (("10000", 0, clean), ("10000", 1, broken), ("10001", 0, broken)):
        rows.append({"row_uid": f"{tile_id}:{row_idx}", "tile_id": tile_id, "row_idx": row_idx, **record})
    table = array_audit_part_table(rows)
    summary = build_array_audit_summary(
        table,
        sampling={
            "method": "systematic stride selection",
            "tile_sort_key": "tile_id then path",
            "stride_derivation": "floor(10 / 1) = 1",
            "start_derivation": "int(...) = 0",
            "seed": 20260815,
            "stride": 1,
            "start": 0,
            "tiles_selected": 2,
        },
        fence_action="initialized",
        provenance={"command": "audit"},
    )
    assert summary["alignment"]["misaligned_rows"] == 0
    assert summary["monotonicity"]["rows_with_breaks"] == 2
    assert summary["monotonicity"]["total_breaks"] == 4
    assert summary["monotonicity"]["rows_by_break_count"]["2_breaks"] == 2
    assert summary["monotonicity"]["break_position_histogram"] == {"49": 2, "99": 2}
    assert summary["arm_overlap"]["br_boundary"]["overlap_width_angstrom"]["median"] == pytest.approx(40.0)
    assert summary["arm_overlap"]["duplicates"]["rows_with_duplicates"] == 2
    assert summary["arm_overlap"]["merge_policy"]["policy"] == "exact_wavelength_dedup"
    assert summary["mask_inventory"]["total_distinct"] == 1
    assert summary["wavelength_range"]["global_max_angstrom"] == pytest.approx(167.0)
    assert isinstance(summary["monotonicity"]["total_breaks"], int)
    assert isinstance(summary["arm_overlap"]["br_boundary"]["rows_measured"], int)
    assert isinstance(summary["sampling"]["rows_selected"], int)
    json.dumps(summary)


def write_audit_corpus(root: Path, tiles: list[tuple[str, int]]) -> list[Path]:
    """Write a synthetic corpus of files with known two-break rows."""
    paths: list[Path] = []
    for tile, n_rows in tiles:
        tile_number = tile.split("_")[1]
        wave = three_arm_wave()
        rows = [
            {
                "wavelength": wave.tolist(),
                "flux": np.ones(len(wave)).tolist(),
                "ivar": np.ones(len(wave)).tolist(),
                "mask_array": np.zeros(len(wave), dtype=np.int64).tolist(),
            }
            for _ in range(n_rows)
        ]
        name = f"{tile}/qso_data_TILE{tile_number}_{n_rows}_qsos.parquet"
        paths.append(write_qso_table(root / name, make_row_table(rows)))
    return paths


def run_script(module, corpus_root: Path, work_dir: Path, *, seed: int, rows_per_tile: int = 2) -> None:
    module.main(
        [
            "--corpus-root",
            str(corpus_root),
            "--work-dir",
            str(work_dir),
            "--workers",
            "2",
            "--seed",
            str(seed),
            "--target-tiles",
            "2",
            "--rows-per-tile",
            str(rows_per_tile),
            "--chunk-size",
            "2",
        ]
    )


def test_script_end_to_end_resume_and_fence(tmp_path: Path, audit) -> None:
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    write_audit_corpus(corpus, [("tile_10000", 3), ("tile_10001", 3), ("tile_10002", 3)])
    run_script(audit, corpus, work_dir, seed=20260815)
    final = pq.read_table(work_dir / ARRAY_AUDIT_FILENAME)
    assert final.schema.names == ARRAY_AUDIT_SCHEMA.names
    assert final.num_rows == 6
    overlaps = final.column("overlap_br_angstrom").to_pylist()
    assert set(overlaps) == {40.0}
    assert all(count == 2 for count in final.column("n_mono_breaks").to_pylist())
    assert all(flag for flag in final.column("align_ok").to_pylist())
    summary = json.loads((work_dir / ARRAY_AUDIT_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert summary["sampling"]["rows_selected"] == 6
    assert summary["arm_overlap"]["merge_policy"]["required"] is True
    parts = sorted((work_dir / "parts" / ARRAY_AUDIT_PART_KIND).glob("*.parquet"))
    assert parts
    mtimes = {path: path.stat().st_mtime_ns for path in parts}
    manifest_before = read_kind_manifest(work_dir, ARRAY_AUDIT_PART_KIND)
    run_script(audit, corpus, work_dir, seed=20260815)
    for path in parts:
        assert path.stat().st_mtime_ns == mtimes[path]
    assert read_kind_manifest(work_dir, ARRAY_AUDIT_PART_KIND) == manifest_before
    other_start = None
    for candidate_seed in range(20260816, 20260916):
        other_start, _, _ = sampled_tile_selection(3, 2, candidate_seed)
        if other_start is not None:
            break
    assert other_start is not None
    max_mtime_before = max(path.stat().st_mtime_ns for path in parts)
    run_script(audit, corpus, work_dir, seed=candidate_seed)
    manifest_after = read_kind_manifest(work_dir, ARRAY_AUDIT_PART_KIND)
    assert manifest_after["sampling_seed"] == candidate_seed
    assert manifest_after != manifest_before
    assert max(path.stat().st_mtime_ns for path in parts) > max_mtime_before


def test_fence_prunes_parts_on_sampling_change(tmp_path: Path, audit) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    corpus = tmp_path / "corpus"
    files = write_audit_corpus(corpus, [("tile_10000", 3), ("tile_10001", 3)])
    current = build_array_audit_run_config(
        corpus,
        files,
        seed=20260815,
        target_tiles=2,
        rows_per_tile=2,
        neardup_tolerance=0.1,
        chunk_size=2,
    )
    assert audit.apply_sampling_fence(work_dir, current) == "initialized"
    stale = work_dir / "parts" / ARRAY_AUDIT_PART_KIND / "0000.parquet"
    stale.write_bytes(b"stale")
    write_kind_manifest(work_dir, ARRAY_AUDIT_PART_KIND, current)
    assert audit.apply_sampling_fence(work_dir, current) == "reused"
    assert stale.is_file()
    changed = build_array_audit_run_config(
        corpus,
        files,
        seed=999,
        target_tiles=2,
        rows_per_tile=2,
        neardup_tolerance=0.1,
        chunk_size=2,
    )
    assert audit.apply_sampling_fence(work_dir, changed) == "pruned_parts"
    assert not stale.is_file()
    assert read_kind_manifest(work_dir, ARRAY_AUDIT_PART_KIND) == changed
    retuned = build_array_audit_run_config(
        corpus,
        files,
        seed=20260815,
        target_tiles=2,
        rows_per_tile=4,
        neardup_tolerance=0.1,
        chunk_size=2,
    )
    assert audit.apply_sampling_fence(work_dir, retuned) == "pruned_parts"


@pytest.mark.parametrize("failure", ["unreadable", "missing_column"])
def test_read_failure_aborts_without_partial_checkpoint_or_summary(tmp_path, audit, failure):
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    files = write_audit_corpus(corpus, [("tile_10000", 3), ("tile_10001", 3)])
    if failure == "unreadable":
        files[1].write_bytes(b"not a parquet file")
    else:
        table = pq.read_table(files[1]).drop(["flux"])
        pq.write_table(table, files[1])
    with pytest.raises(SystemExit, match=r"1 file\(s\) failed to read"):
        run_script(audit, corpus, work_dir, seed=20260815)
    assert not list((work_dir / "parts" / ARRAY_AUDIT_PART_KIND).glob("*.parquet"))
    assert not (work_dir / ARRAY_AUDIT_FILENAME).exists()
    assert not (work_dir / ARRAY_AUDIT_SUMMARY_FILENAME).exists()
