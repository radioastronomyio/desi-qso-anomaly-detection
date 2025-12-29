<!--
---
title: "Work Logs"
description: "Milestone-based development history for DESI QSO Anomaly Detection"
author: "VintageDon"
date: "2025-12-29"
version: "1.0"
status: "Active"
tags:
  - type: directory-readme
  - domain: documentation
---
-->

# Work Logs

Development history organized by milestone. Each phase is self-contained: scripts, outputs, figures, and documentation live together.

---

## 1. Contents

```
work-logs/
├── 01-ideation-and-setup/        # Repository initialization and planning
└── README.md                      # This file
```

---

## 2. Milestones

| Phase | Name | Status | Date |
|-------|------|--------|------|
| [01](01-ideation-and-setup/README.md) | Ideation and Setup | ✅ Complete | Dec 2025 |

**Note**: This project is an ARD consumer. Active development phases will begin after upstream ARD completion (desi-cosmic-void-galaxies Phases 05-07), including Tier 2 embedding computation.

---

## 3. Naming Convention

Scripts and outputs share prefixes for grouping:

```
NN-script-name.py       # Script
NN-output.log           # Its log output
NN-figure.png           # Its figure output
```

---

## 4. Related

| Document | Relationship |
|----------|--------------|
| [Project Root](../README.md) | Parent |
| [Upstream ARD](https://github.com/radioastronomyio/desi-cosmic-void-galaxies) | Data provider |
