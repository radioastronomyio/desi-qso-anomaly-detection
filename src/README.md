<!--
---
title: "Source"
description: "Shared Python package for the DESI DR1 QSO corpus audit"
author: "VintageDon (https://github.com/vintagedon/)"
date: "2026-08-15"
version: "1.0"
status: "Active"
tags:
  - type: directory-readme
  - domain: spectral-analysis
  - tech: [python, parquet, desi]
---
-->

# Source

Shared Python package backing the spectral manifest audit. All reusable logic (corpus discovery, scanning, checkpointing) lives here so every audit stage script stays thin orchestration.

---

## 1. Contents

```
src/
├── dqad_audit/     # Shared audit helpers package
│   └── README.md
└── README.md       # This file
```

---

## 2. Subdirectories

| Directory | Description |
|-----------|-------------|
| [dqad_audit/](dqad_audit/README.md) | Corpus discovery, filename parsing, schema diffing, chunk planning, part-file checkpoints, and the per-file scanner |

---

## 3. Related

| Document | Relationship |
|----------|--------------|
| [Project Root](../README.md) | Parent |
| [Scripts](../scripts/README.md) | Audit stage scripts that consume this package |
| [Tests](../tests/README.md) | Unit tests for this package |
