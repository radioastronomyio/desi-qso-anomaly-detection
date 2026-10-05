<!--
---
title: "Stage 0 erratum: population-gap attribution"
description: "Qualification of the unmeasured causal attribution in the sealed August audit"
author: "GPT, Astra, GPT Work DESI project"
date: "2026-10-04"
version: "1.0"
status: "active"
tags:
  - type: report
  - domain: spectral-analysis
related_documents:
  - "[Sealed Stage 0 audit](../work-logs/2026-08-15-spectral-manifest-audit.md)"
  - "[Review-fix worklog](../work-logs/2026-10-04-stage0-review-fixes.md)"
---
-->

# Stage 0 population-gap erratum — 2026-10-04

The sealed 2026-08-15 spectral manifest audit attributed the entire 519,066-object difference between the 1,550,000 reference population and 1,030,934 measured unique archive targets to the upstream converter's selection. The audit computed this difference and quoted the converter filter; it did not measure the number of reference objects excluded by that filter. Those observations do not establish that the filter accounts for the entire gap or rule out other causes.

The qualified attribution is: **consistent with the converter's SPECTYPE==QSO and ZWARN==0 filter; the excluded count was not measured**.

This qualification applies to the sealed report's causal wording and the historical D2 summary. Future D2 summaries emit the qualified text. The reference subtraction, measured archive counts, zero target duplication, measured array contract and published manifest bytes are unchanged. The August worklog and existing external summaries/manifests remain sealed; this erratum does not represent a new audit or a measured reconciliation with the reference population.

Source: authorized PR #1 review item S4, Claude review seat and Greptile review, 2026-10-04.
