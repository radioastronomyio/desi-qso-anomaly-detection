<!--
---
title: "Scripts"
description: "Audit stage scripts for the DESI DR1 QSO spectral manifest"
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

# Scripts

One script per audit stage, numbered in execution order. Each script is thin orchestration (CLI, logging, phase sequencing) over the shared `dqad_audit` package and writes outputs only to the audit work directory; the corpus is never modified.

---

## 1. Contents

```
scripts/
├── 01_file_inventory.py        # D1 per-file inventory scan
├── 02_identity_duplication.py  # D2 archive-wide identity and duplication analysis
├── 03_array_contract_audit.py  # D3 sampled array contract audit
├── 04_scalars_nuisance.py      # D4+D5 scalar extraction and nuisance baseline
└── 05_build_manifest.py        # D6 manifest artifact assembly
```

---

## 2. Files

| File | Description | Status |
|------|-------------|--------|
| [01_file_inventory.py](01_file_inventory.py) | Parallel, checkpointed scan of all corpus Parquet files; emits inventory.parquet, target_ids.parquet, reference_schema.json, corpus_snapshot.parquet, inventory_summary.json | Active |
| [02_identity_duplication.py](02_identity_duplication.py) | Archive-wide analysis of work/target_ids.parquet (totals, repeat-count distribution, within/cross-tile split, rates, DR1 population gap with converter filter quotes, holdout verdict); emits identity_summary.json | Active |
| [03_array_contract_audit.py](03_array_contract_audit.py) | Sampled audit of array contracts (alignment, monotonicity, arm overlaps, duplicate/near-duplicate wavelengths, ivar sanity, mask inventory, wavelength limits; deterministic seed-20260815 stride sampling); emits array_audit.parquet, array_audit_summary.json | Active |
| [04_scalars_nuisance.py](04_scalars_nuisance.py) | Single full-archive traversal reading target_id/ra/dec/z/flux/ivar/mask_array only (never wavelength); per-row snr_proxy/masked_fraction/array_length, redshift histogram + quantiles, candidate redshift bin table with common rest-frame intervals and grid lengths (no bin selection); emits per_object_raw.parquet, redshift_summary.json | Active |
| [05_build_manifest.py](05_build_manifest.py) | D6 assembly from the D1-D5 work outputs: corpus-untouched verification against the pre-audit snapshot (abort on any size/mtime mismatch), qso_file_manifest_v1.parquet (inventory verbatim), qso_object_manifest_v1.parquet (per-object table LEFT-JOINed with the sampled contract flags on row_uid; hard asserts on row count, row_uid uniqueness, and join coverage), pyarrow round-trip validation with seeded spot checks, and manifest_provenance.json; writes to the manifest directory next to the work directory | Active |

---

## 3. Usage

```bash
python scripts/01_file_inventory.py [--corpus-root PATH] [--work-dir PATH] [--workers N] [--chunk-size N]
python scripts/02_identity_duplication.py [--work-dir PATH] [--converter-source PATH] [--population-reference N] [--holdout-fraction F]
python scripts/03_array_contract_audit.py [--corpus-root PATH] [--work-dir PATH] [--workers N] [--seed N] [--target-tiles N] [--rows-per-tile N] [--chunk-size N] [--neardup-tol F]
python scripts/04_scalars_nuisance.py [--corpus-root PATH] [--work-dir PATH] [--workers N] [--chunk-size N]
python scripts/05_build_manifest.py [--corpus-root PATH] [--work-dir PATH] [--output-dir PATH] [--seed N] [--spot-rows N]
```

Interrupted runs resume from per-chunk part files under `<work-dir>/parts/`. D3 and D4 reuse a part only when the D1 inventory supplies its complete expected row count and the stored count matches. Without those counts (including when `inventory.parquet` is absent), they rescan the chunk; failed chunks are not checkpointed and read failures abort before final output publication. A run-config manifest (corpus root, chunk size, file-list fingerprint) fences the parts directory: checkpoints from a different configuration are pruned before scanning, and assembly reads only part names defined by the current chunk plan. The array audit keeps its own fence at `<work-dir>/parts/array_audit/run_config.json` keyed on the sampling parameters (seed, stride, rows per tile) as well as the corpus identity. The scalars pass keeps its own fence at `<work-dir>/parts/scalars/run_config.json` keyed on the corpus identity, chunk size, column set, and the snr/mask definitions version; it validates every chunk's expected row count against the D1 inventory and requires every corpus row to be present (open failures abort the run). The identity analysis reads only the D1 outputs in `<work-dir>/` and quotes the converter selection filter verbatim from the frozen converter source (read-only). The manifest builder reads only the D1-D5 outputs in `<work-dir>/` plus stat calls on the corpus; it verifies the corpus is untouched (size and mtime identical to the pre-audit snapshot) before writing anything, hard-asserts the object table row count and sampled-join coverage against the D2 and D3 totals, round-trip validates both tables with pyarrow (seeded spot checks), and emits the artifacts plus `manifest_provenance.json` into `<output-dir>` (default: the directory containing `<work-dir>`). The corpus is never modified by any stage.

---

## 4. Related

| Document | Relationship |
|----------|--------------|
| [Project Root](../README.md) | Parent |
| [dqad_audit](../src/dqad_audit/README.md) | Shared helpers used by these scripts |
