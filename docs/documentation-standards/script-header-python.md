# Python Script Header Template

> Template Version: 1.0  
> Applies To: All `.py` files in desi-qso-anomaly-detection  
> Last Updated: 2025-12-29

---

## Template

```python
#!/usr/bin/env python3
"""
Script Name  : script_name.py
Description  : [One-line description of what the script does]
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon)
ORCID        : 0009-0008-7695-4093
Created      : YYYY-MM-DD
Phase        : [Phase XX - Phase Name]
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
[2-4 sentences explaining the script's purpose, what it operates on,
and what outputs it produces. Include any important behavioral notes.]

Usage
-----
    python script_name.py [options]

Examples
--------
    python script_name.py
        [Description of what this invocation does]

    python script_name.py --verbose
        [Description of what this invocation does]
"""

# =============================================================================
# Imports
# =============================================================================

from pathlib import Path

# =============================================================================
# Configuration
# =============================================================================

# [Configuration constants with inline comments]

# =============================================================================
# Functions
# =============================================================================


def main() -> None:
    """Entry point for script execution."""
    pass


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
```

---

## Field Descriptions

| Field | Required | Description |
|-------|----------|-------------|
| Script Name | Yes | Filename for reference (snake_case) |
| Description | Yes | Single line, verb-led description |
| Repository | Yes | Repository name |
| Author | Yes | Name with GitHub profile link |
| ORCID | Yes | Author ORCID identifier |
| Created | Yes | Creation date (YYYY-MM-DD) |
| Phase | Yes | Pipeline phase this script belongs to |
| Link | Yes | Full repository URL |
| Description section | Yes | Expanded multi-line explanation |
| Usage section | Yes | Command syntax |
| Examples section | Yes | At least one usage example |

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

```python
# =============================================================================
# Section Name
# =============================================================================
```

Standard sections (in order):

1. **Imports** — Standard library, third-party, local imports (in that order)
2. **Configuration** — Constants, paths, settings
3. **Functions** — Function and class definitions
4. **Entry Point** — `if __name__ == "__main__":` block

---

## Docstring Style

Use NumPy-style docstrings for functions:

```python
def rank_anomalies(
    anomaly_scores: np.ndarray,
    recon_mse: np.ndarray,
    threshold: float = 0.95
) -> pd.DataFrame:
    """
    Rank QSO candidates by combined anomaly metrics.

    Parameters
    ----------
    anomaly_scores : np.ndarray
        Isolation Forest scores from latent space.
    recon_mse : np.ndarray
        Reconstruction mean squared error from VAE.
    threshold : float, optional
        Percentile threshold for candidate selection. Default is 0.95.

    Returns
    -------
    pd.DataFrame
        Ranked candidates with columns:
        targetid, anomaly_score, recon_mse, combined_rank.

    Raises
    ------
    ValueError
        If array lengths do not match.
    """
    pass
```

---

## Example: Minimal Script

```python
#!/usr/bin/env python3
"""
Script Name  : rank_anomaly_candidates.py
Description  : Queries ARD for high-anomaly QSO candidates
Repository   : desi-qso-anomaly-detection
Author       : VintageDon (https://github.com/vintagedon)
ORCID        : 0009-0008-7695-4093
Created      : 2025-12-29
Phase        : Phase 01 - Candidate Ranking
Link         : https://github.com/radioastronomyio/desi-qso-anomaly-detection

Description
-----------
Filters the upstream QSO ARD table to identify anomaly candidates based on
ANOMALY_SCORE and RECON_MSE thresholds. Excludes known BAL systems and
outputs a ranked candidate list for visual validation.

Usage
-----
    python rank_anomaly_candidates.py [--output candidates.csv]

Examples
--------
    python rank_anomaly_candidates.py
        Ranks candidates and prints top 100 to stdout.

    python rank_anomaly_candidates.py --output candidates.csv
        Ranks all candidates above threshold and writes to CSV.
"""

# =============================================================================
# Imports
# =============================================================================

from pathlib import Path

import pandas as pd
import psycopg2

# =============================================================================
# Configuration
# =============================================================================

PG_CONNECTION = "postgresql://user@proj-pg01:5432/desi_ard"
ANOMALY_THRESHOLD = 0.95  # Percentile threshold
EXCLUDE_BAL = True  # Filter out known BAL systems

# =============================================================================
# Functions
# =============================================================================


def query_anomalies(threshold: float = ANOMALY_THRESHOLD) -> pd.DataFrame:
    """
    Query ARD for anomaly candidates above threshold.

    Parameters
    ----------
    threshold : float
        Anomaly score percentile threshold.

    Returns
    -------
    pd.DataFrame
        Candidate QSOs with columns: targetid, z_helio, anomaly_score, recon_mse.
    """
    query = f"""
    SELECT targetid, z_helio, anomaly_score, recon_mse, bal_prob
    FROM ard.qso_ard
    WHERE anomaly_score > (
        SELECT percentile_cont({threshold}) WITHIN GROUP (ORDER BY anomaly_score)
        FROM ard.qso_ard
    )
    ORDER BY anomaly_score DESC
    """
    # Implementation here
    pass


def main() -> None:
    """Entry point for script execution."""
    print(f"Ranking candidates above {ANOMALY_THRESHOLD} percentile")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
```

---

## Type Hints

Always use type hints for function signatures:

```python
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


def generate_validation_plots(
    spectra_dir: Path,
    target_ids: list[int],
    output_dir: Optional[Path] = None,
) -> dict[int, Path]:
    """Generate diagnostic plots for anomaly candidates."""
    pass
```

---

## Notes

- Use `#!/usr/bin/env python3` for portability
- Module docstring goes immediately after shebang
- Keep Description line under 80 characters
- Use present tense, active voice ("Queries..." not "This script queries...")
- Use `pathlib.Path` instead of string paths
- Use type hints for all function parameters and return values
- Follow PEP 8 style guide
