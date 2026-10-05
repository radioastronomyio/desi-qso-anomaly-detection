#!/usr/bin/env python3
"""
Script Name  : 02_identity_duplication.py
Description  : D2 archive-wide identity and duplication analysis over target_ids.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Reads the D1 output work/target_ids.parquet (tile_id, row_idx, target_id;
one row per corpus row) and computes the archive-wide identity picture:
exact totals and unique/duplicate/excess counts, the repeat-count
distribution of target_ids, the within-tile versus cross-tile duplication
split with the tiles-spanned distribution, duplication rates, the gap
against the DR1 Main Survey QSO population reference (explained with the
converter's selection filter quoted verbatim from the frozen converter
source), and the holdout verdict with the expected leakage of a naive
90/10 row split. All count arithmetic is exact integer math; only rates,
fractions, and the leakage expectation are floats. Emits
work/identity_summary.json.

Usage
-----
    python scripts/02_identity_duplication.py [--work-dir PATH]
        [--converter-source PATH] [--population-reference N]
        [--holdout-fraction F]

Examples
--------
    python scripts/02_identity_duplication.py
        Analyze the default work directory's target_ids.parquet and write
        identity_summary.json next to it.

    python scripts/02_identity_duplication.py --holdout-fraction 0.2
        Same analysis with the naive-split leakage quantified for 80/20.
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import platform
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dqad_audit import (
    CONVERTER_SOURCE_PATH,
    DR1_MAIN_QSO_POPULATION_REFERENCE,
    IDENTITY_SUMMARY_FILENAME,
    NAIVE_HOLDOUT_FRACTION,
    WORK_DIR,
    build_identity_summary,
    converter_filter_block,
    repeat_distribution,
    tile_duplication_split,
)

# =============================================================================
# Configuration
# =============================================================================

logger = logging.getLogger("dqad_audit.02_identity_duplication")

TARGET_IDS_FILENAME = "target_ids.parquet"
INVENTORY_SUMMARY_FILENAME = "inventory_summary.json"
DISTRIBUTION_LOG_LIMIT = 15

# =============================================================================
# Functions
# =============================================================================


def setup_logging() -> None:
    """Configure INFO-level logging for the analysis run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def load_target_ids(work_dir: Path) -> tuple[list[str], list[int], int]:
    """
    Read the archive-wide target_ids table into aligned Python columns.

    Parameters
    ----------
    work_dir : Path
        Audit work directory holding target_ids.parquet.

    Returns
    -------
    tuple[list[str], list[int], int]
        tile_id column, target_id column, and row count.
    """
    path = work_dir / TARGET_IDS_FILENAME
    table = pq.read_table(path)
    if table.schema.names != ["tile_id", "row_idx", "target_id"]:
        raise RuntimeError(f"unexpected target_ids schema {table.schema.names} in {path}")
    total_rows = int(table.num_rows)
    tile_ids = table.column("tile_id").to_pylist()
    target_ids = table.column("target_id").to_pylist()
    logger.info("loaded %d rows from %s", total_rows, path)
    return tile_ids, target_ids, total_rows


def inventory_cross_check(work_dir: Path, total_rows: int) -> dict[str, object]:
    """
    Cross-check the loaded row count against the D1 inventory summary.

    Parameters
    ----------
    work_dir : Path
        Audit work directory holding inventory_summary.json.
    total_rows : int
        Row count of the loaded target_ids table.

    Returns
    -------
    dict[str, object]
        Provenance block with the D1 totals and their agreement flag;
        records the absence of the file when missing.
    """
    path = work_dir / INVENTORY_SUMMARY_FILENAME
    if not path.is_file():
        return {"path": str(path), "present": False, "rows_agree": None}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    d1_rows = int(data["total_actual_rows"])
    return {
        "path": str(path),
        "present": True,
        "total_files_scanned": int(data["total_files_scanned"]),
        "open_failures": int(data["open_failures"]["count"]),
        "total_actual_rows": d1_rows,
        "rows_agree": d1_rows == total_rows,
    }


def log_distribution(distribution: dict[int, int]) -> None:
    """
    Log the repeat-count distribution up to a cut, then the tail if truncated.

    Parameters
    ----------
    distribution : dict[int, int]
        Repeat-count histogram (k -> number of target_ids).
    """
    items = sorted(distribution.items())
    shown = items[:DISTRIBUTION_LOG_LIMIT]
    rendered = ", ".join(f"k={k}: {n}" for k, n in shown)
    if len(items) > DISTRIBUTION_LOG_LIMIT:
        tail = items[DISTRIBUTION_LOG_LIMIT:]
        rendered += ", ... tail: " + ", ".join(f"k={k}: {n}" for k, n in tail)
    logger.info("repeat-count distribution: %s", rendered)


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
    parser = argparse.ArgumentParser(description="D2 archive-wide identity and duplication analysis")
    parser.add_argument("--work-dir", type=Path, default=WORK_DIR, help="audit work directory for input and output")
    parser.add_argument(
        "--converter-source",
        type=Path,
        default=CONVERTER_SOURCE_PATH,
        help="frozen converter source file quoted for the filter explanation",
    )
    parser.add_argument(
        "--population-reference",
        type=int,
        default=DR1_MAIN_QSO_POPULATION_REFERENCE,
        help="DR1 Main Survey QSO population reference figure",
    )
    parser.add_argument(
        "--holdout-fraction",
        type=float,
        default=NAIVE_HOLDOUT_FRACTION,
        help="holdout share used for the naive-split leakage quantification",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """
    Run the D2 identity analysis: load, compute, assemble, and emit the summary.

    Parameters
    ----------
    argv : list[str] or None, optional
        Command-line arguments; defaults to sys.argv[1:].
    """
    setup_logging()
    args = parse_args(argv)
    start = time.perf_counter()
    tile_ids, target_ids, total_rows = load_target_ids(args.work_dir)
    distribution = repeat_distribution(target_ids)
    unique_target_ids = sum(distribution.values())
    duplicated_ids = sum(n for k, n in distribution.items() if k >= 2)
    excess_rows = total_rows - unique_target_ids
    logger.info(
        "totals: rows=%d unique=%d duplicated_ids=%d excess_rows=%d max_k=%d",
        total_rows,
        unique_target_ids,
        duplicated_ids,
        excess_rows,
        max(distribution, default=0),
    )
    log_distribution(distribution)
    split = tile_duplication_split(tile_ids, target_ids)
    logger.info(
        "tile split: within_tile_duplicated_ids=%d cross_tile_duplicated_ids=%d max_tiles_spanned=%d",
        split["within_tile"]["duplicate_target_id_count"],
        split["cross_tile"]["duplicate_target_id_count"],
        split["cross_tile"]["max_tiles_spanned"],
    )
    concerns = []
    if split["within_tile"]["duplicate_target_id_count"] > 0:
        concerns.append(
            "within-tile target_id duplication detected, contradicting the D1 per-file "
            "uniqueness measurement; investigate before trusting downstream identity"
        )
    converter_filter = converter_filter_block(args.converter_source)
    logger.info(
        "converter filter (line %d): %s",
        converter_filter["selection_filter"]["line"],
        converter_filter["selection_filter"]["code"],
    )
    provenance = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "package_versions": {"pyarrow": pa.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        "command": shlex.join(sys.argv),
        "inputs": {
            "target_ids_parquet": {
                "path": str(args.work_dir / TARGET_IDS_FILENAME),
                "rows": total_rows,
            },
            "inventory_summary": inventory_cross_check(args.work_dir, total_rows),
            "converter_source": {
                "path": str(args.converter_source),
                "selection_filter_line": int(converter_filter["selection_filter"]["line"]),
            },
        },
        "outputs": {"identity_summary": str(args.work_dir / IDENTITY_SUMMARY_FILENAME)},
    }
    summary = build_identity_summary(
        total_rows=total_rows,
        unique_target_ids=unique_target_ids,
        distribution=distribution,
        tile_split=split,
        converter_filter=converter_filter,
        provenance=provenance,
        population_reference=args.population_reference,
        holdout_fraction=args.holdout_fraction,
        concerns=concerns,
    )
    summary["provenance"]["timing_seconds"] = {"total": round(time.perf_counter() - start, 3)}
    summary_path = args.work_dir / IDENTITY_SUMMARY_FILENAME
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote summary -> %s", summary_path)
    gap = summary["gap_accounting"]
    logger.info(
        "gap: unique=%d reference=%d gap=%d gap_fraction=%.6f",
        gap["unique_target_ids"],
        gap["population_reference"],
        gap["gap"],
        gap["gap_fraction"],
    )
    holdout = summary["holdout"]
    logger.info(
        "holdout grouping required=%s; expected leaked ids under naive 90/10 row split: %.2f",
        holdout["required"],
        holdout["expected_leakage_naive_90_10"],
    )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
