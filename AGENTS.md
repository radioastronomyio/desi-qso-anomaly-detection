# AGENTS.md

Entry point for AI coding agents working on this repository.

## Project Identity

**Domain:** Astronomy / Machine Learning / Anomaly Detection
**Repository:** https://github.com/radioastronomyio/desi-qso-anomaly-detection
**Purpose:** Systematic discovery of statistically anomalous quasar spectra in DESI DR1 using unsupervised machine learning. Consumes the Analysis-Ready Dataset built by [desi-cosmic-void-galaxies](https://github.com/radioastronomyio/desi-cosmic-void-galaxies) to identify rare physical states, unknown object classes, and unexpected phenomena across ~1.6 million QSO spectra.

**Status:** Skeletal. Blocked on upstream ARD completion (Phase 05-07 in desi-cosmic-void-galaxies).

## Current State

**Phase:** Repository setup complete. Awaiting upstream ARD embeddings.
**Date:** March 2026

### Prerequisites Before Work Begins

1. ARD Phase 05-06 must complete (validates QSO catalog)
2. ARD Phase 07 (Tier 2 compute) must generate spectral embeddings
3. LATENT_VEC, RECON_MSE, ANOMALY_SCORE columns must be populated in the ARD

### What Exists

- Repository structure with documentation standards
- README with methodology overview and architecture
- No code yet; this is intentional until the upstream data is ready

## Key Constraints

- This is an ARD *consumer*, not a data ingestion project
- All catalog data and spectral embeddings come from the upstream ARD factory
- Spectral data lives in Parquet on radio-fs02, catalog data in PostgreSQL on radio-pgsql01
- The VAE architecture (Spender-based) will be the primary anomaly detection method

## Execution Environment

**Primary execution:** ML01 (`/opt/repos/desi-qso-anomaly-detection/`)
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
├── shared/                         # Cross-cutting assets
├── spec/                           # Specifications
├── staging/                        # Staged work (gitignored)
├── work-logs/                      # Milestone-based development history
├── AGENTS.md                       # This file
├── CLAUDE.md                       # Pointer to AGENTS.md
├── LICENSE                         # MIT
├── LICENSE-DATA
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
| `desi-cosmic-void-galaxies` | Upstream ARD provider (blocked on) |
| `desi-quasar-outflows` | Sibling consumer project |
| `astronomy-rag-corpus` | Literature corpus supporting DESI research |
| `analysis-ready-dataset` | ARD methodology spec |
