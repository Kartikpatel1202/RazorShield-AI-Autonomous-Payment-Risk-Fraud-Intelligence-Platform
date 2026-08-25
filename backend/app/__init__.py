"""RazorShield AI backend application package.

The risk API loads the trained model from the sibling ``ml/`` package, which is
not an installed distribution, so the repository root is placed on the import
path here - once, at the earliest point any ``app.*`` import runs. ``ml`` does
the mirror-image thing for ``backend``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if (REPO_ROOT / "ml").is_dir() and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
