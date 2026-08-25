"""Live transaction simulation.

Generates payment *behaviour* and feeds it through the existing risk pipeline.
It contains no risk logic: scenarios describe transactions, and Phases 3-6
decide what those transactions mean. See ``app.simulator.scenarios`` for why
that separation is the point rather than an implementation detail.
"""

from __future__ import annotations
