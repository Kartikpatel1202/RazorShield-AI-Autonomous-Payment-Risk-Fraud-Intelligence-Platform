# RazorShield AI

Autonomous payment risk & fraud management.

RazorShield AI is a payment-risk platform that scores incoming transactions, surfaces anomalous
behaviour, and lets an AI risk agent investigate suspicious activity using tool calls against
first-party data. Risk signals feed a **deterministic** decision engine - the model advises, rules
decide - so every approve, challenge, or block outcome is reproducible and explainable. High-impact
decisions route to a human-in-the-loop review queue, and every automated action is written to an
immutable audit trail.

> **Disclaimer:** RazorShield AI is a hackathon simulation and does not represent real Razorpay
> production infrastructure or real Razorpay transaction data.

---

## Current phase

**Phase 10 - Security, Observability & Production Hardening (complete).**

* **Phase 1** established the scaffolding: FastAPI service, SQLAlchemy + Alembic, React shell,
  Docker packaging, linting, foundation tests. See [docs/phase-1-foundation.md](docs/phase-1-foundation.md).
* **Phase 2** built the data universe: 15 tables, a deterministic 20,000-transaction simulation
  dataset with three demo fraud scenarios, and read-only data-access APIs.
  See [docs/dataset.md](docs/dataset.md).
* **Phase 3** built the supervised risk engine: a leak-free point-in-time feature pipeline
  (74 features), a chronologically split training run comparing logistic regression against
  XGBoost, and a scoring API. See [docs/ml-methodology.md](docs/ml-methodology.md) and
  [docs/ml-evaluation.md](docs/ml-evaluation.md).
* **Phase 4** added an independent behavioral anomaly engine: an Isolation Forest over a
  48-feature behavioral subset, a percentile-based 0-100 score with measured severity bands, and
  its own API. See [docs/anomaly-evaluation.md](docs/anomaly-evaluation.md).
* **Phase 5** added the AI risk investigation agent: a bounded tool-using loop over eight read-only
  tools, evidence-grounded findings, application-computed confidence, and a provider abstraction
  covering Claude, any OpenAI-compatible endpoint and a deterministic mock.
  See [docs/investigation-agent.md](docs/investigation-agent.md).
* **Phase 6** added the deterministic decision engine: a versioned policy of ten typed rules that
  turns the two model signals and the investigation's structured counts into exactly one of
  APPROVE / STEP_UP / REVIEW / BLOCK, with an append-only decision record and a human review
  queue. See [docs/decision-policy.md](docs/decision-policy.md).
* **Phase 7** added the risk operations console: a dashboard over real SQL aggregates, a
  server-side transaction explorer, a full decision-pipeline view per transaction, the human
  review queue, a read-only policy viewer and the audit log.
  See [docs/operations-console.md](docs/operations-console.md).
* **Phase 8** closed the loop: structured analyst feedback recorded beside the immutable machine
  decision, a machine-vs-human confusion matrix, model metrics computed only over labelled data,
  PSI drift detection, per-rule policy effectiveness, the high-risk block funnel, and a
  grounded read-only analytical assistant.
  See [docs/closed-loop-intelligence.md](docs/closed-loop-intelligence.md).
* **Phase 9** added the live layer: a transaction simulator that generates payment *behaviour*
  across five scenarios, an idempotent ingestion pipeline that reuses Phases 3-6 unchanged, a
  durable ordered event stream over server-sent events, and a real-time dashboard.
  See [docs/live-stream.md](docs/live-stream.md).
* **Phase 10** hardened it for production: JWT authentication over bcrypt-hashed passwords,
  permission-based RBAC across every endpoint, per-route rate limiting, security headers, an
  audited read-only tool surface for the AI agent, structured JSON logs with correlation ids,
  Prometheus metrics, liveness/readiness probes, and documented behaviour for every failure path.
  See [docs/security.md](docs/security.md).

Not implemented yet (later phases): a rule builder.

**Phase 2 created the evidence. Phase 3 learns statistical fraud risk from it. Phase 4 detects
behavioural anomalies independently. Phase 5 investigates and explains both signals. Phase 6
decides. Phase 7 shows the work. Phase 8 measures whether any of it is working. Phase 9 runs it
live. Phase 10 locks it down.** The decision is made by deterministic policy rules - no language model participates, and
the agent's `recommended_action` is not even an input. Every figure on the dashboard comes from a
database query; none is hardcoded. Feedback is recorded *beside* the machine decision, never
inside it. The simulator generates behaviour and nothing else: fraud probability, anomaly score
and the decision are all computed by the existing services, exactly as they are for the seeded
dataset. Every `/api` endpoint requires a credential and a permission - an inventory test asserts
there is no exception - and no failure path can turn an outage into an approval.

## Architecture

```
                 +----------------------------+
  Browser  ---->  |  React + TypeScript (Vite) |
                 |  Tailwind CSS UI shell     |
                 +-------------+--------------+
                               |  HTTP / JSON
                 +-------------v--------------+
                 |  FastAPI  (backend/app)    |
                 |  routes -> services -> db  |
                 +-------------+--------------+
                               |  SQLAlchemy 2.0 ORM
                 +-------------v--------------+     +--------------------------+
                 |  PostgreSQL 16             |<--->|  ml/  risk engines       |
                 |  15 tables, Alembic        |     |  point-in-time features  |
                 |  20k simulated payments    |     |   |- XGBoost (fraud)     |
                 +----------------------------+     |   +- IsolationForest     |
                                                    |        (anomaly)         |
                                                    +--------------------------+

                                                            ^
                 +------------------------------------------+ |
                 |  agent/  AI risk investigator            |-+
                 |  8 read-only tools, bounded loop,        |
                 |  evidence-grounded findings              |
                 +------------------------------------------+
```

The two engines share one feature pipeline but stay independent: the anomaly model never sees the
fraud label as an input, neither reads the other's output, and they write to different tables.

`backend/` and `ml/` are sibling packages that each place the other on the import path at import
time, so both work from any entrypoint without an install step or `PYTHONPATH`.

The backend is layered: `api/routes` handles HTTP only, `services` holds logic, `db` owns
connections and sessions, `schemas` defines the typed request/response contracts. Configuration is
read once from the environment into a validated `Settings` object.

### Data model

```
  Merchant --+--> Customer --+--> Transaction --+--> RiskPrediction --> ModelFeedback
             |               |          ^       +--> RiskSignal
             +--> RiskRule   |          |       +--> Investigation
                             |          |       +--> ReviewCase --> AnalystDecision
                    CustomerDevice      |       +--> AuditLog
                             |          |
                         Device --------+
                      IpAddress --------+
```

`customer_devices` is a many-to-many association: a device shared by several customers is a
first-class fact, not something inferred from transactions alone.

The eight risk tables on the right exist but are **empty**. Later phases own them; populating them
now would mean inventing risk intelligence that has not been computed.

## Tech stack

| Layer     | Technology                                                            |
| --------- | --------------------------------------------------------------------- |
| Backend   | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, psycopg 3  |
| Database  | PostgreSQL 16                                                         |
| Frontend  | React 19, TypeScript, Vite, Tailwind CSS v4, React Router             |
| Testing   | pytest + httpx (backend), Vitest + Testing Library (frontend)         |
| ML        | scikit-learn, XGBoost, pandas, numpy, joblib                          |
| Agent     | Anthropic SDK / OpenAI-compatible HTTP, behind a provider abstraction |
| Tooling   | Ruff, mypy (strict), oxlint, TypeScript strict mode                   |
| Packaging | Docker, Docker Compose, nginx                                         |
| Policy    | Pure-Python rule engine over a versioned YAML policy (no LLM, no ML)  |
| Console   | SQL aggregation endpoints + hand-drawn SVG charts (no chart library)  |
| Monitoring| PSI drift, label-gated model metrics, grounded assistant (no LLM)     |
| Live      | asyncio simulator + bounded queue + server-sent events (no broker)    |

## Repository structure

```
RazorShield-AI/
|- backend/                 FastAPI service
|  |- app/
|  |  |- api/routes/        HTTP endpoints (health, catalog, transactions, risk,
|  |  |                    reviews, analytics, operations, feedback, live)
|  |  |- core/              config, logging, error handling
|  |  |- db/                declarative base, engine, session
|  |  |- models/            18 ORM models + shared mixins and enums
|  |  |- schemas/           Pydantic request/response models
|  |  |- seed/              deterministic dataset generator
|  |  |- simulator/         live scenario generators and the run engine
|  |  |- services/          query and aggregation logic
|  |  +- main.py            application factory
|  |- scripts/               dataset CLI, decision reset, demo feedback seed
|  |- tests/                model, seed, API and migration tests
|  |- alembic.ini
|  |- requirements.txt / requirements-dev.txt
|  |- pyproject.toml        ruff / mypy / pytest config
|  +- Dockerfile
|- frontend/                React + TypeScript risk operations console
|  |- src/
|  |  |- components/        layout shell, UI primitives, hand-drawn charts
|  |  |- routes/            dashboard, live, transactions, detail, investigations,
|  |  |                    reviews, feedback, monitoring, rules, audit
|  |  |- hooks/             data fetching with loading/empty/error states
|  |  |- lib/               typed API client, formatting, risk semantics
|  |  +- test/              Vitest setup, render helper and API fixtures
|  |- nginx.conf
|  +- Dockerfile
|- database/
|  |- migrations/           Alembic env + versions/
|  +- seed/                 pointer to the Python generator
|- ml/                      fraud risk engine
|  |- features/             point-in-time feature pipeline + schema contract
|  |- training/             dataset build, split, supervised training, report
|  |- inference/            supervised predictor and batch scoring
|  |- anomaly/              behavioral contract, Isolation Forest, scoring, report
|  +- models/               trained artifacts and metrics (build outputs)
|- agent/                   AI risk investigation agent
|  |- llm/                  provider abstraction: Claude, OpenAI-compatible, mock
|  |- tools/                eight read-only investigation tools
|  |- graph/                bounded investigation loop and state
|  |- schemas/              evidence and investigation contracts
|  +- prompts/              system prompts and the untrusted-data fence
|- policy/                  deterministic decision engine (pure: no db, no LLM)
|  |- actions.py            the four actions and precedence
|  |- reasons.py            stable reason codes
|  |- context.py            what a rule may see - and what it may not
|  |- schema.py             typed policy configuration and validation
|  |- loader.py             load, validate, cache
|  |- rules.py              the ten rules as typed predicates
|  |- engine.py             evaluate, resolve precedence, fingerprint
|  +- explain.py            explanation assembled from measured values
|- config/policies/         versioned policy files (default.yaml)
|- scripts/                 setup helpers
|- docs/                    phase notes and dataset documentation
|- .env.example
+- docker-compose.yml
```

## Local setup

Prerequisites: **Python 3.11+**, **Node.js 20+**, and either **Docker** or a local PostgreSQL 16.

```bash
cp .env.example .env
```

Then fill in the placeholders and run the setup helper for your shell:

```bash
bash scripts/setup.sh
```

```bash
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

Or do it manually:

```bash
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
```

```bash
cd frontend && npm install
```

## Environment variables

Defined in `.env.example`. `.env` is git-ignored and must never be committed.

| Variable            | Required | Description                                                       |
| ------------------- | -------- | ----------------------------------------------------------------- |
| `ENVIRONMENT`       | no       | `local`, `development`, `staging` or `production`                 |
| `LOG_LEVEL`         | no       | Python log level, default `INFO`                                  |
| `DATABASE_URL`      | **yes**  | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/db` |
| `POSTGRES_USER`     | compose  | Username created by the postgres container                        |
| `POSTGRES_PASSWORD` | compose  | Password for that user                                            |
| `POSTGRES_DB`       | compose  | Database created on first start                                   |
| `POSTGRES_PORT`     | no       | Host port mapped to postgres, default `5432`                      |
| `JWT_SECRET`        | **yes**  | Access-token signing key. Refused at startup outside `local` if left at the template value or shorter than 32 characters |
| `ACCESS_TOKEN_TTL_MINUTES` | no | Token lifetime, default 60                                    |
| `RATE_LIMIT_ENABLED`| no       | Per-route rate limiting, default on                               |
| `HSTS_ENABLED`      | no       | Emit `Strict-Transport-Security`. Leave off without TLS           |
| `MAX_REQUEST_BYTES` | no       | Largest accepted request body, default 1 MiB                      |
| `DATABASE_STATEMENT_TIMEOUT_MS` | no | PostgreSQL `statement_timeout`, default 15000 (0 disables) |
| `LLM_PROVIDER`      | no       | `mock` (default), `anthropic` or `openai_compatible`              |
| `LLM_API_KEY`       | no       | API key for the investigation agent. Unset means the mock is used |
| `LLM_MODEL`         | no       | Model id, default `claude-opus-5`                                 |
| `LLM_BASE_URL`      | no       | Endpoint for the OpenAI-compatible provider                       |
| `AGENT_MAX_ITERATIONS` | no    | Cap on the agent's tool-selection rounds, default 8               |
| `CORS_ORIGINS`      | no       | Comma-separated allowed browser origins                           |
| `VITE_API_BASE_URL` | no       | Backend base URL baked into the frontend bundle                   |

## Running the backend

From `backend/`:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

| Endpoint     | Purpose                                                       |
| ------------ | ------------------------------------------------------------- |
| `/health`    | Liveness - `{"status":"ok","service":"razorshield-backend"}`   |
| `/health/db` | Readiness - verifies the PostgreSQL connection                 |
| `/docs`      | Interactive OpenAPI documentation                              |

### Data-access API

All read-only. Path parameters accept either the business key or the numeric primary key.

| Endpoint                                          | Returns                                        |
| ------------------------------------------------- | ---------------------------------------------- |
| `GET /api/merchants`                              | Every merchant                                 |
| `GET /api/customers/{customer_id}`                | One customer with historical counters          |
| `GET /api/customers/{customer_id}/transactions`   | That customer's history, paginated, newest first |
| `GET /api/devices/{device_id}`                    | One device fingerprint                         |
| `GET /api/devices/{device_id}/transactions`       | Every payment from that device, paginated      |
| `GET /api/ip-addresses/{ip_id}`                   | One IP address with its simulated reputation   |
| `GET /api/ip-addresses/{ip_id}/transactions`      | Every payment from that IP, paginated          |
| `GET /api/transactions`                           | Transaction feed, paginated; filters: `merchant_id`, `status`, `is_fraud` |
| `GET /api/transactions/{transaction_id}`          | One transaction                                |
| `GET /api/transactions/{transaction_id}/context`  | Transaction plus customer, device, IP, location, velocity windows and recent history |
| `POST /api/risk/predict`                          | Supervised fraud probability (XGBoost) |
| `POST /api/risk/anomaly`                          | Unsupervised behavioral anomaly (Isolation Forest) |
| `POST /api/investigations`                        | Run an evidence-grounded AI investigation |
| `GET /api/investigations/{investigation_id}`      | Fetch a stored investigation |
| `GET /api/transactions/{transaction_id}/investigation` | The latest investigation of a transaction |

List endpoints take `page` (>=1) and `page_size` (<=200, default 50) and respond with
`{"items": [...], "meta": {...}}`. Monetary and decimal values are serialised as JSON strings to
preserve exactness.

### Scoring a transaction

```bash
curl -X POST http://localhost:8000/api/risk/predict -H 'content-type: application/json' -d '{"transaction_id":"TXN_SCENARIO_B_CURRENT"}'
```

```json
{
  "transaction_id": "TXN_SCENARIO_B_CURRENT",
  "fraud_probability": 0.9996440410614014,
  "risk_score": 100,
  "model_version": "xgboost-v1",
  "threshold": 0.5332090258598328,
  "exceeds_threshold": true,
  "created_at": "2026-08-22T11:05:34.972575Z"
}
```

The probability comes from the trained model; `risk_score` is `round(probability * 100)` and
applies no policy. Results are stored in `risk_predictions`, one current score per transaction.
Returns 404 for an unknown transaction and 503 if no trained model is available.

### Assessing a transaction's behaviour

```bash
curl -X POST http://localhost:8000/api/risk/anomaly -H 'content-type: application/json' -d '{"transaction_id":"TXN_SCENARIO_C_CURRENT_1"}'
```

```json
{
  "transaction_id": "TXN_SCENARIO_C_CURRENT_1",
  "anomaly_score": 100,
  "severity": "CRITICAL",
  "model_version": "isolation-forest-v1",
  "threshold": 96.1,
  "exceeds_threshold": true,
  "customer_deviation_score": 100,
  "customer_deviation_driver": "transactions_last_1h",
  "top_deviations": [{"feature": "transactions_last_1h", "value": 4.0, "percentile": 100.0}]
}
```

`anomaly_score` is a **percentile of normal behaviour**, not a fraud probability: it says the
transaction is more unusual than N% of known-normal traffic, so a perfectly typical payment sits
near 50. Results are written to `risk_signals` and never touch `risk_predictions`.

### Investigating a transaction

```bash
curl -X POST http://localhost:8000/api/investigations -H 'content-type: application/json' -d '{"transaction_id":"TXN_SCENARIO_C_CURRENT_1"}'
```

The agent reads both model signals, chooses read-only tools to fill the gaps, and returns findings
that each cite evidence a tool actually produced. `recommended_action` is **advice** - nothing in
the system executes it. With no `LLM_API_KEY` configured the deterministic mock provider is used
and every investigation is flagged `agent_is_mock: true`.

Method, tools, grounding and the injection defences: [docs/investigation-agent.md](docs/investigation-agent.md).

### Deciding a transaction

```bash
curl -X POST http://localhost:8000/api/risk/decision -H 'content-type: application/json' -d '{"transaction_id":"TXN_SCENARIO_C_CURRENT_1"}'
```

```json
{
  "decision_id": "DEC-...",
  "transaction_id": "TXN_SCENARIO_C_CURRENT_1",
  "decision": "REVIEW",
  "policy_version": "policy-v1",
  "matched_rules": ["HIGH_ANOMALY_WITH_CORROBORATION", "MODEL_DISAGREEMENT_HIGH_ANOMALY", "MODERATE_COMBINED_RISK"],
  "deciding_rules": ["HIGH_ANOMALY_WITH_CORROBORATION", "MODEL_DISAGREEMENT_HIGH_ANOMALY"],
  "reason_codes": ["CRITICAL_BEHAVIORAL_ANOMALY", "MULTIPLE_HIGH_SEVERITY_FINDINGS", "COORDINATED_ACTIVITY", "MODEL_DISAGREEMENT"],
  "requires_human_review": true,
  "input_digest": "73774c79..."
}
```

The decision is made by **deterministic policy rules**. No language model participates - the
agent's `recommended_action` is not an input, and no field in the decision context can carry it.
Every threshold comes from a measured operating point and lives in `config/policies/default.yaml`,
not in Python.

Decisions are appended to `risk_decisions`, which is immutable: re-deciding a transaction adds a
row rather than editing one. `REVIEW` and `BLOCK` open a case in the review queue.

```bash
curl 'http://localhost:8000/api/reviews?status=open'
```

```bash
curl -X POST http://localhost:8000/api/reviews/1/resolve -H 'content-type: application/json' -d '{"resolution":"approved","reason":"verified with the customer"}'
```

A resolution is recorded *alongside* the machine decision, never over it, so analyst overrides
stay countable. Rules, thresholds and their measurements: [docs/decision-policy.md](docs/decision-policy.md).

### Running the live simulator

```bash
curl -X POST http://localhost:8000/api/simulator/start -H 'content-type: application/json' -d '{"scenario":"coordinated_fraud","transactions_per_second":2,"max_transactions":20,"seed":42}'
```

```bash
curl http://localhost:8000/api/simulator/status
```

The simulator generates transaction **behaviour** - amounts, devices, IPs, locations, velocity -
and feeds it through the existing pipeline. It never sets a fraud probability, an anomaly score
or a decision; those are computed by Phases 3, 4 and 6 from the behaviour. Every generated
transaction is prefixed `SIM_` and is never presented as production traffic.

Runs are bounded (`max_transactions`, capped at 5,000) and rate-limited (0.1-50/s). A bounded
queue provides backpressure: if the pipeline is slower than the requested rate the producer waits
rather than dropping events, and `queue_depth` plus `observed_tps` make the saturation visible.

Submit a single event directly:

```bash
curl -X POST http://localhost:8000/api/events/transactions -H 'content-type: application/json' -d '{"transaction_id":"SIM_demo_0001","amount":"24500.00","currency":"INR","customer_id":"SIM_CUS_1","merchant_id":"mrc_0004","payment_method":"card","country":"SG","city":"Singapore","timestamp":"2026-08-24T12:00:00Z","device_id":"SIM_dev_1","device_type":"web_desktop","ip_address":"198.18.100.31","ip_country":"SG","ip_is_proxy":true}'
```

Ingestion is idempotent on `transaction_id`: submitting the same reference twice returns the
first result and creates no second decision.

### Watching the live stream

```bash
curl -N http://localhost:8000/api/events/stream
```

Server-sent events, one per pipeline stage, ordered by a monotonic `sequence` that doubles as the
SSE `id`. A reconnecting client sends it back as `Last-Event-ID` and receives exactly what it
missed. Method, ordering and measured latency: [docs/live-stream.md](docs/live-stream.md).

### Recording analyst feedback

Resolving a case answers *what we did*. Feedback answers *what was true* - a different question,
stored separately so both stay measurable.

```bash
curl -X POST http://localhost:8000/api/feedback -H 'content-type: application/json' -d '{"transaction_id":"TXN_SCENARIO_C_CURRENT_1","outcome":"confirmed_fraud","reason_code":"coordinated_activity","notes":"Shared device and IP confirmed across three customers."}'
```

Outcomes and reasons are closed enums, and the pairing is validated - `legitimate` with
`account_takeover` is rejected as the contradiction it is. Feedback never writes to
`risk_decisions`; the append-only guard would raise.

### Monitoring

```bash
curl 'http://localhost:8000/api/monitoring/models'
```

```bash
curl 'http://localhost:8000/api/monitoring/drift'
```

```bash
curl 'http://localhost:8000/api/monitoring/high-risk-funnel'
```

Model metrics are computed **only** over analyst-labelled transactions; the ~19,800 unlabelled
ones are excluded, never counted as legitimate. Below 30 labels the metric is withheld with
*"Insufficient labeled data"* rather than published. Drift is Population Stability Index against a
baseline window, and a `DRIFT_DETECTED` status means a distribution moved - never that fraud
occurred.

The high-risk funnel answers the question Phase 7 raised: 258 transactions crossed the block
threshold and one was blocked, because 257 of them have no investigation to corroborate the
model. Method, thresholds and their derivation:
[docs/closed-loop-intelligence.md](docs/closed-loop-intelligence.md).

### Seeding demo feedback

The monitoring pages need labels to have anything to measure. This writes **simulated** ones,
each flagged as such in its notes and attributed to no analyst:

```bash
python scripts/seed_demo_feedback.py --dry-run
```

```bash
python scripts/seed_demo_feedback.py --yes
```

Outcomes derive from the dataset's generation-time `is_fraud` column, which makes the resulting
precision and recall a measurement of *the model against the simulator* - a demonstration of the
machinery, not a claim about real-world accuracy. Run `--purge --yes` to empty the table and see
the honest "insufficient labeled data" behaviour instead.

### Training the risk engine

From the repository root, with the database seeded:

```bash
python -m ml.training.build_dataset
```

```bash
python -m ml.training.train
```

```bash
python -m ml.training.report
```

Then score everything at once:

```bash
python -m ml.inference.batch_predict --all
```

Method and leakage controls: [docs/ml-methodology.md](docs/ml-methodology.md).
Measured results: [docs/ml-evaluation.md](docs/ml-evaluation.md).

### Training the anomaly engine

Reuses the Phase 3 dataset and split, so both engines are evaluated on identical folds:

```bash
python -m ml.anomaly.train
```

```bash
python -m ml.anomaly.compare
```

```bash
python -m ml.anomaly.report
```

```bash
python -m ml.anomaly.batch_predict --all
```

`compare` cross-tabulates the two signals into a four-quadrant matrix without combining them.
Measured results: [docs/anomaly-evaluation.md](docs/anomaly-evaluation.md).

### Seeding the dataset

From `backend/`, with the database migrated:

```bash
python scripts/seed_data.py
```

Rebuilds the whole simulation dataset deterministically and prints a summary. The run is one
transaction: if validation fails, nothing is committed. Full options and the dataset's composition
are documented in [docs/dataset.md](docs/dataset.md).

### Other backend commands

All run from `backend/`:

```bash
.venv/Scripts/python.exe -m pytest
```

Lint and format both Python trees from the repository root (config lives in `ruff.toml`):

```bash
backend/.venv/Scripts/ruff.exe check backend/app backend/tests backend/scripts ml
```

```bash
.venv/Scripts/python.exe -m mypy app ../ml
```

```bash
.venv/Scripts/alembic.exe upgrade head
```

Migration tests need a disposable PostgreSQL database and are skipped without one:

```bash
TEST_DATABASE_URL=postgresql+psycopg://razorshield:PASSWORD@localhost:5432/razorshield_test .venv/Scripts/python.exe -m pytest
```

## Running the frontend

From `frontend/`:

```bash
npm run dev
```

Then open http://localhost:5173/dashboard

| Script              | Purpose                         |
| ------------------- | ------------------------------- |
| `npm run dev`       | Vite dev server on port 5173    |
| `npm run build`     | Type-check and build to `dist/` |
| `npm run preview`   | Serve the production build      |
| `npm run lint`      | oxlint                          |
| `npm run typecheck` | TypeScript project build        |
| `npm test`          | Vitest run                      |

## Running with Docker

```bash
docker compose up --build
```

| Service    | URL                   |
| ---------- | --------------------- |
| Frontend   | http://localhost:3000 |
| Backend    | http://localhost:8000 |
| PostgreSQL | `127.0.0.1:5432`      |

The backend container applies Alembic migrations before starting and waits for the postgres health
check to pass. Stop with `docker compose down`, adding `-v` to also drop the database volume.

All three services run unprivileged with `no-new-privileges` and every Linux capability dropped;
the frontend additionally runs on a read-only filesystem. PostgreSQL's port is published to
`127.0.0.1` only. See [docs/security.md](docs/security.md) for the full posture.

### Creating a console account

The console requires a sign-in, and the seed generator deliberately writes no credentials, so the
first account is created explicitly:

```bash
docker compose exec -e RAZORSHIELD_PASSWORD=choose-a-strong-password backend python scripts/manage_users.py create --email ops@example.com --role admin
```

Roles are `admin`, `risk_analyst` (the analyst role), `viewer` and `merchant`. `manage_users.py`
also supports `list`, `set-password` and `deactivate`. The password is never passed as a command
argument - it comes from `RAZORSHIELD_PASSWORD` or an interactive prompt.

### Health and metrics

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /health/live` | none | Process liveness. Touches no dependency, by design |
| `GET /health/ready` | none | Database, both models and the policy config; 503 when any is down |
| `GET /api/metrics` | admin | Prometheus text exposition |

To seed the dataset and train the model inside the running stack:

```bash
docker compose exec backend python scripts/seed_data.py
```

```bash
docker compose exec -w /srv backend python -m ml.training.build_dataset
```

```bash
docker compose exec -w /srv backend python -m ml.training.train
```

The image ships whatever model artifact exists in `ml/models/` at build time. Without one the risk
endpoint returns 503 until a model is trained.
