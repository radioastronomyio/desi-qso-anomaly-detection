#!/usr/bin/env python3
"""
Script Name  : test_run_config.py
Description  : Unit and script tests for the run-config parts fence.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers the run-config manifest that fences the parts directory against
stale cross-configuration reuse: corpus fingerprinting, manifest IO and
matching, fence actions (reused/initialized/reset_unknown/pruned_parts/
pruned_all), plan-driven assembly that ignores orphan parts, and full
script reruns against the same work directory with a different chunk size
or a different corpus root. All fixtures are synthetic files under
tmp_path; the real corpus is never touched.

Usage
-----
    pytest tests/test_run_config.py

Examples
--------
    pytest tests/test_run_config.py -k fence
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from conftest import make_qso_table, write_qso_table
from dqad_audit import (
    build_run_config,
    corpus_fingerprint,
    corpus_identity,
    manifest_matches,
    parts_manifest_path,
    read_parts_manifest,
    write_parts_manifest,
)

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "01_file_inventory.py"

# =============================================================================
# Functions
# =============================================================================


@pytest.fixture(scope="module")
def inv():
    spec = importlib.util.spec_from_file_location("file_inventory_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_corpus(root: Path, tiles: list[tuple[str, int, int]]) -> list[Path]:
    files: list[Path] = []
    for tile, n_rows, claimed in tiles:
        tile_number = tile.split("_")[1]
        name = f"{tile}/qso_data_TILE{tile_number}_{claimed}_qsos.parquet"
        files.append(write_qso_table(root / name, make_qso_table(n_rows)))
    return files


def run_script(module, corpus_root: Path, work_dir: Path, chunk_size: int) -> None:
    module.main(
        [
            "--corpus-root",
            str(corpus_root),
            "--work-dir",
            str(work_dir),
            "--workers",
            "2",
            "--chunk-size",
            str(chunk_size),
        ]
    )


def inventory_paths(work_dir: Path) -> list[str]:
    return pq.read_table(work_dir / "inventory.parquet").column("path").to_pylist()


def test_corpus_fingerprint_stable_under_reorder(tmp_path: Path) -> None:
    files = write_corpus(tmp_path, [("tile_10000", 2, 2), ("tile_10001", 3, 3), ("tile_10002", 1, 1)])
    assert corpus_fingerprint(files, tmp_path) == corpus_fingerprint(list(reversed(files)), tmp_path)


def test_corpus_fingerprint_sensitive_to_file_set(tmp_path: Path) -> None:
    files = write_corpus(tmp_path, [("tile_10000", 2, 2), ("tile_10001", 3, 3)])
    assert corpus_fingerprint(files, tmp_path) != corpus_fingerprint(files[:1], tmp_path)
    extra = write_qso_table(tmp_path / "tile_10002/qso_data_TILE10002_1_qsos.parquet", make_qso_table(1))
    assert corpus_fingerprint(files + [extra], tmp_path) != corpus_fingerprint(files, tmp_path)


def test_corpus_fingerprint_relative_to_root(tmp_path: Path) -> None:
    first = tmp_path / "corpus_a"
    second = tmp_path / "corpus_b"
    files_a = write_corpus(first, [("tile_10000", 2, 2)])
    files_b = write_corpus(second, [("tile_10000", 2, 2)])
    assert corpus_fingerprint(files_a, first) == corpus_fingerprint(files_b, second)
    assert build_run_config(first, files_a, 2)["corpus_root"] != build_run_config(second, files_b, 2)["corpus_root"]


def test_build_run_config_fields(tmp_path: Path) -> None:
    files = write_corpus(tmp_path, [("tile_10000", 2, 2), ("tile_10001", 3, 3)])
    config = build_run_config(tmp_path, files, 7)
    assert config["corpus_root"] == str(tmp_path)
    assert config["chunk_size"] == 7
    assert config["n_files"] == 2
    assert len(config["files_sha256"]) == 64


def test_manifest_roundtrip_missing_and_corrupt(tmp_path: Path) -> None:
    files = write_corpus(tmp_path, [("tile_10000", 2, 2)])
    config = build_run_config(tmp_path, files, 2)
    assert read_parts_manifest(tmp_path) is None
    write_parts_manifest(tmp_path, config)
    assert read_parts_manifest(tmp_path) == config
    parts_manifest_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_parts_manifest(tmp_path) is None
    parts_manifest_path(tmp_path).write_text('["not", "a", "dict"]', encoding="utf-8")
    assert read_parts_manifest(tmp_path) is None


def test_manifest_matches_semantics(tmp_path: Path) -> None:
    files = write_corpus(tmp_path, [("tile_10000", 2, 2)])
    current = build_run_config(tmp_path, files, 2)
    assert manifest_matches(current, current) is True
    assert manifest_matches(None, current) is False
    assert manifest_matches(build_run_config(tmp_path, files, 3), current) is False
    other_root = tmp_path / "elsewhere"
    other_files = write_corpus(other_root, [("tile_10000", 2, 2)])
    assert manifest_matches(build_run_config(other_root, other_files, 2), current) is False


def test_corpus_identity_ignores_chunk_size(tmp_path: Path) -> None:
    files = write_corpus(tmp_path, [("tile_10000", 2, 2)])
    assert corpus_identity(build_run_config(tmp_path, files, 2)) == corpus_identity(
        build_run_config(tmp_path, files, 250)
    )


def test_fence_actions_lifecycle(tmp_path: Path, inv) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    corpus = tmp_path / "corpus"
    files = write_corpus(corpus, [("tile_10000", 2, 2), ("tile_10001", 3, 3)])
    assert inv.apply_run_config_fence(work_dir, corpus, files, 2) == "initialized"
    snapshot = work_dir / "corpus_snapshot.parquet"
    reference = work_dir / "reference_schema.json"
    snapshot.write_bytes(b"stale")
    reference.write_text("{}", encoding="utf-8")
    part = work_dir / "parts" / "inventory" / "0000.parquet"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"stale")
    parts_manifest_path(work_dir).unlink()
    assert inv.apply_run_config_fence(work_dir, corpus, files, 2) == "reset_unknown"
    assert not snapshot.is_file() and not reference.is_file() and not part.is_file()
    assert inv.apply_run_config_fence(work_dir, corpus, files, 2) == "reused"
    snapshot.write_bytes(b"kept")
    reference.write_text("{}", encoding="utf-8")
    assert inv.apply_run_config_fence(work_dir, corpus, files, 5) == "pruned_parts"
    assert snapshot.is_file() and reference.is_file()
    other_corpus = tmp_path / "corpus_b"
    other_files = write_corpus(other_corpus, [("tile_20000", 1, 1)])
    assert inv.apply_run_config_fence(work_dir, other_corpus, other_files, 5) == "pruned_all"
    assert not snapshot.is_file() and not reference.is_file()
    assert read_parts_manifest(work_dir) == build_run_config(other_corpus, other_files, 5)


def test_rerun_different_chunk_size_no_duplicate_rows(tmp_path: Path, inv) -> None:
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    write_corpus(corpus, [(f"tile_{10000 + i}", 2, 2) for i in range(5)])
    run_script(inv, corpus, work_dir, 2)
    assert len(inventory_paths(work_dir)) == 5
    assert len(list((work_dir / "parts" / "inventory").glob("*.parquet"))) == 3
    run_script(inv, corpus, work_dir, 5)
    paths = inventory_paths(work_dir)
    assert len(paths) == 5
    assert len(set(paths)) == 5
    assert sorted(path.name for path in (work_dir / "parts" / "inventory").glob("*.parquet")) == ["0000.parquet"]
    manifest = json.loads(parts_manifest_path(work_dir).read_text(encoding="utf-8"))
    assert manifest["chunk_size"] == 5
    assert pq.read_table(work_dir / "target_ids.parquet").num_rows == 10


def test_rerun_different_corpus_root_invalidates(tmp_path: Path, inv) -> None:
    corpus_a = tmp_path / "corpus_a"
    corpus_b = tmp_path / "corpus_b"
    work_dir = tmp_path / "work"
    write_corpus(corpus_a, [("tile_10000", 2, 2), ("tile_10001", 3, 3)])
    write_corpus(corpus_b, [("tile_20000", 1, 1), ("tile_20001", 2, 2)])
    run_script(inv, corpus_a, work_dir, 2)
    assert all(path.startswith(str(corpus_a)) for path in inventory_paths(work_dir))
    run_script(inv, corpus_b, work_dir, 2)
    paths = inventory_paths(work_dir)
    assert len(paths) == 2
    assert all(path.startswith(str(corpus_b)) for path in paths)
    snapshot_paths = pq.read_table(work_dir / "corpus_snapshot.parquet").column("path").to_pylist()
    assert all(path.startswith(str(corpus_b)) for path in snapshot_paths)
    reference_file = json.loads((work_dir / "reference_schema.json").read_text(encoding="utf-8"))["reference_file"]
    assert reference_file.startswith(str(corpus_b))
    assert pq.read_table(work_dir / "target_ids.parquet").num_rows == 3


def test_rerun_same_config_reuses_checkpoints(tmp_path: Path, inv) -> None:
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    write_corpus(corpus, [("tile_10000", 2, 2), ("tile_10001", 3, 3)])
    run_script(inv, corpus, work_dir, 2)
    parts = sorted((work_dir / "parts").rglob("*.parquet"))
    mtimes = {path: path.stat().st_mtime_ns for path in parts}
    run_script(inv, corpus, work_dir, 2)
    for path in parts:
        assert path.stat().st_mtime_ns == mtimes[path]
    assert inventory_paths(work_dir) == sorted(inventory_paths(work_dir))


def test_assembly_ignores_orphan_parts(tmp_path: Path, inv) -> None:
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    files = write_corpus(corpus, [(f"tile_{10000 + i}", 2, 2) for i in range(5)])
    run_script(inv, corpus, work_dir, 2)
    shutil.copy(
        work_dir / "parts" / "inventory" / "0000.parquet",
        work_dir / "parts" / "inventory" / "0099.parquet",
    )
    shutil.copy(
        work_dir / "parts" / "target_ids" / "0000.parquet",
        work_dir / "parts" / "target_ids" / "0099.parquet",
    )
    inventory, target_ids = inv.assemble_outputs(work_dir, inv.plan_chunks(files, 2))
    assert inventory.num_rows == 5
    assert target_ids.num_rows == 10


def test_assembly_missing_planned_part_raises(tmp_path: Path, inv) -> None:
    corpus = tmp_path / "corpus"
    work_dir = tmp_path / "work"
    files = write_corpus(corpus, [("tile_10000", 2, 2), ("tile_10001", 3, 3), ("tile_10002", 1, 1)])
    run_script(inv, corpus, work_dir, 2)
    (work_dir / "parts" / "inventory" / "0001.parquet").unlink()
    with pytest.raises(RuntimeError, match="missing planned inventory part"):
        inv.assemble_outputs(work_dir, inv.plan_chunks(files, 2))
