"""RazorShield AI machine-learning package.

The ML pipeline reads the payment universe through the backend's SQLAlchemy
models, which are the single source of truth for the schema. ``backend/`` is a
sibling directory rather than an installed distribution, so it is placed on the
import path here - once, explicitly, instead of relying on an environment
variable that every entrypoint would have to set.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"

if BACKEND_ROOT.is_dir() and str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
