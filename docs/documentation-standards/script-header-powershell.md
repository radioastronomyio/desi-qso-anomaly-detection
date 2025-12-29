# PowerShell Script Header Template

> Template Version: 1.0  
> Applies To: All `.ps1` files in desi-qso-anomaly-detection  
> Last Updated: 2025-12-29

---

## Template

```powershell
<#
.SYNOPSIS
    [One-line description of what the script does]

.DESCRIPTION
    [2-4 sentences explaining the script's purpose, what it operates on,
    and what outputs it produces. Include any important behavioral notes.]

.NOTES
    Repository  : desi-qso-anomaly-detection
    Author      : VintageDon (https://github.com/vintagedon)
    ORCID       : 0009-0008-7695-4093
    Created     : YYYY-MM-DD
    Phase       : [Phase XX - Phase Name]

.EXAMPLE
    .\script-name.ps1

    [Description of what this invocation does]

.EXAMPLE
    .\script-name.ps1 -Parameter Value

    [Description of what this invocation does]

.LINK
    https://github.com/radioastronomyio/desi-qso-anomaly-detection
#>

# =============================================================================
# Configuration
# =============================================================================

# [Configuration variables with inline comments]

# =============================================================================
# Functions
# =============================================================================

# [Function definitions if needed]

# =============================================================================
# Execution
# =============================================================================

# [Main script logic]
```

---

## Field Descriptions

| Field | Required | Description |
|-------|----------|-------------|
| `.SYNOPSIS` | Yes | Single line, verb-led description |
| `.DESCRIPTION` | Yes | Expanded explanation of purpose and behavior |
| `.NOTES` | Yes | Static metadata (repository, author, ORCID, dates, phase) |
| `.EXAMPLE` | Yes | At least one usage example with description |
| `.LINK` | Yes | Repository URL |
| `.PARAMETER` | If applicable | Document any script parameters |

---

## Phase Reference

| Phase | Name |
|-------|------|
| Phase 01 | Candidate Ranking |
| Phase 02 | Visual Validation |
| Phase 03 | Cross-Match |
| Phase 04 | Catalog Release |

---

## Section Comments

Use banner comments to separate logical sections:

```powershell
# =============================================================================
# Section Name
# =============================================================================
```

Standard sections (in order):

1. **Configuration** — Variables, paths, settings
2. **Functions** — Helper function definitions (if any)
3. **Execution** — Main script logic

---

## Example: Minimal Script

```powershell
<#
.SYNOPSIS
    Validates anomaly classification labels for consistency.

.DESCRIPTION
    Scans the validation output directory and checks that all classified
    anomalies have required metadata fields and valid category labels.
    Reports any incomplete or inconsistent classifications.

.NOTES
    Repository  : desi-qso-anomaly-detection
    Author      : VintageDon (https://github.com/vintagedon)
    ORCID       : 0009-0008-7695-4093
    Created     : 2025-12-29
    Phase       : Phase 02 - Visual Validation

.EXAMPLE
    .\validate-classifications.ps1

    Validates all classification files in the default output directory.

.LINK
    https://github.com/radioastronomyio/desi-qso-anomaly-detection
#>

# =============================================================================
# Configuration
# =============================================================================

$classificationPath = "\\proj-fs02\desi-anomaly\classifications"

# =============================================================================
# Execution
# =============================================================================

Get-ChildItem -Path $classificationPath -Filter "*.json" | ForEach-Object {
    # Validation logic here
}
```

---

## Notes

- PowerShell comment-based help (`.SYNOPSIS`, `.DESCRIPTION`, etc.) enables `Get-Help script-name.ps1`
- Keep `.SYNOPSIS` under 80 characters
- Use present tense, active voice ("Validates..." not "This script validates...")
- Phase field should match work-log phase names exactly
