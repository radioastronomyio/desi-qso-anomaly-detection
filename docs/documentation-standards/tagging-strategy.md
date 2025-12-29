<!--
---
title: "Tagging Strategy"
description: "Controlled vocabulary for document classification and RAG retrieval in desi-qso-anomaly-detection"
author: "VintageDon - https://github.com/vintagedon"
ai_contributor: "Claude Opus 4.5 (Anthropic)"
date: "2025-12-29"
version: "1.0"
tags:
  - domain: documentation
  - type: specification
related_documents:
  - "[Interior README Template](interior-readme-template.md)"
  - "[General KB Template](general-kb-template.md)"
---
-->

# Tagging Strategy

## 1. Purpose

This document defines the controlled tag vocabulary for all documentation in desi-qso-anomaly-detection, enabling consistent classification for human navigation and RAG system retrieval.

---

## 2. Scope

Covers all tag categories, valid values, and usage guidance. Does not cover front-matter field structure—see individual templates for field requirements.

---

## 3. Tag Categories

### Phase Tags

Pipeline execution phases. Documents may belong to multiple phases.

| Tag | Description |
|-----|-------------|
| `phase-01` | Candidate ranking (ARD queries, anomaly scoring) |
| `phase-02` | Visual validation (human review, classification) |
| `phase-03` | Cross-match (multi-wavelength context) |
| `phase-04` | Catalog release (curation, publication) |

**Usage**: Tag with all phases a document supports. A methodology doc explaining validation UI used in phases 02-03 would carry `phase-02`, `phase-03`.

---

### Domain Tags

Primary functional area. Usually one per document.

| Tag | Description |
|-----|-------------|
| `candidate-ranking` | ARD queries, anomaly score filtering |
| `visual-validation` | Human review interface, classification |
| `crossmatch` | Multi-wavelength catalog joins |
| `catalog` | Curation, formatting, release |
| `ml-pipeline` | VAE architecture, embeddings (upstream reference) |
| `documentation` | Methodology, specifications, standards |
| `infrastructure` | Database, storage, compute configuration |

**Usage**: Choose the primary domain. A document about validating anomaly classifications is `visual-validation`, not `catalog`.

---

### Type Tags

Document purpose and structure.

| Tag | Description |
|-----|-------------|
| `methodology` | How we do something |
| `reference` | Lookup information (scoring thresholds, categories) |
| `guide` | Step-by-step procedures |
| `decision-record` | Why we chose X over Y |
| `specification` | Formal requirements |
| `source-code` | Code files and scripts |
| `configuration` | Config files, parameters |
| `data-manifest` | Data inventory and provenance |

**Usage**: One type per document. If a document explains both *how* and *why*, choose the dominant purpose.

---

### Tech Tags

Data sources and external dependencies.

| Tag | Description |
|-----|-------------|
| `desi-dr1` | DESI Data Release 1 base data |
| `ard` | Analysis-Ready Dataset (upstream) |
| `agn-vac` | AGN/QSO Summary Value-Added Catalog |
| `pytorch` | PyTorch ML framework |
| `spender` | Spender autoencoder architecture |
| `postgresql` | PostgreSQL database |
| `parquet` | Parquet file format |

**Usage**: Tag when the document is specific to that data source or technology. A general methodology doc doesn't need `pytorch`; a doc about VAE architecture does.

---

### ML Layer Tags

For analysis-specific documentation.

| Tag | Description |
|-----|-------------|
| `embeddings` | Latent space representations |
| `reconstruction` | VAE reconstruction metrics |
| `anomaly-scores` | Isolation Forest, combined scoring |

**Usage**: Tag methodology and results documents with the layer(s) they address.

---

## 4. References

| Reference | Link |
|-----------|------|
| Main README | [../../README.md](../../README.md) |
| Interior README Template | [interior-readme-template.md](interior-readme-template.md) |
| General KB Template | [general-kb-template.md](general-kb-template.md) |
| Upstream ARD Schema | [desi-cosmic-void-galaxies ARD Schema](https://github.com/radioastronomyio/desi-cosmic-void-galaxies/blob/main/docs/ARD-SCHEMA-v2.md) |

---
