# AGENTS.md

Entry point for AI coding agents working on this repository.

## Project Identity

**Domain:** Astronomy / Machine Learning / Anomaly Detection
**Repository:** https://github.com/radioastronomyio/desi-qso-anomaly-detection
**Purpose:** Systematic discovery of statistically anomalous quasar spectra in DESI DR1 using unsupervised machine learning. Consumes the Analysis-Ready Dataset built by [desi-cosmic-void-galaxies](https://github.com/radioastronomyio/desi-cosmic-void-galaxies) to identify rare physical states, unknown object classes, and unexpected phenomena across ~1.6 million QSO spectra.

**Status:** Active. Stage 0 (spectral manifest audit) complete; next is the experimental loader implementing the measured corpus contract, then anomaly modeling.

## Current State

**Phase:** Stage 0 (spectral manifest audit) complete. Next: the experimental loader implementing the measured corpus contract (arm merge policy per the findings report), then anomaly modeling.
**Date:** August 2026

### Prerequisites Before Work Begins

None. This repo is no longer blocked on upstream ARD Tier 2 compute; the corpus contract is established by the Stage 0 audit (work-logs/2026-08-15-spectral-manifest-audit.md).

### What Exists

- Repository structure with documentation standards
- README with methodology overview and architecture
- Stage 0 spectral manifest audit, complete: shared `dqad_audit` package (src/), 5 stage scripts covering scanner stages D1-D6 (scripts/), 133 unit tests (tests/)
- Findings report: work-logs/2026-08-15-spectral-manifest-audit.md
- Manifest artifacts outside the repo at /mnt/nvme01/desidr1-manifest/: qso_file_manifest_v1.parquet, qso_object_manifest_v1.parquet, manifest_provenance.json

## Key Constraints

- This is an ARD *consumer*, not a data ingestion project
- Model, representation, scoring, and candidate evaluation belong to this repo; the earlier expectation of provider-computed LATENT_VEC, RECON_MSE, and ANOMALY_SCORE columns is retired
- The Stage 0 corpus is the local QSO Parquet archive at /mnt/nvme01/desidr1-parquet-tiles/desi-qad: 10,793 files, 1,030,934 unique target_ids, zero duplication
- The loader must implement the measured merge contract: stable-sort by wavelength, then deduplicate exactly equal wavelengths (40.0 Angstrom B-R and 100.0 Angstrom R-Z arm overlaps, 177 duplicate wavelengths per row)
- rr_chi2 is absent from the archive and is not part of the measured contract
- The VAE architecture (Spender-based) will be the primary anomaly detection method

## Execution Environment

**Primary execution:** ML01 (`/opt/agents/repos-archive/desi-qso-anomaly-detection/`)
**Agent runtime:** OpenCode (global config at `~/.config/opencode/opencode.json`)
**Session management:** aoe (Agent of Empires)
**Strategic work:** Claude.ai Projects
**Agentic coding:** Claude Code, OpenCode

## Infrastructure

| Resource | Host | Purpose |
|----------|------|---------|
| PostgreSQL 16 | radio-pgsql01 (10.25.20.8) | ARD queries, candidate ranking |
| Spectral tiles | radio-fs02 (10.25.20.15) | QSO spectra for validation |
| GPU compute | ML01 (A4000, 16GB) | Embedding inference, model training |

Connection patterns follow `/opt/global-env/research.env`. Never hardcode credentials.

## Repository Structure

```
desi-qso-anomaly-detection/
├── assets/                         # Images, diagrams, banners
├── docs/
│   ├── documentation-standards/    # Templates, tagging strategy
│   └── data-science-infrastructure.md
├── internal-files/                 # Working documents
├── scripts/                        # Stage 0 audit scripts (D1-D6)
├── shared/                         # Cross-cutting assets
├── staging/                        # Staged work (gitignored)
├── src/
│   └── dqad_audit/                 # Shared audit helpers package
├── tests/                          # Unit tests for dqad_audit and scripts
├── work-logs/                      # Milestone-based development history
├── AGENTS.md                       # This file
├── CLAUDE.md                       # Pointer to AGENTS.md
├── LICENSE                         # MIT
├── LICENSE-DATA
├── pyproject.toml                  # Audit tooling package config
└── README.md
```

## Conventions

- **Documentation:** Use templates from `docs/documentation-standards/`
- **Commits:** Conventional commits (`feat:`, `fix:`, `docs:`)
- **Frontmatter:** YAML frontmatter with tags from `docs/documentation-standards/tagging-strategy.md`
- **Interior READMEs:** Every directory has one

## Related Repositories

| Repository | Relationship |
|-----------|-------------|
| `desi-cosmic-void-galaxies` | Upstream ARD provider; its converter built the QSO Parquet corpus |
| `desi-quasar-outflows` | Sibling consumer project |
| `astronomy-rag-corpus` | Literature corpus supporting DESI research |
| `analysis-ready-dataset` | ARD methodology spec |
