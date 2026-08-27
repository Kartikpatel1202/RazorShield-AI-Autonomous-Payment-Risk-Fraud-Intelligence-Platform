# Phase 7 - Risk Operations Command Center

The console that exposes the RazorShield intelligence stack: what the models
found, what the policy decided, and why.

**Every figure comes from a database query.** No dashboard metric is hardcoded,
estimated or computed in the browser. If a number appears on screen, an
aggregation endpoint produced it from stored rows.

> RazorShield AI is a Real-Time Risk Intelligence. The dataset is synthetic and no real
> Razorpay infrastructure or transaction data is involved.

## 1. Pages

| Route | Purpose |
| --- | --- |
| `/dashboard` | Headline counters, risk trend, four distributions, reason-code breakdown, system health, top-risk table |
| `/transactions` | Server-side searched, filtered, sorted, paginated explorer |
| `/transactions/:id` | The full pipeline for one payment: facts, both models, investigation, decision, audit |
| `/investigations` | Completed agent reports |
| `/reviews` | The human review queue, with resolution |
| `/rules` | Read-only policy viewer |
| `/audit` | Every recorded event, expandable to the full document |

Every sidebar entry is implemented. The Phase 1 "Soon" placeholders are gone.

## 2. API endpoints added

### Analytics (all SQL aggregation)

| Endpoint | Returns |
| --- | --- |
| `GET /api/analytics/overview` | Headline counters plus the thresholds behind them |
| `GET /api/analytics/decisions` | Decision mix and reason-code frequency |
| `GET /api/analytics/risk-distribution` | Decision, probability, anomaly and risk-level distributions |
| `GET /api/analytics/trends?days=N` | Daily volume split by disposition |
| `GET /api/analytics/top-risk?limit=N` | Riskiest current decisions |

### Operations

| Endpoint | Returns |
| --- | --- |
| `GET /api/transactions/explorer` | Joined, filtered, sorted, paginated explorer rows |
| `GET /api/transactions/{id}/detail` | The whole pipeline in one round trip |
| `GET /api/audit` | Filtered, paginated audit trail |
| `GET /api/audit/summary` | Event counts per type |
| `GET /api/policy` | The active policy, read-only |
| `GET /api/system/health` | Every subsystem's status |

`/api/transactions/explorer` is registered **before** `/api/transactions/{id}`.
FastAPI matches in declaration order, and the reverse would answer the explorer
with a 404 for a transaction literally named "explorer".

## 3. Aggregation

### The "current decision" subquery

`risk_decisions` is append-only history, so a transaction can carry several rows.
Every dashboard figure is about the *current* disposition, which means the newest
decision per transaction. That subquery is defined once
(`analytics.latest_decisions`) and joined by overview, all four distributions,
trends, top-risk and the explorer.

It has two implementations, and the difference is not cosmetic:

| Backend | Form | Measured over 20,000 rows |
| --- | --- | ---: |
| PostgreSQL | `DISTINCT ON (transaction_id)` walking `(transaction_id, decided_at)` | **83 ms** |
| SQLite (tests) | `ROW_NUMBER() OVER (PARTITION BY ...)` | n/a |

The portable `ROW_NUMBER` formulation was the original implementation and made
`/api/analytics/decisions` take **595 ms p50**. Switching PostgreSQL to
`DISTINCT ON` brought the same endpoint to **85 ms p50** - a 7x improvement, and
the reason the dashboard is responsive at all. SQLite has no `DISTINCT ON`, so
the tests keep the window version; the results are identical.

### Other dialect splits

* **Day truncation**: `date_trunc('day', ...)` on PostgreSQL, `date(...)` on
  SQLite. There is no common function, so the dialect is asked once rather than
  papered over with a cast that means different things on each backend.
* **JSON array expansion**: `jsonb_array_elements_text` server-side on
  PostgreSQL; counted in Python over the already-bounded current-decision set on
  SQLite.
* **Histogram bucketing**: a `CASE` clamp rather than `least()`, which SQLite
  does not have. The clamp matters - a probability of exactly 1.0 would
  otherwise fall into a non-existent eleventh bucket and vanish.

### Thresholds come from the policy

"High risk" and "critical anomaly" are read from the **active policy**, not from
constants repeated in the analytics layer. The dashboard and the decision engine
therefore cannot disagree about what the words mean, and the endpoint returns the
threshold alongside the count so the figure can be checked.

### Bounds

* Trend windows are clamped to 365 days.
* `top-risk` is capped at 50.
* Page size is capped at 200.
* Sort keys come from a closed enum mapped to real columns.

No endpoint can be asked for an unbounded scan.

### N+1 avoidance

The explorer joins five tables in one SELECT - transaction, customer, merchant,
prediction, anomaly signal and current decision. Fetching those per row would be
a six-fold N+1 over a page of fifty. The audit list joins `transactions` for the
reference; `top-risk` joins merchant and customer names.

## 4. Demo data (§17)

Phase 6 left 223 decision rows, mostly benchmark noise from a latency run.
Presenting that as production traffic would have been dishonest, so
`backend/scripts/reset_decisions.py` narrowly rebuilds the decision layer.

**Removes, and nothing else:**

* every row in `risk_decisions`, `review_cases`, `analyst_decisions`
* `audit_logs` rows whose `event_type` is `risk.decision`,
  `review.case_opened` or `review.resolved`

**Preserves:** all 20,000 transactions, all 20,000 predictions, all 40,000
signals, all investigations and their `investigation.completed` audit entries,
and every customer, merchant, device and IP.

It never drops a table, never drops the database, and never touches the
PostgreSQL volume. It refuses to run when `ENVIRONMENT` looks like production,
requires an explicit `--yes`, prints exactly what it will delete first, and
verifies afterwards that the preserved counts did not move.

It then decides **every** transaction through the real policy engine, producing:

| Decision | Count | Share |
| --- | ---: | ---: |
| APPROVE | 18,038 | 90.19% |
| STEP_UP | 1,551 | 7.76% |
| REVIEW | 410 | 2.05% |
| BLOCK | 1 | 0.005% |

411 review cases opened. Only one BLOCK, because only Scenario B has both a
probability above 0.90 *and* a corroborating investigation - every other
high-probability transaction has its block withheld. That is the Phase 6
fail-safe visible at scale, not a bug.

### The batch path

Deciding 20,000 transactions one at a time takes about 12 minutes.
`app/services/decision_batch.py` loads every signal in three bulk queries,
evaluates the **same pure engine**, and writes with bulk inserts: **14.7
seconds**. It shares `policy.engine.evaluate` rather than reimplementing any
rule - a batch path that made its own decisions would be a second policy.

`decided_at` is set to the transaction's own timestamp, not wall clock. These are
backfilled decisions over historical payments; stamping them all "now" would make
every time-series chart a single spike.

## 5. Decision latency

Phase 6 recorded no per-decision timing, so "average decision latency" had no
data source. Rather than omit the metric or invent one, migration `9c1d4b7a2e50`
adds `risk_decisions.evaluation_ms`.

It is **observability only**: no rule reads it, it takes no part in the decision,
and the reproducibility digest deliberately does not cover it - two runs of the
same context must remain identical decisions even though they will not take
identical time. It is nullable, because decisions written before the migration
have no measurement and inventing one would be worse than admitting it is absent.

Measured across all 20,000 decisions: mean **0.179 ms**, min 0.048 ms, max 98 ms
(first call, cold).

## 6. Design

The console is built for reading under time pressure.

* **Colour carries meaning and nothing else.** Four decision states, four
  severity bands, no decorative hues.
* **Colour is never the only channel.** Every badge renders its label as text,
  and decisions carry a glyph as well (`✓ ↑ ⚑ ✕`). A reader who cannot
  distinguish the hues loses nothing.
* **Every figure states its scope.** The `Stat` component makes `scope`
  mandatory: a count without its denominator or time range is a number that looks
  authoritative and means nothing.
* **No charting dependency.** The bar chart, histogram and trend chart are
  hand-drawn. Four series on one linear scale is about sixty lines, and owning it
  means the palette is the app's own semantic tokens, with no bundle cost. Total
  bundle: 335 KB JS, 24 KB CSS.
* **The histogram is log-scaled and says so.** 96% of transactions sit in the
  lowest probability bucket; a linear axis would hide the high-risk tail
  entirely.
* **Wide tables scroll inside their own container.** No page scrolls
  horizontally at 375 px - verified on every route.
* **Reduced motion is respected.** Nothing conveys information through
  animation, so it can all simply stop.

### Machine decision vs human resolution

The review UI keeps these visually and structurally separate: two labelled
blocks, different badge shapes (`DecisionBadge` vs `ResolutionBadge`), and an
explicit note that the decision record is immutable. Resolving writes to
`review_cases` and `analyst_decisions`; the linked `risk_decisions` row is never
touched, and the append-only guard would raise if it tried.

## 7. States

Every panel goes through `QueryBoundary`, which makes it impossible to render
content without data. The four states are distinct:

* **Loading** - shaped skeletons, `role="status"`.
* **Empty** - "no data matched", visually distinct from an error.
* **Error** - the *server's own message*, because a 422 usually names the exact
  parameter that was wrong, plus a working retry.
* **Loaded** - the only path where children run.

A chart never renders zeros for a failed request.

## 8. Performance

Measured against the seeded PostgreSQL database, 30 samples per endpoint after
warm-up:

| Endpoint | p50 | p95 |
| --- | ---: | ---: |
| `analytics/overview` (dashboard) | 85.2 ms | 102.4 ms |
| `analytics/risk-distribution` | 71.6 ms | 89.7 ms |
| `analytics/decisions` | 84.8 ms | 107.4 ms |
| `analytics/trends` (30d) | 47.3 ms | 68.3 ms |
| `analytics/trends` (365d) | 66.8 ms | 80.3 ms |
| `analytics/top-risk` | 66.0 ms | 84.0 ms |
| `transactions/explorer` (page 1) | 140.6 ms | 168.3 ms |
| `transactions/explorer` (page 200) | 134.6 ms | 155.1 ms |
| `transactions/explorer` (filtered) | 87.0 ms | 107.8 ms |
| `transactions/{id}/detail` | 43.8 ms | 67.3 ms |
| `reviews` (queue) | 52.1 ms | 71.0 ms |
| `audit` | 43.6 ms | 57.2 ms |
| `policy` | 15.3 ms | 23.3 ms |
| `system/health` | 14.9 ms | 33.5 ms |

Deep paging costs no more than the first page - the ordering is indexed and the
count is a separate aggregate.

## 9. Security

| Concern | Control |
| --- | --- |
| SQL injection via search | Charset-validated at the edge (`^[A-Za-z0-9_.:-]*$`), then a bound LIKE parameter |
| SQL injection via sort | Closed `StrEnum`; unknown keys are rejected with 422 before reaching a query |
| Filter tampering | Every filter is a typed query parameter, bound never interpolated |
| Unbounded scans | Page size, trend window and list limits all capped |
| Policy tampering | `/api/policy` is GET-only; POST/PUT/DELETE return 405, and the payload says `editable: false` |
| Credential leakage | Tests scan every response for `password`, `api_key`, connection strings and filesystem paths |
| Transaction id injection | Validated against the Phase 3 reference pattern before lookup |

A parametrised test fires ten hostile query strings at the explorer - SQL
fragments, path traversal, oversized pages, unknown enum values - and asserts
each is rejected with 422.

## 10. Testing

| Suite | Tests |
| --- | ---: |
| Backend total | **735** |
| ...of which Phase 7 analytics | 34 |
| ...of which Phase 7 operations | 51 |
| Frontend | **53** |

Analytics tests do not merely assert 200. Each aggregate is compared against an
independently computed `SELECT COUNT(*)` over the same fixture, because a test
that only checks the status code passes just as happily on wrong numbers.

Phase 2's seed deliberately leaves `risk_predictions` and `risk_signals` empty,
so a `scored` fixture places predictions and anomaly signals across every band -
including values sitting exactly on the thresholds, which is where bucketing bugs
live. Without it the assertions would compare zero against zero.

## 11. Known limitations

* **No authentication or authorisation.** Any caller can read every page and
  resolve any review case; `analyst_id` is supplied by the client and not
  verified. A real deployment would bind it to an authenticated session, and the
  review queue would be role-gated.
* **The dashboard does not auto-refresh.** Figures are fetched on mount and on
  explicit interaction. There is no polling or websocket.
* **The investigations page reads the audit trail** rather than a dedicated
  endpoint. That avoids a second source of truth for the same facts, but it means
  the list is per-event: re-running an investigation adds a row rather than
  replacing one.
* **Trend granularity is fixed at one day.** Windows shorter than a few days
  therefore show very few points.
* **`analytics/decisions` reason-code counting falls back to Python on SQLite.**
  Correct, and bounded by the current-decision set, but it would not scale the way
  the PostgreSQL path does.
* **The explorer's merchant and customer filters are exact-match** on the
  external identifier, not a search.
* **One live review case per transaction.** A re-decision re-points the existing
  case; the decision history keeps every evaluation.
* **Investigations remain mock-produced.** No API key is configured, so all three
  are flagged `agent_is_mock` and the console labels them accordingly.
