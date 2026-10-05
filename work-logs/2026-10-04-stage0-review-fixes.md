<!--
---
title: "Stage 0 PR #1 review fixes"
description: "Authorized S1–S7 robustness fixes and qualifications, preserving the sealed audit and manifests"
author: "GPT, Astra, GPT Work DESI project"
date: "2026-10-04"
version: "1.0"
status: "under-review"
tags:
  - type: worklog
  - domain: spectral-analysis
  - tech: [python, parquet, desi]
related_documents:
  - "[Sealed audit](2026-08-15-spectral-manifest-audit.md)"
---
-->

# Stage 0 PR #1 review fixes

## Authorization and scope

The user authorized S1–S7 from `staging/2026-10-04-astra-loader-experiment/claude-review-pr1.md` and PR #1's Greptile/Codex reviews: one new commit per ID on `spec/2026-08-15-dqad-01-spectral-manifest`, with Co-authored-by, Model and Spec trailers, followed by an ordinary branch push. No amend, rebase, force-push or merge is authorized. The corpus and `/mnt/nvme01` manifests remain read-only; Stage 0 must not be rerun and manifests must not be regenerated. The August worklog is sealed. PR #2 must be checked for clean applicability without changing its branch.

Starting Stage 0 head: `dc14bed6044e02bf99a70bf6cc4fda547ab167b6`. Loader PR #2 head: `6a924642c6d800ec0e60144c3eea8f9a6528ccab`. Fix builder: GPT, Astra, GPT Work DESI project. Spec: `spec/2026-08/2026-08-15-dqad-spec-01-spectral-manifest.md`. This is a review-fix ledger, not a replacement audit. Evidence is indexed in the gitignored `staging/2026-10-04-astra-loader-experiment/stage0-review-fixes/README.md`; the pre-edit record pins the sealed report and external manifest-directory files by SHA-256, size and mtime.

## S1: Fail closed on selected-file read failures

D3 now discards the entire failed chunk from checkpoint publication and aborts before assembling or publishing the final array audit and success summary. Each failed selected path and its read error are logged. Successfully completed independent chunks may remain resumable; a chunk containing even one failed file is never written as complete.

Two synthetic integration regressions exercise a corrupt Parquet file and a readable file missing a required array column. Both previously completed without raising; both now abort with no partial checkpoint, final table or success summary. Full Stage 0 suite: **135 passed**. Focused Ruff/Black pass. No real corpus input was used by these tests.

## S2: Verify checkpoint completeness before reuse

D3 and D4 now require a known expected chunk row count and a matching readable checkpoint before reuse. An absent inventory or incomplete count mapping forces a rescan. D4 also no longer writes failed chunks. This policy handles legacy partial checkpoints without trusting their readability or forgetting a source failure; no new checkpoint metadata or manifest schema is needed. Complete inventory-backed checkpoints still resume without rewriting their part files.

Five regressions failed before the change: both stages reused legacy partial chunks with no inventory, both forgot a still-unreadable source on resume, and D4 wrote failed partial chunks. All now pass. The existing D3 resume test now supplies inventory counts to exercise verified reuse. Full suite: **140 passed**; focused Ruff/Black pass. The operational README records that missing counts disable reuse and cause rescanning. Existing failed legacy parts can remain on disk for inspection but are never accepted without a matching expected count.

## S3: Validate the complete manifest set before publication

D6 now joins and validates both in-memory tables, writes both Parquets into a unique private `.manifest-build-*` directory inside the requested output directory, validates their round trips, assembles all input hashes/provenance, and validates the serialized provenance before replacing any v1 filename. Provenance continues to name the final published paths and the actual validated bytes. Private build directories are retained for inspection.

Before publication, same-filesystem hard links preserve every existing destination. A catchable replacement failure rolls back already-published members to their prior contents (or moves newly published members back into staging when no previous set existed). Ten regressions cover duplicate row IDs, sampled-join coverage, serialized object corruption, missing provenance inputs and a failure during the second publication step, each with and without an existing set. They all failed before the fix and now pass, preserving every prior v1 file's bytes and mtime or publishing nothing on a fresh failure. Full suite: **150 passed**; focused Ruff/Black and whitespace checks pass.

Operational limit: publication remains a single-writer operation over three independent filenames, not an atomic filesystem transaction against power loss, SIGKILL or failure of the rollback filesystem itself. Retained build directories and prior-generation links support inspection/recovery in those circumstances. The implemented guarantee covers validation failures and catchable publication errors without changing the existing v1 path/schema contract. No real manifest build or corpus audit was invoked.

## S4: Qualify the population-gap attribution

Changed future D2 summary wording to: **consistent with the converter's SPECTYPE==QSO and ZWARN==0 filter; the excluded count was not measured**. The [dated erratum](../docs/2026-10-04-stage0-gap-attribution-erratum.md) explains that subtracting the measured archive count from a reference and quoting a filter does not measure the excluded reference population or prove that selection explains the whole difference. The sealed August report and historical external outputs were not edited. No scientific quantity or selection rule changed; existing identity tests and focused Ruff/Black verify the edited module.

The existing identity-summary regression expected the raw converter expression inside the old causal prose and initially failed after this wording change. It now verifies that the verbatim quote remains in the structured converter evidence and that the requested qualification is emitted. The numerical assertions are unchanged. Full suite: **150 passed**.

## S5: Honor D2's supplied argument vector

Forwarded `argv` to `parse_args`. A real synthetic D2 invocation now uses the requested work directory, converter file, population reference (10) and holdout fraction (0.2), despite unrelated hosting-process arguments. Before the fix it rejected the host option instead; afterward the saved summary contains seven rows, a gap of six, the requested source path and expected leakage 0.96. Full suite: **151 passed**. Ruff passes and Black formatted the identity tests, including the assertion introduced for S4; S4's module check passed, but its test-file formatting check had requested that wrapping change. No history was amended.

## S6: Align the advertised Python minimum

Updated the root README's Python badge from 3.11+ to 3.12+, matching `pyproject.toml`'s `requires-python = ">=3.12"`. Verified the README has no remaining 3.11 requirement. This documentation-only change requires no additional numerical test.

## S7: Document the external converter source

The scripts README now identifies the default absolute path in the separate `desi-cosmic-void-galaxies` checkout, explains that neither this package nor the corpus supplies it, and shows an explicit `--converter-source` invocation for another host. It describes read-only text extraction, the required filter/arm/wavelength expressions, recorded source provenance, and failure before summary publication when the file is unavailable or incompatible. The CLI help confirms the documented flag. The S5 integration test also exercises a supplied external source file.

## Final verification and PR #2 compatibility

Final Stage 0 checks: **151 tests passed**, repository-wide Ruff passed, Black passed (16 Python files), and whitespace validation passed. An isolated archive of PR #2 at `6a924642c6d800ec0e60144c3eea8f9a6528ccab` accepted the complete Stage 0 change patch without conflicts. Its combined suite passed **198 tests**, Ruff and Black (24 Python files). Neither branch was merged or rebased for this check. The temporary combined source snapshot and logs are indexed under `stage0-review-fixes/`; no code change on PR #2 is required by these fixes, and its branch head remains unchanged.

The sealed August report and all three external manifest artifacts match their pre-edit SHA-256, byte count and mtime exactly. Stage 0 was not rerun, manifests were not regenerated, and corpus/manifests remained read-only. All executable regression inputs were synthetic temporary fixtures. S1–S7 each receive one new attributed commit. The authorized closeout is an ordinary push to PR #1's existing branch, leaving both PRs open and unmerged; final published heads and links are recorded in the staging index after the push.

## 2026-10-05 authorized T1–T3 review round

Don authorized Action Registry `rec4acLZWCuN1tJk9` at 09:15 EDT after stating at 09:13 EDT that no response content was vetoed. Scope is the T1–T3 findings from Greptile and Codex reviews of `164d094`, one attributed commit per ID, followed by another Greptile/Codex review gate. If neither reviewer reports a new P1/P2, the authorization includes merge commits for PR #1 and PR #2, retargeting PR #2 to main, and fast-forwarding local main. Any new P1/P2 stops the merge sequence. The sealed August report and artifacts remain read-only and must not be regenerated.

### T1: Record supplied D2 arguments

When `main(argv)` is used, D2 now records the resolved script path followed by that supplied argument vector in `provenance.command`; a normal CLI invocation continues to record `sys.argv`. The dedicated synthetic integration regression supplies paths and numeric parameters while setting unrelated host-process arguments. It failed by recording only `hosting-process --unrelated-host-option`, then passed with the exact supplied vector. Full suite: **152 passed**. No sealed artifact was changed.

### T2: Preserve nonfinite values for histogram accounting

`build_redshift_block` now passes the original redshift sequence to `z_histogram`, while finite values remain the input to moments and quantiles. The dedicated regression supplies two finite and two nonfinite values. It failed with `count_nonfinite=2` beside `histogram.count_nonfinite_excluded=0`, then passed with both counts equal to two and two finite values inside the histogram. Full suite: **153 passed**. No Stage 0 output was regenerated.

### T3: Record the actual array-audit sampling stride

`build_array_audit_run_config` now accepts the actual stride returned by the sampling recipe. D3 passes `sampling_recipe["stride"]`; it no longer derives a false value from the achieved selection count. The dedicated default-scale regression supplies 514 selected paths, target 512 and stride 21. It initially failed because the builder had no true-stride input and the old calculation would produce one; it now records stride 21 and 514 selected files. Full suite: **154 passed**.

The [2026-10-05 metadata erratum](../docs/2026-10-05-stage0-metadata-erratum.md) records the sealed-artifact check. The August checkpoint `work/parts/array_audit/run_config.json` says stride 1; the true stride is 21, with start 17 over 10,793 files and 514 selected. `work/array_audit_summary.json` and the sealed report already carry the true values, and sampling was unaffected because file selection used that recipe before the faulty run-config field was built. The sealed identity command (`scripts/02_identity_duplication.py`) is correct because that run used the CLI. The sealed redshift summary is also correct: all 1,030,934 values are finite, so both nonfinite counts are zero and 1,030,934 values are inside the histogram. None of those files was edited or regenerated.

## 2026-10-05 authorized U1–U3 final review round

Don authorized this final PR #1 fix round under Action Registry `rec4acLZWCuN1tJk9`, including manual merge execution on his instruction after the stated review gate. Scope is U1–U3, one attributed commit per ID. After push, a new-head P1 stops the merge sequence. A new P2 becomes a linked GitHub issue unless it says the published manifests or sealed August record are wrong, in which case the merge sequence stops. If the review gate clears, PR #1 and then PR #2 are to be merged with merge commits, with PR #2 retargeted to `main` and confirmed mergeable first. No automated merge is enabled.

### U1: Reject an incomplete D1 inventory before D2 publication

When a present `inventory_summary.json` reports any open failure or its total row count disagrees with `target_ids.parquet`, D2 now exits before publishing `identity_summary.json`. Two synthetic regressions cover the independent failure states. Both failed by completing publication before the fix and now pass without creating a summary. Full Stage 0 suite: **156 passed**; focused Ruff passed and Black formatting passed after applying the formatter. No corpus or sealed artifact was read or regenerated.

### U2: Make D4 warning provenance independent of resume history

D4 now recomputes per-file null-SNR, nonfinite-redshift and nonpositive-redshift warning records from the complete assembled checkpoint table, attaching the source paths from the deterministic task list. Warning provenance therefore describes all assembled data regardless of whether each chunk was scanned or reused in the current invocation. The synthetic resume regression first produced a warning-bearing checkpoint, then reused it; before the fix the second summary replaced all three warning classes with zero, and afterward both summaries carry the same file counts and examples. Full Stage 0 suite: **157 passed**; focused Ruff passed and Black formatting passed after applying the formatter. No corpus or sealed artifact was read or regenerated.
