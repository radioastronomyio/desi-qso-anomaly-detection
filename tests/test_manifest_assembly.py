#!/usr/bin/env python3
"""
Script Name  : test_manifest_assembly.py
Description  : Unit and script tests for the D6 manifest artifact assembly.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers the D6 assembly with synthetic fixtures only (the real corpus is
never touched): row_uid parsing, the sampled-flag LEFT-JOIN (flags land on
exactly the sampled row_uids, unsampled rows null, (tile_id, row_idx)
sort order), the hard asserts (row-count mismatch raises, duplicate
row_uid raises, coverage mismatch raises, per-flag null alignment), the
seeded spot-check index draw, the round-trip validator (clean pass and
mutation detection), the corpus integrity comparator (unchanged, mtime
mismatch, size mismatch, missing and extra files), and the full script
end-to-end over a synthetic work directory including the integrity gate
abort on a modified corpus file.

Usage
-----
    pytest tests/test_manifest_assembly.py

Examples
--------
    pytest tests/test_manifest_assembly.py -k integrity
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dqad_audit import (
    ARRAY_AUDIT_SCHEMA,
    INVENTORY_SCHEMA,
    OBJECT_MANIFEST_SCHEMA,
    PER_OBJECT_SCHEMA,
    SNAPSHOT_SCHEMA,
    compare_corpus_snapshots,
    join_sampled_flags,
    parse_row_uid,
    round_trip_validate,
    select_spot_check_indices,
    snapshot_records,
    validate_object_manifest,
    write_part,
)

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "05_build_manifest.py"

# =============================================================================
# Functions
# =============================================================================


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("build_manifest_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_per_object(rows: list[tuple[str, int, int]]) -> pa.Table:
    """Build a synthetic per-object table; rows are (row_uid, tile_id, target_id)."""
    return pa.Table.from_pylist(
        [
            {
                "row_uid": uid,
                "tile_id": tile_id,
                "target_id": target,
                "ra": 1.0,
                "dec": 2.0,
                "z": 2.5,
                "array_length": 8,
                "snr_proxy": float(index) if index else None,
                "masked_fraction": 0.0,
            }
            for index, (uid, tile_id, target) in enumerate(rows)
        ],
        schema=PER_OBJECT_SCHEMA,
    )


def make_sampled(rows: list[tuple[str, dict[str, object]]]) -> pa.Table:
    """Build a synthetic array-audit table; rows are (row_uid, flag overrides)."""
    defaults: dict[str, object] = {
        "row_uid": "",
        "tile_id": "",
        "row_idx": 0,
        "n_pixels": 8,
        "align_ok": True,
        "n_mono_breaks": 2,
        "first_break_idx": 2750,
        "overlap_br_angstrom": 40.0,
        "overlap_rz_angstrom": 100.0,
        "n_dup_wave": 177,
        "n_neardup_wave": 0,
        "ivar_neg_count": 0,
        "ivar_nonfinite_count": 0,
        "ivar_zero_count": 34,
        "ivar_zero_frac": 0.0044,
        "wave_min": 3600.0,
        "wave_max": 9799.2,
        "mask_value_counts": "{}",
    }
    records = [{**defaults, "row_uid": uid, **overrides} for uid, overrides in rows]
    return pa.Table.from_pylist(records, schema=ARRAY_AUDIT_SCHEMA)


def snapshot_of(paths: list[Path]) -> pa.Table:
    """Build a snapshot table from concrete filesystem paths."""
    return pa.Table.from_pylist(snapshot_records(paths), schema=SNAPSHOT_SCHEMA)


# =============================================================================
# Unit tests: row_uid parsing and uniqueness
# =============================================================================


def test_parse_row_uid_round_trip():
    assert parse_row_uid("10018:7") == ("10018", 7)
    assert parse_row_uid("00123:0") == ("00123", 0)
    with pytest.raises(ValueError):
        parse_row_uid("10018:7x")
    with pytest.raises(ValueError):
        parse_row_uid("no-separator")
    with pytest.raises(ValueError):
        parse_row_uid(":5")


# =============================================================================
# Unit tests: sampled-flag join
# =============================================================================


def test_join_flags_land_on_sampled_rows_only():
    per_object = make_per_object([("10018:0", "10018", 1), ("10018:1", "10018", 2), ("10020:0", "10020", 3)])
    sampled = make_sampled([("10018:1", {"n_dup_wave": 177, "wave_min": 3600.0})])
    joined = join_sampled_flags(per_object, sampled)
    assert joined.schema.equals(OBJECT_MANIFEST_SCHEMA)
    assert joined.num_rows == 3
    align = joined.column("align_ok").to_pylist()
    dups = joined.column("n_dup_wave").to_pylist()
    assert align == [None, True, None]
    assert dups == [None, 177, None]
    assert joined.column("row_uid").to_pylist() == ["10018:0", "10018:1", "10020:0"]
    base_columns = {
        "row_uid",
        "tile_id",
        "target_id",
        "ra",
        "dec",
        "z",
        "array_length",
        "snr_proxy",
        "masked_fraction",
    }
    for field in OBJECT_MANIFEST_SCHEMA:
        if field.name in base_columns:
            continue
        assert joined.column(field.name).to_pylist().count(None) == 2, field.name


def test_join_sorts_by_tile_and_row_idx():
    per_object = make_per_object([("10020:0", "10020", 3), ("10018:1", "10018", 2), ("10018:0", "10018", 1)])
    joined = join_sampled_flags(per_object, make_sampled([]))
    assert joined.column("row_uid").to_pylist() == ["10018:0", "10018:1", "10020:0"]


def test_join_preserves_values_and_null_snr():
    per_object = make_per_object([("10018:0", "10018", 1), ("10018:1", "10018", 2)])
    sampled = make_sampled([("10018:1", {"align_ok": False, "first_break_idx": 12, "wave_max": 9000.5})])
    joined = join_sampled_flags(per_object, sampled)
    assert joined.column("snr_proxy").to_pylist() == [None, 1.0]
    assert joined.column("align_ok").to_pylist() == [None, False]
    assert joined.column("first_break_idx").to_pylist() == [None, 12]
    assert joined.column("wave_max").to_pylist() == [None, 9000.5]


def test_join_duplicate_row_uid_in_either_input_raises():
    per_object = make_per_object([("10018:0", "10018", 1), ("10018:0", "10018", 2)])
    with pytest.raises(ValueError, match="duplicate row_uid"):
        join_sampled_flags(per_object, make_sampled([]))
    clean = make_per_object([("10018:0", "10018", 1)])
    dup_sampled = make_sampled([("10018:0", {}), ("10018:0", {"n_dup_wave": 5})])
    with pytest.raises(ValueError, match="duplicate row_uid"):
        join_sampled_flags(clean, dup_sampled)


# =============================================================================
# Unit tests: hard asserts
# =============================================================================


def test_validate_object_manifest_row_count_mismatch_raises():
    joined = join_sampled_flags(make_per_object([("10018:0", "10018", 1)]), make_sampled([("10018:0", {})]))
    with pytest.raises(RuntimeError, match="!= expected"):
        validate_object_manifest(joined, expected_rows=2, expected_sampled_join=1)


def test_validate_object_manifest_coverage_mismatch_raises():
    joined = join_sampled_flags(make_per_object([("10018:0", "10018", 1)]), make_sampled([("10018:0", {})]))
    with pytest.raises(RuntimeError, match="sampled-join coverage"):
        validate_object_manifest(joined, expected_rows=1, expected_sampled_join=2)


def test_validate_object_manifest_returns_coverage_block():
    rows = [(f"1001{i}:{j}", f"1001{i}", 10 * i + j) for i in range(2) for j in range(4)]
    sampled = make_sampled([(f"1001{i}:{j}", {}) for i in range(2) for j in range(0, 4, 2)])
    joined = join_sampled_flags(make_per_object(rows), sampled)
    block = validate_object_manifest(joined, expected_rows=8, expected_sampled_join=4)
    assert block == {"rows": 8, "unique_row_uids": True, "sampled_join_coverage": 4, "unsampled_rows": 4}


# =============================================================================
# Unit tests: spot-check draw and round-trip validation
# =============================================================================


def test_select_spot_check_indices_deterministic_and_bounded():
    first = select_spot_check_indices(1030934, 10, 20260815)
    second = select_spot_check_indices(1030934, 10, 20260815)
    assert first == second
    assert len(first) == 10
    assert len(set(first)) == 10
    assert all(0 <= index < 1030934 for index in first)
    assert first == sorted(first)
    assert select_spot_check_indices(3, 10, 20260815) == [0, 1, 2]
    with pytest.raises(ValueError):
        select_spot_check_indices(0, 10, 20260815)


def test_round_trip_validate_clean_pass(tmp_path):
    table = make_per_object([(f"10018:{i}", "10018", i) for i in range(50)])
    path = tmp_path / "object.parquet"
    write_part(table, path)
    block = round_trip_validate(path, table, seed=20260815, n_spot=10)
    assert block["passed"] is True
    assert block["schema_matches"] is True
    assert block["rows"] == 50
    assert block["spot_check"]["seed"] == 20260815
    assert len(block["spot_check"]["indices"]) == 10
    assert block["spot_check"]["all_match"] is True


def test_round_trip_validate_detects_value_mutation(tmp_path):
    table = make_per_object([(f"10018:{i}", "10018", i) for i in range(50)])
    path = tmp_path / "object.parquet"
    write_part(table, path)
    mutated = table.set_column(
        table.schema.get_field_index("masked_fraction"),
        "masked_fraction",
        pa.array([0.5] * 50, pa.float64()),
    )
    with pytest.raises(RuntimeError, match="spot-check mismatch"):
        round_trip_validate(path, mutated, seed=20260815, n_spot=10)


def test_round_trip_validate_detects_row_count_change(tmp_path):
    table = make_per_object([(f"10018:{i}", "10018", i) for i in range(50)])
    path = tmp_path / "object.parquet"
    write_part(table, path)
    with pytest.raises(RuntimeError, match="row mismatch"):
        round_trip_validate(path, table.slice(0, 49), seed=20260815, n_spot=10)


def test_round_trip_validate_detects_schema_change(tmp_path):
    table = make_per_object([(f"10018:{i}", "10018", i) for i in range(50)])
    path = tmp_path / "object.parquet"
    write_part(table, path)
    retyped = table.set_column(
        table.schema.get_field_index("target_id"),
        "target_id",
        table.column("target_id").cast(pa.int32()),
    )
    with pytest.raises(RuntimeError, match="schema mismatch"):
        round_trip_validate(path, retyped, seed=20260815, n_spot=10)


# =============================================================================
# Unit tests: corpus integrity comparator
# =============================================================================


def make_snapshot(rows: list[tuple[str, int, int]]) -> pa.Table:
    return pa.Table.from_pylist(
        [{"path": path, "size_bytes": size, "mtime_ns": mtime} for path, size, mtime in rows],
        schema=SNAPSHOT_SCHEMA,
    )


def test_compare_corpus_snapshots_unchanged():
    snapshot = make_snapshot([("/c/a.parquet", 10, 100), ("/c/b.parquet", 20, 200)])
    block = compare_corpus_snapshots(snapshot, snapshot)
    assert block["unchanged"] is True
    assert block["mismatch_count"] == 0
    assert block["files_checked"] == 2
    assert block["missing_files"] == []
    assert block["extra_files"] == []


def test_compare_corpus_snapshots_detects_mtime_mismatch():
    before = make_snapshot([("/c/a.parquet", 10, 100)])
    after = make_snapshot([("/c/a.parquet", 10, 999)])
    block = compare_corpus_snapshots(before, after)
    assert block["unchanged"] is False
    assert block["mismatch_count"] == 1
    assert block["mismatches"][0]["fields"]["mtime_ns"] == {"snapshot": 100, "current": 999}


def test_compare_corpus_snapshots_detects_size_mismatch():
    before = make_snapshot([("/c/a.parquet", 10, 100)])
    after = make_snapshot([("/c/a.parquet", 11, 100)])
    block = compare_corpus_snapshots(before, after)
    assert block["unchanged"] is False
    assert block["mismatches"][0]["fields"]["size_bytes"] == {"snapshot": 10, "current": 11}


def test_compare_corpus_snapshots_missing_and_extra_files():
    before = make_snapshot([("/c/a.parquet", 10, 100), ("/c/b.parquet", 20, 200)])
    after = make_snapshot([("/c/a.parquet", 10, 100), ("/c/c.parquet", 30, 300)])
    block = compare_corpus_snapshots(before, after)
    assert block["unchanged"] is False
    assert block["missing_files"] == ["/c/b.parquet"]
    assert block["extra_files"] == ["/c/c.parquet"]
    assert block["mismatch_count"] == 2


def test_compare_corpus_snapshots_real_file_stability(tmp_path):
    corpus_file = tmp_path / "tile_10000" / "qso_data_TILE10000_3_qsos.parquet"
    corpus_file.parent.mkdir()
    pq.write_table(pa.table({"x": pa.array([1, 2, 3])}), corpus_file)
    snapshot = snapshot_of([corpus_file])
    current = snapshot_of([corpus_file])
    assert compare_corpus_snapshots(snapshot, current)["unchanged"] is True
    os.utime(corpus_file, ns=(1_000_000_000, 1_000_000_001))
    changed = snapshot_of([corpus_file])
    block = compare_corpus_snapshots(snapshot, changed)
    assert block["unchanged"] is False
    assert block["mismatches"][0]["path"] == str(corpus_file)


# =============================================================================
# Script end-to-end over a synthetic work directory
# =============================================================================


def build_synthetic_work(corpus_root: Path, work_dir: Path) -> None:
    """Create a synthetic corpus (2 files, 7 rows) and matching D1-D5 work artifacts."""
    corpus_root.mkdir(parents=True)
    paths = []
    specs = [
        ("tile_10000", "qso_data_TILE10000_4_qsos.parquet", 4),
        ("tile_10020", "qso_data_TILE10020_3_qsos.parquet", 3),
    ]
    for directory, name, n_rows in specs:
        table = pa.table(
            {
                "target_id": pa.array(list(range(1000, 1000 + n_rows)), pa.int64()),
                "ra": pa.array([1.0] * n_rows, pa.float64()),
                "dec": pa.array([2.0] * n_rows, pa.float64()),
                "z": pa.array([2.5] * n_rows, pa.float64()),
                "wavelength": pa.array([[4000.0, 4100.0]] * n_rows, pa.list_(pa.float64())),
                "flux": pa.array([[1.0, 2.0]] * n_rows, pa.list_(pa.float64())),
                "ivar": pa.array([[1.0, 1.0]] * n_rows, pa.list_(pa.float64())),
                "mask_array": pa.array([[0, 0]] * n_rows, pa.list_(pa.int64())),
            }
        )
        path = corpus_root / directory / name
        path.parent.mkdir()
        pq.write_table(table, path)
        paths.append(path)
    work_dir.mkdir(parents=True)
    write_part(snapshot_of(paths), work_dir / "corpus_snapshot.parquet")
    write_part(
        pa.Table.from_pylist(
            [
                {
                    "tile_id": "10000",
                    "path": str(paths[0]),
                    "size_bytes": paths[0].stat().st_size,
                    "n_rows_actual": 4,
                    "n_claimed": 4,
                    "claimed_equals_actual": True,
                    "n_unique_target_id": 4,
                    "columns_dtypes": "x:double",
                    "schema_matches_reference": True,
                    "schema_deviation": "none",
                    "open_error": "",
                },
                {
                    "tile_id": "10020",
                    "path": str(paths[1]),
                    "size_bytes": paths[1].stat().st_size,
                    "n_rows_actual": 3,
                    "n_claimed": 3,
                    "claimed_equals_actual": True,
                    "n_unique_target_id": 3,
                    "columns_dtypes": "x:double",
                    "schema_matches_reference": True,
                    "schema_deviation": "none",
                    "open_error": "",
                },
            ],
            schema=INVENTORY_SCHEMA,
        ),
        work_dir / "inventory.parquet",
    )
    write_part(
        make_per_object(
            [(f"10000:{i}", "10000", 1000 + i) for i in range(4)]
            + [(f"10020:{i}", "10020", 1004 + i) for i in range(3)]
        ),
        work_dir / "per_object_raw.parquet",
    )
    write_part(make_sampled([("10000:1", {}), ("10020:2", {})]), work_dir / "array_audit.parquet")
    write_part(
        pa.table(
            {
                "tile_id": ["10000"] * 4 + ["10020"] * 3,
                "row_idx": list(range(4)) + list(range(3)),
                "target_id": list(range(1000, 1007)),
            },
            schema=pa.schema([("tile_id", pa.string()), ("row_idx", pa.int64()), ("target_id", pa.int64())]),
        ),
        work_dir / "target_ids.parquet",
    )
    (work_dir / "identity_summary.json").write_text(json.dumps({"totals": {"total_rows": 7}}), encoding="utf-8")
    (work_dir / "array_audit_summary.json").write_text(
        json.dumps(
            {
                "sampling": {
                    "seed": 20260815,
                    "method": "synthetic",
                    "stride": 1,
                    "start": 0,
                    "target_tiles": 2,
                    "tiles_selected": 2,
                    "rows_per_tile": 1,
                    "rows_selected": 2,
                    "reproducibility_recipe": "synthetic fixture",
                }
            }
        ),
        encoding="utf-8",
    )
    (work_dir / "inventory_summary.json").write_text(json.dumps({"total_files_scanned": 2}), encoding="utf-8")
    (work_dir / "redshift_summary.json").write_text(json.dumps({"redshift": {}}), encoding="utf-8")


def test_script_end_to_end_assembles_manifests(script, tmp_path):
    corpus_root = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "out"
    build_synthetic_work(corpus_root, work_dir)
    script.main(["--corpus-root", str(corpus_root), "--work-dir", str(work_dir), "--output-dir", str(output_dir)])
    file_manifest = pq.read_table(output_dir / "qso_file_manifest_v1.parquet")
    object_manifest = pq.read_table(output_dir / "qso_object_manifest_v1.parquet")
    provenance = json.loads((output_dir / "manifest_provenance.json").read_text(encoding="utf-8"))
    assert file_manifest.num_rows == 2
    assert file_manifest.schema.equals(INVENTORY_SCHEMA)
    assert object_manifest.num_rows == 7
    assert object_manifest.schema.equals(OBJECT_MANIFEST_SCHEMA)
    assert object_manifest.column("align_ok").to_pylist().count(True) == 2
    assert object_manifest.column("row_uid").to_pylist() == [
        "10000:0",
        "10000:1",
        "10000:2",
        "10000:3",
        "10020:0",
        "10020:1",
        "10020:2",
    ]
    assert provenance["corpus_integrity"]["unchanged"] is True
    assert provenance["corpus_integrity"]["files_checked"] == 2
    assert provenance["round_trip"]["file_manifest"]["passed"] is True
    assert provenance["round_trip"]["object_manifest"]["passed"] is True
    assert provenance["validations"]["object_manifest"]["sampled_join_coverage"] == 2
    assert provenance["outputs"]["file_manifest"]["rows"] == 2
    assert provenance["outputs"]["object_manifest"]["rows"] == 7
    assert provenance["sampling"]["seed"] == 20260815
    assert provenance["tool_commit"]
    assert all(record.get("md5") for record in provenance["inputs"][:5])


def test_script_aborts_when_corpus_modified(script, tmp_path):
    corpus_root = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "out"
    build_synthetic_work(corpus_root, work_dir)
    victim = corpus_root / "tile_10000" / "qso_data_TILE10000_4_qsos.parquet"
    os.utime(victim, ns=(victim.stat().st_atime_ns, victim.stat().st_mtime_ns + 1))
    with pytest.raises(SystemExit, match="corpus modified"):
        script.main(["--corpus-root", str(corpus_root), "--work-dir", str(work_dir), "--output-dir", str(output_dir)])
    assert not (output_dir / "qso_file_manifest_v1.parquet").exists()
    assert not (output_dir / "manifest_provenance.json").exists()


def test_script_aborts_when_work_inputs_disagree(script, tmp_path):
    corpus_root = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    build_synthetic_work(corpus_root, work_dir)
    identity = json.loads((work_dir / "identity_summary.json").read_text(encoding="utf-8"))
    identity["totals"]["total_rows"] = 8
    (work_dir / "identity_summary.json").write_text(json.dumps(identity), encoding="utf-8")
    with pytest.raises(SystemExit, match="inputs disagree"):
        script.main(
            ["--corpus-root", str(corpus_root), "--work-dir", str(work_dir), "--output-dir", str(tmp_path / "out")]
        )
