<!--
---
title: "Spectral Manifest Audit: DESI DR1 QSO Parquet Corpus"
description: "Stage 0 gate findings for the DESI DR1 QSO spectral Parquet corpus: identity, array contract, redshift coverage, nuisance baseline, and the v1 manifest artifacts"
author: "VintageDon (https://github.com/vintagedon/)"
date: "2026-08-15"
version: "1.0"
status: "active"
tags:
  - type: worklog
  - domain: spectral-analysis
  - tech: [python, parquet, desi]
related_documents:
  - "[Scripts](../scripts/README.md)"
  - "[dqad_audit](../src/dqad_audit/README.md)"
---
-->

# Spectral Manifest Audit: DESI DR1 QSO Parquet Corpus

## 1. Summary

This audit examined the DESI DR1 QSO spectral Parquet corpus at `/mnt/nvme01/desidr1-parquet-tiles/desi-qad` (10,793 files, 1,030,934 rows) strictly read-only, across five analysis stages: per-file inventory, archive-wide identity, a sampled array-contract audit, full-archive scalar extraction, and manifest assembly. The gate verdict is positive with recorded defects: row identity is exact (zero target_id duplication), every sampled row satisfies the array-length contract, and the only structural anomaly is the converter's arm concatenation, which leaves 177 exactly duplicated wavelengths per row and two monotonicity breaks at fixed pixel positions. The rr_chi2 column is absent and cannot be assessed. The corpus was verified unchanged (10,793/10,793 files, size and mtime identical) after all scanning, and the manifest artifacts were assembled with round-trip validation.

---

## 2. Scope and Method

Corpus root: `/mnt/nvme01/desidr1-parquet-tiles/desi-qad`, 10,793 Parquet files matching `tile_*/qso_data_TILE*.parquet`, treated read-only throughout. A size-plus-mtime snapshot of every file was taken before any scan (`work/corpus_snapshot.parquet`) and re-verified after all analysis and manifest assembly.

Tool versions: python 3.12.3, pyarrow 23.0.1, numpy 2.4.3, pandas 3.0.1. All tooling lives in this repository (`scripts/01` through `scripts/05` over the shared `dqad_audit` package); the audit applied no filters of its own and read corpus files only.

Scope split:

| Stage | Scope | Coverage |
|---|---|---|
| D1 inventory | full archive | 10,793 files; row counts, schema diff, unique target_id counts |
| D2 identity | full archive | 1,030,934 target_id values |
| D3 array contract | sampled | 24,793 rows in 514 files |
| D4+D5 scalars | full archive | 1,030,934 rows; z, ra, dec, flux/ivar/mask-derived scalars |
| D6 manifest | assembly | file and object manifest tables plus provenance |

D3 sampling design: deterministic systematic selection with seed 20260815. Tile stride is floor(10793/512) = 21 with seeded start offset 17 (from `numpy.random.default_rng(20260815).integers(0, 21)`), giving 514 selected tiles (the target was 512; the systematic stride over 10,793 files yields 514 indices). Per tile, 64 rows were selected by the same systematic scheme with a per-tile child seed from `numpy.random.SeedSequence(20260815).spawn(514)`. The target was 514 x 64 = 32,896 rows; 24,793 were achieved because 185 of the selected files hold fewer than 64 rows and contribute all of their rows. Every selection parameter is recorded in `work/array_audit_summary.json`.

Full provenance for the manifest build (input artifact md5s and row counts, tool commit, sampling parameters, per-table row counts and byte sizes, round-trip and corpus-integrity blocks) is in `/mnt/nvme01/desidr1-manifest/manifest_provenance.json`.

---

## 3. Q1: Identity and Duplication

Total rows: 1,030,934 (exact, from Parquet metadata per file). Unique target_id values: 1,030,934 (exact). Duplication is zero: the repeat-count distribution is entirely k = 1 (max k = 1), within-tile duplication is 0 duplicate ids over 0 (tile_id, target_id) pairs, cross-tile duplication is 0, and the duplication rate is 0.0. Every row is one object.

The archive is a strict subset of the DR1 QSO-classified population. The converter applies the filter

```python
if row["SPECTYPE"] == "QSO" and row["ZWARN"] == 0
```

at line 90 of `02-extract-qso-tile-to-parquet.py` (source: `/opt/agents/repos/desi-cosmic-void-galaxies/work-logs/03-spectral-tile-pipeline/02-extract-qso-tile-to-parquet.py`, frozen reference), applied to the per-tile redrock REDSHIFTS HDU. ZWARN == 0 means the redrock fit passed all quality checks; non-zero ZWARN rows are dropped. Gap accounting: 1,550,000 reference population minus 1,030,934 unique ids = 519,066 (33.4881%), attributable to this selection filter rather than row loss.

Holdout verdict: target_id grouping is not required for leak-free splits. Zero duplicates were measured, so a plain row-level split cannot place the same object on both sides. The physical reason is that DESI HEALPix coadds merge reobservations of a target into a single spectrum per pixel, so the same-object-two-rows leakage the spec was concerned about cannot occur in this archive as built.

---

## 4. Q2: Array Contract

All numbers in this section are measured over the 24,793 sampled rows.

Alignment: 0 failures. All four arrays (wavelength, flux, ivar, mask_array) have equal length in every sampled row; the misalignment rate is 0.0.

Monotonicity: 24,793 of 24,793 rows (100%) are non-monotonic, each with exactly 2 breaks at pixel indices 2750 and 5076. A break means wavelength[i+1] < wavelength[i]; equal adjacent values count as duplicates, not breaks. The two breaks are the B/R and R/Z arm joints.

Arm overlap: B-R overlap is 40.0 Angstrom (B peaks at 5800, R restarts at 5760); R-Z overlap is 100.0 Angstrom (R peaks at 7620, Z restarts at 7520). Both match the nominal bands from the converter comments (5760 to 5800 and 7520 to 7620).

Duplicate wavelengths: 177 exactly duplicated values per row (mean 177.0, max 177, total 4,388,361 across the sample). The split is 51 values inside the B-R overlap band [5760, 5800] and 126 inside the R-Z band [7520, 7620]; a read-only probe of sampled row `10018:1` confirmed all 177 duplicated values fall inside the two bands, none outside. Near-duplicates: 0 rows, 0 pairs at a 0.1 Angstrom tolerance (the tolerance is roughly 8x finer than the ~0.8 Angstrom native grid spacing). Overlapping pixels are exactly equal, never merely close.

ivar: 0 negative values, 0 non-finite values. Exactly-zero ivar pixels are 0.4354% archive-wide over the sample (855,800 of 196,534,111 pixels); the per-row zero fraction has median 0.00442 and a heavy tail reaching 0.3595 (one row class reaches 36% zero-ivar pixels).

mask_array inventory: 14 distinct values across the sample. Top values by pixel count:

| value | count | pixel share |
|---|---|---|
| 0 | 195,684,305 | 0.99568 |
| 1 | 819,453 | 0.00417 |
| 1024 | 20,700 | 0.000105 |
| 128 | 4,481 | 0.0000228 |
| 16 | 1,609 | 0.0000082 |
| 129 | 1,384 | 0.0000070 |
| 4 | 776 | 0.0000039 |
| 132 | 469 | 0.0000024 |
| 3 | 456 | 0.0000023 |
| 5 | 170 | 0.0000009 |
| 133 | 154 | 0.0000008 |
| 1025 | 142 | 0.0000007 |
| 17 | 11 | 0.00000006 |
| 1027 | 1 | 0.000000005 |

Values are reported as measured (raw int64 DESI bitmask values with counts); the D3 summary did not decode them against the DESI ccdmask bit definitions, so no decode is asserted here. 6,797 sampled rows carry an all-zero mask.

Wavelength range: [3600.0, 9799.2001953125] Angstrom globally, identical in every sampled row. All rows have 7,927 pixels.

---

## 5. Q3: Redshift and Coverage

Redshift is finite in all 1,030,934 rows (0 non-finite, 0 at or below zero). Populated range [0.05123, 6.802568], mean 1.7567, population std 0.7116. Histogram: 69 bins of width 0.1 over edges [0.0, 6.9], under/overflow 0/0.

| q | 0 | 0.01 | 0.05 | 0.25 | 0.5 | 0.75 | 0.95 | 0.99 | 1 |
|---|---|---|---|---|---|---|---|---|---|
| z | 0.0512 | 0.3816 | 0.6903 | 1.2418 | 1.7126 | 2.2078 | 2.9905 | 3.6640 | 6.8026 |

rr_chi2 is UNRESOLVABLE. The column is absent from the archive, the documented schema predates corrections, and the D1 measured reference schema is ground truth. No proxy was invented.

Bin selection table (from the corrected intersection-formula `bin_selection` block in `work/redshift_summary.json`; common rest interval = [wave_min/(1+z_min), wave_max/(1+z_max)] with observed limits 3600.0/9799.2 Angstrom):

| bin | count | rest interval (A) | width (A) | linear grid @1.0 A | log grid @1e-4 dex | workability |
|---|---|---|---|---|---|---|
| [0, 0.25) | 3,553 | [3600.0, 7839.4] | 4239.4 | 4,239 | 3,380 | positive_width |
| [0.25, 0.5) | 18,872 | [2880.0, 6532.8] | 3652.8 | 3,653 | 3,557 | positive_width |
| [0.5, 0.75) | 43,633 | [2400.0, 5599.5] | 3199.5 | 3,200 | 3,679 | positive_width |
| [0.75, 1) | 83,327 | [2057.1, 4899.6] | 2842.5 | 2,842 | 3,769 | positive_width |
| [1, 1.25) | 112,539 | [1800.0, 4355.2] | 2555.2 | 2,555 | 3,837 | positive_width |
| [1.25, 1.5) | 130,419 | [1600.0, 3919.7] | 2319.7 | 2,320 | 3,891 | positive_width |
| [1.5, 1.75) | 144,523 | [1440.0, 3563.3] | 2123.3 | 2,123 | 3,935 | positive_width |
| [1.75, 2) | 137,837 | [1309.1, 3266.4] | 1957.3 | 1,957 | 3,971 | positive_width |
| [2, 2.25) | 116,440 | [1200.0, 3015.1] | 1815.1 | 1,815 | 4,001 | positive_width |
| [2.25, 2.5) | 91,287 | [1107.7, 2799.8] | 1692.1 | 1,692 | 4,027 | positive_width |
| [2.5, 2.75) | 59,069 | [1028.6, 2613.1] | 1584.5 | 1,585 | 4,049 | positive_width |
| [2.75, 3) | 39,065 | [960.0, 2449.8] | 1489.8 | 1,490 | 4,069 | positive_width |
| [3, 3.25) | 23,166 | [900.0, 2305.7] | 1405.7 | 1,406 | 4,086 | positive_width |
| [3.25, 3.5) | 12,714 | [847.1, 2177.6] | 1330.5 | 1,331 | 4,101 | positive_width |
| [3.5, 3.75) | 6,147 | [800.0, 2063.0] | 1263.0 | 1,263 | 4,114 | positive_width |
| [3.75, 4) | 4,102 | [757.9, 1959.8] | 1201.9 | 1,202 | 4,126 | positive_width |
| [4, 4.25) | 2,033 | [720.0, 1866.5] | 1146.5 | 1,147 | 4,137 | positive_width |
| [4.25, 4.5) | 876 | [685.7, 1781.7] | 1096.0 | 1,096 | 4,147 | positive_width |
| [4.5, 4.75) | 384 | [654.5, 1704.2] | 1049.7 | 1,050 | 4,156 | positive_width |
| [4.75, 5) | 253 | [626.1, 1633.2] | 1007.1 | 1,007 | 4,164 | positive_width |
| [5, 5.25) | 258 | [600.0, 1567.9] | 967.9 | 968 | 4,172 | positive_width |
| [5.25, 5.5) | 99 | [576.0, 1507.6] | 931.6 | 932 | 4,179 | positive_width |
| [5.5, 5.75) | 227 | [553.8, 1451.7] | 897.9 | 898 | 4,185 | positive_width |
| [5.75, 6) | 52 | [533.3, 1399.9] | 866.6 | 867 | 4,191 | positive_width |
| [6, 6.25) | 40 | [514.3, 1351.6] | 837.3 | 837 | 4,196 | positive_width |
| [6.25, 6.5) | 12 | [496.6, 1306.6] | 810.0 | 810 | 4,202 | positive_width |
| [6.5, 6.75) | 6 | [480.0, 1264.4] | 784.4 | 784 | 4,206 | positive_width |
| [6.75, 7] | 1 | [464.5, 1224.9] | 760.4 | 760 | 4,211 | positive_width |
| [0.5, 2] broad | 652,278 | [2400.0, 3266.4] | 866.4 | 866 | 1,339 | positive_width |
| [1, 2.5] broad | 733,045 | [1800.0, 2799.8] | 999.8 | 1,000 | 1,919 | positive_width |
| [1.5, 3] broad | 588,221 | [1440.0, 2449.8] | 1009.8 | 1,010 | 2,308 | positive_width |
| [2, 3.5] broad | 341,741 | [1200.0, 2177.6] | 977.6 | 978 | 2,588 | positive_width |
| full range | 1,030,934 | [3424.6, 1255.9] | -2168.7 | -2,169 | -4,357 | disjoint |

Fine-bin counts sum exactly to 1,030,934. Every fine and broad bin has a positive-width common rest interval; the full populated range is disjoint (interval width -2168.7 Angstrom), meaning per-object rest windows across the full redshift span no longer intersect and no single common rest-frame grid exists. Reading the tradeoff from the table: narrower redshift bins buy wider common rest coverage at the cost of population (the [6.75, 7] bin holds 1 object), and the broad bins concentrate 33% to 71% of the archive over roughly 870 to 1010 Angstrom of common rest window. Workability here is the mechanical flag only; identifying a workable combination of population size and rest-frame coverage is an operator decision, and no bin is selected in this report.

---

## 6. Q4: Nuisance Baseline

Definitions (plan-fixed, version snr-mask-v1):

```
snr_proxy      = median(flux_i * sqrt(clip(ivar_i, 0, None)) over pixels where mask_array_i == 0)
masked_fraction = mean(mask_array != 0)
```

snr_proxy is an Arrow null (not NaN) when a row has zero unmasked pixels; the null count is 0. Non-finite snr_proxy values excluding nulls: 0.

snr_proxy distribution (1,030,934 rows):

| q | 0 | 0.01 | 0.05 | 0.25 | 0.5 | 0.75 | 0.95 | 0.99 | 1 |
|---|---|---|---|---|---|---|---|---|---|
| snr | -5.205 | 0.312 | 0.562 | 1.231 | 2.325 | 4.663 | 12.404 | 23.077 | 138.033 |

masked_fraction distribution:

| q | 0 | 0.01 | 0.05 | 0.25 | 0.5 | 0.75 | 0.95 | 0.99 | 1 |
|---|---|---|---|---|---|---|---|---|---|
| masked | 0.0 | 0.0 | 0.0 | 0.0 | 0.00416 | 0.00643 | 0.00984 | 0.01262 | 0.21181 |

Pathological tails: 69 rows have snr_proxy at or below zero (share 6.69e-5 of all rows), from negative median flux over unmasked pixels; a downstream consumer can exclude them by sign. 328,246 rows (31.84%) have masked_fraction exactly zero; the maximum is 0.2118 and no row exceeds 0.5. The extreme tails of the two nuisance axes do not co-occur: zero rows combine heavy masking with a null or non-positive snr_proxy.

---

## 7. Loader Requirements

Measured requirements for the anomaly-detection data loader; each is grounded in the numbers above.

1. Arm merge policy: never assume global wavelength sortedness. Stable-sort each row by wavelength, then merge groups of exactly equal wavelengths via inverse-variance (ivar) weighted coadd. Expected reduction is 7,927 to 7,750 pixels per row (177 duplicated values, all inside the two overlap bands, exactly equal and never near-duplicates at 0.1 Angstrom). Keeping the first occurrence instead of coadding is acceptable when coadding is out of scope.
2. Mask handling: mask_array holds int64 DESI bitmask values. Treat nonzero as masked. The measured value set is {0, 1, 3, 4, 5, 16, 17, 128, 129, 132, 133, 1024, 1025, 1027} with the frequencies in section 4; value 0 covers 99.57% of sampled pixels.
3. Alignment guarantees: wavelength/flux/ivar/mask lengths are mutually aligned (0 failures in 24,793 sampled rows), but the wavelength axis is not monotonic until the arms are merged as in item 1.
4. Holdout: target_id grouping is not required. Zero duplicates were measured archive-wide, so a plain row-level train/holdout split is leak-free in target_id terms.
5. ivar handling: clip to >= 0 before any sqrt use. 0 negative and 0 non-finite values were measured, so the clip is cheap insurance rather than a repair. Zero-ivar pixels are 0.44% overall with a heavy row tail (one row class reaches 36%); weighting schemes should tolerate a substantial share of zero-weight pixels in that tail.

---

## 8. Open Defects

Archive-level defects. Each requires either regeneration of the archive or a documented loader-side accommodation; none blocks Stage 0 sign-off because the loader requirements above cover them.

1. rr_chi2 is absent from every file. The documented schema predates corrections; the D1 measured reference schema is ground truth. Fixing the documentation requires no regeneration; obtaining the column would.
2. Wavelength arrays are non-monotonic (2 breaks per row at pixel indices 2750 and 5076) with 177 exactly duplicated wavelengths per row, contradicting any claim of monotonic concatenated grids. Root cause is in the frozen converter: `full_wave = np.concatenate(wave_data)` (line 143) concatenates the B/R/Z arms without de-duplication of overlapping wavelengths. The fix belongs in a v2 converter or in the loader per requirement 1; the defect is recorded here because the archive as built carries it.
3. One row class reaches 36% zero-ivar pixels (measured per-row maximum 0.3595) against an archive-wide 0.44% zero-ivar share. Not a contract violation, but a tail that ivar-weighted consumers should be aware of; if it proves problematic it would need investigation at the converter or upstream calibration level.

---

## 9. Provenance

Source versions: python 3.12.3, pyarrow 23.0.1, numpy 2.4.3, pandas 3.0.1, all runs on host ml01.

Filters applied: the converter's selection filter quoted verbatim in section 3 (`if row["SPECTYPE"] == "QSO" and row["ZWARN"] == 0`, line 90 of `02-extract-qso-tile-to-parquet.py`, applied to the per-tile redrock REDSHIFTS HDU). The audit itself applied no filters; all stages were read-only.

Counts: 10,793 files discovered and scanned; 1,030,934 total rows; 1,030,934 unique target_ids; 0 duplicates; 24,793 sampled rows over 514 tiles; 0 alignment failures; 2 monotonicity breaks per row; 40.0 and 100.0 Angstrom arm overlaps; 177 duplicate wavelengths per row; 0.4354% zero-ivar pixels; z range [0.05123, 6.802568] with median 1.71259; snr_proxy median 2.325 with 69 rows at or below zero; masked_fraction median 0.00416 with 31.84% exactly zero.

Tool commit: b38f2082d747a4e75941b960597bdb0a4d8bb59e (audit code state at manifest build time, recorded via git rev-parse HEAD in the provenance JSON).

Sampling: seed 20260815; systematic stride selection (stride 21, start 17, 514 tiles) with per-tile child seeds from SeedSequence spawning; 64 rows per tile target, 24,793 achieved; full recipe in `work/array_audit_summary.json` and mirrored in the manifest provenance JSON.

Manifest artifacts (assembled by `scripts/05_build_manifest.py`, round-trip validated with pyarrow, spot-check seed 20260815, 10 rows per table):

| Artifact | Rows | Bytes |
|---|---|---|
| `/mnt/nvme01/desidr1-manifest/qso_file_manifest_v1.parquet` | 10,793 | 353,197 |
| `/mnt/nvme01/desidr1-manifest/qso_object_manifest_v1.parquet` | 1,030,934 | 44,984,786 |
| `/mnt/nvme01/desidr1-manifest/manifest_provenance.json` | n/a | n/a |

Corpus integrity verification: 10,793 of 10,793 files unchanged; size_bytes and mtime_ns identical to the pre-audit snapshot for every file (mismatch count 0).
