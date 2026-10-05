<!--
---
title: "Tests"
description: "Synthetic-fixture unit tests for the dqad_audit package and audit scripts"
author: "VintageDon (https://github.com/vintagedon/)"
date: "2026-08-15"
version: "1.0"
status: "Active"
tags:
  - type: directory-readme
  - domain: spectral-analysis
  - tech: [python, parquet]
---
-->

# Tests

Unit tests for the spectral manifest audit. All fixtures are synthetic Parquet files written to pytest `tmp_path`; tests never read the real corpus under /mnt/nvme01 and never write outside their temporary directories.

---

## 1. Contents

```
tests/
├── conftest.py                   # Synthetic QSO table builder and tmp_path file factory
├── test_array_audit.py           # D3 per-row checks, sampling determinism, fence, merge policy, end-to-end
├── test_chunking.py              # Chunk planning, part-file IO, resume, snapshot
├── test_identity.py              # Repeat distribution, tile split, leakage, gap, summary int-ness
├── test_manifest_assembly.py     # D6 join, hard asserts, round-trip, integrity comparator, script end-to-end
├── test_parsing.py               # Filename claim/tile parsing, row_uid, discovery
├── test_run_config.py            # Run-config parts fence: manifest, pruning, plan-driven assembly
├── test_scalars_nuisance.py      # D4+D5 scalars, bin table, nuisance blocks, script end-to-end
├── test_scan.py                  # scan_file/scan_chunk over synthetic corpus files
├── test_schema.py                # Schema serialization and diff deviation strings
└── README.md                     # This file
```

---

## 2. Files

| File | Description | Status |
|------|-------------|--------|
| [conftest.py](conftest.py) | make_qso_table builder mirroring the measured corpus schema | Active |
| [test_parsing.py](test_parsing.py) | Claim parsing, tile id zero padding, sorted discovery | Active |
| [test_schema.py](test_schema.py) | serialize_schema, columns_dtypes, schema_diff cases | Active |
| [test_chunking.py](test_chunking.py) | plan_chunks, part validity, resume selection, snapshot | Active |
| [test_run_config.py](test_run_config.py) | Run-config manifest fence, cross-config reruns, orphan-free assembly | Active |
| [test_scan.py](test_scan.py) | Matching/deviant schemas, claim mismatch, duplicates, corrupt files | Active |
| [test_identity.py](test_identity.py) | Hand-built repeat/tile-split cases, leakage formula, gap math, converter quotes, summary JSON int-ness | Active |
| [test_array_audit.py](test_array_audit.py) | Six per-row checks on hand-built defect rows (exact 40-A overlap), sampling determinism and seed sensitivity, array_audit fence pruning, merge-policy derivation, summary blocks, script end-to-end with resume | Active |
| [test_scalars_nuisance.py](test_scalars_nuisance.py) | snr/masked formulas hand-computed, bin interval and grid-length math, histogram and quantiles, fence, Arrow-null semantics, script end-to-end with inventory-validated resume | Active |
| [test_manifest_assembly.py](test_manifest_assembly.py) | row_uid parsing, sampled-flag LEFT-JOIN placement/sorting/nulls, hard asserts (row count, duplicates, coverage), seeded spot-check draw, round-trip mutation detection, corpus integrity comparator, script end-to-end including the integrity-gate abort | Active |

---

## 3. Usage

```bash
pytest
```

Warnings are treated as errors (pyproject `[tool.pytest.ini_options]`), keeping output pristine.

---

## 4. Related

| Document | Relationship |
|----------|--------------|
| [Project Root](../README.md) | Parent |
| [dqad_audit](../src/dqad_audit/README.md) | Package under test |
