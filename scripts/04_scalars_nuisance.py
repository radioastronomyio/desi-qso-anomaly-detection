#!/usr/bin/env python3
"""
Script Name  : 04_scalars_nuisance.py
Description  : D4+D5 redshift/coverage table and nuisance baseline over the DESI DR1 QSO Parquet corpus.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Single full-archive traversal reading only target_id, ra, dec, z, flux,
ivar, mask_array (never wavelength) from every corpus file, exactly one
read per file. Computes the plan-fixed D5 per-row scalars in float64
(snr_proxy = median(flux * sqrt(clip(ivar, 0, None)) over unmasked
pixels, Arrow-null when a row has zero unmasked pixels; masked_fraction =
mean(mask_array != 0); array_length = len(flux)) and assembles the D4
redshift products from the full-archive z column: histogram (0.1 width
over the populated range with under/overflow counts), quantiles at the
fixed levels, and the candidate bin selection table (0.25-width fine bins
covering the populated range rounded outward, the broad candidate bins,
and the full range) with per-bin object counts, the common rest-frame
interval [3600.0/(1+z_min), 9799.2/(1+z_max)] Angstrom (the intersection
of per-object rest windows; lo bound from z_min, hi bound from z_max)
using the D3 observed limits, and rest-grid lengths at 1.0 Angstrom linear
and 1e-4 dex log resolution, with a mechanical workability flag (hi <= lo
flags disjoint windows) and no bin
selection (operator decision). rr_chi2 is recorded verbatim as
unresolvable (column absent from the archive; no proxy invented).

The scan is parallel across worker processes and checkpointed as part
files under work/parts/scalars/ with a run-config manifest (corpus root,
chunk size, file-list hash, column set, snr/mask definitions version)
fencing the parts directory against stale cross-configuration reuse;
resume skips valid parts and assembly is plan-driven with per-chunk
expected-row validation against the D1 inventory and a final total-row /
row_uid-construction / sortedness validation. The corpus is strictly
read-only. Emits work/per_object_raw.parquet (one row per corpus row,
sorted by (tile_id, row_idx), row_uid = "<tile_id>:<row_idx>") and
work/redshift_summary.json.

Usage
-----
    python scripts/04_scalars_nuisance.py [--corpus-root PATH] [--work-dir PATH]
        [--workers N] [--chunk-size N]

Examples
--------
    python scripts/04_scalars_nuisance.py
        Traverse the default corpus into the default work directory,
        resuming from any existing part-file checkpoints.

    python scripts/04_scalars_nuisance.py --workers 16
        Traverse with 16 worker processes.
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shlex
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dqad_audit import (
    CORPUS_ROOT,
    NUISANCE_DEFINITIONS_VERSION,
    OBSERVED_WAVE_MAX_ANGSTROM,
    OBSERVED_WAVE_MIN_ANGSTROM,
    PER_OBJECT_FILENAME,
    REDSHIFT_SUMMARY_FILENAME,
    SCALAR_COLUMNS,
    SCALARS_PART_KIND,
    WORK_DIR,
    build_redshift_summary,
    build_scalars_run_config,
    discover_corpus_files,
    kind_manifest_path,
    manifest_matches,
    part_dir,
    part_filepath,
    part_is_valid,
    per_object_part_table,
    plan_chunks,
    project_per_object_table,
    read_kind_manifest,
    read_part,
    row_uid,
    scalars_chunk,
    tile_id_from_filename,
    write_kind_manifest,
    write_part,
)

# =============================================================================
# Configuration
# =============================================================================

logger = logging.getLogger("dqad_audit.04_scalars_nuisance")

DEFAULT_WORKERS = min(32, os.cpu_count() or 4)

FENCE_REUSE = "reused"
FENCE_INITIALIZED = "initialized"
FENCE_PRUNED_PARTS = "pruned_parts"

INVENTORY_FILENAME = "inventory.parquet"
ARRAY_AUDIT_SUMMARY_FILENAME = "array_audit_summary.json"
PROGRESS_EVERY = 500
WARNING_FILE_LIST_LIMIT = 50

# =============================================================================
# Functions
# =============================================================================


def setup_logging() -> None:
    """Configure INFO-level logging for the traversal run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def apply_run_fence(work_dir: Path, current: dict[str, object]) -> str:
    """
    Fence the scalars parts directory against cross-configuration reuse.

    Compares parts/scalars/run_config.json against the current run
    configuration (corpus root, chunk size, file-list hash, column set,
    and the snr/mask definitions version). On mismatch all scalars part
    files are pruned; the current manifest is then written.

    Parameters
    ----------
    work_dir : Path
        Audit work directory.
    current : dict[str, object]
        Run configuration from build_scalars_run_config.

    Returns
    -------
    str
        One of reused, initialized, pruned_parts.
    """
    recorded = read_kind_manifest(work_dir, SCALARS_PART_KIND)
    if manifest_matches(recorded, current):
        return FENCE_REUSE
    action = FENCE_INITIALIZED if recorded is None else FENCE_PRUNED_PARTS
    for stale in part_dir(work_dir, SCALARS_PART_KIND).glob("*.parquet"):
        stale.unlink()
    write_kind_manifest(work_dir, SCALARS_PART_KIND, current)
    return action


def load_inventory_row_counts(work_dir: Path) -> dict[str, int] | None:
    """
    Load per-file actual row counts from the D1 inventory table.

    Parameters
    ----------
    work_dir : Path
        Audit work directory holding inventory.parquet.

    Returns
    -------
    dict[str, int] or None
        Mapping of file path to actual row count, or None when the D1
        output is absent (unverifiable parts must then be rescanned).
    """
    path = work_dir / INVENTORY_FILENAME
    if not path.is_file():
        logger.warning(
            "no %s in %s; checkpoint reuse disabled without expected row counts", INVENTORY_FILENAME, work_dir
        )
        return None
    table = read_part(path)
    return dict(zip(table.column("path").to_pylist(), table.column("n_rows_actual").to_pylist()))


def load_observed_limits(work_dir: Path) -> dict[str, object]:
    """
    Load the D3 measured observed wavelength limits for provenance.

    The plan-fixed values 3600.0/9799.2 Angstrom are used for the bin
    interval math regardless; the measured D3 figures are echoed for
    traceability.

    Parameters
    ----------
    work_dir : Path
        Audit work directory holding array_audit_summary.json.

    Returns
    -------
    dict[str, object]
        Block with used values, measured values (or None when the D3
        summary is absent), and the source path.
    """
    path = work_dir / ARRAY_AUDIT_SUMMARY_FILENAME
    measured_min: float | None = None
    measured_max: float | None = None
    if path.is_file():
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            wavelength = summary["wavelength_range"]
            measured_min = float(wavelength["global_min_angstrom"])
            measured_max = float(wavelength["global_max_angstrom"])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("could not read observed limits from %s; provenance will record them as absent", path)
    else:
        logger.warning("no %s in %s; observed limits provenance will record them as absent", path.name, work_dir)
    return {
        "used_angstrom": [OBSERVED_WAVE_MIN_ANGSTROM, OBSERVED_WAVE_MAX_ANGSTROM],
        "measured_d3_angstrom": [measured_min, measured_max],
        "source": str(path),
        "note": "plan-fixed D3 values used verbatim for the bin interval math",
    }


def build_tasks(files: list[Path]) -> list[dict[str, object]]:
    """
    Build one scan task per corpus file, in sorted file order.

    Parameters
    ----------
    files : list[Path]
        Discovered corpus files in deterministic sorted order.

    Returns
    -------
    list[dict[str, object]]
        Task dicts for scalars_chunk with keys path, tile_id.
    """
    return [{"path": str(path), "tile_id": tile_id_from_filename(path.name)} for path in files]


def expected_part_rows(
    chunks: list[list[dict[str, object]]],
    inventory_counts: dict[str, int] | None,
) -> list[int | None]:
    """
    Compute the expected row count of each chunk's part file.

    Every corpus row must be present in this pass, so the expectation is
    the exact sum of the D1 inventory's n_rows_actual over the chunk.

    Parameters
    ----------
    chunks : list[list[dict[str, object]]]
        Planned task chunks over the sorted file list.
    inventory_counts : dict[str, int] or None
        Per-file row counts from the D1 inventory.

    Returns
    -------
    list[int or None]
        Expected row count per chunk, or None when the inventory cannot
        account for every file of the chunk.
    """
    expected: list[int | None] = []
    for chunk in chunks:
        total = 0
        complete = inventory_counts is not None
        for task in chunk:
            n_rows = (inventory_counts or {}).get(str(task["path"]))
            if n_rows is None:
                complete = False
                break
            total += int(n_rows)
        expected.append(total if complete else None)
    return expected


def run_scan(
    chunks: list[list[dict[str, object]]],
    work_dir: Path,
    workers: int,
    expected_rows: list[int | None],
) -> tuple[int, list[dict[str, str]], list[dict[str, object]], float]:
    """
    Scan all pending chunks in parallel and write checkpoint part files.

    Parameters
    ----------
    chunks : list[list[dict[str, object]]]
        Planned task chunks over the sorted file list.
    work_dir : Path
        Audit work directory for parts/scalars/.
    workers : int
        Maximum worker processes.
    expected_rows : list[int or None]
        Expected row count per chunk from expected_part_rows.

    Returns
    -------
    tuple[int, list[dict[str, str]], list[dict[str, object]], float]
        Count of files scanned this pass, per-file open/read errors,
        per-file warning records, and wall seconds spent scanning
        (checkpointed chunks are skipped).
    """
    total_files = sum(len(chunk) for chunk in chunks)
    pending = [
        index
        for index in range(len(chunks))
        if expected_rows[index] is None
        or not part_is_valid(part_filepath(work_dir, SCALARS_PART_KIND, index), expected_rows=expected_rows[index])
    ]
    skipped = total_files - sum(len(chunks[index]) for index in pending)
    logger.info(
        "scan plan: %d files in %d chunks, %d pending, %d already checkpointed",
        total_files,
        len(chunks),
        len(pending),
        skipped,
    )
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, object]] = []
    start = time.perf_counter()
    if not pending:
        return skipped, errors, warnings, time.perf_counter() - start
    completed = skipped
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(scalars_chunk, chunks[index]): index for index in pending}
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            for error in result["errors"]:
                logger.warning("open failure: %s (%s)", error["path"], error["error"])
            errors.extend(result["errors"])
            if result["errors"]:
                continue
            write_part(
                per_object_part_table(result["rows"]),
                part_filepath(work_dir, SCALARS_PART_KIND, index),
            )
            for warning in result["warnings"]:
                logger.warning(
                    "file %s (tile %s): null_snr_rows=%d nonfinite_z_rows=%d z_le_zero_rows=%d",
                    warning["path"],
                    warning["tile_id"],
                    warning["null_snr_rows"],
                    warning["nonfinite_z_rows"],
                    warning["z_le_zero_rows"],
                )
            warnings.extend(result["warnings"])
            chunk_len = len(chunks[index])
            if completed // PROGRESS_EVERY < (completed + chunk_len) // PROGRESS_EVERY:
                logger.info("progress: %d/%d files scanned", completed + chunk_len, total_files)
            completed += chunk_len
    elapsed = time.perf_counter() - start
    logger.info("scan pass complete: %d files scanned in %.1fs", completed - skipped, elapsed)
    return completed - skipped, errors, warnings, elapsed


def assemble_table(work_dir: Path, chunks: list[list[dict[str, object]]]) -> pa.Table:
    """
    Assemble the per-object table strictly from the current chunk plan.

    Only parts/scalars/NNNN.parquet for indices 0..len(chunks)-1 are read;
    stray files beyond the plan are ignored. The concatenated table is
    sorted by (tile_id, row_idx).

    Parameters
    ----------
    work_dir : Path
        Audit work directory holding parts/scalars/.
    chunks : list[list[dict[str, object]]]
        Planned task chunks the parts must correspond to.

    Returns
    -------
    pa.Table
        Concatenated part-schema table sorted by (tile_id, row_idx).

    Raises
    ------
    RuntimeError
        If a planned part file is missing.
    """
    parts: list[pa.Table] = []
    for index in range(len(chunks)):
        path = part_filepath(work_dir, SCALARS_PART_KIND, index)
        if not path.is_file():
            raise RuntimeError(f"missing planned scalars part: {path}")
        parts.append(read_part(path))
    table = pa.concat_tables(parts)
    table = table.take(pc.sort_indices(table, sort_keys=[("tile_id", "ascending"), ("row_idx", "ascending")]))
    logger.info("assembled per-object rows: %d", table.num_rows)
    return table


def validate_assembly(
    table: pa.Table,
    expected_total_rows: int | None,
) -> None:
    """
    Validate the assembled table before it is written as the final output.

    Checks the total row count against the D1 inventory expectation,
    row_uid uniqueness, the row_uid == "<tile_id>:<row_idx>" construction,
    and (tile_id, row_idx) sortedness.

    Parameters
    ----------
    table : pa.Table
        Assembled part-schema table sorted by (tile_id, row_idx).
    expected_total_rows : int or None
        Expected total from the D1 inventory; None skips the count check.

    Raises
    ------
    RuntimeError
        If any validation fails.
    """
    if expected_total_rows is not None and table.num_rows != expected_total_rows:
        raise RuntimeError(f"assembled rows {table.num_rows} != expected {expected_total_rows} from the D1 inventory")
    row_uids = table.column("row_uid").to_pylist()
    if len(set(row_uids)) != table.num_rows:
        raise RuntimeError("duplicate row_uid values in the assembled table")
    tile_ids = table.column("tile_id").to_pylist()
    row_indices = table.column("row_idx").to_pylist()
    for uid, tile_id, row_idx in zip(row_uids, tile_ids, row_indices):
        if uid != row_uid(tile_id, row_idx):
            raise RuntimeError(f"row_uid {uid} does not match construction f'{{tile_id}}:{{row_idx}}'")
    for index in range(table.num_rows - 1):
        if (tile_ids[index], row_indices[index]) > (tile_ids[index + 1], row_indices[index + 1]):
            raise RuntimeError(f"table not sorted by (tile_id, row_idx) at position {index}")
    logger.info(
        "assembly validated: %d rows, unique row_uids, uid construction matches, sorted by (tile_id, row_idx)",
        table.num_rows,
    )


def warnings_from_assembled_table(
    table: pa.Table,
    tasks: list[dict[str, object]],
) -> list[dict[str, object]]:
    """
    Recompute per-file warning records from the complete assembled table.

    This makes warning provenance independent of which chunks were scanned
    during the current invocation. Reused checkpoints contribute exactly the
    same warning classes as newly scanned chunks.

    Parameters
    ----------
    table : pa.Table
        Complete assembled scalar part table.
    tasks : list[dict[str, object]]
        One source-file task per tile, providing paths for warning examples.

    Returns
    -------
    list[dict[str, object]]
        Per-file warning records for files with at least one warning.
    """
    counts: dict[str, dict[str, int]] = {
        str(task["tile_id"]): {"null_snr_rows": 0, "nonfinite_z_rows": 0, "z_le_zero_rows": 0} for task in tasks
    }
    for tile_id, snr, z in zip(
        table.column("tile_id").to_pylist(),
        table.column("snr_proxy").to_pylist(),
        table.column("z").to_pylist(),
    ):
        warning = counts[str(tile_id)]
        if snr is None:
            warning["null_snr_rows"] += 1
        z_float = float(z)
        if not np.isfinite(z_float):
            warning["nonfinite_z_rows"] += 1
        elif z_float <= 0.0:
            warning["z_le_zero_rows"] += 1

    warnings: list[dict[str, object]] = []
    for task in tasks:
        warning = counts[str(task["tile_id"])]
        if any(warning.values()):
            warnings.append({"path": str(task["path"]), "tile_id": str(task["tile_id"]), **warning})
    return warnings


def warning_summary(warnings: list[dict[str, object]]) -> dict[str, object]:
    """
    Summarize per-file warnings for the provenance block.

    Parameters
    ----------
    warnings : list[dict[str, object]]
        Per-file warning records from the scan pass.

    Returns
    -------
    dict[str, object]
        Counts of files with each warning class plus a capped example
        file list per class.
    """
    by_class: dict[str, list[str]] = {"null_snr": [], "nonfinite_z": [], "z_le_zero": []}
    for warning in warnings:
        if warning["null_snr_rows"]:
            by_class["null_snr"].append(str(warning["path"]))
        if warning["nonfinite_z_rows"]:
            by_class["nonfinite_z"].append(str(warning["path"]))
        if warning["z_le_zero_rows"]:
            by_class["z_le_zero"].append(str(warning["path"]))
    return {
        "files_with_null_snr_rows": len(by_class["null_snr"]),
        "files_with_nonfinite_z_rows": len(by_class["nonfinite_z"]),
        "files_with_z_le_zero_rows": len(by_class["z_le_zero"]),
        "example_files_capped_at": WARNING_FILE_LIST_LIMIT,
        "examples": {name: paths[:WARNING_FILE_LIST_LIMIT] for name, paths in by_class.items()},
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse command-line arguments.

    Parameters
    ----------
    argv : list[str] or None, optional
        Argument vector; defaults to sys.argv[1:].

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="D4+D5 scalar extraction and nuisance baseline")
    parser.add_argument("--corpus-root", type=Path, default=CORPUS_ROOT, help="corpus root directory (read-only)")
    parser.add_argument("--work-dir", type=Path, default=WORK_DIR, help="audit work directory for outputs")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="maximum worker processes")
    parser.add_argument("--chunk-size", type=int, default=250, help="files per checkpoint chunk")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """
    Run the full D4+D5 pipeline: fence, scan, assemble, validate, summarize.

    Parameters
    ----------
    argv : list[str] or None, optional
        Command-line arguments; defaults to sys.argv[1:].
    """
    setup_logging()
    args = parse_args(argv)
    total_start = time.perf_counter()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    files = discover_corpus_files(args.corpus_root)
    if not files:
        logger.error("no corpus files found under %s", args.corpus_root)
        raise SystemExit(1)
    logger.info("traversal plan: %d files, columns %s (wavelength never read)", len(files), list(SCALAR_COLUMNS))
    run_config = build_scalars_run_config(
        args.corpus_root,
        files,
        columns=SCALAR_COLUMNS,
        definitions_version=NUISANCE_DEFINITIONS_VERSION,
        chunk_size=args.chunk_size,
    )
    fence_action = apply_run_fence(args.work_dir, run_config)
    if fence_action == FENCE_REUSE:
        logger.info("run config fence: scalars parts match current configuration, reusing checkpoints")
    else:
        logger.warning("run config fence: %s scalars parts", fence_action)
    inventory_counts = load_inventory_row_counts(args.work_dir)
    expected_total_rows = sum(inventory_counts.values()) if inventory_counts is not None else None
    tasks = build_tasks(files)
    chunks = plan_chunks(tasks, args.chunk_size)
    expected_rows = expected_part_rows(chunks, inventory_counts)
    _, errors, _, scan_seconds = run_scan(chunks, args.work_dir, args.workers, expected_rows)
    if errors:
        for error in errors:
            logger.error("unresolvable open failure: %s (%s)", error["path"], error["error"])
        raise SystemExit(f"aborting: {len(errors)} file(s) failed to read; every corpus row is required")
    assembly_start = time.perf_counter()
    parts_table = assemble_table(args.work_dir, chunks)
    validate_assembly(parts_table, expected_total_rows)
    warnings = warnings_from_assembled_table(parts_table, tasks)
    final_table = project_per_object_table(parts_table)
    final_path = args.work_dir / PER_OBJECT_FILENAME
    write_part(final_table, final_path)
    assembly_seconds = time.perf_counter() - assembly_start
    summary_start = time.perf_counter()
    z_values = final_table.column("z").to_pylist()
    snr_values = [None if value is None else float(value) for value in final_table.column("snr_proxy").to_pylist()]
    masked_values = [float(value) for value in final_table.column("masked_fraction").to_pylist()]
    null_snr_count = sum(1 for value in snr_values if value is None)
    provenance = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "package_versions": {"pyarrow": pa.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        "command": shlex.join(sys.argv),
        "workers": int(args.workers),
        "chunk_size": int(args.chunk_size),
        "corpus_root": str(args.corpus_root),
        "files_discovered": int(len(files)),
        "files_sha256": run_config["files_sha256"],
        "columns_read": list(SCALAR_COLUMNS),
        "wavelength_read": False,
        "single_traversal": True,
        "definitions_version": NUISANCE_DEFINITIONS_VERSION,
        "parts_dir": str(part_dir(args.work_dir, SCALARS_PART_KIND)),
        "parts_manifest": str(kind_manifest_path(args.work_dir, SCALARS_PART_KIND)),
        "fence_action": fence_action,
        "inventory_rows_used_for_resume": inventory_counts is not None,
        "expected_total_rows_from_inventory": expected_total_rows,
        "observed_limits": load_observed_limits(args.work_dir),
        "open_failures": {"count": len(errors), "files": errors},
        "warnings": warning_summary(warnings),
        "outputs": {
            "per_object_raw_parquet": str(final_path),
            "redshift_summary": str(args.work_dir / REDSHIFT_SUMMARY_FILENAME),
        },
        "timing_seconds": {},
    }
    summary = build_redshift_summary(
        z_values=z_values,
        snr_values=snr_values,
        masked_fraction_values=masked_values,
        provenance=provenance,
    )
    summary["provenance"]["timing_seconds"] = {
        "scan": round(scan_seconds, 3),
        "assembly": round(assembly_seconds, 3),
        "summary": round(time.perf_counter() - summary_start, 3),
        "total": round(time.perf_counter() - total_start, 3),
    }
    summary_path = args.work_dir / REDSHIFT_SUMMARY_FILENAME
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    redshift = summary["redshift"]
    nuisance = summary["nuisance"]
    logger.info("wrote %d per-object rows -> %s", final_table.num_rows, final_path)
    logger.info("wrote summary -> %s", summary_path)
    logger.info(
        "headline: rows=%d z_min=%.4f z_median=%.4f z_max=%.4f snr_null=%d snr_median=%.4f masked_frac_median=%.4f",
        final_table.num_rows,
        redshift["min"],
        redshift["quantiles"]["0.5"],
        redshift["max"],
        null_snr_count,
        nuisance["snr_proxy"]["quantiles"]["0.5"],
        nuisance["masked_fraction"]["quantiles"]["0.5"],
    )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
