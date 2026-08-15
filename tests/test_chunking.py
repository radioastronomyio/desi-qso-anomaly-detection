#!/usr/bin/env python3
"""
Script Name  : test_chunking.py
Description  : Unit tests for chunk planning, part files, and resume logic.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers fixed-size chunk planning over the sorted file list, part-file path
naming and round-trip IO, checkpoint validity checks (missing, corrupt,
and wrong row count), pending-chunk selection for resume, and the corpus
integrity snapshot writer.

Usage
-----
    pytest tests/test_chunking.py

Examples
--------
    pytest tests/test_chunking.py -k resume
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_qso_table, write_qso_table
from dqad_audit import (
    INVENTORY_SCHEMA,
    SNAPSHOT_SCHEMA,
    inventory_table,
    part_filepath,
    part_is_valid,
    pending_chunk_indices,
    plan_chunks,
    read_part,
    scan_chunk,
    schema_pairs,
    target_ids_table,
    write_corpus_snapshot,
    write_part,
)

# =============================================================================
# Functions
# =============================================================================


def write_three_files(tmp_path: Path) -> list[Path]:
    names = [
        "tile_10000/qso_data_TILE10000_2_qsos.parquet",
        "tile_10001/qso_data_TILE10001_3_qsos.parquet",
        "tile_10002/qso_data_TILE10002_2_qsos.parquet",
    ]
    return [write_qso_table(tmp_path / name, make_qso_table(n)) for name, n in zip(names, [2, 3, 2])]


def placeholder_inventory_rows(count: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        rows.append(
            {
                "tile_id": f"{10000 + index}",
                "path": f"/synthetic/tile_{10000 + index}/file{index}.parquet",
                "size_bytes": 100,
                "n_rows_actual": 2,
                "n_claimed": 2,
                "claimed_equals_actual": True,
                "n_unique_target_id": 2,
                "columns_dtypes": "target_id:int64",
                "schema_matches_reference": True,
                "schema_deviation": "none",
                "open_error": "",
            }
        )
    return rows


def test_plan_chunks_sizes() -> None:
    files = [Path(f"f{i}.parquet") for i in range(7)]
    assert [len(chunk) for chunk in plan_chunks(files, 3)] == [3, 3, 1]


def test_plan_chunks_exact_division() -> None:
    files = [Path(f"f{i}.parquet") for i in range(6)]
    assert [len(chunk) for chunk in plan_chunks(files, 3)] == [3, 3]


def test_plan_chunks_empty_input() -> None:
    assert plan_chunks([], 3) == []


def test_plan_chunks_invalid_size_raises() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        plan_chunks([Path("f.parquet")], 0)


def test_part_filepath_format(tmp_path: Path) -> None:
    assert part_filepath(tmp_path, "inventory", 7) == tmp_path / "parts" / "inventory" / "0007.parquet"
    assert part_filepath(tmp_path, "target_ids", 1043).name == "1043.parquet"


def test_part_roundtrip(tmp_path: Path) -> None:
    path = part_filepath(tmp_path, "inventory", 0)
    table = target_ids_table({"tile_id": ["10000"], "row_idx": [0], "target_id": [7]})
    write_part(table, path)
    assert read_part(path) == table


def test_part_is_valid_states(tmp_path: Path) -> None:
    path = part_filepath(tmp_path, "inventory", 0)
    assert part_is_valid(path) is False
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a parquet file")
    assert part_is_valid(path) is False
    write_part(
        target_ids_table({"tile_id": [], "row_idx": [], "target_id": []}),
        path,
    )
    assert part_is_valid(path) is True
    assert part_is_valid(path, expected_rows=0) is True
    assert part_is_valid(path, expected_rows=2) is False


def test_pending_chunk_indices_resume_flow(tmp_path: Path) -> None:
    files = write_three_files(tmp_path / "corpus")
    work_dir = tmp_path / "work"
    chunks = plan_chunks(files, 2)
    assert pending_chunk_indices(chunks, work_dir) == [0, 1]
    reference = schema_pairs(make_qso_table().schema)
    for index in range(2):
        result = scan_chunk(chunks[index], reference)
        write_part(inventory_table(result["inventory"]), part_filepath(work_dir, "inventory", index))
        write_part(target_ids_table(result["target_ids"]), part_filepath(work_dir, "target_ids", index))
    assert pending_chunk_indices(chunks, work_dir) == []


def test_pending_chunk_indices_rejects_incomplete_inventory_part(tmp_path: Path) -> None:
    files = write_three_files(tmp_path / "corpus")
    work_dir = tmp_path / "work"
    chunks = plan_chunks(files, 2)
    write_part(inventory_table(placeholder_inventory_rows(1)), part_filepath(work_dir, "inventory", 0))
    write_part(
        target_ids_table({"tile_id": [], "row_idx": [], "target_id": []}),
        part_filepath(work_dir, "target_ids", 0),
    )
    assert pending_chunk_indices(chunks, work_dir) == [0, 1]


def test_inventory_table_conforms_to_schema() -> None:
    table = inventory_table(placeholder_inventory_rows(2))
    assert table.schema == INVENTORY_SCHEMA
    assert table.num_rows == 2


def test_write_corpus_snapshot(tmp_path: Path) -> None:
    files = write_three_files(tmp_path / "corpus")
    snapshot_path = tmp_path / "work" / "corpus_snapshot.parquet"
    n_rows = write_corpus_snapshot(files, snapshot_path)
    assert n_rows == 3
    table = read_part(snapshot_path)
    assert table.schema == SNAPSHOT_SCHEMA
    assert table.column("path").to_pylist() == [str(path) for path in files]
    assert all(size > 0 for size in table.column("size_bytes").to_pylist())
    assert all(mtime > 0 for mtime in table.column("mtime_ns").to_pylist())
