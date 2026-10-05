#!/usr/bin/env python3
"""
Script Name  : conftest.py
Description  : Synthetic Parquet fixtures for dqad_audit unit tests.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Builds tiny in-memory QSO Parquet files that mirror the measured corpus
schema (target_id int64, ra/dec/z double, wavelength/flux/ivar
list<double>, mask_array list<int64>) and writes them to pytest tmp_path.
Tests never touch the real corpus under /mnt/nvme01; every fixture is a
synthetic file created in a temporary directory.

Usage
-----
    from tests.conftest import make_qso_table

    table = make_qso_table(n_rows=4, target_ids=[1, 1, 2, 3])

Examples
--------
    def test_something(tmp_path, qso_file):
        row = scan_file(qso_file("tile_10000/qso_data_TILE10000_3_qsos.parquet"), None)
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# =============================================================================
# Configuration
# =============================================================================

REFERENCE_COLUMN_TYPES: dict[str, pa.DataType] = {
    "target_id": pa.int64(),
    "ra": pa.float64(),
    "dec": pa.float64(),
    "z": pa.float64(),
    "wavelength": pa.list_(pa.float64()),
    "flux": pa.list_(pa.float64()),
    "ivar": pa.list_(pa.float64()),
    "mask_array": pa.list_(pa.int64()),
}

# =============================================================================
# Functions
# =============================================================================


def make_qso_table(
    n_rows: int = 3,
    target_ids: Sequence[int] | None = None,
    drop: Sequence[str] = (),
    retype: Mapping[str, pa.DataType] | None = None,
    extra: Mapping[str, pa.Array] | None = None,
) -> pa.Table:
    """
    Build a synthetic QSO table matching the measured corpus schema.

    Parameters
    ----------
    n_rows : int, optional
        Number of rows to generate.
    target_ids : sequence of int, optional
        Explicit target_id values; defaults to 1000, 1001, ... allowing
        duplicates to be expressed for uniqueness tests.
    drop : sequence of str, optional
        Reference columns to omit (schema deviation: removed).
    retype : mapping of str to pa.DataType, optional
        Columns to cast to a different type (schema deviation: retyped).
    extra : mapping of str to pa.Array, optional
        Additional columns to append (schema deviation: added).

    Returns
    -------
    pa.Table
        Synthetic table with n_rows rows.
    """
    if target_ids is None:
        target_ids = list(range(1000, 1000 + n_rows))
    if len(target_ids) != n_rows:
        raise ValueError("target_ids length must equal n_rows")
    scalar_lists = [[0.1 * (i + 1), 0.2 * (i + 1)] for i in range(n_rows)]
    mask_lists = [[0, 1] for _ in range(n_rows)]
    columns: dict[str, pa.Array] = {
        "target_id": pa.array(list(target_ids), pa.int64()),
        "ra": pa.array([150.0] * n_rows, pa.float64()),
        "dec": pa.array([2.2] * n_rows, pa.float64()),
        "z": pa.array([2.5] * n_rows, pa.float64()),
        "wavelength": pa.array(scalar_lists, pa.list_(pa.float64())),
        "flux": pa.array(scalar_lists, pa.list_(pa.float64())),
        "ivar": pa.array(scalar_lists, pa.list_(pa.float64())),
        "mask_array": pa.array(mask_lists, pa.list_(pa.int64())),
    }
    for name in drop:
        columns.pop(name)
    if retype:
        for name, data_type in retype.items():
            columns[name] = columns[name].cast(data_type)
    if extra:
        for name, array in extra.items():
            columns[name] = array
    return pa.table(columns)


def write_qso_table(path: Path, table: pa.Table) -> Path:
    """
    Write a synthetic QSO table to a Parquet file under tmp_path.

    Parameters
    ----------
    path : Path
        Relative destination path (parent directories are created).
    table : pa.Table
        Table to write.

    Returns
    -------
    Path
        Absolute path of the written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return path


@pytest.fixture
def qso_file(tmp_path: Path) -> Callable[[str, ...], Path]:
    """
    Factory writing a synthetic corpus-named QSO Parquet file under tmp_path.

    Returns
    -------
    Callable
        Accepts a relative filename such as
        tile_10000/qso_data_TILE10000_74_qsos.parquet plus make_qso_table
        keyword arguments; returns the written absolute path.
    """

    def _write(name: str, **table_kwargs: Any) -> Path:
        return write_qso_table(tmp_path / name, make_qso_table(**table_kwargs))

    return _write
