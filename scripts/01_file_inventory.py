#!/usr/bin/env python3
"""
Script Name  : 01_file_inventory.py
Description  : Builds the D1 per-file inventory of the DESI DR1 QSO Parquet corpus.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Scans every corpus Parquet file under the (strictly read-only) corpus root
and records tile id, path, size, actual row count, filename-claimed count,
claimed-equals-actual, unique target_id count, column/dtype serialization,
and schema comparison against the reference schema taken from the first
file in sorted order. Open failures are recorded with their error and
never skipped. The scan is parallel across worker processes and checkpointed
as per-chunk part files so interrupted runs resume where they left off.
A run-config manifest (corpus root, chunk size, file-list fingerprint)
fences the parts directory: checkpoints from a different configuration are
pruned before scanning, and assembly reads only the part names the current
chunk plan defines. Outputs work/inventory.parquet, work/target_ids.parquet,
work/reference_schema.json, and work/inventory_summary.json.

Usage
-----
    python scripts/01_file_inventory.py [--corpus-root PATH] [--work-dir PATH]
                                        [--workers N] [--chunk-size N]

Examples
--------
    python scripts/01_file_inventory.py
        Full scan of the default corpus into the default work directory,
        resuming from any existing part-file checkpoints.

    python scripts/01_file_inventory.py --workers 16 --chunk-size 100
        Full scan with at most 16 worker processes and 100 files per chunk.
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
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dqad_audit import (
    CORPUS_ROOT,
    PROGRESS_EVERY,
    WORK_DIR,
    build_run_config,
    corpus_fingerprint,
    corpus_identity,
    discover_corpus_files,
    inventory_table,
    manifest_matches,
    part_dir,
    part_filepath,
    pending_chunk_indices,
    plan_chunks,
    read_part,
    read_parts_manifest,
    scan_chunk,
    serialize_schema,
    target_ids_table,
    write_corpus_snapshot,
    write_part,
    write_parts_manifest,
)

# =============================================================================
# Configuration
# =============================================================================

logger = logging.getLogger("dqad_audit.01_file_inventory")

DEFAULT_WORKERS = min(32, os.cpu_count() or 4)
EXPECTED_TOTAL_CLAIMED = 1_030_934

FENCE_REUSE = "reused"
FENCE_INITIALIZED = "initialized"
FENCE_RESET_UNKNOWN = "reset_unknown"
FENCE_PRUNED_PARTS = "pruned_parts"
FENCE_PRUNED_ALL = "pruned_all"

PART_KINDS = ("inventory", "target_ids")

# =============================================================================
# Functions
# =============================================================================


def setup_logging() -> None:
    """Configure INFO-level logging for the scan run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def apply_run_config_fence(
    work_dir: Path,
    corpus_root: Path,
    files: list[Path],
    chunk_size: int,
) -> str:
    """
    Fence the parts directory against reuse across run configurations.

    Compares the manifest in parts/run_config.json against the current run
    configuration (corpus root, chunk size, file count, file-list
    fingerprint). On mismatch all part files are pruned; when the corpus
    identity (root or fingerprint) changed, or provenance is unknown
    because no manifest exists alongside existing outputs, the snapshot
    and reference schema are deleted too so they are regenerated. The
    current manifest is then written.

    Parameters
    ----------
    work_dir : Path
        Audit work directory.
    corpus_root : Path
        Corpus root directory.
    files : list[Path]
        Sorted corpus file listing.
    chunk_size : int
        Files per checkpoint chunk.

    Returns
    -------
    str
        One of reused, initialized, reset_unknown, pruned_parts, pruned_all.
    """
    current = build_run_config(corpus_root, files, chunk_size)
    recorded = read_parts_manifest(work_dir)
    if manifest_matches(recorded, current):
        return FENCE_REUSE
    same_corpus = recorded is not None and corpus_identity(recorded) == corpus_identity(current)
    if same_corpus:
        action = FENCE_PRUNED_PARTS
    else:
        had_outputs = any(path.is_file() for kind in PART_KINDS for path in part_dir(work_dir, kind).glob("*.parquet"))
        had_outputs = had_outputs or (work_dir / "corpus_snapshot.parquet").is_file()
        had_outputs = had_outputs or (work_dir / "reference_schema.json").is_file()
        if recorded is None:
            action = FENCE_RESET_UNKNOWN if had_outputs else FENCE_INITIALIZED
        else:
            action = FENCE_PRUNED_ALL
    for kind in PART_KINDS:
        directory = part_dir(work_dir, kind)
        for stale in directory.glob("*.parquet"):
            stale.unlink()
    if action in (FENCE_RESET_UNKNOWN, FENCE_PRUNED_ALL):
        (work_dir / "corpus_snapshot.parquet").unlink(missing_ok=True)
        (work_dir / "reference_schema.json").unlink(missing_ok=True)
    write_parts_manifest(work_dir, current)
    return action


def take_snapshot(files: list[Path], work_dir: Path) -> float:
    """
    Write the pre-scan corpus integrity snapshot unless one already exists.

    Parameters
    ----------
    files : list[Path]
        Sorted corpus file listing.
    work_dir : Path
        Audit work directory.

    Returns
    -------
    float
        Wall seconds spent statting and writing the snapshot.
    """
    snapshot_path = work_dir / "corpus_snapshot.parquet"
    start = time.perf_counter()
    if snapshot_path.is_file():
        logger.info("corpus snapshot already exists, preserving pre-scan state: %s", snapshot_path)
        return time.perf_counter() - start
    n_rows = write_corpus_snapshot(files, snapshot_path)
    elapsed = time.perf_counter() - start
    logger.info("wrote corpus snapshot: %d files -> %s", n_rows, snapshot_path)
    return elapsed


def load_or_create_reference(
    files: list[Path],
    work_dir: Path,
) -> tuple[list[tuple[str, str]], str]:
    """
    Load the reference schema from disk or derive it from the first openable file.

    Parameters
    ----------
    files : list[Path]
        Sorted corpus file listing.
    work_dir : Path
        Audit work directory holding reference_schema.json.

    Returns
    -------
    tuple[list[tuple[str, str]], str]
        Reference (name, type) pairs and the absolute path of the reference file.
    """
    reference_path = work_dir / "reference_schema.json"
    if reference_path.is_file():
        with reference_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        pairs = [(column["name"], column["type"]) for column in data["columns"]]
        logger.info("loaded reference schema from %s (reference file %s)", reference_path, data["reference_file"])
        return pairs, data["reference_file"]
    reference_file: Path | None = None
    schema: pa.Schema | None = None
    for candidate in files:
        try:
            schema = pq.ParquetFile(candidate).schema_arrow
            reference_file = candidate
            break
        except Exception as exc:
            logger.warning("reference candidate failed to open, trying next: %s (%s)", candidate, exc)
    if schema is None or reference_file is None:
        raise RuntimeError("no corpus file could be opened to derive the reference schema")
    payload = {
        "reference_file": str(reference_file),
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "columns": serialize_schema(schema),
    }
    reference_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    pairs = [(column["name"], column["type"]) for column in payload["columns"]]
    logger.info("wrote reference schema from %s -> %s (%d columns)", reference_file, reference_path, len(pairs))
    return pairs, str(reference_file)


def run_scan(
    chunks: list[list[Path]],
    work_dir: Path,
    reference_pairs: list[tuple[str, str]],
    workers: int,
) -> float:
    """
    Scan all pending chunks in parallel and write checkpoint part files.

    Parameters
    ----------
    chunks : list[list[Path]]
        Planned chunks over the sorted file list.
    work_dir : Path
        Audit work directory for parts/.
    reference_pairs : list[tuple[str, str]]
        Reference schema pairs.
    workers : int
        Maximum worker processes.

    Returns
    -------
    float
        Wall seconds spent scanning (checkpointed chunks are skipped).
    """
    total_files = sum(len(chunk) for chunk in chunks)
    pending = pending_chunk_indices(chunks, work_dir)
    skipped = total_files - sum(len(chunks[index]) for index in pending)
    logger.info(
        "scan plan: %d files in %d chunks (chunk_size variable), %d pending, %d already checkpointed",
        total_files,
        len(chunks),
        len(pending),
        skipped,
    )
    start = time.perf_counter()
    if not pending:
        return time.perf_counter() - start
    completed = skipped
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(scan_chunk, chunks[index], reference_pairs): index for index in pending}
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            write_part(inventory_table(result["inventory"]), part_filepath(work_dir, "inventory", index))
            write_part(target_ids_table(result["target_ids"]), part_filepath(work_dir, "target_ids", index))
            for row in result["inventory"]:
                if row["open_error"]:
                    logger.warning("open failure: %s (%s)", row["path"], row["open_error"])
                elif not row["schema_matches_reference"]:
                    logger.warning("schema deviation: %s (%s)", row["path"], row["schema_deviation"])
            mismatches = sum(
                1 for row in result["inventory"] if not row["open_error"] and not row["claimed_equals_actual"]
            )
            if mismatches:
                logger.warning("claimed != actual in %d file(s) in chunk %04d", mismatches, index)
            chunk_len = len(chunks[index])
            if completed // PROGRESS_EVERY < (completed + chunk_len) // PROGRESS_EVERY:
                logger.info("progress: %d/%d files scanned", completed + chunk_len, total_files)
            completed += chunk_len
    elapsed = time.perf_counter() - start
    logger.info("scan pass complete: %d files scanned in %.1fs", completed - skipped, elapsed)
    return elapsed


def assemble_outputs(work_dir: Path, chunks: list[list[Path]]) -> tuple[pa.Table, pa.Table]:
    """
    Assemble final tables strictly from the current chunk plan's part names.

    Only parts/inventory/NNNN.parquet and parts/target_ids/NNNN.parquet for
    indices 0..len(chunks)-1 are read; stray files beyond the plan are
    ignored. Each inventory part must carry exactly its chunk's row count.

    Parameters
    ----------
    work_dir : Path
        Audit work directory holding parts/.
    chunks : list[list[Path]]
        Planned chunks the parts must correspond to.

    Returns
    -------
    tuple[pa.Table, pa.Table]
        Inventory table sorted by path and target_ids table sorted by
        (tile_id, row_idx).

    Raises
    ------
    RuntimeError
        If a planned part file is missing or an inventory part's row count
        does not match its chunk length.
    """
    inventory_parts: list[pa.Table] = []
    for index, chunk in enumerate(chunks):
        path = part_filepath(work_dir, "inventory", index)
        if not path.is_file():
            raise RuntimeError(f"missing planned inventory part: {path}")
        table = read_part(path)
        if table.num_rows != len(chunk):
            raise RuntimeError(
                f"inventory part {path.name} has {table.num_rows} rows, expected {len(chunk)} for the current plan"
            )
        inventory_parts.append(table)
    inventory = pa.concat_tables(inventory_parts)
    inventory = inventory.take(pc.sort_indices(inventory, sort_keys=[("path", "ascending")]))
    pq.write_table(inventory, work_dir / "inventory.parquet")
    target_ids_parts: list[pa.Table] = []
    for index in range(len(chunks)):
        path = part_filepath(work_dir, "target_ids", index)
        if not path.is_file():
            raise RuntimeError(f"missing planned target_ids part: {path}")
        target_ids_parts.append(read_part(path))
    target_ids = pa.concat_tables(target_ids_parts)
    target_ids = target_ids.take(
        pc.sort_indices(target_ids, sort_keys=[("tile_id", "ascending"), ("row_idx", "ascending")])
    )
    pq.write_table(target_ids, work_dir / "target_ids.parquet")
    logger.info(
        "assembled outputs: inventory.parquet (%d rows), target_ids.parquet (%d rows)",
        inventory.num_rows,
        target_ids.num_rows,
    )
    return inventory, target_ids


def build_summary(
    inventory: pa.Table,
    target_ids_rows: int,
    reference_file: str,
    timing: dict[str, float],
    run_config: dict[str, int | str],
    corpus_root: Path,
    work_dir: Path,
    files_discovered: int,
) -> dict[str, object]:
    """
    Aggregate the assembled inventory into the run summary dictionary.

    Parameters
    ----------
    inventory : pa.Table
        Assembled inventory table.
    target_ids_rows : int
        Row count of the assembled target_ids table.
    reference_file : str
        Absolute path of the reference schema source file.
    timing : dict[str, float]
        Wall seconds by phase (snapshot, scan, assembly, total).
    run_config : dict[str, int | str]
        Workers, chunk size, and chunk count used.
    corpus_root : Path
        Corpus root directory.
    work_dir : Path
        Audit work directory.
    files_discovered : int
        File count found by corpus discovery.

    Returns
    -------
    dict[str, object]
        Summary payload written to inventory_summary.json.
    """
    paths = inventory.column("path").to_pylist()
    open_errors = inventory.column("open_error").to_pylist()
    failures = [{"path": path, "error": error} for path, error in zip(paths, open_errors) if error]
    rows_actual = inventory.column("n_rows_actual").to_pylist()
    total_actual = sum(value for value in rows_actual if value >= 0)
    claims = inventory.column("n_claimed").to_pylist()
    total_claimed = sum(value for value in claims if value >= 0)
    uniques = inventory.column("n_unique_target_id").to_pylist()
    sum_unique = sum(value for value in uniques if value >= 0)
    matches_reference = inventory.column("schema_matches_reference").to_pylist()
    deviations = inventory.column("schema_deviation").to_pylist()
    deviants = [
        {"path": path, "deviation": deviation}
        for path, matches, deviation, error in zip(paths, matches_reference, deviations, open_errors)
        if error == "" and not matches
    ]
    claimed_equals = inventory.column("claimed_equals_actual").to_pylist()
    match_count = sum(1 for flag, error in zip(claimed_equals, open_errors) if error == "" and flag)
    mismatch_count = sum(1 for flag, error in zip(claimed_equals, open_errors) if error == "" and not flag)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "package_versions": {"pyarrow": pa.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        "corpus_root": str(corpus_root),
        "work_dir": str(work_dir),
        "run_configuration": run_config,
        "timing_seconds": {key: round(value, 3) for key, value in timing.items()},
        "files_discovered": files_discovered,
        "total_files_scanned": inventory.num_rows,
        "total_actual_rows": total_actual,
        "total_claimed_rows": total_claimed,
        "actual_minus_claimed_rows": total_actual - total_claimed,
        "sum_unique_target_id_per_file": sum_unique,
        "target_ids_rows": target_ids_rows,
        "claimed_equals_actual_files": {"match": match_count, "mismatch": mismatch_count},
        "open_failures": {"count": len(failures), "files": failures},
        "schema": {"reference_file": reference_file, "n_deviants": len(deviants), "deviants": deviants},
        "claimed_vs_expected_1030934": {
            "total_claimed": total_claimed,
            "expected": EXPECTED_TOTAL_CLAIMED,
            "delta": total_claimed - EXPECTED_TOTAL_CLAIMED,
            "equal": total_claimed == EXPECTED_TOTAL_CLAIMED,
        },
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
    parser = argparse.ArgumentParser(description="D1 per-file inventory scanner for the DESI DR1 QSO corpus")
    parser.add_argument("--corpus-root", type=Path, default=CORPUS_ROOT, help="corpus root directory")
    parser.add_argument("--work-dir", type=Path, default=WORK_DIR, help="audit work directory for outputs")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="maximum worker processes")
    parser.add_argument("--chunk-size", type=int, default=250, help="files per checkpoint chunk")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """
    Run the full D1 inventory pipeline: snapshot, scan, assemble, summarize.

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
    logger.info("discovered %d corpus files under %s", len(files), args.corpus_root)
    fence_action = apply_run_config_fence(args.work_dir, args.corpus_root, files, args.chunk_size)
    if fence_action == FENCE_REUSE:
        logger.info("run config fence: parts directory matches current configuration, reusing checkpoints")
    elif fence_action in (FENCE_PRUNED_PARTS, FENCE_PRUNED_ALL, FENCE_RESET_UNKNOWN):
        logger.warning(
            "run config fence: %s, pruned stale parts (and snapshot/reference where corpus identity changed)",
            fence_action,
        )
    else:
        logger.info("run config fence: initialized parts manifest for this configuration")
    chunks = plan_chunks(files, args.chunk_size)
    snapshot_seconds = take_snapshot(files, args.work_dir)
    reference_pairs, reference_file = load_or_create_reference(files, args.work_dir)
    scan_seconds = run_scan(chunks, args.work_dir, reference_pairs, args.workers)
    assembly_start = time.perf_counter()
    inventory, target_ids = assemble_outputs(args.work_dir, chunks)
    assembly_seconds = time.perf_counter() - assembly_start
    if inventory.num_rows != len(files):
        logger.error("inventory row count %d != discovered files %d", inventory.num_rows, len(files))
        raise SystemExit(1)
    timing = {
        "snapshot": snapshot_seconds,
        "scan": scan_seconds,
        "assembly": assembly_seconds,
        "total": time.perf_counter() - total_start,
    }
    run_config = {
        "workers": args.workers,
        "chunk_size": args.chunk_size,
        "n_chunks": len(chunks),
        "files_sha256": corpus_fingerprint(files, args.corpus_root),
    }
    summary = build_summary(
        inventory,
        target_ids.num_rows,
        reference_file,
        timing,
        run_config,
        args.corpus_root,
        args.work_dir,
        len(files),
    )
    summary_path = args.work_dir / "inventory_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote summary -> %s", summary_path)
    logger.info(
        "totals: files=%d open_failures=%d total_actual_rows=%d total_claimed_rows=%d "
        "sum_unique_target_id=%d schema_deviants=%d",
        summary["total_files_scanned"],
        summary["open_failures"]["count"],
        summary["total_actual_rows"],
        summary["total_claimed_rows"],
        summary["sum_unique_target_id_per_file"],
        summary["schema"]["n_deviants"],
    )
    comparison = summary["claimed_vs_expected_1030934"]
    logger.info(
        "claimed vs expected 1030934: total_claimed=%d delta=%d equal=%s",
        comparison["total_claimed"],
        comparison["delta"],
        comparison["equal"],
    )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
