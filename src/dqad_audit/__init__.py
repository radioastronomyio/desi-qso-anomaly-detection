#!/usr/bin/env python3
"""
Script Name  : dqad_audit/__init__.py
Description  : Shared helpers for the DESI DR1 QSO spectral Parquet corpus audit.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Shared library for the read-only spectral manifest audit of the DESI DR1 QSO
Parquet corpus. Provides corpus discovery, filename parsing (tile id and
claimed row count), schema serialization and diffing, chunk planning,
checkpoint part-file IO with resume support, corpus integrity snapshotting,
the per-file scanner used by the parallel inventory pipeline, the
archive-wide identity/duplication analysis (repeat-count distribution,
within-tile vs cross-tile split, naive-split leakage expectation, DR1
population gap accounting, converter filter extraction), the sampled
array contract audit (deterministic systematic tile/row sampling, the six
per-row checks over wavelength/flux/ivar/mask arrays, arm-overlap
measurement, merge/de-dup policy recommendation, summary assembly), and
the archive-wide scalar extraction / nuisance baseline pass (per-row
snr_proxy, masked_fraction, array_length over flux/ivar/mask only;
redshift histogram and quantiles; the candidate redshift bin selection
table with common rest-frame interval and grid-length math), and the
D6 manifest assembly (LEFT-JOIN of the sampled contract flags onto the
per-object table keyed by row_uid, the corpus integrity comparator over
size/mtime snapshots, pyarrow round-trip validation with seeded spot
checks, and the manifest provenance payload).

The corpus under CORPUS_ROOT is strictly read-only. Every helper here opens
corpus files for reading only and writes only to caller-provided output
paths (normally the audit work directory).

Usage
-----
    from dqad_audit import discover_corpus_files, scan_chunk, row_uid

    files = discover_corpus_files(corpus_root)
    result = scan_chunk(files[:10], reference_pairs)

    from dqad_audit import audit_arrays

    flags = audit_arrays(wave, flux, ivar, mask)

Examples
--------
    >>> row_uid("10000", 3)
    '10000:3'
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

# =============================================================================
# Configuration
# =============================================================================

CORPUS_ROOT = Path(os.environ.get("DQAD_CORPUS_ROOT", "/mnt/nvme01/desidr1-parquet-tiles/desi-qad"))
WORK_DIR = Path(os.environ.get("DQAD_WORK_DIR", "/mnt/nvme01/desidr1-manifest/work"))

CORPUS_GLOB = "tile_*/qso_data_TILE*.parquet"
CHUNK_SIZE = 250
PROGRESS_EVERY = 500
TILE_ID_WIDTH = 5

RUN_CONFIG_FILENAME = "run_config.json"
RUN_CONFIG_IDENTITY_KEYS = ("corpus_root", "n_files", "files_sha256")

TILE_PATTERN = re.compile(r"TILE(\d+)")
CLAIM_PATTERN = re.compile(r"_(\d+)_qsos")

SCHEMA_MATCH_TOKEN = "none"
OPEN_FAILURE_DEVIATION = "not determined (open failed)"

INVENTORY_SCHEMA = pa.schema(
    [
        ("tile_id", pa.string()),
        ("path", pa.string()),
        ("size_bytes", pa.int64()),
        ("n_rows_actual", pa.int64()),
        ("n_claimed", pa.int64()),
        ("claimed_equals_actual", pa.bool_()),
        ("n_unique_target_id", pa.int64()),
        ("columns_dtypes", pa.string()),
        ("schema_matches_reference", pa.bool_()),
        ("schema_deviation", pa.string()),
        ("open_error", pa.string()),
    ]
)

TARGET_IDS_SCHEMA = pa.schema(
    [
        ("tile_id", pa.string()),
        ("row_idx", pa.int64()),
        ("target_id", pa.int64()),
    ]
)

SNAPSHOT_SCHEMA = pa.schema(
    [
        ("path", pa.string()),
        ("size_bytes", pa.int64()),
        ("mtime_ns", pa.int64()),
    ]
)

CONVERTER_SOURCE_PATH = Path(
    "/opt/agents/repos/desi-cosmic-void-galaxies/work-logs/03-spectral-tile-pipeline/02-extract-qso-tile-to-parquet.py"
)
CONVERTER_FILTER_PATTERN = re.compile(r'if row\["SPECTYPE"\] == "QSO" and row\["ZWARN"\] == 0')
CONVERTER_LINE_PATTERNS: dict[str, re.Pattern[str]] = {
    "wavelength_min": re.compile(r"^\s*MIN_WAVE = \d+"),
    "wavelength_max": re.compile(r"^\s*MAX_WAVE = \d+"),
    "arm_concatenation": re.compile(r"^\s*full_wave = np\.concatenate"),
    "flux_concatenation": re.compile(r"^\s*flux_brz = np\.concatenate"),
    "wavelength_mask": re.compile(r"^\s*mask = \(full_wave >= MIN_WAVE\)"),
}

DR1_MAIN_QSO_POPULATION_REFERENCE = 1_550_000
NAIVE_HOLDOUT_FRACTION = 0.1
IDENTITY_SUMMARY_FILENAME = "identity_summary.json"

ARRAY_SAMPLING_SEED = 20_260_815
ARRAY_TARGET_TILES = 512
ARRAY_ROWS_PER_TILE = 64
ARRAY_NEARDUP_TOLERANCE_ANGSTROM = 0.1
ARRAY_MASK_TOP_N = 64
ARRAY_AUDIT_PART_KIND = "array_audit"
ARRAY_AUDIT_FILENAME = "array_audit.parquet"
ARRAY_AUDIT_SUMMARY_FILENAME = "array_audit_summary.json"
ARRAY_AUDIT_COLUMNS = ("wavelength", "flux", "ivar", "mask_array")
ARRAY_BREAK_EXAMPLE_LIMIT = 10

NOMINAL_ARM_OVERLAPS: dict[str, tuple[float, float]] = {
    "B_R": (5760.0, 5800.0),
    "R_Z": (7520.0, 7620.0),
}

ARRAY_AUDIT_SCHEMA = pa.schema(
    [
        ("row_uid", pa.string()),
        ("tile_id", pa.string()),
        ("row_idx", pa.int64()),
        ("n_pixels", pa.int64()),
        ("align_ok", pa.bool_()),
        ("n_mono_breaks", pa.int64()),
        ("first_break_idx", pa.int64()),
        ("overlap_br_angstrom", pa.float64()),
        ("overlap_rz_angstrom", pa.float64()),
        ("n_dup_wave", pa.int64()),
        ("n_neardup_wave", pa.int64()),
        ("ivar_neg_count", pa.int64()),
        ("ivar_nonfinite_count", pa.int64()),
        ("ivar_zero_count", pa.int64()),
        ("ivar_zero_frac", pa.float64()),
        ("wave_min", pa.float64()),
        ("wave_max", pa.float64()),
        ("mask_value_counts", pa.string()),
    ]
)

ARRAY_AUDIT_AUX_FIELDS = [
    ("br_wave_before", pa.float64()),
    ("br_wave_after", pa.float64()),
    ("rz_wave_before", pa.float64()),
    ("rz_wave_after", pa.float64()),
    ("second_break_idx", pa.int64()),
]

ARRAY_AUDIT_PART_SCHEMA = pa.schema([*list(ARRAY_AUDIT_SCHEMA), *ARRAY_AUDIT_AUX_FIELDS])

ARRAY_ROW_SELECTION_SCHEME = (
    "per selected tile: n_rows from Parquet metadata; if n_rows <= rows_per_tile take all rows; "
    "else stride_r = n_rows // rows_per_tile, start_r = numpy.random.default_rng(child_seed)"
    ".integers(0, n_rows - (rows_per_tile - 1) * stride_r), rows = start_r + i * stride_r for "
    "i in range(rows_per_tile), where child_seed = numpy.random.SeedSequence(seed)"
    ".spawn(n_selected_tiles)[tile_index] and tile_index is the 0-based position in the "
    "selected tile list"
)

# =============================================================================
# Functions
# =============================================================================


def discover_corpus_files(corpus_root: Path | str = CORPUS_ROOT) -> list[Path]:
    """
    Discover all corpus Parquet files in deterministic sorted-path order.

    Parameters
    ----------
    corpus_root : Path or str, optional
        Root directory containing tile_*/qso_data_TILE*.parquet files.

    Returns
    -------
    list[Path]
        Absolute paths sorted lexicographically by string form.
    """
    root = Path(corpus_root)
    return sorted(root.glob(CORPUS_GLOB), key=str)


def tile_id_from_filename(filename: str) -> str:
    """
    Extract the zero-padded tile id from a corpus filename.

    Parameters
    ----------
    filename : str
        Filename such as qso_data_TILE10000_74_qsos.parquet.

    Returns
    -------
    str
        Tile id zero-padded to at least 5 digits, e.g. "10000" or "00123".

    Raises
    ------
    ValueError
        If no TILE<digits> token is present in the filename.
    """
    match = TILE_PATTERN.search(filename)
    if match is None:
        raise ValueError(f"no TILE id pattern in filename: {filename}")
    return match.group(1).zfill(TILE_ID_WIDTH)


def parse_claimed_count(filename: str) -> int:
    """
    Parse the filename-claimed QSO count from a corpus filename.

    Parameters
    ----------
    filename : str
        Filename such as qso_data_TILE10000_74_qsos.parquet.

    Returns
    -------
    int
        Claimed count from the _(\\d+)_qsos token, or -1 when absent.
    """
    match = CLAIM_PATTERN.search(filename)
    if match is None:
        return -1
    return int(match.group(1))


def row_uid(tile_id: str, row_idx: int) -> str:
    """
    Build the stable row identifier used to key corpus rows across audit stages.

    Parameters
    ----------
    tile_id : str
        Zero-padded tile id.
    row_idx : int
        Row position within the tile's file.

    Returns
    -------
    str
        Identifier of the form "<tile_id>:<row_idx>".
    """
    return f"{tile_id}:{row_idx}"


def serialize_schema(schema: pa.Schema) -> list[dict[str, Any]]:
    """
    Serialize an Arrow schema to a JSON-friendly column list.

    Parameters
    ----------
    schema : pa.Schema
        Schema to serialize.

    Returns
    -------
    list[dict[str, Any]]
        One dict per column with keys name, type, nullable.
    """
    return [{"name": field.name, "type": str(field.type), "nullable": field.nullable} for field in list(schema)]


def schema_pairs(schema: pa.Schema) -> list[tuple[str, str]]:
    """
    Project an Arrow schema to ordered (name, type-string) pairs.

    Parameters
    ----------
    schema : pa.Schema
        Schema to project.

    Returns
    -------
    list[tuple[str, str]]
        Ordered pairs of column name and type string.
    """
    return [(field.name, str(field.type)) for field in list(schema)]


def columns_dtypes(schema: pa.Schema) -> str:
    """
    Build the stable name:type serialization stored in the inventory table.

    Parameters
    ----------
    schema : pa.Schema
        Schema to serialize.

    Returns
    -------
    str
        Comma-joined "name:type" tokens in column order.
    """
    return ",".join(f"{name}:{dtype}" for name, dtype in schema_pairs(schema))


def schema_diff(
    reference_pairs: Sequence[tuple[str, str]],
    observed_pairs: Sequence[tuple[str, str]],
) -> tuple[bool, str]:
    """
    Compare an observed schema against the reference schema pair list.

    Parameters
    ----------
    reference_pairs : sequence of tuple[str, str]
        Ordered (name, type) pairs of the reference schema.
    observed_pairs : sequence of tuple[str, str]
        Ordered (name, type) pairs of the observed schema.

    Returns
    -------
    tuple[bool, str]
        Match flag and deviation string. The deviation is "none" when the
        pair lists match exactly; otherwise it lists added, removed, and
        retyped columns, or notes a pure ordering difference.
    """
    reference = list(reference_pairs)
    observed = list(observed_pairs)
    if reference == observed:
        return True, SCHEMA_MATCH_TOKEN
    ref_types = dict(reference)
    obs_types = dict(observed)
    added = sorted(set(obs_types) - set(ref_types))
    removed = sorted(set(ref_types) - set(obs_types))
    retyped = sorted(
        f"{name}:{ref_types[name]}->{obs_types[name]}"
        for name in sorted(set(ref_types) & set(obs_types))
        if ref_types[name] != obs_types[name]
    )
    parts: list[str] = []
    if added:
        parts.append(f"added_columns=[{','.join(added)}]")
    if removed:
        parts.append(f"removed_columns=[{','.join(removed)}]")
    if retyped:
        parts.append(f"retyped_columns=[{','.join(retyped)}]")
    if not parts and [name for name, _ in reference] != [name for name, _ in observed]:
        parts.append("column_order_differs")
    return False, "; ".join(parts)


def plan_chunks(files: Sequence[Path], chunk_size: int = CHUNK_SIZE) -> list[list[Path]]:
    """
    Split the sorted file list into consecutive fixed-size chunks.

    Parameters
    ----------
    files : sequence of Path
        Files in deterministic scan order.
    chunk_size : int, optional
        Maximum number of files per chunk.

    Returns
    -------
    list[list[Path]]
        Consecutive chunks; the final chunk may be shorter.

    Raises
    ------
    ValueError
        If chunk_size is not a positive integer.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    materialized = list(files)
    return [materialized[i : i + chunk_size] for i in range(0, len(materialized), chunk_size)]


def part_dir(work_dir: Path | str, kind: str) -> Path:
    """
    Resolve the directory holding part files of a given kind.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.
    kind : str
        Part family name, e.g. "inventory" or "target_ids".

    Returns
    -------
    Path
        Directory work_dir/parts/<kind>.
    """
    return Path(work_dir) / "parts" / kind


def part_filepath(work_dir: Path | str, kind: str, chunk_index: int) -> Path:
    """
    Resolve the checkpoint part-file path for a chunk.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.
    kind : str
        Part family name, e.g. "inventory" or "target_ids".
    chunk_index : int
        Zero-based chunk index over the sorted file list.

    Returns
    -------
    Path
        Path of the form work_dir/parts/<kind>/NNNN.parquet.
    """
    return part_dir(work_dir, kind) / f"{chunk_index:04d}.parquet"


def write_part(table: pa.Table, path: Path | str) -> None:
    """
    Write a checkpoint part file, creating parent directories as needed.

    Parameters
    ----------
    table : pa.Table
        Table to persist.
    path : Path or str
        Destination part-file path.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, destination)


def read_part(path: Path | str) -> pa.Table:
    """
    Read a checkpoint part file back into memory.

    Parameters
    ----------
    path : Path or str
        Part-file path.

    Returns
    -------
    pa.Table
        Deserialized table.
    """
    return pq.read_table(Path(path))


def part_is_valid(path: Path | str, expected_rows: int | None = None) -> bool:
    """
    Check that a checkpoint part file exists and is a readable Parquet file.

    Parameters
    ----------
    path : Path or str
        Part-file path.
    expected_rows : int, optional
        When given, additionally require this exact row count.

    Returns
    -------
    bool
        True when the part is present, readable, and matches the expectation.
    """
    candidate = Path(path)
    if not candidate.is_file():
        return False
    try:
        metadata = pq.read_metadata(candidate)
    except Exception:
        return False
    if expected_rows is not None and metadata.num_rows != expected_rows:
        return False
    return True


def pending_chunk_indices(chunks: Sequence[Sequence[Path]], work_dir: Path | str) -> list[int]:
    """
    Determine which chunks still need scanning given existing checkpoints.

    A chunk is skipped only when both its inventory part (with the exact
    expected row count) and its target_ids part (readable) are valid.

    Parameters
    ----------
    chunks : sequence of sequence of Path
        Planned chunks over the sorted file list.
    work_dir : Path or str
        Audit work directory holding parts/.

    Returns
    -------
    list[int]
        Indices of chunks that must be (re)scanned.
    """
    pending: list[int] = []
    for index, chunk in enumerate(chunks):
        inventory_part = part_filepath(work_dir, "inventory", index)
        target_ids_part = part_filepath(work_dir, "target_ids", index)
        if part_is_valid(inventory_part, expected_rows=len(chunk)) and part_is_valid(target_ids_part):
            continue
        pending.append(index)
    return pending


def corpus_fingerprint(files: Sequence[Path], corpus_root: Path | str) -> str:
    """
    Fingerprint the corpus file set as a hash over sorted relative paths.

    Parameters
    ----------
    files : sequence of Path
        Corpus files (any order; sorted before hashing).
    corpus_root : Path or str
        Root the relative paths are taken against.

    Returns
    -------
    str
        Hex SHA-256 over the newline-joined sorted relative paths.
    """
    root = Path(corpus_root)
    relative = sorted(str(Path(path).relative_to(root)) for path in files)
    return hashlib.sha256("\n".join(relative).encode("utf-8")).hexdigest()


def build_run_config(
    corpus_root: Path | str,
    files: Sequence[Path],
    chunk_size: int,
) -> dict[str, Any]:
    """
    Build the run configuration that fingerprints a parts directory.

    Parameters
    ----------
    corpus_root : Path or str
        Corpus root directory.
    files : sequence of Path
        Discovered corpus files.
    chunk_size : int
        Files per checkpoint chunk.

    Returns
    -------
    dict[str, Any]
        Keys corpus_root, chunk_size, n_files, files_sha256.
    """
    return {
        "corpus_root": str(Path(corpus_root)),
        "chunk_size": int(chunk_size),
        "n_files": len(files),
        "files_sha256": corpus_fingerprint(files, corpus_root),
    }


def parts_manifest_path(work_dir: Path | str) -> Path:
    """
    Resolve the run-config manifest path inside a parts directory.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.

    Returns
    -------
    Path
        Path work_dir/parts/run_config.json.
    """
    return Path(work_dir) / "parts" / RUN_CONFIG_FILENAME


def write_parts_manifest(work_dir: Path | str, config: dict[str, Any]) -> None:
    """
    Persist the run configuration manifest for a parts directory.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.
    config : dict[str, Any]
        Run configuration from build_run_config.
    """
    path = parts_manifest_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_parts_manifest(work_dir: Path | str) -> dict[str, Any] | None:
    """
    Read the run-config manifest, returning None when absent or unreadable.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.

    Returns
    -------
    dict[str, Any] or None
        Recorded run configuration, or None when missing or corrupt.
    """
    path = parts_manifest_path(work_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def manifest_matches(recorded: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    """
    Check a recorded run configuration against the current one.

    Parameters
    ----------
    recorded : dict[str, Any] or None
        Manifest contents from read_parts_manifest; None never matches.
    current : dict[str, Any]
        Run configuration from build_run_config.

    Returns
    -------
    bool
        True only when every recorded field equals the current one.
    """
    return recorded is not None and all(recorded.get(key) == current[key] for key in current)


def corpus_identity(config: dict[str, Any]) -> tuple[Any, ...]:
    """
    Project a run configuration to its corpus identity fields.

    Chunk size is deliberately excluded: changing only the chunk size keeps
    the same corpus identity (same root and file set), while changing the
    root or the file-set fingerprint does not.

    Parameters
    ----------
    config : dict[str, Any]
        Run configuration from build_run_config.

    Returns
    -------
    tuple[Any, ...]
        Values of corpus_root, n_files, files_sha256.
    """
    return tuple(config[key] for key in RUN_CONFIG_IDENTITY_KEYS)


def snapshot_records(files: Sequence[Path]) -> list[dict[str, Any]]:
    """
    Stat corpus files to build integrity snapshot records.

    Parameters
    ----------
    files : sequence of Path
        Files to snapshot, normally the full sorted corpus listing.

    Returns
    -------
    list[dict[str, Any]]
        One record per file with keys path, size_bytes, mtime_ns.
    """
    records: list[dict[str, Any]] = []
    for path in files:
        stat = Path(path).stat()
        records.append(
            {
                "path": str(path),
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        )
    return records


def write_corpus_snapshot(files: Sequence[Path], snapshot_path: Path | str) -> int:
    """
    Write the corpus integrity snapshot table used for later verification.

    Parameters
    ----------
    files : sequence of Path
        Files to snapshot.
    snapshot_path : Path or str
        Destination path for corpus_snapshot.parquet.

    Returns
    -------
    int
        Number of snapshot rows written.
    """
    table = pa.Table.from_pylist(snapshot_records(files), schema=SNAPSHOT_SCHEMA)
    write_part(table, snapshot_path)
    return table.num_rows


def scan_file(path: Path | str, reference_pairs: Sequence[tuple[str, str]] | None) -> dict[str, Any]:
    """
    Scan one corpus file and produce its inventory row and target_id rows.

    The file is opened read-only. Row counts come from Parquet metadata and
    the unique target_id count comes from reading only the target_id column;
    array columns are never read. Any failure while opening or reading is
    captured in open_error, never raised, and numeric scan fields fall back
    to -1 so the row still counts toward the inventory total.

    Parameters
    ----------
    path : Path or str
        Corpus file to scan.
    reference_pairs : sequence of tuple[str, str] or None
        Reference schema pairs for comparison; None skips the comparison.

    Returns
    -------
    dict[str, Any]
        Keys "inventory" (one inventory row dict) and "target_ids" (dict
        with tile_id, row_idx, target_id lists; empty when open failed).
    """
    file_path = Path(path)
    row: dict[str, Any] = {
        "tile_id": "",
        "path": str(file_path),
        "size_bytes": -1,
        "n_rows_actual": -1,
        "n_claimed": -1,
        "claimed_equals_actual": False,
        "n_unique_target_id": -1,
        "columns_dtypes": "",
        "schema_matches_reference": False,
        "schema_deviation": OPEN_FAILURE_DEVIATION,
        "open_error": "",
    }
    target_ids: dict[str, list[Any]] = {"tile_id": [], "row_idx": [], "target_id": []}
    try:
        tile_id = tile_id_from_filename(file_path.name)
        claimed = parse_claimed_count(file_path.name)
        size_bytes = file_path.stat().st_size
        parquet_file = pq.ParquetFile(file_path)
        n_rows = parquet_file.metadata.num_rows
        schema = parquet_file.schema_arrow
        target_id_values = parquet_file.read(columns=["target_id"]).column("target_id").to_pylist()
        n_unique = len(set(target_id_values))
        if reference_pairs is None:
            matches, deviation = True, SCHEMA_MATCH_TOKEN
        else:
            matches, deviation = schema_diff(reference_pairs, schema_pairs(schema))
        row.update(
            {
                "tile_id": tile_id,
                "size_bytes": int(size_bytes),
                "n_rows_actual": int(n_rows),
                "n_claimed": claimed,
                "claimed_equals_actual": claimed == n_rows,
                "n_unique_target_id": int(n_unique),
                "columns_dtypes": columns_dtypes(schema),
                "schema_matches_reference": matches,
                "schema_deviation": deviation,
            }
        )
        target_ids = {
            "tile_id": [tile_id] * n_rows,
            "row_idx": list(range(n_rows)),
            "target_id": target_id_values,
        }
    except Exception as exc:
        row["open_error"] = f"{type(exc).__name__}: {exc}"
    return {"inventory": row, "target_ids": target_ids}


def inventory_table(rows: Sequence[dict[str, Any]]) -> pa.Table:
    """
    Pack inventory row dictionaries into a typed Arrow table.

    Parameters
    ----------
    rows : sequence of dict[str, Any]
        Inventory row dictionaries from scan_file/scan_chunk.

    Returns
    -------
    pa.Table
        Table conforming to INVENTORY_SCHEMA.
    """
    return pa.Table.from_pylist(list(rows), schema=INVENTORY_SCHEMA)


def target_ids_table(payload: dict[str, list[Any]]) -> pa.Table:
    """
    Pack a target_ids payload dictionary into a typed Arrow table.

    Parameters
    ----------
    payload : dict[str, list[Any]]
        Dictionary with tile_id, row_idx, and target_id lists.

    Returns
    -------
    pa.Table
        Table conforming to TARGET_IDS_SCHEMA.
    """
    return pa.table(
        {
            "tile_id": payload["tile_id"],
            "row_idx": payload["row_idx"],
            "target_id": payload["target_id"],
        },
        schema=TARGET_IDS_SCHEMA,
    )


def scan_chunk(
    paths: Sequence[Path | str],
    reference_pairs: Sequence[tuple[str, str]] | None,
) -> dict[str, Any]:
    """
    Scan a consecutive chunk of corpus files, aggregating per-file results.

    Parameters
    ----------
    paths : sequence of Path or str
        Files of the chunk, in sorted order.
    reference_pairs : sequence of tuple[str, str] or None
        Reference schema pairs forwarded to scan_file.

    Returns
    -------
    dict[str, Any]
        Keys "inventory" (list of row dicts in chunk order) and "target_ids"
        (dict with tile_id, row_idx, target_id lists over all opened rows).
    """
    inventory_rows: list[dict[str, Any]] = []
    target_ids: dict[str, list[Any]] = {"tile_id": [], "row_idx": [], "target_id": []}
    for path in paths:
        result = scan_file(path, reference_pairs)
        inventory_rows.append(result["inventory"])
        for key in target_ids:
            target_ids[key].extend(result["target_ids"][key])
    return {"inventory": inventory_rows, "target_ids": target_ids}


def repeat_distribution(target_ids: Sequence[int]) -> dict[int, int]:
    """
    Compute the repeat-count distribution of target_id values.

    Parameters
    ----------
    target_ids : sequence of int
        Archive-wide target_id column (one entry per corpus row).

    Returns
    -------
    dict[int, int]
        Histogram mapping each repeat count k >= 1 to the exact number of
        target_ids appearing exactly k times, ordered by k.

    Examples
    --------
    >>> repeat_distribution([7, 7, 8, 9, 9, 9])
    {1: 1, 2: 1, 3: 1}
    """
    if not target_ids:
        return {}
    counts = pd.Series(target_ids, dtype="int64").value_counts()
    histogram = counts.value_counts().sort_index()
    return {int(k): int(v) for k, v in histogram.items()}


def tile_duplication_split(tile_ids: Sequence[str], target_ids: Sequence[int]) -> dict[str, Any]:
    """
    Split target_id duplication into within-tile and cross-tile components.

    Parameters
    ----------
    tile_ids : sequence of str
        Archive-wide tile_id column, row-aligned with target_ids.
    target_ids : sequence of int
        Archive-wide target_id column, row-aligned with tile_ids.

    Returns
    -------
    dict[str, Any]
        Keys "within_tile" (duplicate_target_id_count, duplicate_tile_id_pairs,
        excess_rows over (tile_id, target_id) pairs with more than one row),
        "cross_tile" (duplicate_target_id_count of ids whose copies span more
        than one tile, tiles_spanned_distribution histogram of distinct tiles
        spanned over ids appearing in >= 2 rows, max_tiles_spanned), and
        "within_tile_only_duplicate_ids" (duplicated ids confined to a single
        tile). All counts are exact ints.

    Examples
    --------
    >>> tile_duplication_split(["A", "A", "B"], [1, 1, 1])["within_tile"]["duplicate_target_id_count"]
    1
    """
    if len(tile_ids) != len(target_ids):
        raise ValueError(f"tile_ids and target_ids must be aligned, got {len(tile_ids)} vs {len(target_ids)}")
    frame = pd.DataFrame(
        {
            "tile_id": pd.Series(list(tile_ids), dtype="object"),
            "target_id": pd.Series(list(target_ids), dtype="int64"),
        }
    )
    per_id_rows = frame.groupby("target_id", observed=True).size()
    per_id_tiles = frame.groupby("target_id", observed=True)["tile_id"].nunique()
    pair_rows = frame.groupby(["tile_id", "target_id"], observed=True).size()
    duplicated = per_id_rows >= 2
    spanned = per_id_tiles[duplicated]
    spanned_histogram = spanned.value_counts().sort_index()
    duplicate_pairs = pair_rows[pair_rows > 1]
    return {
        "within_tile": {
            "duplicate_target_id_count": int(duplicate_pairs.index.get_level_values("target_id").nunique()),
            "duplicate_tile_id_pairs": int(len(duplicate_pairs)),
            "excess_rows": int((duplicate_pairs - 1).sum()),
        },
        "cross_tile": {
            "duplicate_target_id_count": int((spanned >= 2).sum()),
            "tiles_spanned_distribution": {int(k): int(v) for k, v in spanned_histogram.items()},
            "max_tiles_spanned": int(spanned.max()) if len(spanned) else 0,
        },
        "within_tile_only_duplicate_ids": int((spanned < 2).sum()),
    }


def leakage_expectation(distribution: Mapping[int, int], holdout_fraction: float = NAIVE_HOLDOUT_FRACTION) -> float:
    """
    Expected number of target_ids landing in both splits of a naive random row split.

    For an id appearing in k rows, the probability that its copies are not all
    in the train share nor all in the holdout share is
    1 - (1 - holdout_fraction)**k - holdout_fraction**k, which is 0 for k = 1.

    Parameters
    ----------
    distribution : mapping of int to int
        Repeat-count histogram from repeat_distribution (k -> number of ids).
    holdout_fraction : float, optional
        Holdout share of the naive split; default 0.1 for a 90/10 split.

    Returns
    -------
    float
        Sum over duplicated ids of the both-splits probability, computed from
        the distribution as sum over k >= 2 of n_k * p(k).

    Examples
    --------
    >>> leakage_expectation({1: 2, 2: 1, 3: 1})
    0.45
    """
    train_fraction = 1.0 - holdout_fraction
    return float(sum(n * (1.0 - train_fraction**k - holdout_fraction**k) for k, n in distribution.items() if k >= 2))


def gap_accounting(
    unique_target_ids: int,
    population_reference: int = DR1_MAIN_QSO_POPULATION_REFERENCE,
) -> dict[str, Any]:
    """
    Account for the gap between unique target_ids and the DR1 QSO population reference.

    Parameters
    ----------
    unique_target_ids : int
        Archive-wide count of distinct target_id values.
    population_reference : int, optional
        DR1 Main Survey QSO-classified population reference figure.

    Returns
    -------
    dict[str, Any]
        Keys population_reference, unique_target_ids, gap (reference minus
        unique, exact int), and gap_fraction (gap / reference, full-precision
        float).

    Examples
    --------
    >>> gap_accounting(1_000_000)["gap"]
    550000
    """
    if population_reference <= 0:
        raise ValueError(f"population_reference must be positive, got {population_reference}")
    gap = int(population_reference) - int(unique_target_ids)
    return {
        "population_reference": int(population_reference),
        "unique_target_ids": int(unique_target_ids),
        "gap": gap,
        "gap_fraction": gap / int(population_reference),
    }


def converter_filter_block(source_path: Path | str) -> dict[str, Any]:
    """
    Extract the converter's selection filter and arm handling verbatim from source.

    Parameters
    ----------
    source_path : Path or str
        Path of the frozen converter source file
        (02-extract-qso-tile-to-parquet.py in desi-cosmic-void-galaxies).

    Returns
    -------
    dict[str, Any]
        Quote-bearing block: source path, the selection filter line (redrock
        REDSHIFTS SPECTYPE/ZWARN test), wavelength clip constants and mask
        line, and the B/R/Z arm concatenation lines, each with its 1-based
        line number and verbatim stripped code.

    Raises
    ------
    ValueError
        If the source file cannot be read or a quote pattern is not found.
    """
    path = Path(source_path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read converter source {path}: {exc}") from exc

    def locate(pattern: re.Pattern[str], label: str) -> dict[str, Any]:
        for number, line in enumerate(lines, start=1):
            if pattern.search(line):
                return {"line": number, "code": line.strip()}
        raise ValueError(f"{label} pattern not found in converter source {path}")

    selection = locate(CONVERTER_FILTER_PATTERN, "selection filter")
    quotes = {name: locate(pattern, name) for name, pattern in CONVERTER_LINE_PATTERNS.items()}
    wave_min = int(quotes["wavelength_min"]["code"].split("=")[1])
    wave_max = int(quotes["wavelength_max"]["code"].split("=")[1])
    return {
        "source_path": str(path),
        "source_repository": "desi-cosmic-void-galaxies (frozen; read-only reference)",
        "selection_filter": selection,
        "redshift_source": "per-tile redrock REDSHIFTS HDU (one row per target)",
        "zwarn_note": (
            "ZWARN == 0 means the redrock redshift fit passed all quality checks; non-zero ZWARN rows are dropped"
        ),
        "arm_handling": {
            "wavelength_concatenation": quotes["arm_concatenation"],
            "flux_concatenation": quotes["flux_concatenation"],
            "no_deduplication": (
                "B/R/Z arrays are concatenated per target without de-duplication of overlapping wavelengths"
            ),
        },
        "wavelength_clip": {
            "min": quotes["wavelength_min"],
            "max": quotes["wavelength_max"],
            "mask_line": quotes["wavelength_mask"],
            "range_angstrom": [wave_min, wave_max],
        },
    }


def build_identity_summary(
    *,
    total_rows: int,
    unique_target_ids: int,
    distribution: Mapping[int, int],
    tile_split: Mapping[str, Any],
    converter_filter: Mapping[str, Any],
    provenance: Mapping[str, Any],
    population_reference: int = DR1_MAIN_QSO_POPULATION_REFERENCE,
    holdout_fraction: float = NAIVE_HOLDOUT_FRACTION,
    concerns: Sequence[str] = (),
) -> dict[str, Any]:
    """
    Assemble the D2 identity_summary.json payload from computed analysis pieces.

    Parameters
    ----------
    total_rows : int
        Row count of the archive-wide target_ids table.
    unique_target_ids : int
        Distinct target_id count.
    distribution : mapping of int to int
        Repeat-count histogram from repeat_distribution.
    tile_split : mapping of str to Any
        Output of tile_duplication_split.
    converter_filter : mapping of str to Any
        Output of converter_filter_block.
    provenance : mapping of str to Any
        Inputs with row counts, package versions, timestamp, command.
    population_reference : int, optional
        DR1 Main Survey QSO population reference figure.
    holdout_fraction : float, optional
        Holdout share used for the naive-split leakage quantification.
    concerns : sequence of str, optional
        Evidence-backed notes about causes of the population gap other than
        the converter's selection filter.

    Returns
    -------
    dict[str, Any]
        Structured blocks totals, repeat_distribution, tile_split, rates,
        gap_accounting (including converter_filter quotes), holdout, and
        provenance, with every count a JSON int.

    Raises
    ------
    ValueError
        If the totals, distribution, and tile split disagree on any identity
        accounting invariant.
    """
    duplicated_ids = sum(n for k, n in distribution.items() if k >= 2)
    excess_rows = sum((k - 1) * n for k, n in distribution.items())
    rows_from_distribution = sum(k * n for k, n in distribution.items())
    within_only = int(tile_split["within_tile_only_duplicate_ids"])
    cross_tile = int(tile_split["cross_tile"]["duplicate_target_id_count"])
    checks = {
        "rows_match_repeat_sum": rows_from_distribution == total_rows,
        "unique_matches_distribution_total": sum(distribution.values()) == unique_target_ids,
        "excess_matches_row_difference": excess_rows == total_rows - unique_target_ids,
        "tile_split_accounts_for_duplicated_ids": within_only + cross_tile == duplicated_ids,
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise ValueError(f"identity accounting inconsistency: {', '.join(failed)}")
    if unique_target_ids <= 0:
        raise ValueError("unique_target_ids must be positive")
    duplication_rate = duplicated_ids / unique_target_ids
    excess_row_fraction = excess_rows / total_rows if total_rows else 0.0
    gap = gap_accounting(unique_target_ids, population_reference)
    leakage = leakage_expectation(distribution, holdout_fraction)
    required = duplicated_ids > 0
    train_share = round(1.0 - holdout_fraction, 10)
    holdout_share = round(holdout_fraction, 10)
    if duplicated_ids:
        interpretation = (
            "DESI reobserves targets, so rows with k > 1 are expected to be reobservations of the same "
            "object (a target in multiple HEALPix tiles or multiple exposures coadded differently), "
            "not distinct objects."
        )
    else:
        interpretation = (
            "DESI reobserves targets, and rows with k > 1 would be reobservations of the same object; "
            "the measured distribution is all k = 1, consistent with HEALPix-split coadds folding every "
            "reobservation of a target into a single coadded row per pixel."
        )
    return {
        "totals": {
            "total_rows": int(total_rows),
            "unique_target_ids": int(unique_target_ids),
            "duplicate_target_id_count": int(duplicated_ids),
            "total_excess_rows": int(excess_rows),
        },
        "repeat_distribution": {
            "counts_by_k": {str(k): int(distribution[k]) for k in sorted(distribution)},
            "max_k": int(max(distribution, default=0)),
            "duplicated_ids_total": int(duplicated_ids),
            "interpretation": interpretation,
        },
        "tile_split": {
            "within_tile": {
                "duplicate_target_id_count": int(tile_split["within_tile"]["duplicate_target_id_count"]),
                "duplicate_tile_id_pairs": int(tile_split["within_tile"]["duplicate_tile_id_pairs"]),
                "excess_rows": int(tile_split["within_tile"]["excess_rows"]),
            },
            "cross_tile": {
                "duplicate_target_id_count": int(cross_tile),
                "tiles_spanned_distribution": {
                    str(k): int(v) for k, v in sorted(tile_split["cross_tile"]["tiles_spanned_distribution"].items())
                },
                "max_tiles_spanned": int(tile_split["cross_tile"]["max_tiles_spanned"]),
            },
            "within_tile_only_duplicate_ids": int(within_only),
            "tiles_spanned_note": "histogram of distinct tiles spanned, over target_ids appearing in >= 2 rows",
        },
        "rates": {
            "duplication_rate": duplication_rate,
            "excess_row_fraction": excess_row_fraction,
        },
        "gap_accounting": {
            **gap,
            "accounting": (
                "consistent with the converter's SPECTYPE==QSO and ZWARN==0 filter; "
                "the excluded count was not measured"
            ),
            "converter_filter": dict(converter_filter),
            "other_causes_flagged": list(concerns),
        },
        "holdout": {
            "required": required,
            "rationale": (
                f"{duplicated_ids} of {unique_target_ids} unique target_ids ({duplication_rate:.4%}) appear in "
                f"more than one row ({excess_rows} excess rows spanning up to "
                f"{tile_split['cross_tile']['max_tiles_spanned']} tiles), so a train/holdout split must group "
                f"rows by target_id to keep every object on exactly one side."
                if required
                else "No target_id appears in more than one row, so a naive row-level split cannot leak objects."
            ),
            "expected_leakage_naive_90_10": leakage,
            "naive_split_formula": (
                f"sum over duplicated target_ids of (1 - {train_share}^k - {holdout_share}^k), "
                f"computed from the repeat-count distribution as sum over k >= 2 of n_k * p(k)"
            ),
            "holdout_fraction": float(holdout_fraction),
        },
        "consistency_checks": {name: bool(passed) for name, passed in checks.items()},
        "provenance": dict(provenance),
    }


# =============================================================================
# Array Contract Audit (D3)
# =============================================================================


def sampled_tile_selection(n_files: int, target_tiles: int, seed: int) -> tuple[int, int, list[int]]:
    """
    Draw the deterministic systematic tile-selection indices over a sorted file list.

    Stride is floor(n_files / target_tiles); the start offset is a seeded
    uniform integer in [0, stride) drawn from numpy.random.default_rng(seed).

    Parameters
    ----------
    n_files : int
        Number of files in the tile-sorted corpus listing.
    target_tiles : int
        Approximate number of tiles to select.
    seed : int
        Master sampling seed.

    Returns
    -------
    tuple[int, int, list[int]]
        Start offset, stride, and the selected indices start, start+stride, ...

    Raises
    ------
    ValueError
        If n_files or target_tiles is not positive, or target_tiles exceeds
        n_files (stride would be 0).

    Examples
    --------
    >>> start, stride, indices = sampled_tile_selection(10793, 512, 20260815)
    >>> stride
    21
    """
    if n_files < 1:
        raise ValueError(f"n_files must be >= 1, got {n_files}")
    if target_tiles < 1:
        raise ValueError(f"target_tiles must be >= 1, got {target_tiles}")
    stride = n_files // target_tiles
    if stride < 1:
        raise ValueError(f"target_tiles {target_tiles} exceeds n_files {n_files}; stride would be 0")
    start = int(np.random.default_rng(seed).integers(0, stride))
    return start, stride, list(range(start, n_files, stride))


def select_sampled_tiles(
    files: Sequence[Path],
    target_tiles: int = ARRAY_TARGET_TILES,
    seed: int = ARRAY_SAMPLING_SEED,
) -> tuple[list[Path], dict[str, Any]]:
    """
    Select the audit tile sample from the corpus listing, deterministically.

    Files are sorted by (tile_id, path), then systematic stride selection is
    applied per sampled_tile_selection. The returned recipe records every
    parameter needed to reproduce the selection from the summary alone.

    Parameters
    ----------
    files : sequence of Path
        Discovered corpus files (any order).
    target_tiles : int, optional
        Approximate number of tiles to select.
    seed : int, optional
        Master sampling seed.

    Returns
    -------
    tuple[list[Path], dict[str, Any]]
        Selected files in tile-sorted order and the sampling recipe block.
    """
    ordered = sorted(files, key=lambda path: (tile_id_from_filename(path.name), str(path)))
    start, stride, indices = sampled_tile_selection(len(ordered), target_tiles, seed)
    recipe = {
        "method": "systematic stride selection over the tile-sorted corpus listing",
        "tile_sort_key": "tile_id (zero-padded 5-digit string parsed from the filename), then full path",
        "seed": int(seed),
        "n_files": int(len(ordered)),
        "target_tiles": int(target_tiles),
        "stride": int(stride),
        "stride_derivation": f"floor(n_files / target_tiles) = floor({len(ordered)} / {target_tiles}) = {stride}",
        "start": int(start),
        "start_derivation": f"int(numpy.random.default_rng({seed}).integers(0, {stride})) = {start}",
        "selected_indices": "start, start + stride, start + 2*stride, ... < n_files",
        "tiles_selected": int(len(indices)),
        "per_tile_row_selection": ARRAY_ROW_SELECTION_SCHEME,
    }
    return [ordered[index] for index in indices], recipe


def child_seed_for_tile(seed: int, tile_index: int, n_tiles: int) -> np.random.SeedSequence:
    """
    Derive the per-tile child seed for row selection.

    Child i is numpy.random.SeedSequence(seed).spawn(n_tiles)[i]; spawning is
    keyed by position, so child seeds are stable regardless of how many
    times the derivation is repeated.

    Parameters
    ----------
    seed : int
        Master sampling seed.
    tile_index : int
        Zero-based position of the tile in the selected tile list.
    n_tiles : int
        Total number of selected tiles.

    Returns
    -------
    numpy.random.SeedSequence
        Child seed sequence for the tile.
    """
    return np.random.SeedSequence(seed).spawn(n_tiles)[tile_index]


def select_row_indices(n_rows: int, rows_per_tile: int, child_seed: np.random.SeedSequence) -> list[int]:
    """
    Select the systematic per-tile row sample.

    When n_rows <= rows_per_tile every row is taken. Otherwise the stride is
    n_rows // rows_per_tile and the start is a seeded uniform integer in
    [0, n_rows - (rows_per_tile - 1) * stride), giving exactly rows_per_tile
    strictly increasing indices inside the file.

    Parameters
    ----------
    n_rows : int
        Row count of the tile file (from Parquet metadata).
    rows_per_tile : int
        Exact number of rows to select when n_rows allows it.
    child_seed : numpy.random.SeedSequence
        Per-tile child seed from child_seed_for_tile.

    Returns
    -------
    list[int]
        Selected row indices, strictly increasing.

    Raises
    ------
    ValueError
        If n_rows is negative or rows_per_tile is not positive.

    Examples
    --------
    >>> idx = select_row_indices(100, 8, child_seed_for_tile(20260815, 0, 1))
    >>> len(idx), idx[1] - idx[0]
    (8, 12)
    """
    if n_rows < 0:
        raise ValueError(f"n_rows must be >= 0, got {n_rows}")
    if rows_per_tile < 1:
        raise ValueError(f"rows_per_tile must be >= 1, got {rows_per_tile}")
    if n_rows <= rows_per_tile:
        return list(range(n_rows))
    stride = n_rows // rows_per_tile
    max_start = n_rows - (rows_per_tile - 1) * stride
    start = int(np.random.default_rng(child_seed).integers(0, max_start))
    return [start + offset * stride for offset in range(rows_per_tile)]


def audit_arrays(
    wave: np.ndarray,
    flux: np.ndarray,
    ivar: np.ndarray,
    mask: np.ndarray,
    neardup_tolerance: float = ARRAY_NEARDUP_TOLERANCE_ANGSTROM,
) -> dict[str, Any]:
    """
    Run the six D3 checks over one row's wavelength/flux/ivar/mask arrays.

    Checks: length alignment; wavelength monotonicity (strict decreases
    only; equal values count as duplicates, not breaks); arm overlap extent
    measured at the first two mono breaks as wave[break] - wave[break + 1]
    (local max before the joint minus local min after it); duplicate
    wavelength values by exact equality over the value set (the converter
    concatenated arms without de-duplication, so overlap values repeat
    non-adjacently) and near-duplicates as adjacent sorted-unique gaps
    within the tolerance; ivar negative/non-finite/exactly-zero counts; and
    per-row wavelength extrema. Also emits auxiliary break-boundary
    wavelengths used by the summary (dropped from the final per-row table).

    Parameters
    ----------
    wave, flux, ivar, mask : numpy.ndarray or sequence
        The row's wavelength, flux, ivar, and mask arrays.
    neardup_tolerance : float, optional
        Near-duplicate tolerance in Angstroms applied to gaps between
        sorted unique wavelength values.

    Returns
    -------
    dict[str, Any]
        Per-row flag fields of ARRAY_AUDIT_SCHEMA plus the auxiliary
        br/rz boundary wavelengths and second break index.
    """
    wave_values = np.asarray(wave, dtype=np.float64)
    ivar_values = np.asarray(ivar, dtype=np.float64)
    mask_values = np.asarray(mask, dtype=np.int64)
    n_pixels = int(wave_values.size)
    align_ok = len(flux) == len(ivar) == len(mask) == n_pixels
    diffs = np.diff(wave_values)
    breaks = np.flatnonzero(diffs < 0)
    n_breaks = int(breaks.size)
    overlap_br = float(wave_values[breaks[0]] - wave_values[breaks[0] + 1]) if n_breaks >= 1 else float("nan")
    overlap_rz = float(wave_values[breaks[1]] - wave_values[breaks[1] + 1]) if n_breaks >= 2 else float("nan")
    if n_breaks >= 1:
        br_wave_before = float(wave_values[breaks[0]])
        br_wave_after = float(wave_values[breaks[0] + 1])
    else:
        br_wave_before = br_wave_after = float("nan")
    if n_breaks >= 2:
        rz_wave_before = float(wave_values[breaks[1]])
        rz_wave_after = float(wave_values[breaks[1] + 1])
        second_break_idx = int(breaks[1])
    else:
        rz_wave_before = rz_wave_after = float("nan")
        second_break_idx = -1
    unique_wave = np.unique(wave_values)
    n_dup_wave = n_pixels - int(unique_wave.size)
    unique_gaps = np.diff(unique_wave)
    n_neardup_wave = int(np.count_nonzero(unique_gaps <= neardup_tolerance)) if unique_gaps.size else 0
    ivar_neg_count = int(np.count_nonzero(ivar_values < 0))
    ivar_nonfinite_count = int(np.count_nonzero(~np.isfinite(ivar_values)))
    ivar_zero_count = int(np.count_nonzero(ivar_values == 0))
    if n_pixels:
        mask_unique, mask_counts = np.unique(mask_values, return_counts=True)
        mask_inventory = json.dumps({str(int(value)): int(count) for value, count in zip(mask_unique, mask_counts)})
    else:
        mask_inventory = "{}"
    return {
        "n_pixels": n_pixels,
        "align_ok": bool(align_ok),
        "n_mono_breaks": n_breaks,
        "first_break_idx": int(breaks[0]) if n_breaks else -1,
        "overlap_br_angstrom": overlap_br,
        "overlap_rz_angstrom": overlap_rz,
        "n_dup_wave": int(n_dup_wave),
        "n_neardup_wave": n_neardup_wave,
        "ivar_neg_count": ivar_neg_count,
        "ivar_nonfinite_count": ivar_nonfinite_count,
        "ivar_zero_count": ivar_zero_count,
        "ivar_zero_frac": ivar_zero_count / n_pixels if n_pixels else 0.0,
        "wave_min": float(wave_values.min()) if n_pixels else float("nan"),
        "wave_max": float(wave_values.max()) if n_pixels else float("nan"),
        "mask_value_counts": mask_inventory,
        "br_wave_before": br_wave_before,
        "br_wave_after": br_wave_after,
        "rz_wave_before": rz_wave_before,
        "rz_wave_after": rz_wave_after,
        "second_break_idx": second_break_idx,
    }


def scan_file_arrays(
    path: Path | str,
    tile_id: str,
    *,
    seed: int,
    tile_index: int,
    n_tiles: int,
    rows_per_tile: int = ARRAY_ROWS_PER_TILE,
    neardup_tolerance: float = ARRAY_NEARDUP_TOLERANCE_ANGSTROM,
) -> dict[str, Any]:
    """
    Scan one tile file's sampled rows and produce its per-row audit records.

    The file is opened read-only; the full wavelength/flux/ivar/mask_array
    columns are read once and the systematically selected row indices are
    sliced in memory (recorded in the summary as the row read method).
    Per-row numpy views are taken from the flat value buffers via the list
    offsets, so each row is materialized exactly once.

    Parameters
    ----------
    path : Path or str
        Corpus file to scan (read-only).
    tile_id : str
        Zero-padded tile id of the file.
    seed : int
        Master sampling seed.
    tile_index : int
        Zero-based position of the tile in the selected tile list.
    n_tiles : int
        Total number of selected tiles.
    rows_per_tile : int, optional
        Exact number of rows to sample per tile when the file allows it.
    neardup_tolerance : float, optional
        Near-duplicate tolerance in Angstroms.

    Returns
    -------
    dict[str, Any]
        Keys "rows" (list of per-row record dicts) and "n_rows" (row count
        of the file).
    """
    parquet_file = pq.ParquetFile(path)
    n_rows = int(parquet_file.metadata.num_rows)
    row_indices = select_row_indices(n_rows, rows_per_tile, child_seed_for_tile(seed, tile_index, n_tiles))
    table = parquet_file.read(columns=list(ARRAY_AUDIT_COLUMNS)).combine_chunks()
    flat: dict[str, np.ndarray] = {}
    offsets: dict[str, np.ndarray] = {}
    for name in ARRAY_AUDIT_COLUMNS:
        column = table.column(name)
        if column.null_count:
            raise ValueError(f"null values in column {name} of {path}")
        list_array = column.chunk(0)
        if not isinstance(list_array, pa.ListArray):
            raise TypeError(f"column {name} of {path} is {type(list_array).__name__}, expected ListArray")
        flat[name] = list_array.values.to_numpy(zero_copy_only=False)
        offsets[name] = np.asarray(list_array.offsets)
    rows: list[dict[str, Any]] = []
    for row_idx in row_indices:
        slices = {name: flat[name][int(offsets[name][row_idx]) : int(offsets[name][row_idx + 1])] for name in flat}
        record = audit_arrays(
            slices["wavelength"],
            slices["flux"],
            slices["ivar"],
            slices["mask_array"],
            neardup_tolerance,
        )
        rows.append({"row_uid": row_uid(tile_id, row_idx), "tile_id": tile_id, "row_idx": int(row_idx), **record})
    return {"rows": rows, "n_rows": n_rows}


def audit_chunk(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """
    Audit a chunk of tile-scan tasks, aggregating per-file results.

    Failures opening or reading a file are captured per file and never
    raised, mirroring the D1 scanner contract.

    Parameters
    ----------
    tasks : sequence of mapping of str to Any
        One task per file with keys path, tile_id, seed, tile_index, n_tiles,
        rows_per_tile, neardup_tolerance.

    Returns
    -------
    dict[str, Any]
        Keys "rows" (per-row records in task order) and "errors" (list of
        path/error dicts for files that failed to open or read).
    """
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for task in tasks:
        try:
            result = scan_file_arrays(
                task["path"],
                task["tile_id"],
                seed=int(task["seed"]),
                tile_index=int(task["tile_index"]),
                n_tiles=int(task["n_tiles"]),
                rows_per_tile=int(task["rows_per_tile"]),
                neardup_tolerance=float(task["neardup_tolerance"]),
            )
            rows.extend(result["rows"])
        except Exception as exc:
            errors.append({"path": str(task["path"]), "error": f"{type(exc).__name__}: {exc}"})
    return {"rows": rows, "errors": errors}


def array_audit_part_table(rows: Sequence[dict[str, Any]]) -> pa.Table:
    """
    Pack per-row audit records into a typed part-schema Arrow table.

    Parameters
    ----------
    rows : sequence of dict[str, Any]
        Per-row records from audit_chunk/scan_file_arrays.

    Returns
    -------
    pa.Table
        Table conforming to ARRAY_AUDIT_PART_SCHEMA (brief schema plus the
        auxiliary break-boundary columns used by the summary).
    """
    return pa.Table.from_pylist(list(rows), schema=ARRAY_AUDIT_PART_SCHEMA)


def project_array_audit_table(table: pa.Table) -> pa.Table:
    """
    Project a part-schema table down to the exact D3 per-row output schema.

    Parameters
    ----------
    table : pa.Table
        Table conforming to ARRAY_AUDIT_PART_SCHEMA.

    Returns
    -------
    pa.Table
        Table conforming to ARRAY_AUDIT_SCHEMA (auxiliary columns dropped).
    """
    return table.select([field.name for field in ARRAY_AUDIT_SCHEMA])


def distribution_stats(values: Sequence[float]) -> dict[str, Any]:
    """
    Summarize a numeric sample with count, extremes, quartiles, and mean.

    Parameters
    ----------
    values : sequence of float
        Finite measured values.

    Returns
    -------
    dict[str, Any]
        Keys count, min, p25, median, mean, p75, max; count is a JSON int
        and the rest are floats.
    """
    array = np.asarray(list(values), dtype=np.float64)
    if array.size == 0:
        return {"count": 0}
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "p25": float(np.percentile(array, 25)),
        "median": float(np.median(array)),
        "mean": float(np.mean(array)),
        "p75": float(np.percentile(array, 75)),
        "max": float(np.max(array)),
    }


def recommend_merge_policy(
    *,
    rows_sampled: int,
    rows_with_breaks: int,
    rows_with_dups: int,
    rows_with_neardups: int,
    mean_n_pixels: float,
    mean_dup_per_row: float,
    mean_neardup_per_row: float,
    median_overlap_br: float,
    median_overlap_rz: float,
    misaligned_rows: int,
    neardup_tolerance: float,
) -> dict[str, Any]:
    """
    Derive the loader merge/de-duplication policy from measured audit stats.

    Parameters
    ----------
    rows_sampled : int
        Rows audited.
    rows_with_breaks : int
        Rows with at least one wavelength monotonicity break.
    rows_with_dups : int
        Rows with at least one exactly duplicated wavelength value.
    rows_with_neardups : int
        Rows with at least one near-duplicate wavelength pair.
    mean_n_pixels : float
        Mean array length per row.
    mean_dup_per_row : float
        Mean exactly-duplicated wavelength values per row.
    mean_neardup_per_row : float
        Mean near-duplicate wavelength pairs per row.
    median_overlap_br : float
        Median measured B/R overlap width in Angstroms.
    median_overlap_rz : float
        Median measured R/Z overlap width in Angstroms.
    misaligned_rows : int
        Rows failing length alignment.
    neardup_tolerance : float
        Near-duplicate tolerance in Angstroms used by the audit.

    Returns
    -------
    dict[str, Any]
        Keys required, policy, method (list of loader directives derived
        from the measurements), expected_pixel_reduction_per_row, and
        rationale quoting the measured numbers.
    """
    redundancy_present = rows_with_dups > 0 or rows_with_neardups > 0
    if rows_with_dups > 0 and rows_with_neardups > 0:
        policy = "tolerance_coadd_dedup"
    elif rows_with_dups > 0:
        policy = "exact_wavelength_dedup"
    elif rows_with_neardups > 0:
        policy = "tolerance_wavelength_dedup"
    else:
        policy = "none_pass_through"
    required = redundancy_present or rows_with_breaks > 0 or misaligned_rows > 0
    method: list[str] = []
    if rows_with_breaks > 0:
        method.append(
            f"never assume global wavelength sortedness: {rows_with_breaks} of {rows_sampled} sampled rows restart "
            f"descending at arm joints (median overlap {median_overlap_br:.1f} A at B/R, {median_overlap_rz:.1f} A "
            "at R/Z), so the loader must stably sort each row by wavelength before any merge, de-duplication, or "
            "resampling step"
        )
    if rows_with_dups > 0:
        method.append(
            f"after sorting, merge groups of exactly equal wavelengths ({mean_dup_per_row:.1f} values per row "
            f"measured, sitting inside the arm overlaps where both arms sample the same grid points); combine the "
            "two arm measurements per wavelength via inverse-variance (ivar) weighted coadd, or keep the first "
            "occurrence when coadding is out of scope"
        )
    if rows_with_neardups > 0:
        method.append(
            f"after sorting, also merge wavelengths within {neardup_tolerance} A of each other "
            f"({mean_neardup_per_row:.1f} pairs per row measured) with the same combination rule"
        )
    if misaligned_rows > 0:
        method.append(
            f"validate length alignment of wavelength/flux/ivar/mask before loading: {misaligned_rows} of "
            f"{rows_sampled} sampled rows are misaligned and must be rejected or flagged, not silently loaded"
        )
    if not method:
        method.append("pass rows through unmerged; no redundancy, unsortedness, or misalignment was measured")
    reduction = {
        "mean_pixels_before": float(mean_n_pixels),
        "mean_redundant_values_per_row": float(mean_dup_per_row + mean_neardup_per_row),
        "mean_pixels_after_dedup": float(mean_n_pixels - mean_dup_per_row - mean_neardup_per_row),
    }
    rationale = (
        f"Sampled {rows_sampled} rows: {rows_with_breaks} ({rows_with_breaks / rows_sampled:.2%}) restart descending "
        f"at arm joints, {rows_with_dups} ({rows_with_dups / rows_sampled:.2%}) carry exactly duplicated wavelengths "
        f"({mean_dup_per_row:.1f} per row), and {rows_with_neardups} ({rows_with_neardups / rows_sampled:.2%}) carry "
        f"near-duplicates within {neardup_tolerance} A ({mean_neardup_per_row:.1f} per row). The redundancy sits "
        f"inside the measured arm overlaps (B/R median {median_overlap_br:.1f} A, "
        f"R/Z median {median_overlap_rz:.1f} A) because the converter concatenated the B/R/Z arms without "
        "de-duplication."
    )
    return {
        "required": bool(required),
        "policy": policy,
        "method": method,
        "expected_pixel_reduction_per_row": reduction,
        "rationale": rationale,
    }


def kind_manifest_path(work_dir: Path | str, kind: str) -> Path:
    """
    Resolve the run-config manifest path inside one part family directory.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.
    kind : str
        Part family name, e.g. "array_audit".

    Returns
    -------
    Path
        Path work_dir/parts/<kind>/run_config.json.
    """
    return part_dir(work_dir, kind) / RUN_CONFIG_FILENAME


def write_kind_manifest(work_dir: Path | str, kind: str, config: dict[str, Any]) -> None:
    """
    Persist the run-config manifest inside a part family directory.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.
    kind : str
        Part family name.
    config : dict[str, Any]
        Run configuration to fingerprint the family.
    """
    path = kind_manifest_path(work_dir, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_kind_manifest(work_dir: Path | str, kind: str) -> dict[str, Any] | None:
    """
    Read a part family's run-config manifest, None when absent or corrupt.

    Parameters
    ----------
    work_dir : Path or str
        Audit work directory.
    kind : str
        Part family name.

    Returns
    -------
    dict[str, Any] or None
        Recorded run configuration, or None when missing or unreadable.
    """
    path = kind_manifest_path(work_dir, kind)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_array_audit_run_config(
    corpus_root: Path | str,
    selected_files: Sequence[Path],
    *,
    seed: int,
    target_tiles: int,
    rows_per_tile: int,
    neardup_tolerance: float,
    chunk_size: int,
) -> dict[str, Any]:
    """
    Build the run configuration fingerprinting the array-audit parts family.

    Parameters
    ----------
    corpus_root : Path or str
        Corpus root directory.
    selected_files : sequence of Path
        Selected tile files (tile-sorted order).
    seed : int
        Master sampling seed.
    target_tiles : int
        Target tile count determining the stride with the file count.
    rows_per_tile : int
        Exact rows sampled per tile.
    neardup_tolerance : float
        Near-duplicate tolerance in Angstroms.
    chunk_size : int
        Files per checkpoint chunk.

    Returns
    -------
    dict[str, Any]
        Keys corpus_root, sampling_seed, target_tiles, stride, rows_per_tile,
        neardup_tol_angstrom, chunk_size, n_files_selected, files_sha256.
    """
    return {
        "corpus_root": str(Path(corpus_root)),
        "sampling_seed": int(seed),
        "target_tiles": int(target_tiles),
        "stride": int(len(selected_files) // max(1, target_tiles)) if selected_files else 0,
        "rows_per_tile": int(rows_per_tile),
        "neardup_tol_angstrom": float(neardup_tolerance),
        "chunk_size": int(chunk_size),
        "n_files_selected": int(len(selected_files)),
        "files_sha256": corpus_fingerprint(selected_files, corpus_root),
    }


def _mask_inventory(rows: pa.Table, top_n: int) -> dict[str, Any]:
    """
    Aggregate the archive-wide mask value inventory from per-row JSON blobs.

    Parameters
    ----------
    rows : pa.Table
        Part-schema table (mask_value_counts column).
    top_n : int
        Cap on the number of values reported by frequency.

    Returns
    -------
    dict[str, Any]
        Keys total_distinct, top_values (value/count/pixel_share list capped
        at top_n), and n_rows_all_zero.
    """
    totals: dict[int, int] = {}
    n_rows_all_zero = 0
    for blob in rows.column("mask_value_counts").to_pylist():
        counts = json.loads(blob) if blob else {}
        if set(counts) <= {"0"}:
            n_rows_all_zero += 1
        for value, count in counts.items():
            totals[int(value)] = totals.get(int(value), 0) + int(count)
    total_pixels = sum(totals.values())
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    top_values = [
        {
            "value": int(value),
            "count": int(count),
            "pixel_share": count / total_pixels if total_pixels else 0.0,
        }
        for value, count in ordered[:top_n]
    ]
    return {
        "total_distinct": int(len(totals)),
        "total_pixels_sampled": int(total_pixels),
        "top_values": top_values,
        "capped_at_top_n": int(top_n),
        "n_rows_all_zero_mask": int(n_rows_all_zero),
    }


def build_array_audit_summary(
    rows: pa.Table,
    *,
    sampling: Mapping[str, Any],
    fence_action: str,
    provenance: Mapping[str, Any],
    neardup_tolerance: float = ARRAY_NEARDUP_TOLERANCE_ANGSTROM,
    nominal_overlaps: Mapping[str, Sequence[float]] = NOMINAL_ARM_OVERLAPS,
    mask_top_n: int = ARRAY_MASK_TOP_N,
    example_limit: int = ARRAY_BREAK_EXAMPLE_LIMIT,
) -> dict[str, Any]:
    """
    Assemble the D3 array_audit_summary.json payload from the audit table.

    Parameters
    ----------
    rows : pa.Table
        Concatenated part-schema table (brief columns plus the auxiliary
        break-boundary columns) in tile/row order.
    sampling : mapping of str to Any
        Sampling recipe block from select_sampled_tiles plus achieved row
        counts and per-file short-file accounting.
    fence_action : str
        Run-config fence action taken for this run.
    provenance : mapping of str to Any
        Inputs, versions, timestamp, command, timing.
    neardup_tolerance : float, optional
        Near-duplicate tolerance in Angstroms.
    nominal_overlaps : mapping of str to sequence of float, optional
        Nominal arm overlap bands from the converter comments.
    mask_top_n : int, optional
        Cap on the mask inventory value list.
    example_limit : int, optional
        Cap on example break row_uids.

    Returns
    -------
    dict[str, Any]
        Blocks sampling, alignment, monotonicity, arm_overlap (with the
        derived merge policy), ivar, mask_inventory, wavelength_range,
        array_length, and provenance; every count a JSON int.
    """
    n_rows = int(rows.num_rows)

    def column(name: str) -> list[Any]:
        return rows.column(name).to_pylist()

    n_pixels = column("n_pixels")
    align_ok = column("align_ok")
    tile_ids = column("tile_id")
    n_breaks = column("n_mono_breaks")
    first_break_idx = column("first_break_idx")
    second_break_idx = column("second_break_idx")
    n_dup_wave = column("n_dup_wave")
    n_neardup_wave = column("n_neardup_wave")
    overlap_br = [value for value in column("overlap_br_angstrom") if value == value]
    overlap_rz = [value for value in column("overlap_rz_angstrom") if value == value]
    br_before = [value for value in column("br_wave_before") if value == value]
    br_after = [value for value in column("br_wave_after") if value == value]
    rz_before = [value for value in column("rz_wave_before") if value == value]
    rz_after = [value for value in column("rz_wave_after") if value == value]

    misaligned = [index for index, ok in enumerate(align_ok) if not ok]
    rows_with_breaks = sum(1 for value in n_breaks if value > 0)
    total_breaks = sum(n_breaks)
    rows_exactly = {
        "0_breaks": sum(1 for value in n_breaks if value == 0),
        "1_break": sum(1 for value in n_breaks if value == 1),
        "2_breaks": sum(1 for value in n_breaks if value == 2),
        "gt2_breaks": sum(1 for value in n_breaks if value > 2),
    }
    position_histogram: dict[int, int] = {}
    for first, second, count in zip(first_break_idx, second_break_idx, n_breaks):
        if count >= 1:
            position_histogram[first] = position_histogram.get(first, 0) + 1
        if count >= 2:
            position_histogram[second] = position_histogram.get(second, 0) + 1
    row_uids = column("row_uid")
    example_uids = [row_uids[index] for index, value in enumerate(n_breaks) if value > 0][:example_limit]

    rows_with_dups = sum(1 for value in n_dup_wave if value > 0)
    total_dups = sum(n_dup_wave)
    rows_with_neardups = sum(1 for value in n_neardup_wave if value > 0)
    total_neardups = sum(n_neardup_wave)
    total_pixels = sum(n_pixels)
    ivar_neg_rows = sum(1 for value in column("ivar_neg_count") if value > 0)
    ivar_nonfinite_rows = sum(1 for value in column("ivar_nonfinite_count") if value > 0)
    total_ivar_zero = sum(column("ivar_zero_count"))
    wave_min_values = column("wave_min")
    wave_max_values = column("wave_max")

    nominal_br = list(nominal_overlaps["B_R"])
    nominal_rz = list(nominal_overlaps["R_Z"])
    overlap_br_stats = distribution_stats(overlap_br)
    overlap_rz_stats = distribution_stats(overlap_rz)
    merge_policy = recommend_merge_policy(
        rows_sampled=n_rows,
        rows_with_breaks=rows_with_breaks,
        rows_with_dups=rows_with_dups,
        rows_with_neardups=rows_with_neardups,
        mean_n_pixels=(total_pixels / n_rows if n_rows else 0.0),
        mean_dup_per_row=(total_dups / n_rows if n_rows else 0.0),
        mean_neardup_per_row=(total_neardups / n_rows if n_rows else 0.0),
        median_overlap_br=(float(np.median(overlap_br)) if overlap_br else float("nan")),
        median_overlap_rz=(float(np.median(overlap_rz)) if overlap_rz else float("nan")),
        misaligned_rows=len(misaligned),
        neardup_tolerance=neardup_tolerance,
    )
    recipe = dict(sampling)
    return {
        "sampling": {
            **recipe,
            "rows_selected": n_rows,
            "row_read_method": (
                "read full wavelength/flux/ivar/mask_array columns per selected file, then slice the "
                "systematically selected row indices in memory"
            ),
            "reproducibility_recipe": (
                f"{recipe['method']}: sort discovered files by {recipe['tile_sort_key']}; "
                f"{recipe['stride_derivation']}; {recipe['start_derivation']}; take indices "
                f"start, start+stride, ... < n_files; then per tile {ARRAY_ROW_SELECTION_SCHEME}"
            ),
        },
        "alignment": {
            "rows_checked": n_rows,
            "misaligned_rows": int(len(misaligned)),
            "misaligned_row_rate": len(misaligned) / n_rows if n_rows else 0.0,
            "files_with_misalignment": sorted({tile_ids[index] for index in misaligned}),
            "verdict": (
                "hard defect: misaligned rows present; downstream loader must validate lengths"
                if misaligned
                else "all sampled rows aligned across wavelength/flux/ivar/mask_array"
            ),
        },
        "monotonicity": {
            "rows_checked": n_rows,
            "rows_with_breaks": int(rows_with_breaks),
            "rows_with_breaks_rate": rows_with_breaks / n_rows if n_rows else 0.0,
            "total_breaks": int(total_breaks),
            "rows_by_break_count": rows_exactly,
            "break_position_histogram": {
                str(position): int(count) for position, count in sorted(position_histogram.items())
            },
            "example_break_row_uids": example_uids,
            "note": (
                "break = wavelength[i+1] < wavelength[i] only; equal adjacent values are counted as "
                "duplicates, not breaks"
            ),
        },
        "arm_overlap": {
            "br_boundary": {
                "rows_measured": int(len(overlap_br)),
                "overlap_width_angstrom": overlap_br_stats,
                "wave_before_break": distribution_stats(br_before),
                "wave_after_break": distribution_stats(br_after),
                "nominal_band_angstrom": [float(nominal_br[0]), float(nominal_br[1])],
                "nominal_width_angstrom": float(nominal_br[1] - nominal_br[0]),
                "measured_vs_nominal": (
                    f"measured median overlap {float(np.median(overlap_br)) if overlap_br else float('nan'):.1f} A "
                    f"vs nominal {nominal_br[1] - nominal_br[0]:.1f} A; measured joint: R restarts at "
                    f"{float(np.median(br_after)) if br_after else float('nan'):.1f} A after B peak "
                    f"{float(np.median(br_before)) if br_before else float('nan'):.1f} A, matching the nominal "
                    f"band {nominal_br[0]:.0f}-{nominal_br[1]:.0f} A from the converter comments"
                ),
            },
            "rz_boundary": {
                "rows_measured": int(len(overlap_rz)),
                "overlap_width_angstrom": overlap_rz_stats,
                "wave_before_break": distribution_stats(rz_before),
                "wave_after_break": distribution_stats(rz_after),
                "nominal_band_angstrom": [float(nominal_rz[0]), float(nominal_rz[1])],
                "nominal_width_angstrom": float(nominal_rz[1] - nominal_rz[0]),
                "measured_vs_nominal": (
                    f"measured median overlap {float(np.median(overlap_rz)) if overlap_rz else float('nan'):.1f} A "
                    f"vs nominal {nominal_rz[1] - nominal_rz[0]:.1f} A; measured joint: Z restarts at "
                    f"{float(np.median(rz_after)) if rz_after else float('nan'):.1f} A after R peak "
                    f"{float(np.median(rz_before)) if rz_before else float('nan'):.1f} A, matching the nominal "
                    f"band {nominal_rz[0]:.0f}-{nominal_rz[1]:.0f} A from the converter comments"
                ),
            },
            "rows_with_more_than_two_breaks": int(rows_exactly["gt2_breaks"]),
            "duplicates": {
                "definition": "wavelength values appearing more than once per row (exact equality over the value set)",
                "rows_with_duplicates": int(rows_with_dups),
                "rows_with_duplicates_rate": rows_with_dups / n_rows if n_rows else 0.0,
                "total_duplicate_values": int(total_dups),
                "mean_per_row": total_dups / n_rows if n_rows else 0.0,
                "max_per_row": int(max(n_dup_wave, default=0)),
            },
            "near_duplicates": {
                "definition": (
                    f"adjacent sorted-unique wavelength gaps <= {neardup_tolerance} A; tolerance is ~8x finer "
                    "than the measured ~0.8 A native grid spacing, so it isolates cross-arm near-coincidences"
                ),
                "tolerance_angstrom": float(neardup_tolerance),
                "rows_with_near_duplicates": int(rows_with_neardups),
                "rows_with_near_duplicates_rate": rows_with_neardups / n_rows if n_rows else 0.0,
                "total_near_duplicate_pairs": int(total_neardups),
                "mean_per_row": total_neardups / n_rows if n_rows else 0.0,
                "max_per_row": int(max(n_neardup_wave, default=0)),
            },
            "merge_policy": merge_policy,
        },
        "ivar": {
            "rows_checked": n_rows,
            "rows_with_any_negative": int(ivar_neg_rows),
            "rows_with_any_negative_rate": ivar_neg_rows / n_rows if n_rows else 0.0,
            "rows_with_any_nonfinite": int(ivar_nonfinite_rows),
            "rows_with_any_nonfinite_rate": ivar_nonfinite_rows / n_rows if n_rows else 0.0,
            "total_zero_pixels": int(total_ivar_zero),
            "total_pixels": int(total_pixels),
            "overall_zero_fraction": total_ivar_zero / total_pixels if total_pixels else 0.0,
            "per_row_zero_fraction": distribution_stats(column("ivar_zero_frac")),
        },
        "mask_inventory": _mask_inventory(rows, mask_top_n),
        "wavelength_range": {
            "global_min_angstrom": float(min(wave_min_values, default=float("nan"))),
            "global_max_angstrom": float(max(wave_max_values, default=float("nan"))),
            "per_row_min_distribution": distribution_stats(wave_min_values),
            "per_row_max_distribution": distribution_stats(wave_max_values),
        },
        "array_length": {
            "rows": n_rows,
            **distribution_stats(n_pixels),
            "distinct_lengths": {
                str(length): count for length, count in sorted(pd.Series(n_pixels).value_counts().items())
            },
        },
        "run_fence": {"action": str(fence_action)},
        "provenance": dict(provenance),
    }


# =============================================================================
# Scalar Extraction and Nuisance Baseline (D4+D5)
# =============================================================================


SCALARS_PART_KIND = "scalars"
PER_OBJECT_FILENAME = "per_object_raw.parquet"
REDSHIFT_SUMMARY_FILENAME = "redshift_summary.json"
SCALAR_COLUMNS = ("target_id", "ra", "dec", "z", "flux", "ivar", "mask_array")
NUISANCE_DEFINITIONS_VERSION = "snr-mask-v1"

OBSERVED_WAVE_MIN_ANGSTROM = 3600.0
OBSERVED_WAVE_MAX_ANGSTROM = 9799.2

Z_FINE_BIN_WIDTH = 0.25
Z_BROAD_BINS: tuple[tuple[float, float], ...] = ((0.5, 2.0), (1.0, 2.5), (1.5, 3.0), (2.0, 3.5))
Z_HISTOGRAM_BIN_WIDTH = 0.1
REST_GRID_RESOLUTION_ANGSTROM = 1.0
REST_LOG_RESOLUTION_DEX = 1e-4

QUANTILE_LEVELS = (0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0)
QUANTILE_METHOD = "numpy default (linear interpolation between order statistics)"

SNR_PROXY_FORMULA = (
    "snr_proxy = median(flux * sqrt(clip(ivar, 0, None)) over pixels where mask_array == 0); "
    "null (Arrow null, not NaN) when the row has zero unmasked pixels"
)
MASKED_FRACTION_FORMULA = "masked_fraction = mean(mask_array != 0)"
ARRAY_LENGTH_FORMULA = "array_length = len(flux)"

RR_CHI2_BLOCK = {
    "status": "unresolvable",
    "reason": "column absent from archive; documented schema predates corrections; see D1 schema findings",
}

BIN_INTERVAL_FORMULA = (
    "common rest-frame interval = [wave_min/(1+z_min), wave_max/(1+z_max)] Angstrom: an object at z maps the "
    "observed window [wave_min, wave_max] to rest [wave_min/(1+z), wave_max/(1+z)], and the intersection over "
    "z in [z_min, z_max] is [wave_min/(1+z_min), wave_max/(1+z_max)] (lo bound set by z_min, hi bound by z_max)"
)
BIN_LINEAR_FORMULA = "linear grid length = interval width / resolution (Angstrom)"
BIN_LOG_FORMULA = "log grid length = (log10(hi) - log10(lo)) / dex, dex = 1e-4"
BIN_COUNTING_RULE = (
    "fine bins half-open [lo, hi) with the final fine bin closed [lo, hi]; broad and full-range bins closed "
    "[lo, hi]; counts use finite z only"
)
BIN_WORKABILITY_NOTE = (
    "workability flag is mechanical: common rest-frame interval width > 0 is positive_width and hi <= lo flags "
    "disjoint rest-frame windows (per-object windows no longer overlap across the bin, so no single common "
    "rest-frame grid exists); identifying a workable combination of population size and rest-frame coverage "
    "from the table is an operator decision, not made here"
)

PER_OBJECT_SCHEMA = pa.schema(
    [
        ("row_uid", pa.string()),
        ("tile_id", pa.string()),
        ("target_id", pa.int64()),
        ("ra", pa.float64()),
        ("dec", pa.float64()),
        ("z", pa.float64()),
        ("array_length", pa.int64()),
        ("snr_proxy", pa.float64()),
        ("masked_fraction", pa.float64()),
    ]
)

PER_OBJECT_PART_SCHEMA = pa.schema(
    [
        ("row_uid", pa.string()),
        ("tile_id", pa.string()),
        ("row_idx", pa.int64()),
        ("target_id", pa.int64()),
        ("ra", pa.float64()),
        ("dec", pa.float64()),
        ("z", pa.float64()),
        ("array_length", pa.int64()),
        ("snr_proxy", pa.float64()),
        ("masked_fraction", pa.float64()),
    ]
)


def row_snr_proxy(flux: Sequence[float], ivar: Sequence[float], mask: Sequence[int]) -> float | None:
    """
    Compute the plan-fixed D5 signal-to-noise proxy for one row.

    snr_proxy = median(flux * sqrt(clip(ivar, 0, None)) over pixels where
    mask_array == 0). Negative ivar values are clipped to zero before the
    square root; fully masked rows return None (rendered as an Arrow null,
    never NaN, in the output table).

    Parameters
    ----------
    flux : sequence of float
        Row flux values.
    ivar : sequence of float
        Row inverse-variance values.
    mask : sequence of int
        Row mask_array values; 0 means unmasked.

    Returns
    -------
    float or None
        Median per-pixel SNR over unmasked pixels, or None when no pixel
        has mask == 0.

    Examples
    --------
    >>> row_snr_proxy([1.0, 2.0, 100.0, 4.0], [4.0, -1.0, 100.0, 0.0], [0, 0, 1, 0])
    0.0
    >>> row_snr_proxy([3.0, 4.0], [1.0, 1.0], [0, 0])
    3.5
    >>> row_snr_proxy([1.0], [1.0], [1]) is None
    True
    """
    flux_values = np.asarray(flux, dtype=np.float64)
    ivar_values = np.asarray(ivar, dtype=np.float64)
    mask_values = np.asarray(mask, dtype=np.int64)
    unmasked = mask_values == 0
    if not np.any(unmasked):
        return None
    per_pixel = flux_values[unmasked] * np.sqrt(np.clip(ivar_values[unmasked], 0.0, None))
    return float(np.median(per_pixel))


def row_masked_fraction(mask: Sequence[int]) -> float:
    """
    Compute the plan-fixed D5 masked-pixel fraction for one row.

    masked_fraction = mean(mask_array != 0); a zero-length row is defined
    as 0.0.

    Parameters
    ----------
    mask : sequence of int
        Row mask_array values.

    Returns
    -------
    float
        Share of pixels with a nonzero mask value.

    Examples
    --------
    >>> row_masked_fraction([0, 1, 1, 0])
    0.5
    >>> row_masked_fraction([])
    0.0
    """
    mask_values = np.asarray(mask, dtype=np.int64)
    if mask_values.size == 0:
        return 0.0
    return float(np.count_nonzero(mask_values != 0) / mask_values.size)


def row_scalar_metrics(flux: Sequence[float], ivar: Sequence[float], mask: Sequence[int]) -> dict[str, Any]:
    """
    Compute all three D5 per-row scalar metrics in one call.

    Parameters
    ----------
    flux : sequence of float
        Row flux values (defines array_length).
    ivar : sequence of float
        Row inverse-variance values.
    mask : sequence of int
        Row mask_array values.

    Returns
    -------
    dict[str, Any]
        Keys array_length (int), snr_proxy (float or None), masked_fraction
        (float), all derived in float64.
    """
    return {
        "array_length": int(np.asarray(flux).size),
        "snr_proxy": row_snr_proxy(flux, ivar, mask),
        "masked_fraction": row_masked_fraction(mask),
    }


def common_rest_interval(
    z_min: float,
    z_max: float,
    wave_min: float = OBSERVED_WAVE_MIN_ANGSTROM,
    wave_max: float = OBSERVED_WAVE_MAX_ANGSTROM,
) -> tuple[float, float]:
    """
    Common rest-frame interval of a redshift bin under observed wavelength limits.

    An object at z maps the observed window [wave_min, wave_max] to the
    rest window [wave_min/(1+z), wave_max/(1+z)]; the intersection over
    z in [z_min, z_max] is [wave_min/(1+z_min), wave_max/(1+z_max)] (the
    lo bound is set by z_min and the hi bound by z_max).

    Parameters
    ----------
    z_min : float
        Lower redshift bound of the bin.
    z_max : float
        Upper redshift bound of the bin.
    wave_min : float, optional
        Observed-frame lower limit in Angstroms.
    wave_max : float, optional
        Observed-frame upper limit in Angstroms.

    Returns
    -------
    tuple[float, float]
        Rest-frame interval [lo, hi] in Angstroms; hi <= lo flags disjoint
        rest-frame windows (per-object windows no longer overlap across
        the bin).

    Examples
    --------
    >>> common_rest_interval(2.0, 4.0)
    (1200.0, 1959.8400000000001)
    >>> common_rest_interval(0.5, 4.5)
    (2400.0, 1781.6727272727273)
    """
    return (float(wave_min) / (1.0 + float(z_min)), float(wave_max) / (1.0 + float(z_max)))


def interval_width_angstrom(interval: Sequence[float]) -> float:
    """
    Width of a closed interval [lo, hi]; negative for disjoint windows.

    Parameters
    ----------
    interval : sequence of float
        Two-element interval.

    Returns
    -------
    float
        hi - lo.
    """
    return float(interval[1]) - float(interval[0])


def rest_interval_workability(interval: Sequence[float]) -> str:
    """
    Mechanical workability flag for a candidate bin's rest-frame interval.

    Parameters
    ----------
    interval : sequence of float
        Common rest-frame interval [lo, hi].

    Returns
    -------
    str
        "positive_width" when hi - lo > 0, else "disjoint" (width zero or
        negative).
    """
    return "positive_width" if interval_width_angstrom(interval) > 0.0 else "disjoint"


def linear_grid_length(interval: Sequence[float], resolution: float = REST_GRID_RESOLUTION_ANGSTROM) -> float:
    """
    Rest-frame grid length of an interval at a linear resolution.

    Parameters
    ----------
    interval : sequence of float
        Common rest-frame interval [lo, hi] in Angstroms.
    resolution : float, optional
        Linear resolution in Angstroms per grid step.

    Returns
    -------
    float
        Interval width divided by the resolution.

    Examples
    --------
    >>> linear_grid_length((1000.0, 3546.4), 1.0)
    2546.4
    """
    if resolution <= 0:
        raise ValueError(f"resolution must be positive, got {resolution}")
    return interval_width_angstrom(interval) / float(resolution)


def log_grid_length(interval: Sequence[float], dex: float = REST_LOG_RESOLUTION_DEX) -> float | None:
    """
    Rest-frame grid length of an interval at a log-lambda resolution.

    Parameters
    ----------
    interval : sequence of float
        Common rest-frame interval [lo, hi] in Angstroms.
    dex : float, optional
        Log resolution in dex per grid step.

    Returns
    -------
    float or None
        (log10(hi) - log10(lo)) / dex, or None when either bound is
        non-positive (log grid undefined).

    Examples
    --------
    >>> round(log_grid_length((100.0, 1000.0), 1e-4))
    10000
    """
    if dex <= 0:
        raise ValueError(f"dex must be positive, got {dex}")
    lo, hi = float(interval[0]), float(interval[1])
    if lo <= 0.0 or hi <= 0.0:
        return None
    return (math.log10(hi) - math.log10(lo)) / float(dex)


def outward_bin_edges(value_min: float, value_max: float, width: float) -> list[float]:
    """
    Bin edges of the given width covering [value_min, value_max], rounded outward.

    The lower edge is floor(value_min/width)*width and the upper edge is
    ceil(value_max/width)*width, so the edges always cover the populated
    range; a tiny relative guard suppresses float-division noise at exact
    grid multiples. Edges are rounded to 10 decimals to keep JSON output
    clean for widths like 0.1 that are not exact in binary.

    Parameters
    ----------
    value_min : float
        Populated minimum.
    value_max : float
        Populated maximum.
    width : float
        Positive bin width.

    Returns
    -------
    list[float]
        Edges lo, lo+width, ..., hi with at least two entries.

    Raises
    ------
    ValueError
        If width is not positive or value_max < value_min.

    Examples
    --------
    >>> outward_bin_edges(0.13, 0.62, 0.25)
    [0.0, 0.25, 0.5, 0.75]
    >>> outward_bin_edges(0.5, 1.0, 0.25)
    [0.5, 0.75, 1.0]
    """
    if width <= 0:
        raise ValueError(f"width must be positive, got {width}")
    if value_max < value_min:
        raise ValueError(f"value_max {value_max} below value_min {value_min}")
    first = math.floor(value_min / width + 1e-9)
    last = math.ceil(value_max / width - 1e-9)
    edges = [round(index * width, 10) for index in range(first, last + 1)]
    if len(edges) < 2:
        edges = [edges[0], round(edges[0] + width, 10)]
    return edges


def quantile_profile(values: Sequence[float], levels: Sequence[float] = QUANTILE_LEVELS) -> dict[str, Any]:
    """
    Quantile profile of a finite sample at the fixed reporting levels.

    Parameters
    ----------
    values : sequence of float
        Finite measured values (caller filters non-finite entries).
    levels : sequence of float, optional
        Quantile levels in [0, 1].

    Returns
    -------
    dict[str, Any]
        One float per level keyed by its %g form, plus the method string
        stating numpy's default linear interpolation.
    """
    array = np.asarray(list(values), dtype=np.float64)
    profile: dict[str, Any] = {}
    if array.size == 0:
        for level in levels:
            profile[f"{level:g}"] = None
    else:
        computed = np.quantile(array, list(levels))
        for level, value in zip(levels, computed):
            profile[f"{level:g}"] = float(value)
    profile["method"] = QUANTILE_METHOD
    return profile


def z_histogram(
    z_values: Sequence[float],
    bin_width: float = Z_HISTOGRAM_BIN_WIDTH,
    z_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """
    Histogram of finite redshifts over the populated range plus under/overflow counts.

    Edges cover the populated [min, max] (or the explicit z_range) rounded
    outward to the bin grid; the last bin is closed on both ends
    (numpy.histogram semantics). Non-finite z values are excluded from the
    counts and reported separately.

    Parameters
    ----------
    z_values : sequence of float
        Redshift values (may contain non-finite entries).
    bin_width : float, optional
        Histogram bin width in z.
    z_range : tuple of float, optional
        Explicit (lo, hi) range to cover instead of the populated range;
        used to exercise under/overflow counting in tests.

    Returns
    -------
    dict[str, Any]
        Keys bin_width, edges, counts, underflow, overflow, count_inside,
        count_nonfinite_excluded.

    Examples
    --------
    >>> z_histogram([0.05, 0.15], 0.1, (0.1, 0.1))["underflow"]
    1
    """
    z = np.asarray(list(z_values), dtype=np.float64)
    finite = z[np.isfinite(z)]
    if z_range is not None:
        edges = outward_bin_edges(float(z_range[0]), float(z_range[1]), bin_width)
    elif finite.size:
        edges = outward_bin_edges(float(finite.min()), float(finite.max()), bin_width)
    else:
        edges = []
    if not edges:
        return {
            "bin_width": float(bin_width),
            "edges": [],
            "counts": [],
            "underflow": 0,
            "overflow": 0,
            "count_inside": 0,
            "count_nonfinite_excluded": int(z.size),
        }
    counts = np.histogram(finite, bins=edges)[0] if finite.size else np.zeros(len(edges) - 1, dtype=np.int64)
    return {
        "bin_width": float(bin_width),
        "edges": [float(edge) for edge in edges],
        "counts": [int(count) for count in counts],
        "underflow": int(np.count_nonzero(finite < edges[0])),
        "overflow": int(np.count_nonzero(finite > edges[-1])),
        "count_inside": int(np.sum(counts)),
        "count_nonfinite_excluded": int(z.size - finite.size),
    }


def count_in_z_bin(z_values: np.ndarray, z_min: float, z_max: float, *, closed: bool) -> int:
    """
    Count finite redshifts inside one candidate bin.

    Parameters
    ----------
    z_values : numpy.ndarray
        Finite redshift values as a float64 array.
    z_min, z_max : float
        Bin bounds.
    closed : bool
        True for a closed bin [z_min, z_max]; False for half-open
        [z_min, z_max).

    Returns
    -------
    int
        Exact object count.

    Examples
    --------
    >>> count_in_z_bin(np.asarray([0.1, 0.25, 0.3]), 0.0, 0.25, closed=False)
    2
    >>> count_in_z_bin(np.asarray([0.1, 0.25, 0.3]), 0.0, 0.25, closed=True)
    3
    """
    upper = (z_values <= z_max) if closed else (z_values < z_max)
    return int(np.count_nonzero((z_values >= z_min) & upper))


def build_bin_selection(
    z_values: Sequence[float],
    *,
    wave_min: float = OBSERVED_WAVE_MIN_ANGSTROM,
    wave_max: float = OBSERVED_WAVE_MAX_ANGSTROM,
    fine_width: float = Z_FINE_BIN_WIDTH,
    broad_bins: Sequence[tuple[float, float]] = Z_BROAD_BINS,
    linear_resolution: float = REST_GRID_RESOLUTION_ANGSTROM,
    log_resolution_dex: float = REST_LOG_RESOLUTION_DEX,
) -> dict[str, Any]:
    """
    Build the D4 candidate redshift bin selection table from full-archive z.

    Candidate bins are fine bins of the given width covering the populated
    z range rounded outward to the bin grid, the fixed broad candidate
    bins, and the full populated range. Per bin: z range, exact object
    count, common rest-frame interval, linear and log grid lengths, and
    the mechanical workability flag. No bin is selected; the note states
    that selection is an operator decision.

    Parameters
    ----------
    z_values : sequence of float
        Full-archive redshift column (may contain non-finite entries).
    wave_min, wave_max : float, optional
        Observed-frame wavelength limits from D3.
    fine_width : float, optional
        Fine candidate bin width in z.
    broad_bins : sequence of tuple[float, float], optional
        Broad candidate bin bounds.
    linear_resolution : float, optional
        Linear rest-grid resolution in Angstroms.
    log_resolution_dex : float, optional
        Log rest-grid resolution in dex.

    Returns
    -------
    dict[str, Any]
        Block with observed limits, formulae, counting rule, populated
        range, the bins list, and the fine-bin count-sum consistency check.
    """
    z = np.asarray(list(z_values), dtype=np.float64)
    finite = z[np.isfinite(z)]
    if finite.size == 0:
        raise ValueError("no finite z values; bin selection undefined")
    z_min_populated = float(finite.min())
    z_max_populated = float(finite.max())
    edges = outward_bin_edges(z_min_populated, z_max_populated, fine_width)
    candidates: list[dict[str, Any]] = []
    for index, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        last = index == len(edges) - 2
        candidates.append(
            {
                "kind": "fine",
                "label": f"[{lo:g}, {hi:g}]" if last else f"[{lo:g}, {hi:g})",
                "z_min": float(lo),
                "z_max": float(hi),
                "closed": bool(last),
            }
        )
    for lo, hi in broad_bins:
        candidates.append(
            {"kind": "broad", "label": f"[{lo:g}, {hi:g}]", "z_min": float(lo), "z_max": float(hi), "closed": True}
        )
    candidates.append(
        {
            "kind": "full",
            "label": f"full populated range [{z_min_populated:g}, {z_max_populated:g}]",
            "z_min": z_min_populated,
            "z_max": z_max_populated,
            "closed": True,
        }
    )
    bins: list[dict[str, Any]] = []
    for candidate in candidates:
        interval = common_rest_interval(candidate["z_min"], candidate["z_max"], wave_min, wave_max)
        width = interval_width_angstrom(interval)
        workability = rest_interval_workability(interval)
        record: dict[str, Any] = {
            "kind": candidate["kind"],
            "label": candidate["label"],
            "z_min": candidate["z_min"],
            "z_max": candidate["z_max"],
            "count": count_in_z_bin(finite, candidate["z_min"], candidate["z_max"], closed=candidate["closed"]),
            "rest_interval_angstrom": [float(interval[0]), float(interval[1])],
            "interval_width_angstrom": float(width),
            "linear_grid_length": float(linear_grid_length(interval, linear_resolution)),
            "log_grid_length": log_grid_length(interval, log_resolution_dex),
            "workability": workability,
        }
        if workability != "positive_width":
            record["note"] = "disjoint rest-frame windows (interval width <= 0); count and interval reported together"
        bins.append(record)
    fine_sum = sum(record["count"] for record in bins if record["kind"] == "fine")
    return {
        "observed_limits_angstrom": {
            "min": float(wave_min),
            "max": float(wave_max),
            "source": "D3 array audit summary wavelength_range (plan-fixed values used verbatim)",
        },
        "interval_formula": BIN_INTERVAL_FORMULA,
        "resolution_assumptions": {
            "linear_angstrom": float(linear_resolution),
            "log_dex": float(log_resolution_dex),
            "linear_formula": BIN_LINEAR_FORMULA,
            "log_formula": BIN_LOG_FORMULA,
        },
        "counting_rule": BIN_COUNTING_RULE,
        "interval_z_source": "fine and broad bins use their stated bin edges; full-range uses populated z min/max",
        "populated_range": {
            "z_min": z_min_populated,
            "z_max": z_max_populated,
            "n_finite": int(finite.size),
            "n_excluded_nonfinite": int(z.size - finite.size),
        },
        "bins": bins,
        "fine_bins_count_sum": {"sum": int(fine_sum), "equals_n_finite": bool(fine_sum == int(finite.size))},
        "workability_note": BIN_WORKABILITY_NOTE,
    }


def build_redshift_block(z_values: Sequence[float]) -> dict[str, Any]:
    """
    Build the D4 redshift distribution block from the full-archive z column.

    Parameters
    ----------
    z_values : sequence of float
        Full-archive redshift column (may contain non-finite entries).

    Returns
    -------
    dict[str, Any]
        Keys count_total, count_nonfinite, count_finite, count_z_le_zero,
        min, max, mean, std_population_ddof0, quantiles, histogram, and
        the quantile/histogram method strings.
    """
    z = np.asarray(list(z_values), dtype=np.float64)
    finite = z[np.isfinite(z)]
    return {
        "count_total": int(z.size),
        "count_nonfinite": int(z.size - finite.size),
        "count_finite": int(finite.size),
        "count_z_le_zero": int(np.count_nonzero(finite <= 0.0)),
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
        "mean": float(np.mean(finite)) if finite.size else None,
        "std_population_ddof0": float(np.std(finite)) if finite.size else None,
        "quantiles": quantile_profile(finite),
        "histogram": z_histogram(finite, Z_HISTOGRAM_BIN_WIDTH),
        "quantile_method": QUANTILE_METHOD,
        "histogram_definition": (
            f"bin width {Z_HISTOGRAM_BIN_WIDTH} over the populated range, edges rounded outward to the bin grid; "
            "underflow/overflow count finite z outside the edge range; the last bin is closed; non-finite z excluded"
        ),
    }


def build_nuisance_block(
    snr_values: Sequence[float | None],
    masked_fraction_values: Sequence[float],
) -> dict[str, Any]:
    """
    Build the D5 nuisance distribution block from per-row scalars.

    Parameters
    ----------
    snr_values : sequence of float or None
        Full-archive snr_proxy column; None marks an Arrow null (zero
        unmasked pixels).
    masked_fraction_values : sequence of float
        Full-archive masked_fraction column, row-aligned with snr_values.

    Returns
    -------
    dict[str, Any]
        Blocks snr_proxy (quantiles, null count, non-finite count,
        pathological tail of values <= 0), masked_fraction (quantiles,
        zero share, tail counts above 0.5 and 0.9), and joint (overlap of
        extreme masking with null/non-positive snr_proxy plus the Task 4
        zero-ivar context note).
    """
    snr = list(snr_values)
    masked = np.asarray(list(masked_fraction_values), dtype=np.float64)
    if masked.size != len(snr):
        raise ValueError(f"snr and masked_fraction must be aligned, got {len(snr)} vs {masked.size}")
    n_rows = len(snr)
    present = np.asarray([value for value in snr if value is not None], dtype=np.float64)
    count_null = n_rows - int(present.size)
    finite = present[np.isfinite(present)] if present.size else present
    count_nonfinite = int(present.size - finite.size)
    nonpositive = int(np.count_nonzero(finite <= 0.0))
    masked_zero = int(np.count_nonzero(masked == 0.0))
    masked_above_05 = int(np.count_nonzero(masked > 0.5))
    masked_above_09 = int(np.count_nonzero(masked > 0.9))
    extreme_masked_snr_bad = 0
    for value, fraction in zip(snr, masked):
        if fraction > 0.9 and (value is None or value != value or value <= 0.0):
            extreme_masked_snr_bad += 1
    tail_note = (
        f"{nonpositive} rows have snr_proxy <= 0 (negative median flux over unmasked pixels); reported alongside "
        "the null count so a downstream consumer can exclude them"
        if nonpositive
        else "no rows with snr_proxy <= 0 among non-null values"
    )
    return {
        "snr_proxy": {
            "count_rows": int(n_rows),
            "count_null": int(count_null),
            "null_semantics": "Arrow null (not NaN) when the row has zero unmasked pixels",
            "count_nonfinite_excluding_nulls": count_nonfinite,
            "quantiles": quantile_profile(finite),
            "pathological_tail": {
                "count_snr_le_zero": nonpositive,
                "share_of_non_null": nonpositive / int(present.size) if present.size else 0.0,
                "share_of_all_rows": nonpositive / n_rows if n_rows else 0.0,
                "note": tail_note,
            },
        },
        "masked_fraction": {
            "count_rows": int(masked.size),
            "count_zero": masked_zero,
            "share_zero": masked_zero / masked.size if masked.size else 0.0,
            "quantiles": quantile_profile(masked),
            "pathological_tail": {
                "count_above_0_5": masked_above_05,
                "share_above_0_5": masked_above_05 / masked.size if masked.size else 0.0,
                "count_above_0_9": masked_above_09,
                "share_above_0_9": masked_above_09 / masked.size if masked.size else 0.0,
                "note": (
                    f"{masked_above_09} rows exceed 0.9 masked pixels and {masked_above_05} exceed 0.5"
                    if masked_above_05
                    else "no rows exceed 0.5 masked pixels"
                ),
            },
        },
        "joint": {
            "count_masked_above_0_9": masked_above_09,
            "of_which_snr_null_or_le_zero": int(extreme_masked_snr_bad),
            "note": (
                "Task 4's D3 audit measured a row class reaching 36% zero-ivar pixels; masked_fraction is a "
                "different quantity (share of pixels with nonzero mask_array, not ivar == 0), so both tails are "
                "characterized independently above; the joint field reports how many of the >0.9-masked rows "
                "also carry a null or non-positive snr_proxy"
            ),
        },
    }


def scan_file_scalars(path: Path | str, tile_id: str) -> dict[str, Any]:
    """
    Scan one corpus file and produce its per-row D4+D5 scalar records.

    The file is opened read-only and read exactly once with only the seven
    D4+D5 columns (target_id, ra, dec, z, flux, ivar, mask_array;
    wavelength is never read). Per-row flux/ivar/mask slices are taken
    from the flat value buffers via the list offsets, and the metrics are
    computed in float64.

    Parameters
    ----------
    path : Path or str
        Corpus file to scan (read-only).
    tile_id : str
        Zero-padded tile id of the file.

    Returns
    -------
    dict[str, Any]
        Keys "rows" (payload dict with row_uid, tile_id, row_idx,
        target_id, ra, dec, z, array_length, snr_proxy, masked_fraction
        lists), "n_rows" (row count of the file), and "warnings" (dict
        with path, tile_id, null_snr_rows, nonfinite_z_rows,
        z_le_zero_rows counts for this file).

    Raises
    ------
    Exception
        Any open/read failure propagates to the chunk scanner's error
        capture (unlike D1/D3 sampled scans, this pass requires every row).
    """
    parquet_file = pq.ParquetFile(path)
    n_rows = int(parquet_file.metadata.num_rows)
    table = parquet_file.read(columns=list(SCALAR_COLUMNS)).combine_chunks()
    target_ids = table.column("target_id").to_pylist()
    ra_values = table.column("ra").to_pylist()
    dec_values = table.column("dec").to_pylist()
    z_values = table.column("z").to_pylist()
    flat: dict[str, np.ndarray] = {}
    offsets: dict[str, np.ndarray] = {}
    for name in ("flux", "ivar", "mask_array"):
        column = table.column(name)
        if column.null_count:
            raise ValueError(f"null values in column {name} of {path}")
        list_array = column.chunk(0)
        if not isinstance(list_array, pa.ListArray):
            raise TypeError(f"column {name} of {path} is {type(list_array).__name__}, expected ListArray")
        flat[name] = list_array.values.to_numpy(zero_copy_only=False)
        offsets[name] = np.asarray(list_array.offsets)
    rows: dict[str, list[Any]] = {name: [] for name in PER_OBJECT_PART_SCHEMA.names}
    null_snr_rows = 0
    nonfinite_z_rows = 0
    z_le_zero_rows = 0
    for row_idx in range(n_rows):
        slices = {name: flat[name][int(offsets[name][row_idx]) : int(offsets[name][row_idx + 1])] for name in flat}
        metrics = row_scalar_metrics(slices["flux"], slices["ivar"], slices["mask_array"])
        if metrics["snr_proxy"] is None:
            null_snr_rows += 1
        z = z_values[row_idx]
        z_float = float(z)
        if not math.isfinite(z_float):
            nonfinite_z_rows += 1
        elif z_float <= 0.0:
            z_le_zero_rows += 1
        rows["row_uid"].append(row_uid(tile_id, row_idx))
        rows["tile_id"].append(tile_id)
        rows["row_idx"].append(int(row_idx))
        rows["target_id"].append(target_ids[row_idx])
        rows["ra"].append(ra_values[row_idx])
        rows["dec"].append(dec_values[row_idx])
        rows["z"].append(z)
        rows["array_length"].append(metrics["array_length"])
        rows["snr_proxy"].append(metrics["snr_proxy"])
        rows["masked_fraction"].append(metrics["masked_fraction"])
    return {
        "rows": rows,
        "n_rows": n_rows,
        "warnings": {
            "path": str(path),
            "tile_id": tile_id,
            "null_snr_rows": int(null_snr_rows),
            "nonfinite_z_rows": int(nonfinite_z_rows),
            "z_le_zero_rows": int(z_le_zero_rows),
        },
    }


def scalars_chunk(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """
    Scan a chunk of file tasks, aggregating per-file scalar results.

    Unlike the D1/D3 scanners, open/read failures are captured per file
    without raising, but they make the run fail later at the expected-rows
    validation because this pass requires every corpus row.

    Parameters
    ----------
    tasks : sequence of mapping of str to Any
        One task per file with keys path, tile_id.

    Returns
    -------
    dict[str, Any]
        Keys "rows" (payload dict over all files of the chunk), "warnings"
        (per-file warning dicts, only files with any warning), and
        "errors" (list of path/error dicts for failed files).
    """
    rows: dict[str, list[Any]] = {name: [] for name in PER_OBJECT_PART_SCHEMA.names}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for task in tasks:
        try:
            result = scan_file_scalars(task["path"], task["tile_id"])
        except Exception as exc:
            errors.append({"path": str(task["path"]), "error": f"{type(exc).__name__}: {exc}"})
            continue
        for name in rows:
            rows[name].extend(result["rows"][name])
        warning = result["warnings"]
        if warning["null_snr_rows"] or warning["nonfinite_z_rows"] or warning["z_le_zero_rows"]:
            warnings.append(warning)
    return {"rows": rows, "warnings": warnings, "errors": errors}


def per_object_part_table(payload: Mapping[str, list[Any]]) -> pa.Table:
    """
    Pack a scalars payload dictionary into a typed part-schema table.

    Parameters
    ----------
    payload : mapping of str to list
        Dictionary with one list per PER_OBJECT_PART_SCHEMA column.

    Returns
    -------
    pa.Table
        Table conforming to PER_OBJECT_PART_SCHEMA; snr_proxy None entries
        become true Arrow nulls.
    """
    return pa.table({name: payload[name] for name in PER_OBJECT_PART_SCHEMA.names}, schema=PER_OBJECT_PART_SCHEMA)


def project_per_object_table(table: pa.Table) -> pa.Table:
    """
    Project a part-schema table down to the exact D4+D5 output schema.

    Parameters
    ----------
    table : pa.Table
        Table conforming to PER_OBJECT_PART_SCHEMA.

    Returns
    -------
    pa.Table
        Table conforming to PER_OBJECT_SCHEMA (row_idx dropped).
    """
    return table.select([field.name for field in PER_OBJECT_SCHEMA])


def build_scalars_run_config(
    corpus_root: Path | str,
    files: Sequence[Path],
    *,
    columns: Sequence[str] = SCALAR_COLUMNS,
    definitions_version: str = NUISANCE_DEFINITIONS_VERSION,
    chunk_size: int = CHUNK_SIZE,
) -> dict[str, Any]:
    """
    Build the run configuration fingerprinting the scalars parts family.

    Parameters
    ----------
    corpus_root : Path or str
        Corpus root directory.
    files : sequence of Path
        Discovered corpus files in sorted order.
    columns : sequence of str, optional
        Column set read per file (changing it invalidates checkpoints).
    definitions_version : str, optional
        Version string of the snr/masked-fraction definitions.
    chunk_size : int, optional
        Files per checkpoint chunk.

    Returns
    -------
    dict[str, Any]
        Keys corpus_root, chunk_size, n_files, files_sha256, columns,
        definitions_version.
    """
    return {
        "corpus_root": str(Path(corpus_root)),
        "chunk_size": int(chunk_size),
        "n_files": len(files),
        "files_sha256": corpus_fingerprint(files, corpus_root),
        "columns": list(columns),
        "definitions_version": str(definitions_version),
    }


def build_redshift_summary(
    *,
    z_values: Sequence[float],
    snr_values: Sequence[float | None],
    masked_fraction_values: Sequence[float],
    provenance: Mapping[str, Any],
    wave_min: float = OBSERVED_WAVE_MIN_ANGSTROM,
    wave_max: float = OBSERVED_WAVE_MAX_ANGSTROM,
) -> dict[str, Any]:
    """
    Assemble the D4+D5 redshift_summary.json payload.

    Parameters
    ----------
    z_values : sequence of float
        Full-archive redshift column.
    snr_values : sequence of float or None
        Full-archive snr_proxy column.
    masked_fraction_values : sequence of float
        Full-archive masked_fraction column.
    provenance : mapping of str to Any
        Inputs, command, versions, wall time, timestamp, fence action.
    wave_min, wave_max : float, optional
        Observed-frame limits from D3 used by the bin table.

    Returns
    -------
    dict[str, Any]
        Blocks provenance, rr_chi2 (verbatim unresolvable block),
        redshift, bin_selection, nuisance, and formulae.
    """
    return {
        "provenance": dict(provenance),
        "rr_chi2": dict(RR_CHI2_BLOCK),
        "redshift": build_redshift_block(z_values),
        "bin_selection": build_bin_selection(z_values, wave_min=wave_min, wave_max=wave_max),
        "nuisance": build_nuisance_block(snr_values, masked_fraction_values),
        "formulae": {
            "snr_proxy": SNR_PROXY_FORMULA,
            "masked_fraction": MASKED_FRACTION_FORMULA,
            "array_length": ARRAY_LENGTH_FORMULA,
            "quantile_method": QUANTILE_METHOD,
        },
    }


# =============================================================================
# Manifest Assembly (D6)
# =============================================================================

FILE_MANIFEST_FILENAME = "qso_file_manifest_v1.parquet"
OBJECT_MANIFEST_FILENAME = "qso_object_manifest_v1.parquet"
MANIFEST_PROVENANCE_FILENAME = "manifest_provenance.json"

SNAPSHOT_FILENAME = "corpus_snapshot.parquet"

MANIFEST_SPOT_CHECK_ROWS = 10
MANIFEST_ROUND_TRIP_SEED = ARRAY_SAMPLING_SEED

SAMPLED_FLAG_COLUMNS = (
    "align_ok",
    "n_mono_breaks",
    "first_break_idx",
    "overlap_br_angstrom",
    "overlap_rz_angstrom",
    "n_dup_wave",
    "ivar_neg_count",
    "ivar_nonfinite_count",
    "ivar_zero_frac",
    "wave_min",
    "wave_max",
)

OBJECT_MANIFEST_SCHEMA = pa.schema(
    [
        *list(PER_OBJECT_SCHEMA),
        *[field for field in ARRAY_AUDIT_SCHEMA if field.name in SAMPLED_FLAG_COLUMNS],
    ]
)

MD5_CHUNK_BYTES = 1 << 20


def parse_row_uid(uid: str) -> tuple[str, int]:
    """
    Split a row_uid into its tile_id and row_idx parts.

    Parameters
    ----------
    uid : str
        Identifier of the form "<tile_id>:<row_idx>" built by row_uid.

    Returns
    -------
    tuple[str, int]
        The tile_id string and the zero-based row_idx integer.

    Raises
    ------
    ValueError
        If the uid has no colon separator or a non-numeric row index.

    Examples
    --------
    >>> parse_row_uid("10018:7")
    ('10018', 7)
    """
    tile_id, separator, index = uid.partition(":")
    if not separator or not index.isdigit() or not tile_id:
        raise ValueError(f"malformed row_uid (expected '<tile_id>:<row_idx>'): {uid!r}")
    return tile_id, int(index)


def require_unique_row_uid(table: pa.Table, label: str) -> None:
    """
    Assert that a table's row_uid column contains no duplicate values.

    Parameters
    ----------
    table : pa.Table
        Table with a row_uid string column.
    label : str
        Human-readable table name for the error message.

    Raises
    ------
    ValueError
        If any row_uid value appears more than once.
    """
    uids = table.column("row_uid").to_pylist()
    if len(set(uids)) != len(uids):
        raise ValueError(f"duplicate row_uid values in {label}")


def join_sampled_flags(per_object: pa.Table, sampled: pa.Table) -> pa.Table:
    """
    LEFT-JOIN the sampled array-contract flags onto the per-object table.

    The per-object table keeps exactly one row per corpus row; the eleven
    D3 flag columns are attached by row_uid and are Arrow-null on every
    row the D3 sample did not cover. The result is sorted by
    (tile_id, row_idx), with row_idx recovered from the row_uid, and cast
    to OBJECT_MANIFEST_SCHEMA. The D3 mask_value_counts column and the
    auxiliary columns (n_pixels, n_neardup_wave, ivar_zero_count) are not
    carried.

    Parameters
    ----------
    per_object : pa.Table
        Table conforming to PER_OBJECT_SCHEMA (one row per corpus row).
    sampled : pa.Table
        Table conforming to ARRAY_AUDIT_SCHEMA (sampled rows only).

    Returns
    -------
    pa.Table
        Table conforming to OBJECT_MANIFEST_SCHEMA, sorted by
        (tile_id, row_idx).

    Raises
    ------
    ValueError
        If either input carries duplicate row_uid values (the join would
        silently fan rows out).
    """
    require_unique_row_uid(per_object, "per-object table")
    require_unique_row_uid(sampled, "sampled audit table")
    flags = sampled.select(["row_uid", *SAMPLED_FLAG_COLUMNS])
    joined = per_object.join(flags, keys="row_uid", join_type="left outer")
    row_indices = pa.array([parse_row_uid(uid)[1] for uid in joined.column("row_uid").to_pylist()], pa.int64())
    joined = joined.append_column("row_idx", row_indices)
    joined = joined.take(pc.sort_indices(joined, sort_keys=[("tile_id", "ascending"), ("row_idx", "ascending")]))
    return joined.select(OBJECT_MANIFEST_SCHEMA.names).cast(OBJECT_MANIFEST_SCHEMA)


def validate_object_manifest(
    table: pa.Table,
    *,
    expected_rows: int,
    expected_sampled_join: int,
) -> dict[str, Any]:
    """
    Run the hard asserts over the assembled object manifest table.

    Checks the exact row count against the D2 archive-wide total, row_uid
    uniqueness, and that the number of rows carrying sampled flags (non-null
    align_ok) equals the D3 sampled-row total.

    Parameters
    ----------
    table : pa.Table
        Table conforming to OBJECT_MANIFEST_SCHEMA.
    expected_rows : int
        Archive-wide row total from identity_summary.json.
    expected_sampled_join : int
        Sampled-row total from array_audit_summary.json.

    Returns
    -------
    dict[str, Any]
        Block with rows, unique_row_uids, sampled_join_coverage, and
        unsampled_rows (null-flag rows).

    Raises
    ------
    RuntimeError
        If the row count, row_uid uniqueness, sampled-join coverage, or
        flag-column null alignment fails.
    """
    if table.num_rows != expected_rows:
        raise RuntimeError(
            f"object manifest rows {table.num_rows} != expected {expected_rows} from the D2 identity total"
        )
    require_unique_row_uid(table, "object manifest")
    coverage = table.num_rows - table.column("align_ok").null_count
    if coverage != expected_sampled_join:
        raise RuntimeError(
            f"sampled-join coverage {coverage} != expected {expected_sampled_join} from the D3 audit total"
        )
    for name in SAMPLED_FLAG_COLUMNS:
        nulls = table.column(name).null_count
        if nulls != table.num_rows - coverage:
            raise RuntimeError(
                f"flag column {name} has {nulls} nulls; expected {table.num_rows - coverage} "
                "(sampled flags must be null on exactly the unsampled rows)"
            )
    return {
        "rows": int(table.num_rows),
        "unique_row_uids": True,
        "sampled_join_coverage": int(coverage),
        "unsampled_rows": int(table.num_rows - coverage),
    }


def select_spot_check_indices(n_rows: int, n_spot: int, seed: int) -> list[int]:
    """
    Draw deterministic spot-check row indices without replacement.

    Parameters
    ----------
    n_rows : int
        Row count of the table to spot-check.
    n_spot : int
        Requested number of rows; capped at n_rows.
    seed : int
        Seed for numpy's default_rng.

    Returns
    -------
    list[int]
        Sorted distinct row indices.

    Raises
    ------
    ValueError
        If n_rows or n_spot is not positive.
    """
    if n_rows < 1:
        raise ValueError(f"n_rows must be >= 1, got {n_rows}")
    if n_spot < 1:
        raise ValueError(f"n_spot must be >= 1, got {n_spot}")
    count = min(n_spot, n_rows)
    drawn = np.random.default_rng(seed).choice(n_rows, size=count, replace=False)
    return sorted(int(index) for index in drawn)


def round_trip_validate(
    path: Path | str,
    expected: pa.Table,
    *,
    seed: int = MANIFEST_ROUND_TRIP_SEED,
    n_spot: int = MANIFEST_SPOT_CHECK_ROWS,
) -> dict[str, Any]:
    """
    Validate a written manifest table by reading it back with pyarrow.

    Asserts the read-back schema equals the expected schema, the row count
    matches, and n_spot seeded random rows are value-identical (including
    null placement) to the expected table.

    Parameters
    ----------
    path : Path or str
        Written Parquet file.
    expected : pa.Table
        In-memory table the file must round-trip to.
    seed : int, optional
        Spot-check seed.
    n_spot : int, optional
        Number of spot-check rows.

    Returns
    -------
    dict[str, Any]
        Block with path, rows, schema_matches, spot_check (seed, rows,
        indices, all_match), and passed, for the provenance JSON.

    Raises
    ------
    RuntimeError
        If the schema, row count, or any spot-check row disagrees.
    """
    read_back = pq.read_table(Path(path))
    schema_matches = read_back.schema.equals(expected.schema, check_metadata=False)
    rows_match = read_back.num_rows == expected.num_rows
    indices = select_spot_check_indices(expected.num_rows, n_spot, seed)
    spot_rows_match = all(
        read_back.slice(index, 1).to_pylist() == expected.slice(index, 1).to_pylist() for index in indices
    )
    block = {
        "path": str(path),
        "rows": int(read_back.num_rows),
        "schema_matches": bool(schema_matches),
        "spot_check": {
            "seed": int(seed),
            "rows_checked": len(indices),
            "indices": indices,
            "all_match": bool(spot_rows_match),
        },
        "passed": bool(schema_matches and rows_match and spot_rows_match),
    }
    if not rows_match:
        raise RuntimeError(f"round-trip row mismatch for {path}: {read_back.num_rows} vs {expected.num_rows}")
    if not schema_matches:
        raise RuntimeError(f"round-trip schema mismatch for {path}")
    if not spot_rows_match:
        raise RuntimeError(f"round-trip spot-check mismatch for {path} at seed {seed}")
    return block


def compare_corpus_snapshots(snapshot: pa.Table, current: pa.Table) -> dict[str, Any]:
    """
    Compare a fresh corpus stat listing against the pre-audit snapshot.

    Files are keyed by absolute path string. A file is unchanged only when
    both size_bytes and mtime_ns match exactly.

    Parameters
    ----------
    snapshot : pa.Table
        Pre-audit snapshot conforming to SNAPSHOT_SCHEMA.
    current : pa.Table
        Fresh re-stat conforming to SNAPSHOT_SCHEMA.

    Returns
    -------
    dict[str, Any]
        Block with files_checked (snapshot rows), files_present_now,
        missing_files, extra_files, mismatch_count, mismatches (capped
        detail list with per-field before/after values), and the overall
        unchanged flag.

    Examples
    --------
    >>> a = pa.table({"path": ["f"], "size_bytes": [1], "mtime_ns": [2]}, schema=SNAPSHOT_SCHEMA)
    >>> b = pa.table({"path": ["f"], "size_bytes": [1], "mtime_ns": [3]}, schema=SNAPSHOT_SCHEMA)
    >>> compare_corpus_snapshots(a, b)["unchanged"]
    False
    """
    before = {record["path"]: (record["size_bytes"], record["mtime_ns"]) for record in snapshot.to_pylist()}
    after = {record["path"]: (record["size_bytes"], record["mtime_ns"]) for record in current.to_pylist()}
    missing = sorted(set(before) - set(after))
    extra = sorted(set(after) - set(before))
    mismatches: list[dict[str, Any]] = []
    for path in sorted(set(before) & set(after)):
        fields: dict[str, Any] = {}
        for name, index in (("size_bytes", 0), ("mtime_ns", 1)):
            if before[path][index] != after[path][index]:
                fields[name] = {"snapshot": before[path][index], "current": after[path][index]}
        if fields:
            mismatches.append({"path": path, "fields": fields})
    return {
        "files_checked": int(snapshot.num_rows),
        "files_present_now": int(current.num_rows),
        "missing_files": missing,
        "extra_files": extra,
        "mismatch_count": len(mismatches) + len(missing) + len(extra),
        "mismatches": mismatches[:ARRAY_BREAK_EXAMPLE_LIMIT],
        "mismatch_examples_capped_at": ARRAY_BREAK_EXAMPLE_LIMIT,
        "unchanged": not (missing or extra or mismatches),
    }


def file_md5(path: Path | str, chunk_bytes: int = MD5_CHUNK_BYTES) -> str:
    """
    Compute the hex MD5 digest of a file in bounded memory.

    Parameters
    ----------
    path : Path or str
        File to hash.
    chunk_bytes : int, optional
        Read chunk size.

    Returns
    -------
    str
        Hex digest.
    """
    digest = hashlib.md5()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(block)
    return digest.hexdigest()


def input_artifact_record(name: str, path: Path | str, rows: int | None = None) -> dict[str, Any]:
    """
    Build a provenance record for one input work artifact (md5 + row count).

    Parameters
    ----------
    name : str
        Artifact name, e.g. "inventory_parquet".
    path : Path or str
        Artifact path.
    rows : int or None, optional
        Row count when the artifact is a table.

    Returns
    -------
    dict[str, Any]
        Record with name, path, rows (or null), size_bytes, md5.
    """
    resolved = Path(path)
    record: dict[str, Any] = {
        "name": name,
        "path": str(resolved),
        "rows": int(rows) if rows is not None else None,
        "size_bytes": int(resolved.stat().st_size),
        "md5": file_md5(resolved),
    }
    return record


def build_manifest_provenance(
    *,
    generated_at: str,
    host: str,
    python_version: str,
    package_versions: Mapping[str, str],
    command: str,
    tool_commit: str,
    corpus_root: Path | str,
    corpus_files_sha256: str,
    inputs: Sequence[Mapping[str, Any]],
    sampling: Mapping[str, Any],
    outputs: Mapping[str, Mapping[str, Any]],
    round_trip: Mapping[str, Any],
    corpus_integrity: Mapping[str, Any],
    validations: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Assemble the manifest_provenance.json payload.

    Parameters
    ----------
    generated_at : str
        ISO-8601 build timestamp.
    host : str
        Hostname.
    python_version : str
        Python version string.
    package_versions : mapping of str to str
        pyarrow/numpy/pandas versions.
    command : str
        Shell-joined command line of the build.
    tool_commit : str
        git rev-parse HEAD at build time.
    corpus_root : Path or str
        Root of the read-only corpus.
    corpus_files_sha256 : str
        SHA-256 fingerprint of the corpus file list.
    inputs : sequence of mapping of str to Any
        input_artifact_record blocks for every work artifact consumed.
    sampling : mapping of str to Any
        D3 sampling parameters (seed, method, strides, achieved counts).
    outputs : mapping of str to mapping of str to Any
        Per-artifact output records (path, rows, size_bytes, md5).
    round_trip : mapping of str to Any
        Round-trip validation blocks keyed by table name.
    corpus_integrity : mapping of str to Any
        compare_corpus_snapshots block.
    validations : mapping of str to Any
        Hard-assert results (row counts, coverage).

    Returns
    -------
    dict[str, Any]
        Structured provenance payload for manifest_provenance.json.
    """
    return {
        "generated_at": generated_at,
        "host": host,
        "python_version": python_version,
        "package_versions": dict(package_versions),
        "command": command,
        "tool_commit": tool_commit,
        "corpus_root": str(Path(corpus_root)),
        "corpus_files_sha256": corpus_files_sha256,
        "corpus_read_only": True,
        "inputs": [dict(record) for record in inputs],
        "sampling": dict(sampling),
        "outputs": {name: dict(record) for name, record in outputs.items()},
        "validations": dict(validations),
        "round_trip": dict(round_trip),
        "corpus_integrity": dict(corpus_integrity),
    }
