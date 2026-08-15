#!/usr/bin/env python3
"""
Script Name  : test_parsing.py
Description  : Unit tests for corpus filename parsing and row identifiers.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers claimed-count parsing from the _(\\d+)_qsos filename token, tile id
extraction with zero padding, row_uid composition, and sorted corpus
discovery against synthetic files in tmp_path.

Usage
-----
    pytest tests/test_parsing.py

Examples
--------
    pytest tests/test_parsing.py -k claimed
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import pytest

from conftest import make_qso_table, write_qso_table
from dqad_audit import discover_corpus_files, parse_claimed_count, row_uid, tile_id_from_filename

# =============================================================================
# Functions
# =============================================================================


def test_parse_claimed_count_extracts_count() -> None:
    assert parse_claimed_count("qso_data_TILE10000_74_qsos.parquet") == 74


def test_parse_claimed_count_extracts_multidigit_count() -> None:
    assert parse_claimed_count("qso_data_TILE49151_121_qsos.parquet") == 121


def test_parse_claimed_count_no_token_returns_minus_one() -> None:
    assert parse_claimed_count("qso_data_TILE10000_qsos.parquet") == -1
    assert parse_claimed_count("some_other_file.parquet") == -1


def test_tile_id_from_filename() -> None:
    assert tile_id_from_filename("qso_data_TILE10000_74_qsos.parquet") == "10000"


def test_tile_id_zero_pads_to_five() -> None:
    assert tile_id_from_filename("qso_data_TILE123_5_qsos.parquet") == "00123"


def test_tile_id_preserves_long_ids() -> None:
    assert tile_id_from_filename("qso_data_TILE123456_5_qsos.parquet") == "123456"


def test_tile_id_missing_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no TILE id pattern"):
        tile_id_from_filename("not_a_corpus_file.parquet")


def test_row_uid_composition() -> None:
    assert row_uid("10000", 3) == "10000:3"
    assert row_uid("00123", 0) == "00123:0"


def test_discover_corpus_files_sorted_and_filtered(tmp_path) -> None:
    write_qso_table(tmp_path / "tile_10002/qso_data_TILE10002_2_qsos.parquet", make_qso_table(2))
    write_qso_table(tmp_path / "tile_10000/qso_data_TILE10000_1_qsos.parquet", make_qso_table(1))
    write_qso_table(tmp_path / "tile_10001/qso_data_TILE10001_3_qsos.parquet", make_qso_table(3))
    notes_dir = tmp_path / "tile_10003"
    notes_dir.mkdir()
    (notes_dir / "notes.txt").write_text("not a parquet file", encoding="utf-8")
    (tmp_path / "tile_10004").mkdir()
    files = discover_corpus_files(tmp_path)
    assert [path.name for path in files] == [
        "qso_data_TILE10000_1_qsos.parquet",
        "qso_data_TILE10001_3_qsos.parquet",
        "qso_data_TILE10002_2_qsos.parquet",
    ]


def test_discover_corpus_files_empty_root(tmp_path) -> None:
    assert discover_corpus_files(tmp_path) == []
