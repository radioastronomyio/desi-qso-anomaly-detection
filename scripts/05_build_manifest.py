#!/usr/bin/env python3
"""
Script Name  : 05_build_manifest.py
Description  : D6 manifest artifact assembly for the DESI DR1 QSO spectral audit gate.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Assembles the Stage 0 deliverable artifacts from the D1-D5 work outputs
(read-only inputs, no corpus array data read):

1. qso_file_manifest_v1.parquet: the D1 inventory table verbatim (one row
   per corpus file, 10,793 rows), written next to the work directory.
2. qso_object_manifest_v1.parquet: the D4+D5 per-object table LEFT-JOINed
   with the eleven D3 sampled contract-flag columns on row_uid (null on
   unsampled rows; mask_value_counts not carried), sorted by
   (tile_id, row_idx). Hard asserts: row count == the D2 archive-wide
   total (1,030,934), row_uid uniqueness, sampled-join coverage == the
   D3 sampled total (24,793 non-null align_ok).
3. Corpus-untouched verification before anything is written: every corpus
   file is re-statted and joined to the pre-audit corpus_snapshot.parquet
   on path; size_bytes AND mtime_ns must be identical for every file. Any
   mismatch aborts the run (integrity gate).
4. Round-trip validation: both tables are read back with pyarrow;
   schema equality, row-count equality, and 10 seeded spot-check rows
   (seed 20260815, indices recorded) must match the assembled tables.
5. manifest_provenance.json: source versions, input artifact md5s and row
   counts, corpus root and file-list fingerprint, build timestamp, tool
   commit, sampling parameters, per-table row counts and byte sizes, the
   round_trip block, and the corpus_integrity block.

The corpus is strictly read-only (stat calls only in this stage).

Usage
-----
    python scripts/05_build_manifest.py [--corpus-root PATH] [--work-dir PATH]
        [--output-dir PATH] [--seed N] [--spot-rows N]

Examples
--------
    python scripts/05_build_manifest.py
        Assemble the default manifests from the default work directory.
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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

import numpy as np
import pandas as pd
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dqad_audit import (
    ARRAY_AUDIT_FILENAME,
    ARRAY_AUDIT_SUMMARY_FILENAME,
    CORPUS_ROOT,
    FILE_MANIFEST_FILENAME,
    IDENTITY_SUMMARY_FILENAME,
    INVENTORY_SCHEMA,
    MANIFEST_PROVENANCE_FILENAME,
    OBJECT_MANIFEST_FILENAME,
    PER_OBJECT_FILENAME,
    REDSHIFT_SUMMARY_FILENAME,
    SNAPSHOT_FILENAME,
    WORK_DIR,
    build_manifest_provenance,
    compare_corpus_snapshots,
    corpus_fingerprint,
    discover_corpus_files,
    file_md5,
    input_artifact_record,
    join_sampled_flags,
    read_part,
    round_trip_validate,
    snapshot_records,
    validate_object_manifest,
    write_part,
)

# =============================================================================
# Configuration
# =============================================================================

logger = logging.getLogger("dqad_audit.05_build_manifest")

DEFAULT_OUTPUT_DIR = WORK_DIR.parent

INVENTORY_FILENAME = "inventory.parquet"
INVENTORY_SUMMARY_FILENAME = "inventory_summary.json"
TARGET_IDS_FILENAME = "target_ids.parquet"

INTEGRITY_EXAMPLE_LIMIT = 10

# =============================================================================
# Functions
# =============================================================================


def setup_logging() -> None:
    """Configure INFO-level logging for the assembly run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def load_json(path: Path) -> dict[str, object]:
    """
    Load a JSON summary, raising SystemExit when absent or corrupt.

    Parameters
    ----------
    path : Path
        JSON file to read.

    Returns
    -------
    dict[str, object]
        Parsed payload.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"required input {path} missing or corrupt: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"required input {path} is not a JSON object")
    return payload


def git_head(repo_root: Path) -> str:
    """
    Resolve the tool commit (git rev-parse HEAD) for provenance.

    Parameters
    ----------
    repo_root : Path
        Repository root to run in.

    Returns
    -------
    str
        Commit SHA, or "unavailable: <reason>" when git cannot answer.
    """
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"unavailable: {exc}"


def verify_corpus_untouched(corpus_root: Path, snapshot_path: Path) -> dict[str, object]:
    """
    Re-stat the corpus and compare against the pre-audit snapshot.

    Parameters
    ----------
    corpus_root : Path
        Read-only corpus root.
    snapshot_path : Path
        corpus_snapshot.parquet taken before any scan.

    Returns
    -------
    dict[str, object]
        compare_corpus_snapshots block.

    Raises
    ------
    SystemExit
        If any file is missing, extra, or has a changed size or mtime.
    """
    snapshot = read_part(snapshot_path)
    files = discover_corpus_files(corpus_root)
    current = pa.Table.from_pylist(snapshot_records(files), schema=snapshot.schema)
    integrity = compare_corpus_snapshots(snapshot, current)
    if not integrity["unchanged"]:
        logger.error(
            "corpus integrity check FAILED: %d mismatched/missing/extra files",
            integrity["mismatch_count"],
        )
        for record in integrity["mismatches"][:INTEGRITY_EXAMPLE_LIMIT]:
            logger.error("changed: %s %s", record["path"], record["fields"])
        for path in integrity["missing_files"][:INTEGRITY_EXAMPLE_LIMIT]:
            logger.error("missing: %s", path)
        for path in integrity["extra_files"][:INTEGRITY_EXAMPLE_LIMIT]:
            logger.error("extra: %s", path)
        raise SystemExit(
            "corpus modified since the pre-audit snapshot; aborting before any manifest artifact is written"
        )
    logger.info(
        "corpus integrity: %d/%d files unchanged (size_bytes and mtime_ns identical)",
        integrity["files_checked"],
        integrity["files_checked"],
    )
    return integrity


def sampling_block(audit_summary: dict[str, object]) -> dict[str, object]:
    """
    Project the D3 sampling recipe into the manifest provenance block.

    Parameters
    ----------
    audit_summary : dict[str, object]
        array_audit_summary.json payload.

    Returns
    -------
    dict[str, object]
        Seed, method, strides, and achieved selection counts.
    """
    sampling = dict(audit_summary["sampling"])
    return {
        "seed": sampling["seed"],
        "method": sampling["method"],
        "stride": sampling["stride"],
        "start": sampling["start"],
        "target_tiles": sampling["target_tiles"],
        "tiles_selected": sampling["tiles_selected"],
        "rows_per_tile": sampling["rows_per_tile"],
        "rows_selected": sampling["rows_selected"],
        "reproducibility_recipe": sampling["reproducibility_recipe"],
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
    parser = argparse.ArgumentParser(description="D6 manifest artifact assembly")
    parser.add_argument("--corpus-root", type=Path, default=CORPUS_ROOT, help="corpus root directory (read-only)")
    parser.add_argument("--work-dir", type=Path, default=WORK_DIR, help="audit work directory holding D1-D5 outputs")
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="directory for the manifest artifacts"
    )
    parser.add_argument("--seed", type=int, default=20_260_815, help="round-trip spot-check seed")
    parser.add_argument("--spot-rows", type=int, default=10, help="round-trip spot-check rows per table")
    return parser.parse_args(argv)


def publish_validated_set(staged: list[Path], output_dir: Path) -> None:
    """Publish a prevalidated set, restoring prior files on a catchable failure.

    Same-filesystem hard links retain the old generation in the private build
    directory. No published file changes until every backup is available.
    This is a single-writer operation; independent paths are not a filesystem
    transaction against power loss or SIGKILL. Retained builds aid recovery.
    """
    backups: dict[Path, Path] = {}
    for source in staged:
        destination = output_dir / source.name
        if destination.exists():
            backup = source.parent / f"previous-{source.name}"
            backup.hardlink_to(destination)
            backups[destination] = backup
    published: list[tuple[Path, Path]] = []
    try:
        for source in staged:
            destination = output_dir / source.name
            source.replace(destination)
            published.append((source, destination))
    except BaseException:
        for source, destination in reversed(published):
            if destination in backups:
                backups[destination].replace(destination)
            else:
                destination.replace(source)
        raise


def main(argv: list[str] | None = None) -> None:
    """
    Run the D6 assembly: integrity gate, both manifests, round-trip, provenance.

    Parameters
    ----------
    argv : list[str] or None, optional
        Command-line arguments; defaults to sys.argv[1:].
    """
    setup_logging()
    args = parse_args(argv)
    total_start = time.perf_counter()

    inventory_path = args.work_dir / INVENTORY_FILENAME
    per_object_path = args.work_dir / PER_OBJECT_FILENAME
    array_audit_path = args.work_dir / ARRAY_AUDIT_FILENAME
    snapshot_path = args.work_dir / SNAPSHOT_FILENAME
    identity_summary = load_json(args.work_dir / IDENTITY_SUMMARY_FILENAME)
    audit_summary = load_json(args.work_dir / ARRAY_AUDIT_SUMMARY_FILENAME)
    inventory_summary = load_json(args.work_dir / INVENTORY_SUMMARY_FILENAME)
    load_json(args.work_dir / REDSHIFT_SUMMARY_FILENAME)
    expected_rows = int(identity_summary["totals"]["total_rows"])
    expected_sampled = int(audit_summary["sampling"]["rows_selected"])

    integrity = verify_corpus_untouched(args.corpus_root, snapshot_path)

    inventory = read_part(inventory_path)
    if not inventory.schema.equals(INVENTORY_SCHEMA):
        raise SystemExit(f"inventory schema deviates from INVENTORY_SCHEMA: {inventory.schema}")
    inventory_rows_sum = sum(inventory.column("n_rows_actual").to_pylist())
    if inventory_rows_sum != expected_rows:
        raise SystemExit(
            f"D1 inventory row sum {inventory_rows_sum} != D2 identity total {expected_rows}; inputs disagree"
        )
    per_object = read_part(per_object_path)
    array_audit = read_part(array_audit_path)
    if per_object.num_rows != expected_rows:
        raise SystemExit(f"per-object rows {per_object.num_rows} != D2 identity total {expected_rows}")
    if array_audit.num_rows != expected_sampled:
        raise SystemExit(f"array audit rows {array_audit.num_rows} != D3 sampled total {expected_sampled}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    file_manifest_path = args.output_dir / FILE_MANIFEST_FILENAME
    object_manifest_path = args.output_dir / OBJECT_MANIFEST_FILENAME
    provenance_path = args.output_dir / MANIFEST_PROVENANCE_FILENAME

    file_manifest = inventory.select(INVENTORY_SCHEMA.names).cast(INVENTORY_SCHEMA)
    object_manifest = join_sampled_flags(per_object, array_audit)
    object_validation = validate_object_manifest(
        object_manifest,
        expected_rows=expected_rows,
        expected_sampled_join=expected_sampled,
    )
    build_dir = Path(mkdtemp(prefix=".manifest-build-", dir=args.output_dir))
    staged_file = build_dir / FILE_MANIFEST_FILENAME
    staged_object = build_dir / OBJECT_MANIFEST_FILENAME
    staged_provenance = build_dir / MANIFEST_PROVENANCE_FILENAME
    logger.info("staging manifest validation in %s", build_dir)
    write_part(file_manifest, staged_file)
    write_part(object_manifest, staged_object)

    round_trip = {
        "file_manifest": round_trip_validate(staged_file, file_manifest, seed=args.seed, n_spot=args.spot_rows),
        "object_manifest": round_trip_validate(staged_object, object_manifest, seed=args.seed, n_spot=args.spot_rows),
    }
    logger.info(
        "round-trip validation passed for both tables (seed %d, %d spot rows each)",
        args.seed,
        args.spot_rows,
    )

    round_trip["file_manifest"]["path"] = str(file_manifest_path)
    round_trip["object_manifest"]["path"] = str(object_manifest_path)
    files = discover_corpus_files(args.corpus_root)
    provenance = build_manifest_provenance(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        host=platform.node(),
        python_version=platform.python_version(),
        package_versions={"pyarrow": pa.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        command=shlex.join(sys.argv if argv is None else [str(Path(__file__).resolve()), *argv]),
        tool_commit=git_head(Path(__file__).resolve().parents[1]),
        corpus_root=args.corpus_root,
        corpus_files_sha256=corpus_fingerprint(files, args.corpus_root),
        inputs=[
            input_artifact_record("inventory_parquet", inventory_path, rows=inventory.num_rows),
            input_artifact_record("target_ids_parquet", args.work_dir / TARGET_IDS_FILENAME, rows=expected_rows),
            input_artifact_record("array_audit_parquet", array_audit_path, rows=array_audit.num_rows),
            input_artifact_record("per_object_raw_parquet", per_object_path, rows=per_object.num_rows),
            input_artifact_record("corpus_snapshot_parquet", snapshot_path, rows=integrity["files_checked"]),
            {
                "name": "identity_summary",
                "path": str(args.work_dir / IDENTITY_SUMMARY_FILENAME),
                "expected_total_rows": expected_rows,
            },
            {
                "name": "array_audit_summary",
                "path": str(args.work_dir / ARRAY_AUDIT_SUMMARY_FILENAME),
                "expected_sampled_rows": expected_sampled,
            },
            {
                "name": "inventory_summary",
                "path": str(args.work_dir / INVENTORY_SUMMARY_FILENAME),
                "total_files_scanned": inventory_summary.get("total_files_scanned"),
            },
            {"name": "redshift_summary", "path": str(args.work_dir / REDSHIFT_SUMMARY_FILENAME)},
        ],
        sampling=sampling_block(audit_summary),
        outputs={
            "file_manifest": {
                "path": str(file_manifest_path),
                "rows": int(file_manifest.num_rows),
                "size_bytes": int(staged_file.stat().st_size),
                "md5": file_md5(staged_file),
            },
            "object_manifest": {
                "path": str(object_manifest_path),
                "rows": int(object_manifest.num_rows),
                "size_bytes": int(staged_object.stat().st_size),
                "md5": file_md5(staged_object),
            },
        },
        round_trip=round_trip,
        corpus_integrity=integrity,
        validations={
            "file_manifest_rows": int(file_manifest.num_rows),
            "object_manifest": object_validation,
            "hard_asserts": [
                f"object manifest rows == {expected_rows} (D2 identity_summary totals.total_rows)",
                "row_uid uniqueness on the object manifest",
                f"sampled-join coverage == {expected_sampled} non-null align_ok "
                "(D3 array_audit_summary sampling.rows_selected)",
                "corpus integrity: size_bytes and mtime_ns identical for every snapshot file",
            ],
        },
    )
    provenance["timing_seconds"] = {"total": round(time.perf_counter() - total_start, 3)}
    staged_provenance.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    if load_json(staged_provenance) != provenance:
        raise RuntimeError("provenance JSON failed round-trip validation")
    publish_validated_set([staged_file, staged_object, staged_provenance], args.output_dir)
    logger.info("wrote provenance -> %s", provenance_path)
    logger.info(
        "headline: file_manifest=%d rows, object_manifest=%d rows, sampled_join=%d, corpus_integrity=%d/%d unchanged",
        file_manifest.num_rows,
        object_manifest.num_rows,
        object_validation["sampled_join_coverage"],
        integrity["files_checked"],
        integrity["files_checked"],
    )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
