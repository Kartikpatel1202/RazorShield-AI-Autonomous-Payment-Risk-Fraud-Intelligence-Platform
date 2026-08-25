"""RazorShield AI deterministic risk decision engine (Phase 6).

Given the Phase 3 fraud probability, the Phase 4 anomaly score and the
structured output of a Phase 5 investigation, this package produces exactly one
action - APPROVE, STEP_UP, REVIEW or BLOCK - together with the rules that fired,
the reason codes, and an explanation assembled from measured values.

**The package is pure.** It imports no database session, no model artefact and
no LLM client; every module here is a function of its arguments. The service
layer in ``backend`` assembles the context and persists the outcome. That split
is what makes the decision reproducible: re-running the engine on the same
context under the same policy version must give a byte-identical result, and
there is nowhere for hidden state to enter.

**No language-model output can reach a rule.** See ``policy.context`` for the
inputs a rule is permitted to see, and what is deliberately excluded.

Unlike ``ml`` and ``agent`` this package needs no import bootstrap - it depends
on nothing outside the standard library and PyYAML.
"""

from __future__ import annotations
