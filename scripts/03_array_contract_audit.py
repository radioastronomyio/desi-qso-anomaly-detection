#!/usr/bin/env python3
"""
Script Name  : 03_array_contract_audit.py
Description  : D3 sampled array contract audit over the DESI DR1 QSO Parquet corpus.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Audits a deterministic systematic sample of corpus tiles and rows against
the array contract: length alignment across wavelength/flux/ivar/mask_array,
wavelength monotonicity (arm-joint breaks), B/R and R/Z arm overlap extent
in Angstroms with boundary locations, exact-duplicate and near-duplicate
wavelength values, ivar negative/non-finite/zero sanity, mask value
inventory, and observed wavelength/array-length distributions. Sampling is
seeded (default 20260815), stride-based over the tile-sorted corpus, exactly
rows-per-tile rows per file, and fully documented in the summary so every
figure is reproducible. The scan is parallel across worker processes and
checkpointed as part files under work/parts/array_audit/ with a run-config
manifest (corpus root, sampling parameters including seed/stride/
rows-per-tile, selected file-list fingerprint) fencing the parts directory
against stale cross-configuration reuse. The corpus is strictly read-only.
Emits work/array_audit.parquet (per-row flags keyed by row_uid for the
manifest join) and work/array_audit_summary.json (all counts, rates, the
measured merge/de-dup policy recommendation for the downstream loader, and
observed wavelength limits for the redshift bin table).

Usage
-----
    python scripts/03_array_contract_audit.py [--corpus-root PATH] [--work-dir PATH]
        [--workers N] [--seed N] [--target-tiles N] [--rows-per-tile N]
        [--chunk-size N] [--neardup-tol F]

Examples
--------
    python scripts/03_array_contract_audit.py
        Audit the default sample (seed 20260815, ~512 tiles, 64 rows/tile)
        of the default corpus into the default work directory, resuming
        from any existing part-file checkpoints.

    python scripts/03_array_contract_audit.py --rows-per-tile 16
        Audit with 16 rows per tile; the fence prunes checkpoints from the
        default configuration first.
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
    ARRAY_AUDIT_COLUMNS,
    ARRAY_AUDIT_FILENAME,
    ARRAY_AUDIT_PART_KIND,
    ARRAY_AUDIT_SUMMARY_FILENAME,
    ARRAY_MASK_TOP_N,
    ARRAY_NEARDUP_TOLERANCE_ANGSTROM,
    ARRAY_ROWS_PER_TILE,
    ARRAY_SAMPLING_SEED,
    ARRAY_TARGET_TILES,
    CORPUS_ROOT,
    WORK_DIR,
    array_audit_part_table,
    audit_chunk,
    build_array_audit_run_config,
    build_array_audit_summary,
    discover_corpus_files,
    kind_manifest_path,
    manifest_matches,
    part_dir,
    part_filepath,
    part_is_valid,
    plan_chunks,
    project_array_audit_table,
    read_kind_manifest,
    read_part,
    select_sampled_tiles,
    tile_id_from_filename,
    write_kind_manifest,
    write_part,
)

# =============================================================================
# Configuration
# =============================================================================

logger = logging.getLogger("dqad_audit.03_array_contract_audit")

DEFAULT_WORKERS = min(32, os.cpu_count() or 4)

FENCE_REUSE = "reused"
FENCE_INITIALIZED = "initialized"
FENCE_PRUNED_PARTS = "pruned_parts"

INVENTORY_FILENAME = "inventory.parquet"
PROGRESS_EVERY = 50
SHORT_FILE_LIST_LIMIT = 50

# =============================================================================
# Functions
# =============================================================================


def setup_logging() -> None:
    """Configure INFO-level logging for the audit run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def apply_sampling_fence(work_dir: Path, current: dict[str, object]) -> str:
    """
    Fence the array-audit parts directory against cross-configuration reuse.

    Compares parts/array_audit/run_config.json against the current run
    configuration (corpus root, sampling parameters including seed, stride,
    rows per tile, near-duplicate tolerance, chunk size, and the selected
    file-list fingerprint). On mismatch all array_audit part files are
    pruned; the current manifest is then written.

    Parameters
    ----------
    work_dir : Path
        Audit work directory.
    current : dict[str, object]
        Run configuration from build_array_audit_run_config.

    Returns
    -------
    str
        One of reused, initialized, pruned_parts.
    """
    recorded = read_kind_manifest(work_dir, ARRAY_AUDIT_PART_KIND)
    if manifest_matches(recorded, current):
        return FENCE_REUSE
    action = FENCE_INITIALIZED if recorded is None else FENCE_PRUNED_PARTS
    for stale in part_dir(work_dir, ARRAY_AUDIT_PART_KIND).glob("*.parquet"):
        stale.unlink()
    write_kind_manifest(work_dir, ARRAY_AUDIT_PART_KIND, current)
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


def build_tasks(
    selected: list[Path],
    seed: int,
    rows_per_tile: int,
    neardup_tolerance: float,
) -> list[dict[str, object]]:
    """
    Build one scan task per selected tile file, in selected order.

    Parameters
    ----------
    selected : list[Path]
        Selected tile files (tile-sorted order).
    seed : int
        Master sampling seed.
    rows_per_tile : int
        Exact rows to sample per tile when the file allows it.
    neardup_tolerance : float
        Near-duplicate tolerance in Angstroms.

    Returns
    -------
    list[dict[str, object]]
        Task dicts for audit_chunk, carrying each tile's child-seed
        position so per-tile row starts are reproducible.
    """
    n_tiles = len(selected)
    return [
        {
            "path": str(path),
            "tile_id": tile_id_from_filename(path.name),
            "seed": int(seed),
            "tile_index": tile_index,
            "n_tiles": n_tiles,
            "rows_per_tile": int(rows_per_tile),
            "neardup_tolerance": float(neardup_tolerance),
        }
        for tile_index, path in enumerate(selected)
    ]


def expected_part_rows(
    chunks: list[list[dict[str, object]]],
    inventory_counts: dict[str, int] | None,
    rows_per_tile: int,
) -> list[int | None]:
    """
    Compute the expected row count of each chunk's part file.

    Parameters
    ----------
    chunks : list[list[dict[str, object]]]
        Planned task chunks over the selected tile list.
    inventory_counts : dict[str, int] or None
        Per-file row counts from the D1 inventory.
    rows_per_tile : int
        Rows sampled per tile.

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
            total += min(rows_per_tile, n_rows)
        expected.append(total if complete else None)
    return expected


def run_audit(
    chunks: list[list[dict[str, object]]],
    work_dir: Path,
    workers: int,
    expected_rows: list[int | None],
) -> tuple[int, list[dict[str, str]], float]:
    """
    Audit all pending chunks in parallel and write checkpoint part files.

    Parameters
    ----------
    chunks : list[list[dict[str, object]]]
        Planned task chunks over the selected tile list.
    work_dir : Path
        Audit work directory for parts/array_audit/.
    workers : int
        Maximum worker processes.
    expected_rows : list[int or None]
        Expected row count per chunk from expected_part_rows.

    Returns
    -------
    tuple[int, list[dict[str, str]], float]
        Count of files audited this pass, per-file open/read errors, and
        wall seconds spent auditing (checkpointed chunks are skipped).
    """
    total_files = sum(len(chunk) for chunk in chunks)
    pending = [
        index
        for index in range(len(chunks))
        if expected_rows[index] is None
        or not part_is_valid(part_filepath(work_dir, ARRAY_AUDIT_PART_KIND, index), expected_rows=expected_rows[index])
    ]
    skipped = total_files - sum(len(chunks[index]) for index in pending)
    logger.info(
        "audit plan: %d tiles in %d chunks, %d pending, %d already checkpointed",
        total_files,
        len(chunks),
        len(pending),
        skipped,
    )
    errors: list[dict[str, str]] = []
    start = time.perf_counter()
    if not pending:
        return skipped, errors, time.perf_counter() - start
    completed = skipped
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(audit_chunk, chunks[index]): index for index in pending}
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            for error in result["errors"]:
                logger.warning("open failure: %s (%s)", error["path"], error["error"])
            errors.extend(result["errors"])
            if result["errors"]:
                continue
            write_part(array_audit_part_table(result["rows"]), part_filepath(work_dir, ARRAY_AUDIT_PART_KIND, index))
            chunk_len = len(chunks[index])
            if completed // PROGRESS_EVERY < (completed + chunk_len) // PROGRESS_EVERY:
                logger.info("progress: %d/%d tiles audited", completed + chunk_len, total_files)
            completed += chunk_len
    elapsed = time.perf_counter() - start
    logger.info("audit pass complete: %d tiles audited in %.1fs", completed - skipped, elapsed)
    return completed - skipped, errors, elapsed


def assemble_table(work_dir: Path, chunks: list[list[dict[str, object]]]) -> pa.Table:
    """
    Assemble the per-row audit table strictly from the current chunk plan.

    Only parts/array_audit/NNNN.parquet for indices 0..len(chunks)-1 are
    read; stray files beyond the plan are ignored.

    Parameters
    ----------
    work_dir : Path
        Audit work directory holding parts/array_audit/.
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
        path = part_filepath(work_dir, ARRAY_AUDIT_PART_KIND, index)
        if not path.is_file():
            raise RuntimeError(f"missing planned array_audit part: {path}")
        parts.append(read_part(path))
    table = pa.concat_tables(parts)
    table = table.take(pc.sort_indices(table, sort_keys=[("tile_id", "ascending"), ("row_idx", "ascending")]))
    logger.info("assembled array audit rows: %d", table.num_rows)
    return table


def short_file_accounting(
    inventory_counts: dict[str, int] | None,
    selected: list[Path],
    rows_per_tile: int,
) -> dict[str, object]:
    """
    Account for selected files holding fewer rows than rows_per_tile.

    Parameters
    ----------
    inventory_counts : dict[str, int] or None
        Per-file row counts from the D1 inventory.
    selected : list[Path]
        Selected tile files.
    rows_per_tile : int
        Rows sampled per tile.

    Returns
    -------
    dict[str, object]
        Block with the count of short files, their tile ids (capped), and
        the rows-per-tile target for the achieved-count rationale.
    """
    if inventory_counts is None:
        return {"count": None, "note": "D1 inventory unavailable; short-file count not precomputed"}
    short_tiles = [
        tile_id_from_filename(path.name)
        for path in selected
        if inventory_counts.get(str(path), rows_per_tile) < rows_per_tile
    ]
    return {
        "count": len(short_tiles),
        "tiles": short_tiles[:SHORT_FILE_LIST_LIMIT],
        "note": (
            f"{len(short_tiles)} selected files hold fewer than {rows_per_tile} rows and contribute all their "
            "rows; every other file contributes exactly rows_per_tile"
        ),
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
    parser = argparse.ArgumentParser(description="D3 sampled array contract audit for the DESI DR1 QSO corpus")
    parser.add_argument("--corpus-root", type=Path, default=CORPUS_ROOT, help="corpus root directory (read-only)")
    parser.add_argument("--work-dir", type=Path, default=WORK_DIR, help="audit work directory for outputs")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="maximum worker processes")
    parser.add_argument("--seed", type=int, default=ARRAY_SAMPLING_SEED, help="master sampling seed")
    parser.add_argument("--target-tiles", type=int, default=ARRAY_TARGET_TILES, help="approximate tiles to sample")
    parser.add_argument("--rows-per-tile", type=int, default=ARRAY_ROWS_PER_TILE, help="exact rows per tile")
    parser.add_argument("--chunk-size", type=int, default=250, help="files per checkpoint chunk")
    parser.add_argument(
        "--neardup-tol",
        type=float,
        default=ARRAY_NEARDUP_TOLERANCE_ANGSTROM,
        help="near-duplicate wavelength tolerance in Angstroms",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """
    Run the full D3 audit pipeline: sample, fence, scan, assemble, summarize.

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
    selected, sampling_recipe = select_sampled_tiles(files, args.target_tiles, args.seed)
    logger.info(
        "sampling: %d files discovered, stride %d, start %d, %d tiles selected, %d rows/tile target",
        len(files),
        sampling_recipe["stride"],
        sampling_recipe["start"],
        len(selected),
        args.rows_per_tile,
    )
    run_config = build_array_audit_run_config(
        args.corpus_root,
        selected,
        seed=args.seed,
        target_tiles=args.target_tiles,
        stride=sampling_recipe["stride"],
        rows_per_tile=args.rows_per_tile,
        neardup_tolerance=args.neardup_tol,
        chunk_size=args.chunk_size,
    )
    fence_action = apply_sampling_fence(args.work_dir, run_config)
    if fence_action == FENCE_REUSE:
        logger.info("run config fence: array_audit parts match current configuration, reusing checkpoints")
    else:
        logger.warning("run config fence: %s array_audit parts", fence_action)
    inventory_counts = load_inventory_row_counts(args.work_dir)
    tasks = build_tasks(selected, args.seed, args.rows_per_tile, args.neardup_tol)
    chunks = plan_chunks(tasks, args.chunk_size)
    expected_rows = expected_part_rows(chunks, inventory_counts, args.rows_per_tile)
    _, errors, audit_seconds = run_audit(chunks, args.work_dir, args.workers, expected_rows)
    if errors:
        for error in errors:
            logger.error("unresolvable read failure: %s (%s)", error["path"], error["error"])
        raise SystemExit(f"aborting: {len(errors)} file(s) failed to read; every selected file is required")
    assembly_start = time.perf_counter()
    parts_table = assemble_table(args.work_dir, chunks)
    final_table = project_array_audit_table(parts_table)
    final_path = args.work_dir / ARRAY_AUDIT_FILENAME
    write_part(final_table, final_path)
    assembly_seconds = time.perf_counter() - assembly_start
    sampling = {
        **sampling_recipe,
        "rows_per_tile": int(args.rows_per_tile),
        "target_rows": int(len(selected) * args.rows_per_tile),
        "short_files": short_file_accounting(inventory_counts, selected, args.rows_per_tile),
        "columns_read": list(ARRAY_AUDIT_COLUMNS),
        "corpus_read_only": True,
    }
    provenance = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "package_versions": {"pyarrow": pa.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        "command": shlex.join(sys.argv if argv is None else [str(Path(__file__).resolve()), *argv]),
        "workers": int(args.workers),
        "chunk_size": int(args.chunk_size),
        "corpus_root": str(args.corpus_root),
        "files_discovered": int(len(files)),
        "files_selected": int(len(selected)),
        "selected_files_sha256": run_config["files_sha256"],
        "parts_dir": str(part_dir(args.work_dir, ARRAY_AUDIT_PART_KIND)),
        "parts_manifest": str(kind_manifest_path(args.work_dir, ARRAY_AUDIT_PART_KIND)),
        "inventory_rows_used_for_resume": inventory_counts is not None,
        "open_failures": {"count": len(errors), "files": errors},
        "outputs": {
            "array_audit_parquet": str(final_path),
            "array_audit_summary": str(args.work_dir / ARRAY_AUDIT_SUMMARY_FILENAME),
        },
        "auxiliary_part_columns_note": (
            "part files carry auxiliary break-boundary wavelength columns and a second break index used by "
            "the summary; these are dropped from the final per-row table"
        ),
    }
    summary = build_array_audit_summary(
        parts_table,
        sampling=sampling,
        fence_action=fence_action,
        provenance=provenance,
        neardup_tolerance=args.neardup_tol,
        mask_top_n=ARRAY_MASK_TOP_N,
    )
    summary["provenance"]["timing_seconds"] = {
        "audit": round(audit_seconds, 3),
        "assembly": round(assembly_seconds, 3),
        "total": round(time.perf_counter() - total_start, 3),
    }
    summary_path = args.work_dir / ARRAY_AUDIT_SUMMARY_FILENAME
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %d per-row flags -> %s", final_table.num_rows, final_path)
    logger.info("wrote summary -> %s", summary_path)
    alignment = summary["alignment"]
    monotonicity = summary["monotonicity"]
    arm = summary["arm_overlap"]
    ivar = summary["ivar"]
    wavelength = summary["wavelength_range"]
    logger.info(
        "headline: rows=%d alignment_failures=%d mono_break_rows=%d overlap_br=%.1fA overlap_rz=%.1fA "
        "dup_rows=%d neardup_rows=%d ivar_zero_frac=%.6f wave=[%.1f, %.1f]",
        summary["sampling"]["rows_selected"],
        alignment["misaligned_rows"],
        monotonicity["rows_with_breaks"],
        arm["br_boundary"]["overlap_width_angstrom"].get("median", float("nan")),
        arm["rz_boundary"]["overlap_width_angstrom"].get("median", float("nan")),
        arm["duplicates"]["rows_with_duplicates"],
        arm["near_duplicates"]["rows_with_near_duplicates"],
        ivar["overall_zero_fraction"],
        wavelength["global_min_angstrom"],
        wavelength["global_max_angstrom"],
    )
    logger.info(
        "merge policy: required=%s policy=%s",
        arm["merge_policy"]["required"],
        arm["merge_policy"]["policy"],
    )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
