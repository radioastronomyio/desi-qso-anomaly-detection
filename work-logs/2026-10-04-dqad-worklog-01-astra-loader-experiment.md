<!--
---
title: "Astra Loader and First Experiment: Gate Worklog"
description: "Gate 0–2 evidence, resolved publication boundary and three-window baseline results"
author: "Codex"
date: "2026-10-04"
version: "0.1"
status: "under-review"
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

# Astra Loader and First Experiment: Gate Worklog

## Summary

| Attribute | Value |
|-----------|-------|
| Status | Gates 0–2 validated; prepared for authorized stacked PR publication |
| Authorization | User reports Don approved this unit on 2026-10-04 at 21:10 ET: "Approved, execute" |
| Intended spec | `staging/2026-10-04-astra-loader-experiment/2026-10-04-dqad-spec-01-astra-loader-experiment.md` |
| Gates completed | 0 (483aa86), 1 (2500923), 2 (this checkpoint commit) |
| Boundary | B-01 resolved by explicit authorization; final window decision reserved for Don |

Objective: specify and build an experimental manifest-driven spectral loader, compare two or three candidate redshift bins with a bounded baseline, and publish the working branch and PR for Don's window decision.

Outcome: B-01 initially stopped preflight and was subsequently resolved by the explicit authorization recorded in section 5. The experimental loader and three-window comparison are complete, with 167 passing tests and unchanged source integrity. No window is adopted. Sections 1–4 retain the initial stop record; subsequent sections record resumption and completed gates.

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


## 8. Gate 2: Bounded Baseline and Decision Report

Implemented `src/dqad_audit/experiment.py`, `scripts/06_loader_experiment.py` and seven baseline/report tests. Ran exactly the frozen three candidates, seed 20261004, 576 training plus 192 holdout selections per candidate. All 2,304 selections (2,303 unique targets; one reused between W2/W3) passed inclusion. The globally shared tile split has no train/holdout tile or target intersection. No hyperparameter search, adaptive sample increase, GPU use or package installation occurred.

| Candidate | Population | Actual common-grid edges A | Pixels | Median score | Score–SNR rho | Score–mask rho | Score–zero-ivar rho |
|-----------|-----------:|----------------------------|-------:|-------------:|--------------:|---------------:|-------------------:|
| W1 [0.5,0.75) | 43,633 | 2400–5596 | 799 | 1.3827 | 0.7597 | -0.1425 | -0.1426 |
| W2 [1.5,1.75) | 144,523 | 1440–3560 | 530 | 1.354 | 0.851 | -0.114 | -0.110 |
| W3 [1,2.5) | 733,045 | 1800–2796 | 249 | 1.142 | 0.722 | -0.155 | -0.155 |

Each row above summarizes 192 held-out scores; full precision is preserved in `run-01/*-diagnostics.json` and `*-summary.json`. The weighted residual ranking strongly tracks SNR in every window. This is an intended diagnostic result, not a violation of the loader contract or justification to select a window. Absolute scores are not calibrated for comparing grids or claiming physical anomalies.

Overlap sensitivity: median residual shares 2.75%/2.59%/2.47%, median pixel shares 2.96%/2.93%/3.25%; omitting those residual pixels retains all ten top-ranked objects in each candidate, with score correlations 0.9989/0.9988/0.9939. This is a post-fit sensitivity check, not a refit. Tile checks use only 24/21/21 objects on repeated tiles; descriptive permutation p-values are 0.450/0.646/0.112. Sparse repeated-tile coverage limits any conclusion about tile effects. Redshift/valid-coverage correlations, score deciles and per-tile tables are also saved.

All eighteen top-ranked spectrum panels and three nuisance panels were visually inspected, including full target IDs, reconstructions, propagated diagonal uncertainties, masks and overlap locations. Nuisance axes were made legible as percentages after inspection. No physical classification is asserted. Selected raw zero-ivar fractions reached only 5.89%/3.14%/2.88%; the archive's extreme approximately 36% tail was not empirically exercised by this small sample.

Artifacts: `staging/2026-10-04-astra-loader-experiment/report.md` is the decision brief, with N-01–N-04 findings and W1/W2/W3 closed questions. `run-01/report.md` contains the complete measured report; its README indexes selections, included/excluded rows, arrays, PCA models, reconstructions, scores, nuisance diagnostics, PNG/PDF figures, snapshots and provenance. The run took **127.1 seconds** and produced approximately **25 MB**. The staging root README is the entry point.

Reproducibility: all three saved-array replays matched inclusion, numerical scores (rtol 1e-10, atol 1e-12) and rank order. Exact input hashes, source-code hashes, seed, software versions and source commit at run time are in `run-01/provenance.json`. Numerical modules still match those recorded hashes. The renderer was subsequently repaired for insufficient correlations and half-open interval labels; the exact executed runner is retained as `06_loader_experiment.executed.py`, and display-only changes are recorded in `run-01/report-render-provenance.json`. No numeric experiment was rerun or tuned.

Integrity: all **10,793** manifest corpus-file size/mtime pairs matched the original Stage 0 snapshot and the before/after experiment snapshots; mismatch count zero. Both manifest Parquets and manifest_provenance.json have unchanged SHA-256 hashes. These corpus checks attest size/mtime, not byte-level content hashes. Corpus/manifests were never opened for writing.

Split limitations beyond target duplication are explicit in both reports: shared sky/calibration/exposure/camera/observing conditions, upstream QSO/ZWARN selection, redshift uncertainty and coverage, normalization and heteroskedastic noise, correlated rebinned pixels and omission of a resolution model, sparse tail evidence, and cross-candidate reuse. Choosing or tuning on these results consumes the holdout as a development set; later final evaluation needs a fresh design.

## 9. Final Review and Verification

A fresh reviewer inspected the whole change against Stage 0 and the frozen spec, including untracked Gate 2 code. It found no Critical/Important numerical implementation issue. It ran 33 focused tests and independently matched 100 randomized irregular-grid resampling cases to a scalar calculation. The review raised two lower-priority follow-ups.

**Final ruling:** promoted the report's inability to render a missing overlap correlation to an implementation issue because the spec requires explicit insufficient-data outcomes. A new test reproduced the TypeError, then passed after the display fix. This changes no fitted model or numeric result; the cost of the ruling is limited to a more robust renderer. That test also preserves target IDs above float64's exact integer range.

**Deferred minor:** add a multi-row-group temporary Parquet fixture to explicitly test row-group boundary offsets. Existing fixtures, nine-row smoke checks and the full experiment verify identity and nonzero offsets; the reviewer found the row-group implementation correct. This is additional regression coverage, not an unresolved measured defect.

Reviewer topics set aside were handled as follows: live plots and artifacts were reviewed by the root agent; physical interpretation and final window adoption remain Don's; no claim of production readiness beyond the bounded experimental contract is made. These scope decisions avoid scientific claims unsupported by this sample.

Final verification: **167 tests passed in 1.71s** (34 new); focused Ruff and Black checks pass for all seven new code/test files; `git diff --check` is clean. Frozen spec SHA-256, numerical source hashes and all report/index local links were verified. Gate 2 includes only code, tests and this worklog. All spec/plan/experiment artifacts remain gitignored. No new stop boundary arose after B-01 resolution.

## 10. Closeout Handoff

Gate 0 commit: `483aa86`; Gate 1 commit: `2500923`; this Gate 2 checkpoint receives its commit before the authorized ordinary branch push. The loader PR targets `spec/2026-08-15-dqad-01-spectral-manifest`, explicitly stacked on [Stage 0 PR #1](https://github.com/radioastronomyio/desi-qso-anomaly-detection/pull/1). Publication URLs and final head verification are recorded in the staging README and chat after PR creation; no extra gate or history rewrite is needed to put a commit's own SHA inside itself.

Stage 0 remains exactly `dc14bed6044e02bf99a70bf6cc4fda547ab167b6`. Neither PR may be merged by the agent. Working branch and local artifacts remain for review. The intended unit ends with Don's window decision, and no downstream VAE training is dispatched here.

## 11. Authorized PR #2 Review Fix Round

The user authorized R1–R6 from `staging/2026-10-04-astra-loader-experiment/claude-review-pr2.md`, with one new commit per review ID, ordinary pushes to PR #2's existing branch, attribution trailers on every new commit, and no rebase/amend/force-push/merge. Scope now explicitly includes dependency declarations and their clean-install validation. Run-01 must remain the recorded decision evidence and must not be rerun; its W2 selection is the next unit's reference sample. Corpus and manifests remain read-only.

Builder attribution for this unit and fix round: **GPT, Astra, GPT Work DESI project, spec `2026-10-04-dqad-spec-01`**. New commits use the existing repository co-author identity `astronomy-coding-bot <astronomy-coding-bot@radioastronomy.site>`, with `Model: GPT (Astra; GPT Work DESI project)` and `Spec: 2026-10-04-dqad-spec-01`. The original three gate commits are preserved unchanged.

Starting head: `85bb36494dfcacb93c932dd727fcbb406e549a5f`; local and fetched remote heads agree. The fix-round evidence directory is `staging/2026-10-04-astra-loader-experiment/review-fixes/`. Its `run-01-before.json` records SHA-256, byte count and mtime for all 46 run-01 files before any fixes. No data experiment will be invoked during this round.

### R1: Runtime dependencies and clean installation

Declared SciPy (`>=1.12`) and Matplotlib (`>=3.8`) as runtime dependencies in `pyproject.toml`, so the ordinary install includes experiment and report imports. A fresh wheel build also exposed the pre-existing invalid `project.authors[0].url` field; the author name is preserved and its URL moved to the valid `[project.urls]` table. This metadata repair is necessary to satisfy R1's clean-install requirement and changes no scientific behavior.

Verified install path from the repository root:

```bash
python -m venv /tmp/dqad-pr2-clean-env
/tmp/dqad-pr2-clean-env/bin/python -m pip install '.[dev]'
MPLCONFIGDIR=/tmp/dqad-pr2-matplotlib OPENBLAS_NUM_THREADS=2 \
  /tmp/dqad-pr2-clean-env/bin/python -m pytest
```

The concrete validation used newly created `/tmp/dqad-pr2-review-venv.l0PnUN`, without system/shared site packages. `pip install '.[dev]'` built and installed the package successfully; **167 tests passed in 2.10s**; `pip check` reported no broken requirements. An isolated (`python -I`) import resolved `dqad_audit.experiment` from that environment's installed package, with SciPy 1.18.1 and Matplotlib 3.11.2, and no shared-venv path. Full resolved versions and install/test logs are preserved under `review-fixes/R1-*`. The initial metadata failure is retained in `R1-install.log`; its successful correction is in `R1-install-retry.log`. Run-01 was not rerun or changed.

### R2: Parquet row-group boundary regression coverage

Extended the temporary archive fixture to write multiple rows and configurable row-group sizes. The new test writes seven distinct spectra in groups of sizes `[2,2,2,1]` and requests offsets `[6,2,1,4,3]`. It crosses both sides of group boundaries, including offsets 2, 4 and 6 exactly equal to cumulative group ends, and checks exact target IDs, redshifts, full flux arrays, selected row_uids, and unchanged source size/mtime.

All eight manifest-loader tests pass. An in-memory mutation changing `searchsorted(..., side="right")` to `side="left"` makes the new test fail at a boundary; the checked-out implementation was never changed. Full suite: **168 passed**. Focused Ruff/Black pass. The previously deferred multi-row-group coverage item is now resolved; no loader algorithm change was needed.

### R3: Independent window seeds and pinned W2 reference

`select_candidates` now uses fixed `SeedSequence(seed).spawn(3)` children keyed permanently to window IDs: W1 `(0,)`, W2 `(1,)`, W3 `(2,)`. Reordering or omitting other candidates no longer changes a window's sample. The global tile split retains its original independent `default_rng(seed)` permutation; callers must supply the full manifest even when requesting `window_ids=("W2",)`. Future provenance identifies `window-child-seeds-v2` and each window's spawn key.

Before the fix, regressions demonstrated that omitting W1 or moving W2 to the end changed its selected rows. They now pass, and each W1/W2/W3 subset exactly matches the corresponding full-selection result. Full suite: **173 passed**. Ruff/Black checks pass. Tests use synthetic manifests only; no data experiment was rerun.

**Reference sample for the next unit:** `/opt/agents/repos/desi-qso-anomaly-detection/staging/2026-10-04-astra-loader-experiment/run-01/W2-selection.csv`; SHA-256 `229e16debf3688edb3799876e5424d7050a49f3dfa55928ca2995bb2b56cc997`; 94,101 bytes; 768 unique targets, split into 576 training and 192 holdout rows with zero shared tiles. The original source state is `85bb364`. The next unit must load that file and its recorded split rather than regenerate it with the new sampler. `review-fixes/W2-reference-sample.json` and the staging README carry the same explicit handoff. All run-01 files remain the original recorded evidence.

### R4: Builder attribution and prospective commit trailers

Updated PR #2's body with explicit builder attribution: **GPT, Astra, GPT Work DESI project, spec `2026-10-04-dqad-spec-01`**. Read the published body back and verified that exact attribution. Existing reviewer-generated body content was preserved. This attribution applies to the original loader unit as well as this fix round; the original gate commits remain unchanged.

Every new fix commit, beginning with R1, carries the established `Co-authored-by: astronomy-coding-bot <astronomy-coding-bot@radioastronomy.site>` identity, `Model: GPT (Astra; GPT Work DESI project)`, and `Spec: 2026-10-04-dqad-spec-01`. R1/R2/R3 commits are `d83404d`, `b978d57` and `1118eae`; this R4 checkpoint and the remaining fixes use the same trailer block. This documentation-only item requires no new numerical test. No rebase, amendment or history rewrite was performed.
