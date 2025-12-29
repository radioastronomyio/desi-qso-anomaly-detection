# Shell Script Header Template

> Template Version: 1.0  
> Applies To: All `.sh` files in desi-qso-anomaly-detection  
> Last Updated: 2025-12-29

---

## Template

```bash
#!/usr/bin/env bash
# =============================================================================
# Script Name  : script-name.sh
# Description  : [One-line description of what the script does]
# Repository   : desi-qso-anomaly-detection
# Author       : VintageDon (https://github.com/vintagedon)
# ORCID        : 0009-0008-7695-4093
# Created      : YYYY-MM-DD
# Phase        : [Phase XX - Phase Name]
# Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection
# =============================================================================
#
# DESCRIPTION
#   [2-4 sentences explaining the script's purpose, what it operates on,
#   and what outputs it produces. Include any important behavioral notes.]
#
# USAGE
#   ./script-name.sh [options]
#
# EXAMPLES
#   ./script-name.sh
#       [Description of what this invocation does]
#
#   ./script-name.sh --verbose
#       [Description of what this invocation does]
#
# =============================================================================

set -euo pipefail  # Exit on error, undefined vars, pipe failures

# =============================================================================
# Configuration
# =============================================================================

# [Configuration variables with inline comments]

# =============================================================================
# Functions
# =============================================================================

# [Function definitions if needed]

# =============================================================================
# Main
# =============================================================================

main() {
    # [Main script logic]
}

main "$@"
```

---

## Field Descriptions

| Field | Required | Description |
|-------|----------|-------------|
| Script Name | Yes | Filename for reference |
| Description | Yes | Single line, verb-led description |
| Repository | Yes | Repository name |
| Author | Yes | Name with GitHub profile link |
| ORCID | Yes | Author ORCID identifier |
| Created | Yes | Creation date (YYYY-MM-DD) |
| Phase | Yes | Pipeline phase this script belongs to |
| Link | Yes | Full repository URL |
| DESCRIPTION block | Yes | Expanded multi-line explanation |
| USAGE block | Yes | Command syntax |
| EXAMPLES block | Yes | At least one usage example |

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

```bash
# =============================================================================
# Section Name
# =============================================================================
```

Standard sections (in order):

1. **Configuration** — Variables, paths, settings
2. **Functions** — Helper function definitions (if any)
3. **Main** — Entry point function

---

## Best Practices

```bash
# Always use strict mode
set -euo pipefail

# Use main() function pattern for cleaner structure
main() {
    # Script logic here
}

main "$@"

# Quote variables to prevent word splitting
echo "${variable}"

# Use lowercase for local variables, UPPERCASE for exports/constants
local data_path="/mnt/proj-fs02/desi-dr1"
export DATA_ROOT="/mnt/proj-fs02/desi-dr1"
```

---

## Example: Minimal Script

```bash
#!/usr/bin/env bash
# =============================================================================
# Script Name  : fetch-validation-spectra.sh
# Description  : Retrieves spectral tiles for anomaly candidate validation
# Repository   : desi-qso-anomaly-detection
# Author       : VintageDon (https://github.com/vintagedon)
# ORCID        : 0009-0008-7695-4093
# Created      : 2025-12-29
# Phase        : Phase 02 - Visual Validation
# Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection
# =============================================================================
#
# DESCRIPTION
#   Reads a candidate list CSV and retrieves corresponding spectral tiles
#   from the proj-fs02 network share. Copies tiles to local cache for
#   validation plotting.
#
# USAGE
#   ./fetch-validation-spectra.sh candidates.csv
#
# EXAMPLES
#   ./fetch-validation-spectra.sh candidates.csv
#       Fetches spectra for all candidates in the input file.
#
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

SPECTRAL_ROOT="/mnt/proj-fs02/desi-dr1/spectral-tiles"
LOCAL_CACHE="/home/user/validation-cache"

# =============================================================================
# Main
# =============================================================================

main() {
    local candidate_file="${1:-}"
    
    if [[ -z "${candidate_file}" ]]; then
        echo "Usage: $0 <candidate_file.csv>"
        exit 1
    fi
    
    echo "Fetching spectra from ${SPECTRAL_ROOT}"
    echo "Local cache: ${LOCAL_CACHE}"
}

main "$@"
```

---

## Notes

- Always use `#!/usr/bin/env bash` for portability
- `set -euo pipefail` catches common errors early
- Use `main()` function pattern even for simple scripts — it's easier to extend
- Keep Description line under 80 characters
- Use present tense, active voice ("Retrieves..." not "This script retrieves...")
