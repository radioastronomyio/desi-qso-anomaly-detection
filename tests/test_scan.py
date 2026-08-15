#!/usr/bin/env python3
"""
Script Name  : test_scan.py
Description  : Unit tests for the per-file and per-chunk corpus scanner.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers scan_file and scan_chunk against synthetic QSO Parquet files in
tmp_path: matching schema, deviant schemas (added, removed, retyped),
claimed-count mismatch, duplicate target_ids within a file, corrupt-file
open failures with numeric fallbacks, and target_ids row extraction.

Usage
-----
    pytest tests/test_scan.py

Examples
--------
    pytest tests/test_scan.py -k corrupt
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dqad_audit import scan_chunk, scan_file, schema_pairs

# =============================================================================
# Configuration
# =============================================================================

GOOD_FILE_NAME = "tile_10000/qso_data_TILE10000_3_qsos.parquet"


# =============================================================================
# Functions
# =============================================================================


@pytest.fixture
def reference_pairs(qso_file) -> list[tuple[str, str]]:
    reference_path = qso_file(GOOD_FILE_NAME)
    return schema_pairs(pq.ParquetFile(reference_path).schema_arrow)


def test_scan_file_matching(tmp_path: Path, qso_file, reference_pairs) -> None:
    path = qso_file(GOOD_FILE_NAME)
    result = scan_file(path, reference_pairs)
    row = result["inventory"]
    assert row["tile_id"] == "10000"
    assert row["path"] == str(path)
    assert row["size_bytes"] == path.stat().st_size
    assert row["n_rows_actual"] == 3
    assert row["n_claimed"] == 3
    assert row["claimed_equals_actual"] is True
    assert row["n_unique_target_id"] == 3
    assert row["columns_dtypes"].startswith("target_id:int64,ra:double,")
    assert row["schema_matches_reference"] is True
    assert row["schema_deviation"] == "none"
    assert row["open_error"] == ""


def test_scan_file_claim_mismatch(qso_file, reference_pairs) -> None:
    path = qso_file("tile_10001/qso_data_TILE10001_74_qsos.parquet", n_rows=3)
    row = scan_file(path, reference_pairs)["inventory"]
    assert row["n_claimed"] == 74
    assert row["n_rows_actual"] == 3
    assert row["claimed_equals_actual"] is False


def test_scan_file_duplicate_target_ids(qso_file, reference_pairs) -> None:
    path = qso_file("tile_10002/qso_data_TILE10002_4_qsos.parquet", n_rows=4, target_ids=[9, 9, 8, 8])
    row = scan_file(path, reference_pairs)["inventory"]
    assert row["n_rows_actual"] == 4
    assert row["n_unique_target_id"] == 2


def test_scan_file_extra_column(tmp_path: Path, qso_file, reference_pairs) -> None:
    extra_flux = pa.array([1.0, 1.0, 1.0], pa.float64())
    path = qso_file(GOOD_FILE_NAME, extra={"flux2": extra_flux})
    row = scan_file(path, reference_pairs)["inventory"]
    assert row["schema_matches_reference"] is False
    assert row["schema_deviation"] == "added_columns=[flux2]"


def test_scan_file_missing_column(qso_file, reference_pairs) -> None:
    path = qso_file(GOOD_FILE_NAME, drop=["z"])
    row = scan_file(path, reference_pairs)["inventory"]
    assert row["schema_matches_reference"] is False
    assert row["schema_deviation"] == "removed_columns=[z]"


def test_scan_file_retyped_column(qso_file, reference_pairs) -> None:
    path = qso_file(GOOD_FILE_NAME, retype={"ra": pa.float32()})
    row = scan_file(path, reference_pairs)["inventory"]
    assert row["schema_matches_reference"] is False
    assert "retyped_columns=[ra:" in row["schema_deviation"]


def test_scan_file_corrupt_records_error(tmp_path: Path, reference_pairs) -> None:
    corrupt = tmp_path / "tile_10003" / "qso_data_TILE10003_5_qsos.parquet"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"this is not a parquet file")
    row = scan_file(corrupt, reference_pairs)["inventory"]
    assert row["open_error"].startswith("ArrowInvalid")
    assert row["tile_id"] == ""
    assert row["n_rows_actual"] == -1
    assert row["n_claimed"] == -1
    assert row["claimed_equals_actual"] is False
    assert row["n_unique_target_id"] == -1
    assert row["columns_dtypes"] == ""
    assert row["schema_matches_reference"] is False


def test_scan_file_without_reference_skips_comparison(qso_file) -> None:
    path = qso_file(GOOD_FILE_NAME, drop=["z"])
    row = scan_file(path, None)["inventory"]
    assert row["schema_matches_reference"] is True
    assert row["schema_deviation"] == "none"
    assert row["n_rows_actual"] == 3


def test_scan_file_target_ids_payload(qso_file, reference_pairs) -> None:
    path = qso_file("tile_10004/qso_data_TILE10004_3_qsos.parquet", n_rows=3, target_ids=[50, 51, 52])
    target_ids = scan_file(path, reference_pairs)["target_ids"]
    assert target_ids == {"tile_id": ["10004", "10004", "10004"], "row_idx": [0, 1, 2], "target_id": [50, 51, 52]}


def test_scan_chunk_aggregates_and_keeps_failures(tmp_path: Path, qso_file, reference_pairs) -> None:
    good_one = qso_file("tile_10000/qso_data_TILE10000_2_qsos.parquet", n_rows=2)
    good_two = qso_file("tile_10001/qso_data_TILE10001_3_qsos.parquet", n_rows=3)
    corrupt = tmp_path / "tile_10002" / "qso_data_TILE10002_5_qsos.parquet"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"corrupt payload")
    result = scan_chunk([good_one, good_two, corrupt], reference_pairs)
    inventory = result["inventory"]
    assert len(inventory) == 3
    assert [row["tile_id"] for row in inventory] == ["10000", "10001", ""]
    assert inventory[2]["open_error"] != ""
    assert result["target_ids"]["tile_id"] == ["10000", "10000", "10001", "10001", "10001"]
    assert result["target_ids"]["row_idx"] == [0, 1, 0, 1, 2]
    assert len(result["target_ids"]["target_id"]) == 5


def test_scan_file_reference_file_matches_itself(qso_file, reference_pairs) -> None:
    path = qso_file(GOOD_FILE_NAME)
    assert scan_file(path, reference_pairs)["inventory"]["schema_deviation"] == "none"
