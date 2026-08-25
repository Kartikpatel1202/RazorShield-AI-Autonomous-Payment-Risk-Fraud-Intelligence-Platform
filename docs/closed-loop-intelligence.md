# Phase 8 - Closed-Loop Risk Intelligence & Human Feedback

The loop closes here. Phases 3-6 detect, investigate and decide; Phase 7 shows
the work; Phase 8 captures what analysts concluded and measures where the risk
system is succeeding and where it is failing.

**Nothing in this phase changes production behaviour.** No model is retrained, no
threshold moved, no policy edited, no decision revised. Every output is
analytical, and the code contains no path that could do otherwise.

> RazorShield AI is a hackathon simulation. The dataset is synthetic and no real
> Razorpay infrastructure or transaction data is involved.

## 1. The architecture, end to end

```
Transaction
  -> ML risk (Phase 3)          fraud probability
  -> Anomaly detection (Phase 4) behavioural score
  -> AI investigation (Phase 5)  evidence-grounded findings
  -> Deterministic policy (6)    APPROVE / STEP_UP / REVIEW / BLOCK
  -> Machine decision            immutable, append-only
  -> Human review (Phase 6/7)    an analyst settles the case
  -> Analyst feedback (Phase 8)  what they concluded was TRUE
  -> Monitoring (Phase 8)        model metrics, drift, policy effectiveness
  -> Recommendations (Phase 8)   analytical only - never an action
```

## 2. Resolution is not feedback

The single most important distinction in this phase, and the one that would
corrupt every downstream metric if collapsed.

| | Question it answers | Stored in |
| --- | --- | --- |
| **Resolution** | What did we *do* with the payment? | `review_cases.resolution` |
| **Feedback outcome** | What was *true* about the transaction? | `analyst_feedback.outcome` |

A case can be resolved `REJECTED` (payment stopped) while the outcome is
`INSUFFICIENT_EVIDENCE` - the analyst acted cautiously without confirming fraud.
Treating that as a fraud label would turn every cautious block into a confirmed
fraud and inflate precision on data that never supported it.

The two are separate columns, separate enums, and the UI renders them in
separate blocks.

## 3. The feedback model

`analyst_feedback` (migration `c4a71f9e83b2`):

| Column | Purpose |
| --- | --- |
| `public_id` | `FBK-...`, the API-facing identifier |
| `transaction_id` | The payment judged |
| `risk_decision_id` | The machine decision judged. Nullable - a transaction can be labelled before it was decided |
| `review_case_id` | The case it came from, when it came from one |
| `analyst_id` | Who concluded it. Null for simulated labels |
| `outcome` | One of six `FeedbackOutcome` values |
| `reason_code` | One of a closed `FeedbackReason` vocabulary |
| `notes` | The analyst's own words - stored verbatim, never aggregated |

Two indexes support the monitoring queries: `(outcome, created_at)` for the
summary, `(risk_decision_id, outcome)` for the confusion matrix join.

### Ground truth is only four of six outcomes

```python
CONFIRMED_FRAUD        -> ground truth, fraud
LEGITIMATE             -> ground truth, clean
FALSE_POSITIVE         -> ground truth, clean
FALSE_NEGATIVE         -> ground truth, fraud
INSUFFICIENT_EVIDENCE  -> NOT ground truth; question still open
ESCALATED              -> NOT ground truth; question still open
```

`INSUFFICIENT_EVIDENCE` and `ESCALATED` record that the analyst *declined to
decide*. Counting them as either class would fabricate a label nobody gave, so
they are excluded from every metric and reported separately as "open outcomes".

### Reasons are a closed vocabulary

Free text cannot be aggregated, so it cannot drive monitoring. The analyst's
prose goes in `notes`, where nothing counts it. `FEEDBACK_REASONS_BY_OUTCOME`
additionally constrains which reasons are valid for which outcome - the API
rejects `LEGITIMATE` + `ACCOUNT_TAKEOVER` as the contradiction it is.

## 4. Unlabelled is not negative

The discipline that makes every metric in this phase honest.

20,000 transactions exist; 203 carry a label. Treating the other 19,797 as
legitimate examples would produce a flattering accuracy that describes nothing -
they were not reviewed, so nothing is known about them.

The model metrics endpoint therefore reports three categories side by side and
never merges them:

| Category | Count | Used as ground truth |
| --- | ---: | --- |
| Confirmed labels | 203 | **Yes** |
| Open outcomes | 0 | No - question still open |
| Unlabelled | 19,797 | No - excluded entirely |
| Simulated `is_fraud` flags | 295 | **No** - a generation-time property of the simulator, reported for reference only |

### Below the floor, the metric is withheld

Under `min_labeled_samples` (30) the response carries `sufficient: false` and the
message *"Insufficient labeled data"* rather than a number. At n < 30 a single
label swings a rate by more than three points, which is not a measurement.

### Selection bias is stated, not hidden

Labels come mostly from the review queue, which by construction contains only
*flagged* transactions. A sample with no un-flagged examples yields recall = 1.0
and FNR = 0.0 by arithmetic rather than merit. When that happens the response
carries a `selection_bias_note` saying so. The demo seed deliberately audits a
stride-sampled slice of *approved* transactions for exactly this reason - it is
what makes recall measurable at all.

## 5. Drift detection

Population Stability Index over ten quantile bins, the standard measure in
credit and fraud scoring:

```
PSI = sum over bins of (current% - baseline%) * ln(current% / baseline%)
```

| Band | Status |
| --- | --- |
| PSI < 0.10 | `NORMAL` |
| 0.10 - 0.25 | `WATCH` |
| PSI >= 0.25 | `DRIFT_DETECTED` |
| either window < 100 rows | `INSUFFICIENT_DATA` |

**Why PSI and not a KS test.** KS is more sensitive, and on 20,000 rows that is
the problem: it flags statistically significant differences that are
operationally meaningless. A monitor that cries wolf is a monitor people switch
off. The bands above are the long-standing industry convention, not numbers
invented here, and they live in `config/monitoring.yaml` so an operator can tune
sensitivity without editing code.

Categorical features (merchant, device, location) use the same formula over
category shares, so one scale covers both kinds and an operator learns one
banding rather than two.

**Drift is not fraud.** A `DRIFT_DETECTED` status means a distribution moved. It
may mean a new merchant launched, a campaign ran, or an upstream model changed.
Nothing in this system treats drift as evidence of fraud, and the API response
says so in a `note` field that the UI renders verbatim.

### Measured on the current dataset

| Feature | PSI | Status |
| --- | ---: | --- |
| amount | 0.001 | NORMAL |
| fraud_probability | 0.054 | NORMAL |
| **anomaly_score** | **0.371** | **DRIFT_DETECTED** |
| merchant | 0.001 | NORMAL |
| device | 0.004 | NORMAL |
| location | 0.001 | NORMAL |

The anomaly score genuinely shifted between the two windows (mean 50.74 -> 52.23).
That is a real finding about the simulated traffic, not a threshold artefact.

## 6. Policy effectiveness

Per rule: trigger count, the decisions it produced, resolved cases, human
overrides and the override rate.

**An override means the analyst contradicted a position the engine took.** A
`REVIEW` decision is the engine *declining to decide* and asking for a human, so
nothing the analyst concludes can contradict it - `REVIEW` cases are never
overrides. Because the queue is filled almost entirely by `REVIEW`, override
rates are structurally low here, and the UI says so rather than letting a reader
mistake a near-zero rate for unanimous agreement.

Rates are withheld below `min_rule_triggers` (10) resolved cases: one override
out of two is not a 50% rate in any useful sense. A rule above
`high_override_rate` (30%) is surfaced as a candidate for review - surfaced, not
acted on.

## 7. The high-risk block funnel

Phase 7 surfaced an observation this phase exists to explain: 258 transactions
crossed the block threshold and exactly one was blocked. The funnel makes each
filtering stage countable.

| Stage | Count |
| --- | ---: |
| `HIGH_FRAUD_SCORE` (p >= 0.90) | 258 |
| `DECIDED` | 258 |
| `INVESTIGATION_AVAILABLE` | 1 |
| `SUFFICIENT_CORROBORATION` | 1 |
| `BLOCK_ELIGIBLE` | 1 |
| `FINAL_DECISION_BLOCK` | 1 |

**257 had their block withheld** and were routed to human review instead.

The answer is not that the model was ignored. Phase 6 requires independent
corroboration before taking the one action a customer cannot undo in the moment,
and 257 of those transactions have no investigation at all - there is nothing to
corroborate them with. The gap is the safeguard working.

Corroboration is read back from the reason codes the engine actually recorded,
not recomputed. The funnel must describe the decisions that *were made*, not
decisions it would make now under a possibly-different policy.

## 8. The risk learning assistant

Answers six operational questions from structured metrics.

**It has no language model.** Not as a fallback, not for phrasing, not for
summarising. Every sentence is assembled from numbers a query produced, and an
invented figure would have to come from somewhere - there is nowhere.

That is not timidity. An assistant that generates prose about metrics will
eventually generate a number that is not in the metrics, and on a risk console a
confidently wrong figure is worse than no figure.

The question set is closed (the UI renders them as buttons) because an assistant
accepting free-form questions must either understand them - which needs a model -
or guess, and guessing about risk metrics is what this design exists to avoid.

Every answer exposes:

* `metric_sources` - the endpoints the numbers came from
* `time_window` - the period covered
* `data_availability` - how much data was behind it
* `sufficient` - false when the data cannot answer the question

## 9. Recommendations

Generated from measured conditions - a high override rate, a drifted feature, an
elevated false-positive rate, a large withheld-block gap.

Every response carries:

> Recommendations are analytical only. No model is retrained, no threshold moved,
> no policy edited and no decision revised by this system.

This is enforced by construction: the monitoring package imports no training
code, no policy writer and no decision service. There is no code path from a
recommendation to an action.

## 10. API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/feedback` | GET | List feedback, filtered and paginated |
| `/api/feedback` | POST | Record structured feedback |
| `/api/feedback/summary` | GET | Counts and the machine-vs-human confusion matrix |
| `/api/monitoring/models` | GET | Precision, recall, F1, error rates |
| `/api/monitoring/scores` | GET | Baseline vs current score distributions |
| `/api/monitoring/drift` | GET | PSI per feature |
| `/api/monitoring/policy` | GET | Per-rule effectiveness and override rates |
| `/api/monitoring/high-risk-funnel` | GET | The five-stage block funnel |
| `/api/monitoring/recommendations` | GET | Analytical recommendations |
| `/api/assistant/questions` | GET | The closed question set |
| `/api/assistant/answer` | GET | One grounded answer |

`POST /api/feedback` is the only mutating endpoint in the phase. Every
monitoring endpoint returns 405 for POST, PUT, DELETE and PATCH.

## 11. Immutability

Feedback is recorded **beside** the machine decision, never inside it. The
guarantee is verified directly: hashing every `risk_decisions` row before and
after recording feedback produces an identical digest.

```
digest before  dfa687d9bf643094e262c02d3e830457
POST /api/feedback -> FBK-32eb251b13cf45af created
digest after   dfa687d9bf643094e262c02d3e830457   unchanged
```

The append-only guard from Phase 6 (`app.db.immutability`) still stands: any ORM
update or delete of a `RiskDecision` raises at flush time, on every backend.

## 12. Performance

Measured against the containerised stack, 30 samples per endpoint after warm-up:

| Endpoint | p50 | p95 |
| --- | ---: | ---: |
| `feedback/summary` | 16.4 ms | 32.2 ms |
| `feedback` (list) | 18.7 ms | 33.2 ms |
| `monitoring/models` | 17.8 ms | 36.5 ms |
| `monitoring/scores` | 44.0 ms | 54.1 ms |
| `monitoring/drift` | 180.2 ms | 196.6 ms |
| `monitoring/policy` | 86.7 ms | 103.4 ms |
| `monitoring/high-risk-funnel` | 51.5 ms | 66.2 ms |
| `monitoring/recommendations` | 330.6 ms | 375.1 ms |
| `assistant/answer` | 44.2 ms | 56.3 ms |

`recommendations` is the slowest because it composes four full analyses - model
metrics, drift, policy effectiveness and the funnel. It is a monitoring page
loaded occasionally, not a hot path, and the frontend fetches the panels in
parallel.

Drift avoids streaming 40,000 floats per feature into Python by computing
quantile edges and bin counts server-side; that change took the endpoint from
~600 ms to 180 ms.

### No Phase 7 regression

| Endpoint | Phase 7 baseline | Phase 8 measured |
| --- | ---: | ---: |
| Dashboard | 85 ms | **55.4 ms** |
| Explorer | 141 ms | **96.6 ms** |
| Detail | 44 ms | **27.4 ms** |
| Reviews | 52 ms | **30.8 ms** |
| Audit | 44 ms | **31.8 ms** |
| Policy | 15 ms | **15.2 ms** |

## 13. Security

| Concern | Control |
| --- | --- |
| SQL injection via filters | Every filter is a typed query parameter, bound never interpolated |
| Invalid enum values | Rejected with 422 before reaching a query |
| Oversized pagination | `page_size` capped at 200, `page` >= 1 |
| Unbounded drift windows | `baseline_days` and `current_days` bounded |
| Unintended mutation | Monitoring endpoints are GET-only; 405 on every write verb |
| Credential leakage | Responses scanned for passwords, keys, connection strings and paths |
| Contradictory feedback | Outcome/reason pairs validated server-side against a closed map |

A parametrised test fires SQL fragments, path traversal, oversized pages and
unknown enum values at the feedback endpoint; every one is rejected with 422.

## 14. The demo seed

`backend/scripts/seed_demo_feedback.py` writes **simulated** labels so the
monitoring pages have data.

Every row it writes says so in its `notes`, and `analyst_id` is null precisely so
the rows can never be mistaken for a person's work. Outcomes derive from the
dataset's generation-time `is_fraud` column, which makes the resulting precision
and recall a measurement of *the model against the simulator* - a demonstration
of the machinery, not a claim about real-world accuracy.

Run `--purge --yes` to empty the table and see the honest "insufficient labeled
data" behaviour instead.

It writes `analyst_feedback`, and for reviewed cases the resolution fields on
`review_cases` plus an `analyst_decisions` row. It never writes to
`risk_decisions` and never modifies a transaction, prediction or signal.

## 15. Known limitations

* **Most labels are simulated.** 201 of 203 come from the demo seed and derive
  from the simulator's own fraud flag. Only the two Scenario C labels were
  entered through the UI as a human would.
* **Metrics measure the model against the simulator**, not against reality. That
  is stated in the seed's docstring, in every seeded row's notes, and here.
* **No authentication.** `analyst_id` is supplied by the caller and unverified;
  any caller can record feedback. A real deployment would bind it to a session.
* **The confusion matrix uses "flagged" as the positive class** - anything other
  than a clean APPROVE. It does not distinguish STEP_UP from BLOCK, which are
  very different interventions.
* **Drift compares two fixed windows**, not a rolling series. It answers "did it
  move?" and not "when did it start moving?".
* **The assistant answers six questions.** Anything else needs a new builder
  function - deliberately, since the alternative is guessing.
* **Recommendations are heuristic**, derived from threshold comparisons rather
  than causal analysis. They point at things worth a human's attention; they do
  not diagnose.
* **PSI bins come from the baseline window.** If the baseline is unrepresentative
  the index is measured against a poor reference, and nothing here detects that.
