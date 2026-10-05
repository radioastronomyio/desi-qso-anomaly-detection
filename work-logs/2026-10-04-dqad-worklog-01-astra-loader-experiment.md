<!--
---
title: "Astra Loader and First Experiment: Boundary Checkpoint"
description: "Pre-Gate 0 stop because the Stage 0 branch required as the stacked PR base is not published on origin"
author: "Codex"
date: "2026-10-04"
version: "0.1"
status: "active"
tags:
  - type: worklog
  - domain: ard-consumer
  - tech: [python, parquet, desi]
related_documents:
  - "[Repository instructions](../AGENTS.md)"
  - "[Stage 0 audit](2026-08-15-spectral-manifest-audit.md)"
  - "[Staging index](../staging/2026-10-04-astra-loader-experiment/README.md)"
---
-->

# Astra Loader and First Experiment: Boundary Checkpoint

## Summary

| Attribute | Value |
|-----------|-------|
| Status | Stopped before Gate 0 completion |
| Authorization | User reports Don approved this unit on 2026-10-04 at 21:10 ET: "Approved, execute" |
| Intended spec | `staging/2026-10-04-astra-loader-experiment/2026-10-04-dqad-spec-01-astra-loader-experiment.md` (not written) |
| Gates completed | None |
| Boundary | B-01: required stacked PR base is absent from origin |

Objective: specify and build an experimental manifest-driven spectral loader, compare two or three candidate redshift bins with a bounded baseline, and publish the working branch and PR for Don's window decision.

Outcome: read-only preflight found that the required Stage 0 base is local only. Execution stopped under the user's boundary rule. No loader, tests, experiment, gate commit, working branch, push, or PR was created.

## 1. Work Completed

- Read the repository's `AGENTS.md` first. It gives project context and conventions but does not define an additional context-loading sequence.
- Read the Stage 0 audit, the explicitly required `/opt/agents/repos/local-agent-skills/skills/spec-driven-prompt/SKILL.md` and its template, and repository documentation standards. The user's explicit staging location, allowed committed files, gate cadence, and authorization govern this unit.
- Confirmed the initial checkout was clean on `spec/2026-08-15-dqad-01-spectral-manifest`.
- Ran the requested `git fetch origin`, then inspected fetched history and advertised remote heads.
- Confirmed host `ml01`; the corpus and manifest directories exist. No corpus arrays or manifest tables were read or modified, and no experiment was run.
- Confirmed the existing shared Python environment has the analysis dependencies. No installation or environment modification was performed.

## 2. B-01: Stacked PR Base Is Not Published

Observed evidence:

```text
git rev-parse HEAD origin/main
dc14bed6044e02bf99a70bf6cc4fda547ab167b6
f5ad82bc8d4c9b8bc4e7d81164e0a3e2f380c6a3

git branch -r --contains dc14bed
(no output)

git ls-remote --heads origin
f5ad82bc8d4c9b8bc4e7d81164e0a3e2f380c6a3 refs/heads/main
```

Remote: `https://github.com/radioastronomyio/desi-qso-anomaly-detection.git`.

The user requires branching from Stage 0 and targeting it with a stacked PR when Stage 0 is absent from `origin/main`. Stage 0 is absent from `origin/main`, and its branch is also absent from the remote's advertised heads. A stacked PR cannot target that unpublished base.

Resolution would require publishing the pre-existing Stage 0 branch in addition to this unit's working branch, or changing the prescribed PR base. The former widens the remote write scope; the latter changes an explicit instruction. Neither was performed. This is an execution-scope boundary, not a loader or scientific finding.

**Decision B-01:** Does Don authorize publishing the existing `spec/2026-08-15-dqad-01-spectral-manifest` branch at `dc14bed6044e02bf99a70bf6cc4fda547ab167b6` as the stacked PR base? Alternatively, the coordinator can publish that base separately and direct this unit to resume.

## 3. Files Changed

Only this uncommitted worklog and the gitignored staging `README.md` were created to satisfy the requested stop record. No completed-gate commit is claimed. The intended spec filename above determines this worklog's matching name.

The default sandbox runner failed before executing its first reads (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`). Approved host-runner calls supplied the read-only preflight. No automatic approval rejection occurred.

## 4. Resumption

After B-01 is resolved, recheck repository state and fetch origin. Apply the user's original base-selection rule, finish Gate 0's spec and checkpoint commit, then execute Gates 1 and 2 under their unchanged validations and boundaries. Preserve the final redshift-window choice for Don. Corpus and manifests remain read-only.

<!-- Source: User-authorized Codex unit, 2026-10-04; stopped at B-01 before Gate 0. -->

## 5. B-01 Resolved: Explicit Authorization and Stage 0 Publication

The user explicitly authorized: "Push spec/2026-08-15-dqad-01-spectral-manifest to origin at dc14bed exactly as it stands: no rebase, no amend, no force. Open a PR for it against main as the Stage 0 unit, referencing its spec and work-logs/2026-08-15-spectral-manifest-audit.md. Then resume at Gate 0 and open the loader PR against that branch as a stacked PR ... Do not merge either PR." This expands only the specified remote publication scope.

Published exactly `dc14bed6044e02bf99a70bf6cc4fda547ab167b6` and opened [Stage 0 PR #1](https://github.com/radioastronomyio/desi-qso-anomaly-detection/pull/1) against main, referencing the archived ML01 spec and findings worklog. No rebase, amendment, force-push, or merge occurred. Fresh baseline verification: `python -m pytest` reports **133 passed in 0.94s**. Created `codex/2026-10-04-astra-loader-experiment` from the Stage 0 branch after fetching origin.

## 6. Gate 0: Spec and Plan

Wrote the loader/experiment spec and plan in the requested staging directory. The spec freezes inverse-variance exact-overlap coaddition, strict valid-support rest-frame bin averaging with squared-coefficient variance propagation, three candidate windows, 768 selected rows per candidate, an 8-component PCA baseline, global tile holdout, nuisance checks and the final decision surface for Don. It does not adopt a window.

Measured prerequisites: manifest row counts 10,793/1,030,934; both MD5s match manifest_provenance.json. File-manifest SHA-256: `2fb6a9297f9a40c8d1b4016e730e7dbd7f15a43283eee2eb018ae7ff4f736579`; object-manifest SHA-256: `cc7a38ab5ec75b65f5799461e937c992916d3a90acf77e3ee1088ca70d21b477`. Candidate counts independently match 43,633 / 144,523 / 733,045. Live target 39633179815971105 has four 7,927-length arrays, breaks [2750,5076], 7,750 unique wavelengths and bounds [3600.0,9799.2001953125]. Null sampled-audit columns in the object manifest are expected; zero-ivar fraction will be recomputed from loaded arrays rather than interpreting null as zero.

Runtime: ML01, Python 3.12.3, NumPy 2.4.3, PyArrow 23.0.1, pandas 3.0.1, SciPy 1.17.1, scikit-learn 1.8.0 (installed, not needed by planned NumPy PCA), Matplotlib 3.10.8, pytest 9.0.2. No package installation.

Self-review: each gate has discriminating validation; all user deliverables have an owning plan task; exact committed-file scope is preserved. User-selected staging and worklog paths supersede generic workflow locations; the worklog is the execution ledger, and no cleanup deletion is permitted.

Gate 0 spec SHA-256: `0b188e3c3342d381adbebcef3301deb2e3ecd8e1b1aedbafec646fd63822c802`. Gate 0 commit includes only this worklog.

## 7. Gate 1: Experimental Loader

Implemented `spectral_loader.py` and `manifest_loader.py` within the existing audit package, plus 27 discriminating tests. Stable exact-wavelength coaddition excludes masked, zero-weight, and nonfinite-flux contributions; source mask bits are separate from merged validity. Rest-frame bins use piecewise-constant native pixels and squared overlap coefficients for variance. A bin with incomplete valid support has zero weight rather than a fabricated interpolation. Manifest row_uids are resolved to file row groups and checked against target_id, redshift, array length, source stats, and the measured wavelength contract.

Evidence: the new tests initially failed because the two implementation modules did not exist, then all 27 passed. Full repository suite: **160 passed in 0.85s**. Focused Ruff and Black checks pass; `git diff --check` is clean. Tests cover unequal ivar coaddition, masked/all-invalid duplicates, exact versus near-equal wavelengths, audit shape and breaks, hand-calculated resampling variance, masks/zero-ivar/extrapolation, rest coordinates and window endpoints, manifest membership, identity, outside paths and live contract changes.

The nine-row live smoke check passed over three seeded rows per candidate, with all 7,927 input pixels satisfying the audit contract. Output grids contain 799/530/249 bins for W1/W2/W3; valid-pixel counts were 779–792 / 519–530 / 249. Evidence: `staging/2026-10-04-astra-loader-experiment/gate1-smoke.json`, including exact target IDs, row_uids and rest bounds. All selected source size/mtime pairs and both manifest hashes were unchanged. No rr_chi2 proxy was introduced.

Limitations carried forward: independent-arm diagonal noise assumption, induced covariance between bins sharing native pixels, and observed flux-density amplitudes on transformed wavelength coordinates. Gate 1 does not make a physical-anomaly claim. No new scope boundary was encountered.
