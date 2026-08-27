# Phase 6 - deterministic risk decision engine

Two models and an investigation produce evidence. This phase turns that evidence
into exactly one action: **APPROVE**, **STEP_UP**, **REVIEW** or **BLOCK**.

**No language model participates in the decision.** Not as a tie-breaker, not as
a rule, not as a suggestion the engine weighs. The agent's `recommended_action`
is not an input, and there is no field in the decision context through which it
could become one.

> RazorShield AI is a Real-Time Risk Intelligence. The dataset is synthetic and no real
> Razorpay infrastructure or transaction data is involved.

## 1. Architecture

```
POST /api/risk/decision
        |
        v
  app/services/decision.py         <- the only bridge to the database
        |
        |  build_context()  reads risk_predictions, risk_signals, investigations
        v
   RiskContext                      <- counts and numbers only, no model prose
        |
        v
  policy.engine.evaluate()          <- PURE: no db, no clock, no network, no LLM
        |
        |  every enabled rule is evaluated
        |  matches resolved by action precedence
        v
   PolicyResult                     <- action, matched rules, reason codes,
        |                              explanation, input digest
        v
  store_decision()   -> risk_decisions   (append-only, immutable)
  write_audit_entry()-> audit_logs
  open_case_for_decision() -> review_cases  (only when a human is required)
```

The `policy/` package imports no database session, no model artefact and no LLM
client. That is what makes the engine reproducible: there is nowhere for hidden
state to enter, so the same context under the same policy version always yields
byte-identical output.

| Module | Responsibility |
| --- | --- |
| `policy/actions.py` | The four actions and the precedence helpers |
| `policy/reasons.py` | Stable reason codes (append-only vocabulary) |
| `policy/context.py` | What a rule is allowed to see |
| `policy/schema.py` | Typed configuration and its validation |
| `policy/loader.py` | Load, validate, cache |
| `policy/rules.py` | The ten rules, as typed predicates |
| `policy/engine.py` | Evaluate, resolve precedence, fingerprint |
| `policy/explain.py` | Assemble the explanation from measured values |

## 2. What a rule may see - and what it may not

`policy/context.py` is the boundary between the AI layers and the deterministic
one. The investigation contributes only quantities the *application* computed:

* `confidence` - computed by Phase 5 from measured breadth, corroboration,
  completeness and signal agreement, never from the model's own claim;
* counts of findings and evidence by severity, derived from evidence that tools
  produced after ungrounded citations were dropped;
* `shared_entity_observed` - read from the structured `customer_count` key that
  only the device and IP tools emit, never by matching claim text.

**Deliberately excluded, and structurally unreachable because no field carries
them:** the investigation's `summary` (model prose), its `risk_level` (a model
judgement), and above all its `recommended_action`.

A rule cannot read the agent's recommendation because there is nowhere for it to
be read from. A test drives a stored investigation whose `recommended_action` is
`BLOCK` against otherwise-quiet signals and asserts the decision is `APPROVE`.

## 3. Where the thresholds come from

Every threshold is a **measured operating point**, not a round number. All
figures are on the held-out test fold: 3,000 transactions, 60 fraud, 2.00%
prevalence.

### Supervised probability (Phase 3, `xgboost-v1`)

| Threshold | Value | Measurement |
| --- | ---: | --- |
| `fraud_block` | 0.90 | 37 flagged, 37 true positives. Precision **1.000**, recall 0.617 |
| `fraud_high` | 0.533209 | Phase 3's F2-selected operating point. Precision 0.957, recall 0.733 |
| `fraud_medium` | 0.15 | Below this, measured fraud rate is at or near the 2.00% base rate in every anomaly band except CRITICAL |

Precision at neighbouring candidates, for context:

```
p >= 0.70   flagged=43  tp=43  precision=1.000  recall=0.717
p >= 0.90   flagged=37  tp=37  precision=1.000  recall=0.617
p >= 0.99   flagged=26  tp=26  precision=1.000  recall=0.433
```

0.90 was chosen over 0.70 because a block is unreviewable by the customer in the
moment; the extra recall at 0.70 is better captured by REVIEW, where a person can
correct the model. It was chosen over 0.99 because 0.99 discards a third of the
catchable volume for no measured precision gain.

### Behavioural anomaly (Phase 4, `isolation-forest-v1`)

Phase 4's measured severity boundaries, read off the validation score
distribution at percentiles 90/97/99. Measured fraud rate inside each band on the
test fold, against a 2.00% base rate:

| Threshold | Value | Band fraud rate |
| --- | ---: | ---: |
| `anomaly_critical` | 99.4 | **57.50%** |
| `anomaly_high` | 97.91 | 22.81% |
| `anomaly_medium` | 92.41 | 6.76% |

### The joint distribution

This is what justifies treating disagreement as a signal in its own right:

```
p>=0.533        a>=99.4    n=13    fraud=13   rate=1.000
p>=0.533        a<92.4     n=8     fraud=6    rate=0.750
0.15<=p<0.533   a>=99.4    n=7     fraud=5    rate=0.714   <- Scenario C1's cell
p<0.15          a>=99.4    n=15    fraud=3    rate=0.200
p<0.15          a<92.4     n=2658  fraud=2    rate=0.001
```

The third row is the one the supervised model alone gets wrong. A 71.4% fraud
rate in a cell the fraud model rates below its own operating point is exactly
the case a human should settle - hence `MODEL_DISAGREEMENT_HIGH_ANOMALY`.

## 4. The rules

Rule *logic* is code (`policy/rules.py`), typed and reviewable. Rule *thresholds*
and *enablement* are configuration (`config/policies/default.yaml`). Configuration
can tune and disable behaviour; it cannot invent it. A policy naming an
unimplemented rule id is rejected at load.

| Rule | Action | Fires when |
| --- | --- | --- |
| `CRITICAL_SUPERVISED_RISK` | BLOCK | `p >= 0.90` **and** 2+ independent HIGH-severity evidence sources **and** investigation confidence >= 0.50 |
| `HIGH_ANOMALY_WITH_CORROBORATION` | REVIEW | `anomaly >= 99.4` and 2+ high-severity findings and confidence >= 0.50 |
| `MODEL_DISAGREEMENT_HIGH_ANOMALY` | REVIEW | `anomaly >= 99.4` and `p < 0.533209` |
| `HIGH_SUPERVISED_RISK` | REVIEW | `0.533209 <= p < 0.90` |
| `MODERATE_COMBINED_RISK` | STEP_UP | `0.15 <= p < 0.533209` and `anomaly >= 92.41` |
| `ELEVATED_ANOMALY_ONLY` | STEP_UP | `97.91 <= anomaly < 99.4` and `p < 0.15` |
| `MISSING_SUPERVISED_SIGNAL` | REVIEW | No stored fraud prediction |
| `MISSING_ANOMALY_SIGNAL` | REVIEW | No stored anomaly score |
| `MISSING_INVESTIGATION` | STEP_UP | No usable investigation **and** a signal is already elevated |
| `LOW_RISK` | APPROVE | Both signals present, both quiet, no high-severity findings |

### Why a block needs corroboration

A block is the one action a customer cannot undo in the moment, so it requires
two independent things at once: a probability in the band where the model was
measured error-free on held-out data, *and* an investigation that independently
found the same thing through different tools.

Where corroboration is missing the rule still fires but **downgrades itself to
REVIEW**, emitting `BLOCK_WITHHELD_PENDING_INVESTIGATION`. The concern is real;
the certainty is not.

### Why `MISSING_INVESTIGATION` is gated

An investigation is evidence the policy *needs* only once a model has raised a
concern. Firing on every quiet payment would step up essentially all traffic:
2,658 of 3,000 test transactions sit below both medium thresholds, and none
warrants friction for the absence of an investigation nobody had reason to run.
That would not be a fail-safe but an outage.

### Why `LOW_RISK` requires both signals

An approval is a positive statement that both models looked and neither
objected - never the absence of an opinion. The rule cannot fire when either
signal is unavailable, so a missing model can never read as a clean bill.

## 5. Precedence

```yaml
precedence: [BLOCK, REVIEW, STEP_UP, APPROVE]
```

Every enabled rule is evaluated and **every match is recorded**, even when a more
restrictive one also matched, so the audit record shows the whole picture rather
than only the winner. The final action is the most restrictive matched action.

**Precedence resolves between matched rules; it never creates a match.** A block
therefore cannot arise from ordering - only from a rule whose own explicit
conditions were satisfied. A test sets every signal to its maximum except the
supervised probability (0.89, just under `fraud_block`) and asserts no rule
returns BLOCK and the outcome is not BLOCK.

No `combined_score = 0.5 * p + 0.5 * anomaly` exists anywhere. A weighted blend
would make the decision uninterpretable and would silently let a strong signal on
one axis manufacture an action the evidence on that axis does not support.

## 6. Fail-safes

```yaml
missing_supervised_signal: REVIEW
missing_anomaly_signal:    REVIEW
missing_investigation:     STEP_UP
require_investigation_for_block: true
```

A missing signal is an unknown, not a clean bill. The configuration layer itself
refuses to load a policy that sets any of these to `APPROVE`.

An exhaustive sweep over 9 probability values x 9 anomaly values asserts that
**no input combination with an unavailable signal ever produces APPROVE**.

## 7. Policy validation

`PolicyConfig.validate()` reports *every* problem found, not just the first, so a
broken policy is fixed in one pass. Rejected:

- thresholds outside `[0, 1]` (probability) or `[0, 100]` (anomaly)
- non-ascending threshold orderings (an inverted pair makes a rule unreachable)
- `min_independent_sources_for_block < 1` - a block may never rest on zero sources
- unknown action names, unknown rule ids, duplicate precedence entries
- a precedence that omits an action or does not put BLOCK first
- any fail-safe set to APPROVE
- missing or unexpected configuration keys, malformed YAML, a non-mapping document

An invalid policy **raises**. It never loads partially and never falls back to a
default nobody configured; the API returns 503 rather than deciding under a
policy the operator did not choose.

## 8. Reproducibility

Every decision carries an `input_digest`: SHA-256 over the signals and the policy
snapshot, with sorted keys and a fixed separator so it is stable across
processes.

Equal digests must yield equal decisions. Tests assert that 50 re-evaluations of
one context produce one distinct action, one digest and one explanation; that
changing a single input changes the digest; and that evaluating the same context
under a different policy version changes it too.

## 9. Immutability

`risk_decisions` is append-only. Re-deciding a transaction **inserts a row**; it
never edits the previous one, so "what did we do, when, and under which policy"
stays answerable.

This is enforced, not documented: `app/db/immutability.py` registers a
`before_flush` listener that raises `ImmutableRecordError` on any UPDATE or
DELETE of a `RiskDecision`. It runs on every session in the process - the app,
the scripts and the test suite - and works identically on SQLite and PostgreSQL.

Note the deliberate absence of a unique constraint on `transaction_id`: decisions
are history, not current state.

## 10. Human review

A resolution is recorded **alongside** the machine decision, never over it. The
two live in different tables and the decision table rejects writes after insert,
so the pair "engine decided X, analyst decided Y" stays visible - the only
arrangement in which "how often do analysts overturn us?" is answerable.

| Resolution | Meaning | Case status | Ledger entry |
| --- | --- | --- | --- |
| `APPROVED` | Let the payment proceed | resolved | `approve` |
| `REJECTED` | Stop the payment | resolved | `block` |
| `ESCALATED` | Pass to a senior reviewer | escalated (**still live**) | `escalated` |

`REVIEW` and `BLOCK` both open a case. BLOCK is included deliberately: an
incorrectly blocked customer needs a route to recourse, and a blocked payment is
exactly what a reviewer should see.

Each resolution appends an `AnalystDecision` row and an audit entry recording
both outcomes and an explicit `overrides_machine_decision` flag, so disagreement
is countable in SQL rather than inferred later from a join.

## 11. Schema

`risk_decisions` (new):

| Column | Purpose |
| --- | --- |
| `public_id` | `DEC-...`, the API-facing identifier |
| `action`, `policy_version`, `decided_at` | The decision and its provenance |
| `matched_rules`, `reason_codes`, `explanation` | The justification |
| `requires_human_review` | Whether a case was opened |
| `fraud_probability`, `risk_score`, `fraud_model_version` | Supervised input, copied in |
| `anomaly_score`, `anomaly_severity`, `anomaly_model_version` | Anomaly input, copied in |
| `investigation_public_id`, `investigation_confidence` | Investigation input |
| `input_digest` | Reproducibility fingerprint |
| `detail` | Per-rule conditions with measured values, plus the policy snapshot |

The model inputs are **copied into the row** rather than only referenced, so a
historical decision stays interpretable even if the source signals are later
recomputed.

`review_cases` gains `risk_decision_id`, `resolution` and `resolution_reason`.
`AnalystDecisionType` gains `escalated`. Migration `5fe7ba2834e5`.

## 12. API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/risk/decision` | Decide a transaction and record the outcome |
| `GET /api/transactions/{id}/decisions` | The full immutable decision history |
| `GET /api/reviews` | The review queue, filtered and paginated |
| `POST /api/reviews/{id}/resolve` | Record an analyst's resolution |

`GET /api/reviews` filters by `status`, `resolution`, `decision`,
`transaction_id`, `created_after` and `created_before`, all as bound parameters.

## 13. Security

| Concern | How it is addressed |
| --- | --- |
| LLM deciding | The recommendation is not in the context; no field carries it |
| LLM-generated code or SQL | The engine imports no LLM client at all |
| LLM modifying policy | Policy is a file on disk; nothing in `agent/` writes it |
| Arbitrary SQL | Every query is a typed SQLAlchemy statement with bound parameters |
| Transaction id injection | Validated against `^[A-Za-z0-9_.:-]{1,64}$` before lookup |
| Invalid policy | Rejected at load; the endpoint returns 503 rather than deciding |
| Rewriting history | `risk_decisions` is append-only, enforced at flush |
| Leaking internals | Tests scan responses for credentials, connection strings and paths |

## 14. The demo scenarios

Measured live against the seeded PostgreSQL database, using the signals Phases
3, 4 and 5 actually stored:

| Transaction | p | Anomaly | Investigation | Decision | Deciding rule |
| --- | ---: | ---: | --- | --- | --- |
| A (normal) | 0.00005 | 58 LOW | completed, 0 high findings | **APPROVE** | `LOW_RISK` |
| B (suspicious) | 0.99964 | 100 CRITICAL | completed, 5 high-severity sources | **BLOCK** | `CRITICAL_SUPERVISED_RISK` |
| C1 (ring) | 0.20029 | 100 CRITICAL | completed, shared device + IP | **REVIEW** | `MODEL_DISAGREEMENT_HIGH_ANOMALY` |
| C2 | 0.99555 | 100 CRITICAL | **none** | **REVIEW** | `CRITICAL_SUPERVISED_RISK` (block withheld) |
| C3 | 0.99648 | 100 CRITICAL | **none** | **REVIEW** | `CRITICAL_SUPERVISED_RISK` (block withheld) |

Three of these are worth reading closely.

**C1** is the case Phase 3 alone rates at 0.20 - below every supervised
threshold. It reaches human review through the anomaly engine and the
disagreement rule, which is the whole reason both rules exist. Its reason codes
include `COORDINATED_ACTIVITY`, because the investigation found the same device
and the same IP serving three customers.

**C2 and C3 are not blocked**, despite fraud probabilities of 0.996 - higher than
B's threshold clearance. No investigation was ever run for them, so there is no
independent corroboration, and the block downgrades itself to REVIEW with
`BLOCK_WITHHELD_PENDING_INVESTIGATION`. This is the corroboration requirement
doing exactly what it exists for, observed on real stored data rather than
asserted in a test.

**B is blocked** because its investigation produced HIGH-or-worse evidence from
five distinct tools, clearing the two-source floor.

## 15. Performance

Measured against the seeded PostgreSQL database over 200 end-to-end requests
across the five scenarios, after warming the policy cache and connection pool:

| Path | p50 | p95 | p99 |
| --- | ---: | ---: | ---: |
| `POST /api/risk/decision` (end to end) | 36.97 ms | 58.73 ms | 64.83 ms |
| `policy.engine.evaluate()` alone (n=5,000) | 0.0499 ms | 0.0612 ms | 0.0854 ms |

The policy evaluation is **three orders of magnitude cheaper than the request it
sits inside**. Essentially all of the end-to-end cost is database I/O: three
SELECTs to assemble the context, then the decision insert, the audit insert and
the commit. Rule count is not a performance consideration at this scale, which is
why the engine evaluates every enabled rule rather than stopping at the first
match.

## 16. Limitations

* **Thresholds are fitted to this synthetic dataset.** The measured operating
  points are real measurements, but of a generated distribution. Real traffic
  would need them re-derived, which is why they live in configuration.
* **Precision 1.000 in the block band is a property of 37 test transactions**,
  not a claim about production. The interval around it is wide, and the
  corroboration requirement exists partly because of that.
* **One live review case per transaction.** A re-decision re-points the existing
  case rather than opening a second; the decision history keeps every evaluation.
* **No authentication or authorisation.** `analyst_id` is supplied by the caller
  and not verified. A real deployment would bind it to an authenticated session.
* **No policy rollout tooling.** Changing policy means editing the file and
  restarting; there is no staged rollout, shadow mode or A/B comparison.
