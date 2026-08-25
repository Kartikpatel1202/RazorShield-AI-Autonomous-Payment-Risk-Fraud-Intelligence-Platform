# Phase 9 - Real-time transaction simulation and live risk stream

A transaction simulator that generates payment *behaviour*, feeds it through the
existing risk pipeline, and streams every stage to the browser as it happens.

**There is no second risk engine here.** The live path calls the same Phase 3,
4, 5 and 6 services the batch path calls. If this layer ever disagreed with the
batch path about a transaction, that would be a bug in this layer.

> RazorShield AI is a hackathon simulation. The dataset is synthetic, every
> simulator-generated transaction is prefixed `SIM_`, and no real Razorpay
> infrastructure or transaction data is involved.

## 1. Architecture

```
ScenarioGenerator          behaviour only - no scores, no decisions
        |
        v
  asyncio.Queue(32)        bounded; the producer awaits space
        |
        v
  3 worker tasks           each hands the event to a thread with its own Session
        |
        v
  app.services.ingest      persist -> score -> detect -> investigate? -> decide
        |                  (every step delegated to the phase that owns it)
        v
  risk_events              durable, ordered, one row per stage
        |
        +--> EventBroker --> SSE --> /live
```

| Module | Responsibility |
| --- | --- |
| `app/simulator/scenarios.py` | Five behaviour generators, seeded and deterministic |
| `app/simulator/engine.py` | Lifecycle, rate control, bounded queue, workers |
| `app/services/ingest.py` | The pipeline: reuses Phases 3-6, emits events |
| `app/services/events.py` | Recording, ordering, replay and in-process fan-out |
| `app/api/routes/live.py` | Ingestion, simulator control, SSE, live metrics |

## 2. The rule that shapes everything: behaviour in, decisions out

A scenario controls amounts, devices, IPs, locations, timing and velocity. It
never sets a fraud probability, an anomaly score, a risk level or a decision.

That is not fastidiousness, it is the difference between a demonstration and a
puppet show. A simulator that assigned outcomes could "prove" any result
regardless of whether the models could actually reach it. Because these
generators only describe behaviour, what the dashboard shows is what the real
pipeline decided - and a scenario that *fails* to produce its intended outcome is
telling you something true about the models.

Two tests enforce it: one asserts the generated event object has no field
through which a risk outcome could be expressed; another parses the simulator
package's imports and fails if it reaches `ml` or `policy` directly.

## 3. Scenarios

| Scenario | Behaviour generated |
| --- | --- |
| `NORMAL` | ₹300-₹6,000 from returning customers on their own devices and home IPs, in their home city |
| `SUSPICIOUS` | ₹20,000-₹90,000 from a first-seen device and IP, in a foreign country, with occasional prior failures |
| `HIGH_FRAUD` | ₹80,000-₹150,000 on brand-new devices and proxy IPs, several per minute from one customer, after consecutive failures |
| `COORDINATED_FRAUD` | Three customers sharing one device and one proxy IP, minutes apart, escalating amounts, interleaved failures |
| `MODEL_DISAGREEMENT` | Moderate amounts on a shared, hours-old device from a foreign proxy - unusual without being expensive |

Each is documented at `GET /api/simulator/scenarios`, and every description talks
about the *signal* the pipeline should notice, never the decision it should
reach.

### What COORDINATED_FRAUD actually produced

A nine-transaction run at seed 7, decided entirely by the real policy:

| # | Fraud probability | Anomaly | Decision | Reason codes |
| ---: | ---: | ---: | --- | --- |
| 1 | 0.895 | 98 | REVIEW | HIGH_FRAUD_PROBABILITY, MODEL_AGREEMENT |
| 2 | 0.897 | 100 | REVIEW | HIGH_FRAUD_PROBABILITY, MODEL_AGREEMENT |
| 3 | 0.907 | 100 | **BLOCK** | VERY_HIGH_FRAUD_PROBABILITY, INDEPENDENT_CORROBORATION |
| 4 | 0.106 | 100 | REVIEW | MODEL_DISAGREEMENT, CRITICAL_BEHAVIORAL_ANOMALY, COORDINATED_ACTIVITY |
| 5 | 0.200 | 100 | REVIEW | MODEL_DISAGREEMENT, COORDINATED_ACTIVITY, ... |
| 6-9 | 0.019-0.155 | 100 | REVIEW | MODEL_DISAGREEMENT, CRITICAL_BEHAVIORAL_ANOMALY, COORDINATED_ACTIVITY |

Two things in that table are worth more than the demo.

**The first live BLOCK the system has ever produced organically.** Transaction 3
crossed 0.90 *and* had an investigation with independent corroboration - the two
conditions Phase 6 requires. Nothing arranged that; the behaviour happened to
satisfy the rule.

**The supervised probability collapses as the ring establishes itself** - 0.90,
then 0.11, 0.02 - while the anomaly score stays pinned at 100. As the shared
device accumulates history the supervised model finds it increasingly *familiar*,
and the behavioural engine keeps flagging it. The result is that the run
reproduces the seeded C1 signature (`MODEL_DISAGREEMENT` + `COORDINATED_ACTIVITY`)
without anything being hardcoded to do so. It is also a real, slightly
uncomfortable finding about the supervised model: a ring that keeps going gets
*less* suspicious to it over time, which is precisely why the second engine
exists.

## 4. Idempotency

`transactions.transaction_id` is unique. Re-submitting an event returns the first
result and creates nothing.

The reason this matters is not tidiness. Decisions are append-only history, and
two decisions for one submitted event would be a permanent artefact in the audit
trail that nobody could later explain. So the duplicate path reports the stored
outcome rather than recomputing it.

Concurrency is handled too: if two submissions race, the loser catches the
`IntegrityError`, rolls back and returns the winner's result. Both callers get
the same answer.

Verified: one transaction, one prediction, one anomaly signal, one investigation
where applicable, one decision, and no additional events.

## 5. Event ordering

Two counters, because they answer different questions.

* `sequence` - monotonic across the whole stream, from a dedicated PostgreSQL
  sequence. It is the SSE `id:` field, so a browser echoes it back as
  `Last-Event-ID`. It is deliberately *not* the primary key: tying a public
  protocol value to a surrogate key would make the key impossible to change.
* `transaction_sequence` - position within one transaction's own ordering,
  starting at 1. A consumer can spot a missing stage without consulting the
  global order.

The happy path:

```
transaction_received -> risk_scored -> anomaly_detected
  -> [investigation_started -> investigation_completed]   when warranted
  -> decision_created
```

`processing_failed` is terminal and has no fixed position - it can replace any
later stage.

A test asserts the emitted stages are a *subsequence* of the declared order,
which is the right shape: the investigation pair is legitimately absent for
quiet traffic, and asserting exact equality would forbid that.

### The investigation gate is the policy's own

`policy.rules.investigation_warranted` was extracted from the
`MISSING_INVESTIGATION` rule and is now called by both. If the pipeline used its
own idea of "elevated", it would either skip an investigation the policy then
penalises it for missing, or run one on traffic the policy never wanted
investigated. One predicate, two callers, no daylight between them.

## 6. Server-sent events

`GET /api/events/stream`.

**Resuming without gaps or duplicates.** A reconnecting client sends the last
sequence it rendered; the server replays exactly what it missed from
`risk_events`, then resumes live. The browser also drops anything at or below its
cursor, so a replayed backlog cannot double up rows already on screen.

**Subscribe before reading the backlog.** The other order leaves a window in
which an event published between the read and the subscribe is in neither, and
the client silently misses it.

**A slow client is dropped, not tolerated.** Each subscriber gets a 64-event
queue. A browser that cannot keep up fills its own queue and is disconnected;
the pipeline is never slowed by it, and nothing is lost because the durable copy
is already in `risk_events`.

**Heartbeats.** A comment line every 15 seconds. Without it, an idle stream is
indistinguishable from a dead one to every proxy in between.

**`X-Accel-Buffering: no`.** Nginx buffers proxied responses by default, which
would hold events until the buffer filled and make a working stream look dead.

### A bug this design caused, and the test that now catches it

The server names every frame with an SSE `event:` type. That is useful - a
`curl` consumer can filter on it - but it means `EventSource` dispatches to a
*per-type listener* and `onmessage` never fires at all. The first version of the
hook used `onmessage`, so the page connected successfully, reported LIVE, and
showed an empty feed.

The test double now mimics the server faithfully: it dispatches only to
per-type listeners. A fake that used `onmessage` would have passed while the real
page stayed blank.

## 7. Backpressure

The queue holds 32 events and the producer **awaits** space on it. That is the
whole mechanism: when the pipeline is slower than the requested rate, the
producer stops producing rather than growing a list until the process dies.

Nothing is silently lost, because nothing is accepted that cannot be queued. The
observable signals are `queue_depth` (sustained depth means saturation) and
`observed_tps` measured over a rolling 10-second window - if it sits below the
requested rate, the backend is the constraint. Both are on the dashboard.

Bounds: rate 0.1-50/s, at most 5,000 transactions, default 50. The simulator
never runs indefinitely.

## 8. Failure handling

A live stream must report a bad event, not stop.

| Failure | Behaviour |
| --- | --- |
| Model unavailable | Whole unit of work rolls back; `processing_failed` recorded; **no decision at all** |
| Unknown merchant | `IngestError` propagates; the route answers 422 |
| Duplicate reference | First result returned; no second decision |
| Malformed event | Rejected at the schema boundary with 422 |
| SSE disconnect | Client shows DISCONNECTED, retries with its cursor |
| Slow client | Dropped from fan-out; durable copy retained |

The property tested explicitly: **a broken risk service never becomes an
approval.** When the fraud model is made to fail, the transaction reaches no
decision at all. Where a signal is merely missing rather than broken, Phase 6's
own fail-safe rules see the gap and route to `REVIEW`.

## 9. Security

Simulator and ingestion endpoints cannot change a policy, retrain or reload a
model, alter a historical decision, run SQL or Python, or read a secret.

The ingestion schema is the boundary. It uses `extra="forbid"` and accepts only
what a payment processor would legitimately know. It notably does **not** accept
`is_fraud` - the dataset's ground-truth label - nor a fraud probability, anomaly
score or decision. A caller can describe a payment; it cannot assert a risk
outcome. `is_fraud` is hardcoded `False` on every ingested transaction, because
letting the wire set it would poison every metric that treats it as ground truth.

Other controls: charset-validated identifiers and IPs, a rejected naive timestamp
(ambiguous ordering silently changes what counts as history), bounded pagination,
405 on write verbs for read-only endpoints, and response scans for credentials
and paths.

Simulated IPs come from `198.18.0.0/15`, the RFC 2544 benchmarking range -
reserved for testing and never routable, so a simulated address can never
collide with a real one.

## 10. Marking simulated traffic

Every generated transaction is prefixed `SIM_`. A prefix rather than a column,
so a 20,000-row table needs no migration and the marking is visible in every log
line, URL and audit entry without a join. The API returns `simulated: true`, the
event payload carries it, and the UI renders a SIMULATED badge. Live metrics are
scoped to `SIM_` traffic - mixing 20,000 seeded transactions into a "live"
counter would make the number meaningless the moment the page loaded.

## 11. Performance

Measured against the running stack.

### Ingestion

| Path | p50 | p95 |
| --- | ---: | ---: |
| `POST /api/events/transactions` (no investigation) | 265.0 ms | 305.2 ms |
| `POST /api/events/transactions` (investigated) | 290.8 ms | 317.2 ms |

### Stages

| Stage | p50 | p95 |
| --- | ---: | ---: |
| Risk scoring (Phase 3) | 46.9 ms | 61.4 ms |
| Anomaly detection (Phase 4) | 141.8 ms | 158.2 ms |
| Decision (Phase 6) | 15.1 ms | 18.0 ms |

Anomaly detection dominates: it builds 48 behavioural features point-in-time
against 20,000 stored transactions. Investigation adds only ~26 ms because the
provider is the deterministic mock.

### Event delivery

| Measurement | p50 | p95 |
| --- | ---: | ---: |
| `occurred_at` to client parse (130 events) | 73.7 ms | 220.0 ms |

This is measured honestly and is worth explaining. A first attempt timed "POST
returned" to "frame arrived" and got 0.0 ms - because the broker publishes inside
the request handler, so the event reaches a connected client *before* the HTTP
response reaches the submitter. True, but not a latency measurement.

The number above times each event's own `occurred_at` against the moment a client
parses it. It therefore includes the rest of the pipeline: events are published
as a batch *after* the transaction commits, so a `transaction_received` event
waits for scoring, detection and the decision before it is released. That is a
deliberate trade - an event never claims a decision the database has not got -
and the p95 is essentially the pipeline duration, not network cost.

### Read endpoints

| Endpoint | p50 | p95 |
| --- | ---: | ---: |
| `GET /api/live/metrics` | 24.7 ms | 30.9 ms |
| `GET /api/events?limit=50` | 20.9 ms | 33.2 ms |
| `GET /api/simulator/status` | 4.0 ms | 5.1 ms |

No Kafka, no Redis. The spec asked for them only if a measured bottleneck
required one, and a single process serving one dashboard is not that bottleneck.

## 12. API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/events/transactions` | POST | Submit one payment event to the pipeline |
| `/api/events/stream` | GET | SSE stream, resumable via `Last-Event-ID` |
| `/api/events` | GET | Durable events, or everything after a sequence |
| `/api/simulator/start` | POST | Begin a bounded run |
| `/api/simulator/stop` | POST | Cancel and drain |
| `/api/simulator/pause` | POST | Hold the producer |
| `/api/simulator/resume` | POST | Release it |
| `/api/simulator/reset` | POST | Clear counters (deletes nothing) |
| `/api/simulator/status` | GET | Live run state |
| `/api/simulator/scenarios` | GET | Documented scenarios |
| `/api/simulator/replay/{scenario}` | POST | Re-run a scenario's behaviour |
| `/api/live/metrics` | GET | Live counters over simulated traffic |

`POST /api/simulator/reset` clears run counters only. It does not delete a
transaction, decision or event.

### Replay

Replay regenerates the *behaviour* and lets Phases 3-6 decide afresh. It does not
copy the stored decisions of the seeded scenario - replaying a recorded answer
would demonstrate nothing. A test asserts two replays of the same seed produce
two distinct decision ids, so both were genuinely decided.

## 13. Database

One new table, `risk_events` (migration `3b8e5c1a94d7`). Nothing existing was
altered; no historical decision, prediction or signal was touched.

The Phase 6 immutability guard is deliberately **not** extended to it. That guard
protects decisions - the record of what the system did. These are observations of
it happening, and conflating the two would dilute what the guard means.

## 14. The flagship demo

1. Open `/live`, start `NORMAL` - approvals arrive as the pipeline decides them.
2. Switch to `COORDINATED_FRAUD` - the ring builds.
3. Watch anomaly scores reach 100/CRITICAL.
4. Watch investigations run and complete in the live panel.
5. See device/IP sharing in the evidence.
6. See `MODEL_DISAGREEMENT` + `COORDINATED_ACTIVITY` and a `REVIEW` decision.
7. Open `/reviews`, resolve a `SIM_` case with structured feedback.
8. `/feedback` shows the new label; `/monitoring` folds it into model metrics.

Verified end to end: a live-simulated transaction (`SIM_f8ec2916_000022`) reached
`REVIEW`, was labelled `CONFIRMED_FRAUD` / `COORDINATED_ACTIVITY`, the machine
decision stayed `review` with exactly one row, and the label lifted the corpus to
204 ground-truth labels feeding precision 0.540 / recall 0.918.

## 15. Known limitations

* **Single process.** The broker and the engine are process-local. A
  multi-worker deployment would need a shared bus; this is stated rather than
  pretended away, and is the point at which Redis would earn its place.
* **One run at a time.** Starting while a run is in progress returns 409.
* **`sequence` on SQLite falls back to `MAX(sequence) + 1`**, which is safe for
  the single-writer test process and would not be under concurrency. PostgreSQL
  uses a real sequence.
* **Investigations are mock-produced.** No API key is configured, so every live
  investigation is flagged `agent_is_mock`, exactly as in Phase 5.
* **Simulated customers are created on first contact** with placeholder country
  and city. Their feature history is genuinely empty, which is correct for a new
  account but means early transactions in a run are scored with thin context.
* **Events are published after commit**, so delivery latency includes the
  remaining pipeline. Publishing per-stage would look faster and could announce
  a decision that a later rollback erased.
* **The feed holds 300 events in the browser** and older ones are dropped from
  view. The durable record is complete; only the on-screen window is bounded.
* **Simulated traffic accumulates.** Runs add real rows to `transactions`,
  `risk_predictions` and `risk_decisions`. They are all prefixed `SIM_` and can
  be identified, but nothing prunes them automatically.
