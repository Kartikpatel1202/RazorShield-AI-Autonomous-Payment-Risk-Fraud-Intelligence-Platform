"""RazorShield AI investigation agent (Phase 5).

An evidence-grounded risk investigator. It reads the transaction and the two
model signals, chooses read-only tools to fill the gaps, and produces a
structured investigation whose every finding cites evidence a tool actually
produced.

It investigates and explains. It does not decide: ``recommended_action`` is
advice for the deterministic policy engine in a later phase, and nothing here
can approve, block or modify a payment.

``backend`` is a sibling directory rather than an installed distribution, so it
is placed on the import path here - the same bootstrap ``ml`` uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"

if BACKEND_ROOT.is_dir() and str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
