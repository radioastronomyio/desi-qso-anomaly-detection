#!/usr/bin/env python3
"""
Script Name  : test_schema.py
Description  : Unit tests for schema serialization and reference diffing.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers Arrow schema serialization to name/type/nullable records, the
stable name:type columns string, and schema_diff deviation strings for
identical, added, removed, retyped, and reordered column sets.

Usage
-----
    pytest tests/test_schema.py

Examples
--------
    pytest tests/test_schema.py -k deviation
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import pyarrow as pa

from conftest import make_qso_table
from dqad_audit import columns_dtypes, schema_diff, schema_pairs, serialize_schema

# =============================================================================
# Functions
# =============================================================================


def reference_pairs() -> list[tuple[str, str]]:
    return schema_pairs(make_qso_table().schema)


def test_serialize_schema_records() -> None:
    records = serialize_schema(make_qso_table().schema)
    assert records[0] == {"name": "target_id", "type": "int64", "nullable": True}
    assert [record["name"] for record in records] == [
        "target_id",
        "ra",
        "dec",
        "z",
        "wavelength",
        "flux",
        "ivar",
        "mask_array",
    ]


def test_columns_dtypes_serialization() -> None:
    list_double = str(pa.list_(pa.float64()))
    list_int = str(pa.list_(pa.int64()))
    assert columns_dtypes(make_qso_table().schema) == (
        f"target_id:int64,ra:double,dec:double,z:double,"
        f"wavelength:{list_double},flux:{list_double},ivar:{list_double},mask_array:{list_int}"
    )


def test_schema_diff_identical() -> None:
    pairs = reference_pairs()
    assert schema_diff(pairs, list(pairs)) == (True, "none")


def test_schema_diff_added_column() -> None:
    observed = reference_pairs() + [("flux2", "double")]
    matches, deviation = schema_diff(reference_pairs(), observed)
    assert matches is False
    assert deviation == "added_columns=[flux2]"


def test_schema_diff_removed_column() -> None:
    observed = [pair for pair in reference_pairs() if pair[0] != "z"]
    matches, deviation = schema_diff(reference_pairs(), observed)
    assert matches is False
    assert deviation == "removed_columns=[z]"


def test_schema_diff_retyped_column() -> None:
    observed = [(name, "float" if name == "ra" else dtype) for name, dtype in reference_pairs()]
    matches, deviation = schema_diff(reference_pairs(), observed)
    assert matches is False
    assert deviation == "retyped_columns=[ra:double->float]"


def test_schema_diff_reordered_only() -> None:
    reordered = [reference_pairs()[i] for i in (1, 0, 2, 3, 4, 5, 6, 7)]
    matches, deviation = schema_diff(reference_pairs(), reordered)
    assert matches is False
    assert deviation == "column_order_differs"


def test_schema_diff_multiple_deviations() -> None:
    observed = [("target_id", "int64"), ("ra", "float"), ("mask_array", "list<item: int64>")]
    matches, deviation = schema_diff(reference_pairs(), observed)
    assert matches is False
    assert deviation == ("removed_columns=[dec,flux,ivar,wavelength,z]; retyped_columns=[ra:double->float]")


def test_schema_diff_against_real_deviant_tables() -> None:
    pairs = reference_pairs()
    dropped = schema_pairs(make_qso_table(drop=["ivar"]).schema)
    retyped = schema_pairs(make_qso_table(retype={"ra": pa.float32()}).schema)
    assert schema_diff(pairs, dropped)[1] == "removed_columns=[ivar]"
    assert schema_diff(pairs, retyped)[1] == "retyped_columns=[ra:double->float]"
