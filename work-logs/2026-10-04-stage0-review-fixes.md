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
