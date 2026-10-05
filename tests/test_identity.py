#!/usr/bin/env python3
"""
Script Name  : test_identity.py
Description  : Unit tests for the D2 identity and duplication analysis helpers.
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon/)
Created      : 2026-08-15
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Covers the archive-wide identity helpers against hand-built tables and
synthetic converter sources in tmp_path: repeat-count distribution
correctness, within-tile versus cross-tile duplication split with the
tiles-spanned distribution, the naive-split leakage formula on hand-computed
cases, DR1 population gap arithmetic, verbatim converter filter extraction
with line numbers, and summary JSON schema/int-ness (counts serialize as
JSON ints, never floats) plus accounting-invariant rejection.

Usage
-----
    pytest tests/test_identity.py

Examples
--------
    pytest tests/test_identity.py -k leakage
"""

# =============================================================================
# Imports
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import pytest

from dqad_audit import (
    build_identity_summary,
    converter_filter_block,
    gap_accounting,
    leakage_expectation,
    repeat_distribution,
    tile_duplication_split,
)

# =============================================================================
# Configuration
# =============================================================================

# Hand-built archive: tile 10000 holds tids 1,1,2; tile 10001 holds 2,3;
# tile 10002 holds 3,4. tid 1 is duplicated within one tile; tids 2 and 3
# are duplicated across two tiles each; tid 4 is a singleton.
TILES = ["10000", "10000", "10000", "10001", "10001", "10002", "10002"]
TIDS = [1, 1, 2, 2, 3, 3, 4]

CONVERTER_FIXTURE_LINES = [
    "import numpy as np",
    "",
    "MIN_WAVE = 3600",
    "MAX_WAVE = 9800",
    "",
    "        full_wave = np.concatenate(wave_data)",
    "        flux_brz = np.concatenate([f[idx, :] for f in flux_data]).astype(np.float32)",
    "        mask = (full_wave >= MIN_WAVE) & (full_wave <= MAX_WAVE)",
    "",
    '            if row["SPECTYPE"] == "QSO" and row["ZWARN"] == 0',
]

COUNT_PATHS = [
    ("totals", "total_rows"),
    ("totals", "unique_target_ids"),
    ("totals", "duplicate_target_id_count"),
    ("totals", "total_excess_rows"),
    ("repeat_distribution", "max_k"),
    ("repeat_distribution", "duplicated_ids_total"),
    ("tile_split", "within_tile", "duplicate_target_id_count"),
    ("tile_split", "within_tile", "duplicate_tile_id_pairs"),
    ("tile_split", "within_tile", "excess_rows"),
    ("tile_split", "cross_tile", "duplicate_target_id_count"),
    ("tile_split", "cross_tile", "max_tiles_spanned"),
    ("tile_split", "within_tile_only_duplicate_ids"),
    ("gap_accounting", "population_reference"),
    ("gap_accounting", "unique_target_ids"),
    ("gap_accounting", "gap"),
    ("provenance", "inputs", "target_ids_parquet", "rows"),
]

FLOAT_PATHS = [
    ("rates", "duplication_rate"),
    ("rates", "excess_row_fraction"),
    ("gap_accounting", "gap_fraction"),
    ("holdout", "expected_leakage_naive_90_10"),
    ("holdout", "holdout_fraction"),
]


# =============================================================================
# Functions
# =============================================================================


@pytest.fixture
def converter_source(tmp_path):
    """Write a synthetic converter source mirroring the frozen original's quotable lines."""
    path = tmp_path / "02-extract-qso-tile-to-parquet.py"
    path.write_text("\n".join(CONVERTER_FIXTURE_LINES) + "\n", encoding="utf-8")
    return path


def dig(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Fetch a nested value from a parsed summary payload."""
    node: Any = payload
    for key in path:
        node = node[key]
    return node


# =============================================================================
# Repeat-count distribution
# =============================================================================


def test_repeat_distribution_hand_built() -> None:
    assert repeat_distribution(TIDS) == {1: 1, 2: 3}


def test_repeat_distribution_mixed_multiplicities() -> None:
    assert repeat_distribution([5, 5, 7, 7, 9, 9, 9, 11]) == {1: 1, 2: 2, 3: 1}


def test_repeat_distribution_all_singletons() -> None:
    assert repeat_distribution([4, 5, 6]) == {1: 3}


def test_repeat_distribution_empty() -> None:
    assert repeat_distribution([]) == {}


# =============================================================================
# Within-tile versus cross-tile split
# =============================================================================


def test_tile_split_within_and_across() -> None:
    split = tile_duplication_split(TILES, TIDS)
    assert split["within_tile"] == {"duplicate_target_id_count": 1, "duplicate_tile_id_pairs": 1, "excess_rows": 1}
    assert split["cross_tile"]["duplicate_target_id_count"] == 2
    assert split["cross_tile"]["tiles_spanned_distribution"] == {1: 1, 2: 2}
    assert split["cross_tile"]["max_tiles_spanned"] == 2
    assert split["within_tile_only_duplicate_ids"] == 1


def test_tile_split_no_duplication() -> None:
    split = tile_duplication_split(["10000", "10001", "10002"], [1, 2, 3])
    assert split["within_tile"]["duplicate_target_id_count"] == 0
    assert split["within_tile"]["duplicate_tile_id_pairs"] == 0
    assert split["within_tile"]["excess_rows"] == 0
    assert split["cross_tile"]["duplicate_target_id_count"] == 0
    assert split["cross_tile"]["tiles_spanned_distribution"] == {}
    assert split["cross_tile"]["max_tiles_spanned"] == 0
    assert split["within_tile_only_duplicate_ids"] == 0


def test_tile_split_three_tiles_one_id() -> None:
    split = tile_duplication_split(["A", "A", "B", "C"], [1, 1, 1, 1])
    assert split["within_tile"]["duplicate_target_id_count"] == 1
    assert split["within_tile"]["excess_rows"] == 1
    assert split["cross_tile"]["duplicate_target_id_count"] == 1
    assert split["cross_tile"]["tiles_spanned_distribution"] == {3: 1}
    assert split["cross_tile"]["max_tiles_spanned"] == 3
    assert split["within_tile_only_duplicate_ids"] == 0


def test_tile_split_rejects_misaligned_columns() -> None:
    with pytest.raises(ValueError, match="aligned"):
        tile_duplication_split(["A", "B"], [1, 2, 3])


# =============================================================================
# Leakage formula (hand-computed)
# =============================================================================


def test_leakage_formula_hand_computed_cases() -> None:
    assert leakage_expectation({2: 1}) == pytest.approx(1 - 0.9**2 - 0.1**2)
    assert leakage_expectation({3: 1}) == pytest.approx(1 - 0.9**3 - 0.1**3)
    assert leakage_expectation({4: 1}) == pytest.approx(1 - 0.9**4 - 0.1**4)


def test_leakage_formula_aggregate_case() -> None:
    # k=2 id contributes 0.18, k=3 id contributes 0.27; singletons contribute 0.
    assert leakage_expectation({1: 2, 2: 1, 3: 1}) == pytest.approx(0.18 + 0.27)


def test_leakage_formula_singletons_only() -> None:
    assert leakage_expectation({1: 10_000}) == 0.0


def test_leakage_formula_alternate_fraction() -> None:
    assert leakage_expectation({2: 1}, holdout_fraction=0.2) == pytest.approx(1 - 0.8**2 - 0.2**2)


# =============================================================================
# Gap accounting
# =============================================================================


def test_gap_accounting_reference_math() -> None:
    gap = gap_accounting(1_000_000)
    assert gap["population_reference"] == 1_550_000
    assert gap["unique_target_ids"] == 1_000_000
    assert gap["gap"] == 550_000
    assert gap["gap_fraction"] == pytest.approx(550_000 / 1_550_000)


def test_gap_accounting_custom_reference() -> None:
    assert gap_accounting(100, population_reference=200)["gap"] == 100


def test_gap_accounting_rejects_nonpositive_reference() -> None:
    with pytest.raises(ValueError, match="positive"):
        gap_accounting(100, population_reference=0)


# =============================================================================
# Converter filter extraction
# =============================================================================


def test_converter_filter_block_verbatim_quotes_and_lines(converter_source) -> None:
    block = converter_filter_block(converter_source)
    assert block["source_path"] == str(converter_source)
    assert block["selection_filter"] == {
        "line": 10,
        "code": 'if row["SPECTYPE"] == "QSO" and row["ZWARN"] == 0',
    }
    assert block["wavelength_clip"]["min"] == {"line": 3, "code": "MIN_WAVE = 3600"}
    assert block["wavelength_clip"]["max"] == {"line": 4, "code": "MAX_WAVE = 9800"}
    assert block["wavelength_clip"]["range_angstrom"] == [3600, 9800]
    assert block["wavelength_clip"]["mask_line"]["line"] == 8
    assert block["arm_handling"]["wavelength_concatenation"] == {
        "line": 6,
        "code": "full_wave = np.concatenate(wave_data)",
    }
    assert block["arm_handling"]["flux_concatenation"]["line"] == 7


def test_converter_filter_block_missing_pattern_raises(tmp_path) -> None:
    bare = tmp_path / "bare.py"
    bare.write_text("print('no filter here')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="selection filter"):
        converter_filter_block(bare)


def test_converter_filter_block_missing_file_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        converter_filter_block(tmp_path / "absent.py")


# =============================================================================
# Summary assembly: schema, int-ness, holdout verdict
# =============================================================================


def build_fixture_summary(converter_source):
    """Assemble a summary over the hand-built archive table."""
    distribution = repeat_distribution(TIDS)
    split = tile_duplication_split(TILES, TIDS)
    return build_identity_summary(
        total_rows=len(TIDS),
        unique_target_ids=len(set(TIDS)),
        distribution=distribution,
        tile_split=split,
        converter_filter=converter_filter_block(converter_source),
        provenance={
            "generated_at": "2026-08-15T00:00:00-04:00",
            "command": "python scripts/02_identity_duplication.py",
            "inputs": {"target_ids_parquet": {"path": "/synthetic/target_ids.parquet", "rows": len(TIDS)}},
        },
    )


def test_summary_totals_and_distribution(converter_source) -> None:
    summary = build_fixture_summary(converter_source)
    assert summary["totals"] == {
        "total_rows": 7,
        "unique_target_ids": 4,
        "duplicate_target_id_count": 3,
        "total_excess_rows": 3,
    }
    assert summary["repeat_distribution"]["counts_by_k"] == {"1": 1, "2": 3}
    assert summary["repeat_distribution"]["max_k"] == 2
    assert summary["tile_split"]["cross_tile"]["tiles_spanned_distribution"] == {"1": 1, "2": 2}
    assert summary["consistency_checks"] == {
        "rows_match_repeat_sum": True,
        "unique_matches_distribution_total": True,
        "excess_matches_row_difference": True,
        "tile_split_accounts_for_duplicated_ids": True,
    }


def test_summary_rates_gap_and_holdout_derived_from_data(converter_source) -> None:
    summary = build_fixture_summary(converter_source)
    assert summary["rates"]["duplication_rate"] == pytest.approx(3 / 4)
    assert summary["rates"]["excess_row_fraction"] == pytest.approx(3 / 7)
    assert summary["gap_accounting"]["gap"] == 1_550_000 - 4
    assert summary["gap_accounting"]["gap_fraction"] == pytest.approx((1_550_000 - 4) / 1_550_000)
    assert summary["gap_accounting"]["converter_filter"]["selection_filter"]["code"] == (
        'if row["SPECTYPE"] == "QSO" and row["ZWARN"] == 0'
    )
    assert summary["gap_accounting"]["accounting"] == (
        "consistent with the converter's SPECTYPE==QSO and ZWARN==0 filter; " "the excluded count was not measured"
    )
    assert summary["holdout"]["required"] is True
    assert summary["holdout"]["expected_leakage_naive_90_10"] == pytest.approx(3 * 0.18)
    assert "reobservations" in summary["repeat_distribution"]["interpretation"]


def test_summary_counts_serialize_as_json_ints(converter_source) -> None:
    payload = json.loads(json.dumps(build_fixture_summary(converter_source)))
    for path in COUNT_PATHS:
        value = dig(payload, path)
        assert isinstance(value, int) and not isinstance(value, bool), f"{path} serialized as {type(value)}"
    for value in payload["repeat_distribution"]["counts_by_k"].values():
        assert isinstance(value, int) and not isinstance(value, bool)
    for value in payload["tile_split"]["cross_tile"]["tiles_spanned_distribution"].values():
        assert isinstance(value, int) and not isinstance(value, bool)
    for path in FLOAT_PATHS:
        assert isinstance(dig(payload, path), float), f"{path} did not serialize as float"
    assert 'total_rows": 7' in json.dumps(payload)
    assert "expected_leakage_naive_90_10" in payload["holdout"]
    assert payload["holdout"]["rationale"]


def test_summary_holdout_not_required_without_duplication(converter_source) -> None:
    tiles = ["10000", "10001", "10002"]
    tids = [1, 2, 3]
    summary = build_identity_summary(
        total_rows=3,
        unique_target_ids=3,
        distribution=repeat_distribution(tids),
        tile_split=tile_duplication_split(tiles, tids),
        converter_filter=converter_filter_block(converter_source),
        provenance={"command": "synthetic"},
    )
    assert summary["holdout"]["required"] is False
    assert summary["holdout"]["expected_leakage_naive_90_10"] == 0.0
    assert "all k = 1" in summary["repeat_distribution"]["interpretation"]


def test_summary_rejects_inconsistent_totals(converter_source) -> None:
    with pytest.raises(ValueError, match="inconsistency"):
        build_identity_summary(
            total_rows=9,
            unique_target_ids=2,
            distribution={1: 2},
            tile_split=tile_duplication_split(["A", "B"], [1, 2]),
            converter_filter=converter_filter_block(converter_source),
            provenance={"command": "synthetic"},
        )


def test_identity_main_uses_supplied_paths_and_population(tmp_path, converter_source, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    path = Path(__file__).resolve().parents[1] / "scripts" / "02_identity_duplication.py"
    spec = importlib.util.spec_from_file_location("identity_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    work_dir = tmp_path / "requested-work"
    work_dir.mkdir()
    pq.write_table(
        pa.table({"tile_id": TILES, "row_idx": [0, 1, 2, 0, 1, 0, 1], "target_id": TIDS}),
        work_dir / "target_ids.parquet",
    )
    monkeypatch.setattr(sys, "argv", ["hosting-process", "--unrelated-host-option"])
    module.main(
        [
            "--work-dir",
            str(work_dir),
            "--converter-source",
            str(converter_source),
            "--population-reference",
            "10",
            "--holdout-fraction",
            "0.2",
        ]
    )
    summary = json.loads((work_dir / "identity_summary.json").read_text())
    assert summary["totals"]["total_rows"] == 7
    assert summary["gap_accounting"]["population_reference"] == 10
    assert summary["gap_accounting"]["gap"] == 6
    assert summary["gap_accounting"]["converter_filter"]["source_path"] == str(converter_source)
    assert summary["holdout"]["expected_leakage_naive_90_10"] == pytest.approx(0.96)
