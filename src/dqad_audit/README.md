<!--
---
title: "dqad_audit"
description: "Shared helpers for the read-only DESI DR1 QSO Parquet corpus audit"
author: "VintageDon (https://github.com/vintagedon/)"
date: "2026-08-15"
version: "1.0"
status: "Active"
tags:
  - type: directory-readme
  - domain: spectral-analysis
  - tech: [python, parquet, desi]
---
-->

# dqad_audit

Shared library for the spectral manifest audit. Centralizes corpus discovery, filename parsing, schema serialization and diffing, chunk planning, part-file checkpoint IO with resume, corpus integrity snapshotting, the per-file Parquet scanner, the archive-wide identity/duplication analysis, the sampled array contract audit (deterministic tile/row sampling, six per-row checks, arm-overlap measurement, merge-policy recommendation), the full-archive scalar extraction and nuisance baseline, and the D6 manifest assembly helpers (sampled-flag join, integrity comparator, round-trip validation, provenance payload). The corpus is treated as strictly read-only; the package writes only to caller-provided work paths.

---

## 1. Contents

```
dqad_audit/
└── __init__.py     # All shared helpers (single-module package by design)
```

---

## 2. Files

| File | Description | Status |
|------|-------------|--------|
| [__init__.py](__init__.py) | Corpus discovery, tile/claim parsing, row_uid, schema diff, chunk planning, part IO + resume, snapshot writer, scan_file/scan_chunk, identity/duplication analysis | Active |

---

## 3. Key Helpers

| Helper | Purpose |
|--------|---------|
| `discover_corpus_files` | Sorted glob of tile_*/qso_data_TILE*.parquet |
| `scan_file` / `scan_chunk` | Per-file and per-chunk scanner; open failures recorded, never raised |
| `schema_diff` | Reference comparison with added/removed/retyped/reordered deviation strings |
| `plan_chunks` / `part_filepath` / `part_is_valid` / `pending_chunk_indices` | Checkpoint planning and resume |
| `build_run_config` / `write_parts_manifest` / `read_parts_manifest` / `manifest_matches` / `corpus_identity` | Run-config fence over the parts directory (corpus root, chunk size, file-list fingerprint) |
| `write_corpus_snapshot` | Pre-scan (path, size, mtime_ns) integrity snapshot |
| `row_uid` | Stable row identifier `<tile_id>:<row_idx>` |
| `repeat_distribution` / `tile_duplication_split` | Archive-wide repeat-count histogram and within-tile vs cross-tile duplication split (exact ints) |
| `leakage_expectation` / `gap_accounting` | Naive-split expected leakage formula and DR1 population gap arithmetic |
| `converter_filter_block` / `build_identity_summary` | Verbatim converter filter extraction and identity_summary.json assembly with accounting-invariant checks |
| `sampled_tile_selection` / `select_sampled_tiles` / `child_seed_for_tile` / `select_row_indices` | Deterministic systematic sampling: seeded stride tile selection and exact per-tile row selection with a reproducible recipe |
| `audit_arrays` / `scan_file_arrays` / `audit_chunk` | The six D3 per-row checks (alignment, monotonicity, arm overlap, duplicates/near-duplicates, ivar sanity, wavelength range) and the read-only per-file/per-chunk scanners |
| `array_audit_part_table` / `project_array_audit_table` | Part-schema packing (with auxiliary break-boundary columns) and projection to the exact final per-row schema |
| `recommend_merge_policy` / `build_array_audit_summary` | Loader merge/de-dup policy derived from measured stats and array_audit_summary.json assembly |
| `build_array_audit_run_config` / `write_kind_manifest` / `read_kind_manifest` / `kind_manifest_path` | Run-config fence for the array_audit parts family (corpus root, sampling parameters, selected file-list fingerprint) |
| `row_snr_proxy` / `row_masked_fraction` / `row_scalar_metrics` | D5 per-row scalars (snr_proxy, masked_fraction, array_length) in float64 with Arrow-null snr semantics |
| `common_rest_interval` / `linear_grid_length` / `log_grid_length` / `outward_bin_edges` / `count_in_z_bin` / `build_bin_selection` | D4 bin-table math: true intersection common rest interval, rest-grid lengths, outward bin edges, exact bin counts |
| `quantile_profile` / `z_histogram` / `build_redshift_block` / `build_nuisance_block` / `build_redshift_summary` | D4+D5 summary blocks (quantiles, histogram, nuisance tails, rr_chi2 verbatim-unresolvable) |
| `scan_file_scalars` / `scalars_chunk` / `per_object_part_table` / `project_per_object_table` / `build_scalars_run_config` | Single-pass seven-column per-file scanner and part packing for the scalars family |
| `parse_row_uid` / `require_unique_row_uid` / `join_sampled_flags` / `validate_object_manifest` | D6 row_uid parsing and the sampled-flag LEFT-JOIN with hard asserts (row count, uniqueness, coverage) |
| `select_spot_check_indices` / `round_trip_validate` / `compare_corpus_snapshots` | D6 round-trip validation with seeded spot checks and the corpus size/mtime integrity comparator |
| `file_md5` / `input_artifact_record` / `build_manifest_provenance` | D6 provenance payload: input md5s and row counts, outputs, validations, round_trip and corpus_integrity blocks |

---

## 4. Related

| Document | Relationship |
|----------|--------------|
| [Parent](../README.md) | src/ package root |
| [01_file_inventory.py](../../scripts/01_file_inventory.py) | First consumer of this package |
