<!--
---
title: "Stage 0 erratum: audit metadata"
description: "Correction of the array-audit checkpoint stride and verification of related sealed summary values"
author: "GPT, Astra, GPT Work DESI project"
date: "2026-10-05"
version: "1.0"
status: "active"
tags:
  - type: report
  - domain: spectral-analysis
related_documents:
  - "[Sealed Stage 0 audit](../work-logs/2026-08-15-spectral-manifest-audit.md)"
  - "[Review-fix worklog](../work-logs/2026-10-04-stage0-review-fixes.md)"
  - "[Population-gap erratum](2026-10-04-stage0-gap-attribution-erratum.md)"
---
-->

# Stage 0 audit-metadata erratum — 2026-10-05

The sealed August `work/parts/array_audit/run_config.json` records `stride: 1`. That value is wrong. The true systematic tile-selection stride was **21**, computed as `floor(10,793 / 512)`. The seeded start offset was 17 and the selection contained 514 files. The sealed `work/array_audit_summary.json` records those values correctly.

Sampling itself was unaffected. D3 selected the files before constructing the checkpoint run configuration, using the true stride returned by `select_sampled_tiles`; the erroneous value was confined to the checkpoint-fence metadata. The sealed findings report also states the true stride, start, and selected-file count. Future run configurations receive the actual selection stride explicitly.

Two related sealed values were checked:

- `work/identity_summary.json` records `provenance.command` as `scripts/02_identity_duplication.py`. The sealed run used the normal command-line path, so this value is correct. T1 fixes only programmatic `main(argv)` calls, which previously recorded the hosting process arguments.
- `work/redshift_summary.json` records 1,030,934 total and finite redshifts, zero nonfinite redshifts, `histogram.count_nonfinite_excluded: 0`, and 1,030,934 values inside the histogram. Those sealed counts are correct because the archive had no nonfinite redshifts. T2 fixes the latent accounting error for inputs that do contain nonfinite values.

No sealed report, summary, checkpoint configuration, manifest, or corpus file was edited or regenerated. This erratum corrects metadata interpretation only.

Source: authorized PR #1 review items T1–T3, Greptile and Codex reviews, 2026-10-05.
