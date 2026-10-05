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
