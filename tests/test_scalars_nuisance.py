#!/usr/bin/env python3
"""
Script Name  : test_scalars_nuisance.py
Description  : Unit and script tests for the D4+D5 scalar extraction and nuisance baseline.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers the D4+D5 pass with hand-computed synthetic fixtures only: the
snr_proxy formula (mask exclusion, negative-ivar clipping, even/odd
medians, all-masked null), masked_fraction, array_length, the bin-table
interval and grid-length math including a disjoint guard case, outward
bin edges, histogram binning with under/overflow, quantile spot values,
the run-config fence over the scalars parts family (definition-version
change invalidates checkpoints), Arrow-null (not NaN) semantics, the
per-file scanner over synthetic Parquet files, and the full script over
a synthetic corpus including inventory-validated resume and fence
pruning. The real corpus is never touched.

Usage
-----
    pytest tests/test_scalars_nuisance.py

Examples
--------
    pytest tests/test_scalars_nuisance.py -k snr
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import write_qso_table
from dqad_audit import (
    NUISANCE_DEFINITIONS_VERSION,
    OBSERVED_WAVE_MAX_ANGSTROM,
    OBSERVED_WAVE_MIN_ANGSTROM,
    PER_OBJECT_FILENAME,
    PER_OBJECT_SCHEMA,
    REDSHIFT_SUMMARY_FILENAME,
    RR_CHI2_BLOCK,
    SCALAR_COLUMNS,
    SCALARS_PART_KIND,
    Z_BROAD_BINS,
    build_bin_selection,
    build_nuisance_block,
    build_redshift_block,
    build_redshift_summary,
    build_scalars_run_config,
    common_rest_interval,
    count_in_z_bin,
    linear_grid_length,
    log_grid_length,
    outward_bin_edges,
    per_object_part_table,
    project_per_object_table,
    quantile_profile,
    read_kind_manifest,
    rest_interval_workability,
    row_masked_fraction,
    row_scalar_metrics,
    row_snr_proxy,
    scalars_chunk,
    scan_file_scalars,
    write_kind_manifest,
    z_histogram,
)

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "04_scalars_nuisance.py"


# =============================================================================
# Functions
# =============================================================================


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("scalars_nuisance_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_scalars_table(rows: list[dict[str, object]]) -> pa.Table:
    """Build a synthetic corpus-shaped table from per-row specs."""
    columns: dict[str, pa.Array] = {
        "target_id": pa.array([row["target_id"] for row in rows], pa.int64()),
        "ra": pa.array([row["ra"] for row in rows], pa.float64()),
        "dec": pa.array([row["dec"] for row in rows], pa.float64()),
        "z": pa.array([row["z"] for row in rows], pa.float64()),
        "wavelength": pa.array([[4000.0 + i for i in range(len(row["flux"]))] for row in rows], pa.list_(pa.float64())),
        "flux": pa.array([row["flux"] for row in rows], pa.list_(pa.float64())),
        "ivar": pa.array([row["ivar"] for row in rows], pa.list_(pa.float64())),
        "mask_array": pa.array([row["mask_array"] for row in rows], pa.list_(pa.int64())),
    }
    return pa.table(columns)


def hand_rows() -> list[dict[str, object]]:
    """Rows exercising every snr/masked corner case with hand-known values."""
    return [
        {
            "target_id": 11,
            "ra": 10.0,
            "dec": -1.0,
            "z": 2.0,
            "flux": [1.0, 2.0, 100.0, 4.0],
            "ivar": [4.0, -1.0, 100.0, 0.0],
            "mask_array": [0, 0, 1, 0],
        },
        {
            "target_id": 12,
            "ra": 11.0,
            "dec": -2.0,
            "z": -0.5,
            "flux": [3.0, 4.0],
            "ivar": [1.0, 1.0],
            "mask_array": [0, 0],
        },
        {
            "target_id": 13,
            "ra": 12.0,
            "dec": -3.0,
            "z": float("nan"),
            "flux": [7.0, 8.0],
            "ivar": [1.0, 4.0],
            "mask_array": [1, 1],
        },
    ]


def test_row_snr_proxy_hand_computed_with_mask_exclusion_and_clip() -> None:
    # Unmasked pixels 0, 1, 3: 1*sqrt(4) = 2, 2*sqrt(clip(-1)) = 0, 4*sqrt(0) = 0.
    # Masked pixel 2 (100*sqrt(100) = 1000) must be excluded. median([2, 0, 0]) = 0.
    assert row_snr_proxy([1.0, 2.0, 100.0, 4.0], [4.0, -1.0, 100.0, 0.0], [0, 0, 1, 0]) == 0.0
    # Even count interpolates: median([3, 4]) = 3.5.
    assert row_snr_proxy([3.0, 4.0], [1.0, 1.0], [0, 0]) == 3.5
    # Odd count with all pixels contributing: median([2, 0]) -> [sqrt(4), 0*...].
    assert row_snr_proxy([2.0, 0.0, 6.0], [1.0, 5.0, 4.0], [0, 0, 0]) == 2.0


def test_row_snr_proxy_null_when_all_masked() -> None:
    assert row_snr_proxy([1.0, 2.0], [1.0, 1.0], [1, 1]) is None
    assert row_snr_proxy([], [], []) is None


def test_row_masked_fraction() -> None:
    assert row_masked_fraction([0, 1, 1, 0]) == 0.5
    assert row_masked_fraction([0, 0, 0]) == 0.0
    assert row_masked_fraction([2, 4]) == 1.0
    assert row_masked_fraction([]) == 0.0


def test_row_scalar_metrics_array_length() -> None:
    metrics = row_scalar_metrics([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], [0, 1, 0])
    assert metrics["array_length"] == 3
    assert metrics["masked_fraction"] == pytest.approx(1 / 3)
    assert metrics["snr_proxy"] == pytest.approx(2.0)
    assert isinstance(metrics["array_length"], int)


def test_common_rest_interval_hand_computed() -> None:
    # z in [2, 4], observed [3600, 9799.2]: intersection = [3600/3, 9799.2/5] = [1200, 1959.84].
    interval = common_rest_interval(2.0, 4.0)
    assert interval[0] == pytest.approx(3600.0 / 3.0)
    assert interval[1] == pytest.approx(9799.2 / 5.0)
    # Cross-check the intersection semantics against per-object rest windows: the
    # common interval is [max_i lo_i, min_i hi_i] over objects in the bin, i.e.
    # [3600/(1+z_min), 9799.2/(1+z_max)] (lo bound from z_min, hi bound from z_max).
    windows = [
        (OBSERVED_WAVE_MIN_ANGSTROM / (1.0 + z), OBSERVED_WAVE_MAX_ANGSTROM / (1.0 + z))
        for z in np.linspace(2.0, 4.0, 1001)
    ]
    assert interval[0] == pytest.approx(max(w[0] for w in windows))
    assert interval[1] == pytest.approx(min(w[1] for w in windows))
    # Finding's hand case: bin [1.0, 1.25] -> [1800, 4355.2].
    assert common_rest_interval(1.0, 1.25) == (pytest.approx(1800.0), pytest.approx(4355.2))


def test_common_rest_interval_disjoint_guard_case() -> None:
    # Wide ordered bins are genuinely disjoint under intersection semantics:
    # z in [0.5, 4.5] -> [3600/1.5, 9799.2/5.5] = [2400, 1781.6727...] (negative width).
    interval = common_rest_interval(0.5, 4.5)
    assert interval[0] == pytest.approx(2400.0)
    assert interval[1] == pytest.approx(9799.2 / 5.5)
    assert rest_interval_workability(interval) == "disjoint"
    # Second disjoint case: z in [0, 4] -> [3600/1, 9799.2/5] = [3600, 1959.84].
    zero_based = common_rest_interval(0.0, 4.0)
    assert zero_based[0] == pytest.approx(3600.0)
    assert zero_based[1] == pytest.approx(9799.2 / 5.0)
    assert rest_interval_workability(zero_based) == "disjoint"
    assert rest_interval_workability((1200.0, 1959.84)) == "positive_width"
    assert rest_interval_workability((100.0, 100.0)) == "disjoint"


def test_grid_length_math_hand_computed() -> None:
    interval = common_rest_interval(2.0, 4.0)
    assert linear_grid_length(interval, 1.0) == pytest.approx(1959.84 - 1200.0)
    assert linear_grid_length(interval, 0.5) == pytest.approx((1959.84 - 1200.0) / 0.5)
    assert log_grid_length((100.0, 1000.0), 1e-4) == pytest.approx((math.log10(1000.0) - math.log10(100.0)) / 1e-4)
    assert log_grid_length(interval, 1e-4) == pytest.approx((math.log10(1959.84) - math.log10(1200.0)) / 1e-4)
    assert log_grid_length((-5.0, 100.0), 1e-4) is None
    with pytest.raises(ValueError):
        linear_grid_length(interval, 0.0)
    with pytest.raises(ValueError):
        log_grid_length(interval, 0.0)


def test_outward_bin_edges() -> None:
    assert outward_bin_edges(0.13, 0.62, 0.25) == [0.0, 0.25, 0.5, 0.75]
    assert outward_bin_edges(0.5, 1.0, 0.25) == [0.5, 0.75, 1.0]
    assert outward_bin_edges(0.31, 0.89, 0.1) == [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    assert outward_bin_edges(0.3, 0.3, 0.1) == [0.3, 0.4]
    with pytest.raises(ValueError):
        outward_bin_edges(1.0, 0.0, 0.25)
    with pytest.raises(ValueError):
        outward_bin_edges(0.0, 1.0, 0.0)


def test_count_in_z_bin_half_open_and_closed() -> None:
    z = np.asarray([0.1, 0.25, 0.3])
    assert count_in_z_bin(z, 0.0, 0.25, closed=False) == 1
    assert count_in_z_bin(z, 0.0, 0.25, closed=True) == 2
    assert count_in_z_bin(z, 0.0, 0.5, closed=False) == 3


def test_z_histogram_under_and_overflow() -> None:
    values = [0.05, 0.15, 0.55, 0.95]
    block = z_histogram(values, 0.1, (0.1, 0.9))
    assert block["underflow"] == 1
    assert block["overflow"] == 1
    assert block["count_inside"] == 2
    assert sum(block["counts"]) == 2
    assert block["count_nonfinite_excluded"] == 0
    populated = z_histogram([0.05, 0.15, 0.55, 0.95], 0.1)
    assert populated["underflow"] == 0
    assert populated["overflow"] == 0
    assert populated["count_inside"] == 4
    assert populated["edges"][0] == 0.0
    assert populated["edges"][-1] == 1.0
    nonfinite = z_histogram([0.1, float("nan"), float("inf"), 0.3], 0.1)
    assert nonfinite["count_nonfinite_excluded"] == 2
    assert nonfinite["count_inside"] == 2


def test_redshift_block_histogram_reports_nonfinite_exclusions() -> None:
    block = build_redshift_block([0.1, float("nan"), float("inf"), 0.3])
    assert block["count_nonfinite"] == 2
    assert block["count_finite"] == 2
    assert block["histogram"]["count_nonfinite_excluded"] == 2
    assert block["histogram"]["count_inside"] == 2


def test_quantile_profile_spot_values() -> None:
    profile = quantile_profile(list(map(float, range(1, 11))))
    assert profile["0"] == 1.0
    assert profile["0.25"] == 3.25
    assert profile["0.5"] == 5.5
    assert profile["1"] == 10.0
    assert "linear interpolation" in profile["method"]
    assert quantile_profile([])["0.5"] is None


def test_build_bin_selection_counts_sum_and_hand_row() -> None:
    z = [0.1, 0.3, 0.4, 2.6, 2.7]
    block = build_bin_selection(z)
    assert block["populated_range"]["z_min"] == 0.1
    assert block["populated_range"]["z_max"] == 2.7
    assert block["populated_range"]["n_finite"] == 5
    fine = [record for record in block["bins"] if record["kind"] == "fine"]
    assert [record["label"] for record in fine][0] == "[0, 0.25)"
    counts_by_label = {record["label"]: record["count"] for record in block["bins"]}
    assert counts_by_label["[0, 0.25)"] == 1
    assert counts_by_label["[0.25, 0.5)"] == 2
    assert counts_by_label["[2.5, 2.75]"] == 2
    assert counts_by_label["[0.5, 2]"] == 0
    assert counts_by_label["full populated range [0.1, 2.7]"] == 5
    assert block["fine_bins_count_sum"] == {"sum": 5, "equals_n_finite": True}
    first = fine[0]
    assert first["rest_interval_angstrom"] == [
        pytest.approx(3600.0 / 1.0),
        pytest.approx(9799.2 / 1.25),
    ]
    assert first["interval_width_angstrom"] == pytest.approx(7839.36 - 3600.0)
    assert first["linear_grid_length"] == pytest.approx(7839.36 - 3600.0)
    assert first["log_grid_length"] == pytest.approx((math.log10(7839.36) - math.log10(3600.0)) / 1e-4)
    assert first["workability"] == "positive_width"
    # Finding's hand case: bin [1.0, 1.25] -> intersection [1800, 4355.2].
    narrow = [record for record in fine if record["label"] == "[1, 1.25)"][0]
    assert narrow["rest_interval_angstrom"] == [pytest.approx(1800.0), pytest.approx(4355.2)]
    assert narrow["interval_width_angstrom"] == pytest.approx(4355.2 - 1800.0)
    full = [record for record in block["bins"] if record["kind"] == "full"][0]
    assert full["rest_interval_angstrom"][0] == pytest.approx(3600.0 / 1.1)
    assert full["rest_interval_angstrom"][1] == pytest.approx(9799.2 / 3.7)
    assert full["interval_width_angstrom"] < 0
    assert full["workability"] == "disjoint"
    assert "disjoint" in full["note"]
    assert all(record["count"] >= 0 for record in block["bins"])
    assert set(record["kind"] for record in block["bins"]) == {"fine", "broad", "full"}
    assert len([record for record in block["bins"] if record["kind"] == "broad"]) == len(Z_BROAD_BINS)


def test_build_bin_selection_nonfinite_excluded() -> None:
    block = build_bin_selection([0.1, float("nan"), 0.4])
    assert block["populated_range"]["n_finite"] == 2
    assert block["populated_range"]["n_excluded_nonfinite"] == 1
    with pytest.raises(ValueError):
        build_bin_selection([float("nan")])


def test_per_object_part_table_null_semantics() -> None:
    payload = {
        "row_uid": ["10000:0", "10000:1"],
        "tile_id": ["10000", "10000"],
        "row_idx": [0, 1],
        "target_id": [1, 2],
        "ra": [1.0, 2.0],
        "dec": [3.0, 4.0],
        "z": [0.5, 0.6],
        "array_length": [2, 2],
        "snr_proxy": [1.5, None],
        "masked_fraction": [0.0, 1.0],
    }
    table = per_object_part_table(payload)
    snr = table.column("snr_proxy")
    assert snr.null_count == 1
    assert snr.to_pylist() == [1.5, None]
    assert snr.combine_chunks().to_pylist()[1] is None
    final = project_per_object_table(table)
    assert final.schema.names == PER_OBJECT_SCHEMA.names
    assert "row_idx" not in final.schema.names


def test_scan_file_scalars_over_synthetic_file(tmp_path: Path) -> None:
    rows = hand_rows()
    path = write_qso_table(tmp_path / "tile_10000/qso_data_TILE10000_3_qsos.parquet", make_scalars_table(rows))
    result = scan_file_scalars(path, "10000")
    assert result["n_rows"] == 3
    payload = result["rows"]
    assert payload["row_uid"] == ["10000:0", "10000:1", "10000:2"]
    assert payload["target_id"] == [11, 12, 13]
    assert payload["array_length"] == [4, 2, 2]
    assert payload["snr_proxy"][0] == 0.0
    assert payload["snr_proxy"][1] == 3.5
    assert payload["snr_proxy"][2] is None
    assert payload["masked_fraction"] == [0.25, 0.0, 1.0]
    assert payload["z"][1] == -0.5
    assert result["warnings"]["null_snr_rows"] == 1
    assert result["warnings"]["nonfinite_z_rows"] == 1
    assert result["warnings"]["z_le_zero_rows"] == 1


def test_scalars_chunk_aggregates_and_captures_errors(tmp_path: Path) -> None:
    table = make_scalars_table(hand_rows()[:1])
    path = write_qso_table(tmp_path / "tile_10000/qso_data_TILE10000_1_qsos.parquet", table)
    tasks = [
        {"path": str(path), "tile_id": "10000"},
        {"path": str(tmp_path / "missing.parquet"), "tile_id": "99999"},
    ]
    result = scalars_chunk(tasks)
    assert result["rows"]["row_uid"] == ["10000:0"]
    assert result["rows"]["snr_proxy"] == [0.0]
    assert len(result["errors"]) == 1
    assert "missing.parquet" in result["errors"][0]["path"]
    assert result["warnings"] == []


def test_fence_prunes_parts_on_definition_version_change(tmp_path: Path, script) -> None:
    corpus = tmp_path / "corpus"
    files = [corpus / "tile_10000/qso_data_TILE10000_1_qsos.parquet"]
    current = build_scalars_run_config(corpus, files, chunk_size=2)
    assert script.apply_run_fence(tmp_path, current) == "initialized"
    stale = tmp_path / "parts" / SCALARS_PART_KIND / "0000.parquet"
    stale.write_bytes(b"stale")
    write_kind_manifest(tmp_path, SCALARS_PART_KIND, current)
    assert script.apply_run_fence(tmp_path, current) == "reused"
    assert stale.is_file()
    changed = build_scalars_run_config(
        corpus,
        files,
        definitions_version=f"{NUISANCE_DEFINITIONS_VERSION}-changed",
        chunk_size=2,
    )
    assert changed["definitions_version"] != current["definitions_version"]
    assert script.apply_run_fence(tmp_path, changed) == "pruned_parts"
    assert not stale.is_file()
    assert read_kind_manifest(tmp_path, SCALARS_PART_KIND) == changed
    retuned = build_scalars_run_config(corpus, files, chunk_size=3)
    assert script.apply_run_fence(tmp_path, retuned) == "pruned_parts"


def write_scalars_corpus(root: Path, tiles: list[tuple[str, int]]) -> list[Path]:
    """Write a synthetic corpus of files with hand-known scalar rows."""
    paths: list[Path] = []
    for tile, n_rows in tiles:
        tile_number = tile.split("_")[1]
        rows = [
            {
                "target_id": 1000 + row_idx,
                "ra": 150.0 + row_idx,
                "dec": 2.2 + row_idx,
                "z": 0.5 + 0.1 * row_idx,
                "flux": [1.0, 4.0, 9.0],
                "ivar": [1.0, 4.0, 0.0],
                "mask_array": [0, 0, 1],
            }
            for row_idx in range(n_rows)
        ]
        name = f"{tile}/qso_data_TILE{tile_number}_{n_rows}_qsos.parquet"
        paths.append(write_qso_table(root / name, make_scalars_table(rows)))
    return paths


def write_inventory(work_dir: Path, corpus_root: Path, files: list[Path]) -> None:
    """Write a minimal D1-style inventory.parquet for resume validation."""
    from dqad_audit import inventory_table, scan_file

    records = [scan_file(path, None)["inventory"] for path in files]
    table = inventory_table(records)
    write_path = work_dir / "inventory.parquet"
    write_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, write_path)


def run_script(module, corpus_root: Path, work_dir: Path) -> None:
    module.main(
        [
            "--corpus-root",
            str(corpus_root),
            "--work-dir",
            str(work_dir),
            "--workers",
            "2",
            "--chunk-size",
            "2",
        ]
    )


def test_script_end_to_end_resume_and_fence(tmp_path: Path, script) -> None:
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    files = write_scalars_corpus(corpus, [("tile_10000", 3), ("tile_10001", 2), ("tile_10002", 1)])
    write_inventory(work_dir, corpus, files)
    run_script(script, corpus, work_dir)
    final = pq.read_table(work_dir / PER_OBJECT_FILENAME)
    assert final.schema.names == PER_OBJECT_SCHEMA.names
    assert final.num_rows == 6
    assert final.column("row_uid").to_pylist() == [
        "10000:0",
        "10000:1",
        "10000:2",
        "10001:0",
        "10001:1",
        "10002:0",
    ]
    # Hand-computed scalars: unmasked pixels 0, 1 -> [1*sqrt(1), 4*sqrt(4)] = [1, 8] -> median 4.5; mask 1/3.
    assert final.column("snr_proxy").to_pylist() == [4.5] * 6
    assert final.column("masked_fraction").to_pylist() == [pytest.approx(1 / 3)] * 6
    assert final.column("array_length").to_pylist() == [3] * 6
    summary = json.loads((work_dir / REDSHIFT_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert summary["rr_chi2"] == RR_CHI2_BLOCK
    assert summary["rr_chi2"]["reason"] == (
        "column absent from archive; documented schema predates corrections; see D1 schema findings"
    )
    assert summary["redshift"]["count_total"] == 6
    assert summary["redshift"]["min"] == 0.5
    assert summary["redshift"]["max"] == pytest.approx(0.7)
    assert summary["bin_selection"]["fine_bins_count_sum"]["equals_n_finite"] is True
    assert summary["nuisance"]["snr_proxy"]["count_null"] == 0
    assert summary["formulae"]["snr_proxy"].startswith("snr_proxy = median(")
    assert summary["provenance"]["columns_read"] == list(SCALAR_COLUMNS)
    assert summary["provenance"]["wavelength_read"] is False
    parts = sorted((work_dir / "parts" / SCALARS_PART_KIND).glob("*.parquet"))
    assert parts
    mtimes = {path: path.stat().st_mtime_ns for path in parts}
    run_script(script, corpus, work_dir)
    for path in parts:
        assert path.stat().st_mtime_ns == mtimes[path]
    rebuilt = pq.read_table(work_dir / PER_OBJECT_FILENAME)
    assert rebuilt.num_rows == final.num_rows
    assert rebuilt.column("row_uid").to_pylist() == final.column("row_uid").to_pylist()
    changed = build_scalars_run_config(
        corpus,
        files,
        definitions_version="other-definitions",
        chunk_size=2,
    )
    assert script.apply_run_fence(work_dir, changed) == "pruned_parts"


def test_build_redshift_summary_blocks(tmp_path: Path) -> None:
    z = [0.1, 0.2, float("nan"), -0.01, 2.6]
    snr = [2.5, None, -1.0, 0.0, 3.0]
    masked = [1 / 3, 1.0, 0.95, 0.0, 0.1]
    summary = build_redshift_summary(
        z_values=z,
        snr_values=snr,
        masked_fraction_values=masked,
        provenance={"command": "unit-test", "outputs": {"per_object_raw_parquet": str(tmp_path / PER_OBJECT_FILENAME)}},
    )
    assert list(summary) == ["provenance", "rr_chi2", "redshift", "bin_selection", "nuisance", "formulae"]
    redshift = summary["redshift"]
    assert redshift["count_nonfinite"] == 1
    assert redshift["count_z_le_zero"] == 1
    assert redshift["count_finite"] == 4
    assert redshift["min"] == -0.01
    assert redshift["max"] == 2.6
    nuisance = summary["nuisance"]
    assert nuisance["snr_proxy"]["count_null"] == 1
    assert nuisance["snr_proxy"]["pathological_tail"]["count_snr_le_zero"] == 2
    assert nuisance["masked_fraction"]["count_zero"] == 1
    assert nuisance["masked_fraction"]["pathological_tail"]["count_above_0_5"] == 2
    assert nuisance["masked_fraction"]["pathological_tail"]["count_above_0_9"] == 2
    assert nuisance["joint"]["of_which_snr_null_or_le_zero"] == 2
    assert summary["formulae"]["masked_fraction"] == "masked_fraction = mean(mask_array != 0)"
    json.dumps(summary)


def test_build_nuisance_block_aligned_validation() -> None:
    with pytest.raises(ValueError):
        build_nuisance_block([1.0], [0.5, 0.5])


@pytest.mark.parametrize("source_unreadable", [False, True])
def test_resume_without_inventory_rechecks_legacy_partial_chunk(tmp_path, script, source_unreadable):
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    files = write_scalars_corpus(corpus, [("tile_10000", 3), ("tile_10001", 3)])
    script.apply_run_fence(work_dir, build_scalars_run_config(corpus, files, chunk_size=2))
    tasks = script.build_tasks(files)
    legacy = per_object_part_table(scalars_chunk(tasks[:1])["rows"])
    part = work_dir / "parts" / SCALARS_PART_KIND / "0000.parquet"
    pq.write_table(legacy, part)
    before = part.read_bytes()
    if source_unreadable:
        files[1].write_bytes(b"unreadable source")
        with pytest.raises(SystemExit, match="failed to read"):
            run_script(script, corpus, work_dir)
        assert part.read_bytes() == before
        assert not (work_dir / PER_OBJECT_FILENAME).exists()
        assert not (work_dir / REDSHIFT_SUMMARY_FILENAME).exists()
    else:
        run_script(script, corpus, work_dir)
        assert pq.read_table(part).num_rows == 6
        result = pq.read_table(work_dir / PER_OBJECT_FILENAME)
        assert result.column("row_uid").to_pylist() == [
            "10000:0",
            "10000:1",
            "10000:2",
            "10001:0",
            "10001:1",
            "10001:2",
        ]


def test_scalar_read_failure_does_not_checkpoint_partial_rows(tmp_path, script):
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    files = write_scalars_corpus(corpus, [("tile_10000", 3), ("tile_10001", 3)])
    files[1].write_bytes(b"unreadable source")
    with pytest.raises(SystemExit, match="failed to read"):
        run_script(script, corpus, work_dir)
    assert not list((work_dir / "parts" / SCALARS_PART_KIND).glob("*.parquet"))
    assert not (work_dir / PER_OBJECT_FILENAME).exists()
    assert not (work_dir / REDSHIFT_SUMMARY_FILENAME).exists()


def test_resumed_run_recomputes_warning_provenance_from_reused_chunk(tmp_path, script):
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    path = write_qso_table(
        corpus / "tile_10000/qso_data_TILE10000_3_qsos.parquet",
        make_scalars_table(hand_rows()),
    )
    write_inventory(work_dir, corpus, [path])

    run_script(script, corpus, work_dir)
    first = json.loads((work_dir / REDSHIFT_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    run_script(script, corpus, work_dir)
    resumed = json.loads((work_dir / REDSHIFT_SUMMARY_FILENAME).read_text(encoding="utf-8"))

    expected = {
        "files_with_null_snr_rows": 1,
        "files_with_nonfinite_z_rows": 1,
        "files_with_z_le_zero_rows": 1,
        "example_files_capped_at": script.WARNING_FILE_LIST_LIMIT,
        "examples": {
            "null_snr": [str(path)],
            "nonfinite_z": [str(path)],
            "z_le_zero": [str(path)],
        },
    }
    assert first["provenance"]["warnings"] == expected
    assert resumed["provenance"]["warnings"] == expected
