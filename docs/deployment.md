# Deploying RazorShield AI

Render (API) + Vercel (console) + Supabase (PostgreSQL).

This document exists because a deployment of this platform can be completely
healthy and completely unusable at the same time. Migrations create the schema;
nothing creates the data, the model outputs, or an account that can sign in. The
result is a backend whose `/health/ready` reports every dependency ok, a console
that loads, and a dashboard that truthfully reports zero for everything.

---

## The two things that break a fresh deployment

**1. CORS.** The API allows exactly the origins named in `CORS_ORIGINS`. Deploy
the console to a hostname not on that list and every browser request fails at
the preflight, which the console can only report as a network error. Verify it
directly rather than through the UI:

```bash
curl -i -X OPTIONS https://razorshield-backend.onrender.com/api/auth/signup -H "Origin: https://YOUR-CONSOLE.vercel.app" -H "Access-Control-Request-Method: POST"
```

`access-control-allow-origin` echoing your origin means it is configured. A
`400 Disallowed CORS origin` means it is not.

**2. An empty database.** See the bootstrap section below.

---

## Supabase

Take the **connection pooler** URI from *Project Settings → Database*, and
convert it to the SQLAlchemy form the backend expects:

```text
postgresql+psycopg://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

Three details that are easy to get wrong:

- The scheme must be `postgresql+psycopg://`. A bare `postgresql://` selects a
  driver that is not installed.
- Prefer the pooler over the direct connection. Render's free instance sleeps
  and wakes, and each wake opens a fresh pool against a database with a
  connection ceiling.
- **Use the pooler's session mode (port `5432`), not transaction mode (port
  `6543`).** psycopg3 promotes a statement to a server-side prepared statement
  once it has seen it a few times, and transaction-mode pooling hands the next
  execution to a different backend that has never heard of it. The failure is
  delayed and confusing — normal browsing works, and then the bootstrap, which
  runs the same queries thousands of times, fails partway through with
  `prepared statement "_pg3_0" already exists`.

  If you must use port `6543`, disable the behaviour explicitly:

  ```text
  postgresql+psycopg://...@...pooler.supabase.com:6543/postgres?prepare_threshold=0
  ```

The backend reaches Supabase over the Postgres wire protocol with this URL.
**It never uses a Supabase API key**, so there is no `SUPABASE_URL` and no
`SUPABASE_ANON_KEY` anywhere in this project. If you find yourself putting a
service-role key in an environment variable, something has gone wrong: that key
bypasses row-level security, and it belongs nowhere near the frontend.

---

## Render — API

Docker deployment from the repository root `Dockerfile`. No build or start
command: the image's entrypoint handles both.

### Environment

| Variable | Value | Why |
|---|---|---|
| `ENVIRONMENT` | `production` | disables `/docs`, and refuses a placeholder signing key |
| `DATABASE_URL` | the Supabase pooler URI above | |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` | startup **fails** on a known-weak or short value |
| `AUTH_EXPOSE_DEV_RESET_TOKEN` | `false` | startup **fails** if true in production — it hands a password-reset capability to anyone who asks |
| `CORS_ORIGINS` | `https://YOUR-CONSOLE.vercel.app` | exact origin, no trailing slash |
| `CORS_ORIGIN_REGEX` | `^https://razor-shield-ai-[a-z0-9-]+\.vercel\.app$` | Vercel mints a new hostname per preview build; scope this to **your** project, never to `*.vercel.app` |
| `HSTS_ENABLED` | `true` | Render terminates TLS |
| `BOOTSTRAP_ON_START` | `true` | see below |
| `BOOTSTRAP_TRANSACTIONS` | `3000` | |
| `BOOTSTRAP_ADMIN_EMAIL` | your demo operator address | |
| `BOOTSTRAP_ADMIN_PASSWORD` | 12+ characters, generated | mark as secret |

`LLM_PROVIDER` defaults to `mock`, which runs the investigation agent as a
deterministic test double and flags every investigation `agent_is_mock=true`. It
needs no API key. Set `LLM_PROVIDER=anthropic` with `LLM_API_KEY` only if you
want real agent reasoning, and note that the bootstrap then makes up to
`BOOTSTRAP_INVESTIGATIONS` (default 200) metered calls.

### Health

- `GET /health` — liveness. Touches nothing.
- `GET /health/ready` — database, both models, and the policy. Use this one as
  Render's health check path.

### Cold start

The free instance sleeps after inactivity and takes ~50s to wake. The first
request from the console after an idle period will be slow, and the login form
can look unresponsive. Waking it before a demo is the practical mitigation:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://razorshield-backend.onrender.com/health/ready
```

---

## Vercel — console

- Root directory: `frontend`
- Build: `npm run build` · Output: `dist`
- Environment: `VITE_API_BASE_URL=https://razorshield-backend.onrender.com`

Only `VITE_`-prefixed variables reach the browser, and everything that does is
compiled into a public bundle. `VITE_API_BASE_URL` is a URL and is safe there.
Nothing else in this project belongs in a `VITE_` variable — no database URL, no
JWT secret, no API key.

The frontend origin must appear in the API's `CORS_ORIGINS` (or match
`CORS_ORIGIN_REGEX`). Setting `VITE_API_BASE_URL` alone is half the wiring.

---

## Bootstrap — turning an empty database into a working demo

`backend/scripts/bootstrap.py` runs seven stages in dependency order. Each is
skipped when its output already exists, so it is safe on every deploy.

```text
1  schema          alembic upgrade head
2  dataset         merchants, customers, devices, IPs, transactions
3  predictions     Phase 3 supervised fraud probability, per transaction
4  signals         Phase 4 behavioural anomaly score, per transaction
5  investigations  Phase 5 over the transactions the policy wants investigated
6  decisions       Phase 6 policy, plus the review cases and audit rows it opens
7  accounts        the operator account, from the environment
```

Stage 2 matters more than it looks: **the simulator refuses to start when no
merchant exists**, because a simulated payment has to be attributed to one. An
empty `merchants` table is why `POST /api/simulator/start` answers 503 on a
fresh deployment.

Stage 5 is the one that is easy to leave out, and the symptom is specific. The
policy will not block without a usable investigation
(`require_investigation_for_block: true`), so a backfilled transaction the model
scored at 0.99 downgrades to REVIEW for want of evidence nobody gathered. Skip
this stage and BLOCK never appears anywhere in the demo — not because the policy
declined to block, but because it was never given what its own rule requires.

With `BOOTSTRAP_ON_START=true` the container entrypoint runs it **in the
background** and binds the port immediately. That ordering is deliberate: a
platform decides a deploy succeeded by watching for the port, and a multi-minute
seed ahead of the bind gets the deploy killed and retried — restarting the seed.
Nothing the API serves during that window is wrong; the aggregates are live
queries that climb as rows land.

A bootstrap failure is logged and does not stop the API. A backend that refuses
to start because seeding failed takes authentication and health down with it,
and leaves no way in to find out why.

### Running it by hand

From the Render Shell, or locally with `DATABASE_URL` pointed at production:

```bash
python scripts/bootstrap.py --status
```

```bash
python scripts/bootstrap.py
```

### What it will not do

It never reseeds a database that already has transactions. The seed generator's
first act is to truncate every simulation table — including `users` — so the
stage is gated on `transactions` being empty. A redeploy cannot clear a
populated database or delete the accounts people signed up with.

---

## Accounts and the simulator permission

`POST /api/auth/signup` always creates a **viewer**, and the request schema has
no role field. That is why a freshly signed-up account is told on the Live page
that it may observe the stream but not start it: `simulator:control` is an admin
permission, and it is enforced on the endpoint, not just on the button.

There is deliberately no HTTP endpoint that creates an administrator — such an
endpoint has to be reachable by someone who is not one yet. The two supported
paths are:

**During deployment**, via `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`.
An existing account keeps its password; only a missing one is filled in, so a
redeploy cannot reset a password an operator changed.

**Afterwards**, for an account that already signed up, from the Render Shell:

```bash
python scripts/manage_users.py set-role --email you@example.com --role admin
```

This takes effect on that account's **next request** — no new login needed. The
role in a token is never trusted; every request re-reads the account row.

### Promoting an account without shell access

Render's Shell and One-Off Jobs are paid features. On the free instance the
bootstrap is the whole story, and it handles this case deliberately: point
`BOOTSTRAP_ADMIN_EMAIL` at an address that **already signed up** and the next
deploy promotes that account to admin.

The password you set in `BOOTSTRAP_ADMIN_PASSWORD` is **not** applied to an
account that already has one. Only a missing hash is filled in. So an operator
who changed their password keeps it, and a redeploy cannot quietly reset a
credential back to whatever is sitting in the platform's environment. The
variable is still required — it is what creates the account when the address is
not registered yet.

---

## Verifying a deployment

```bash
curl -s https://razorshield-backend.onrender.com/health/ready
```

Expect `ready: true` with `database`, `fraud_model`, `anomaly_model` and
`policy` all `ok`.

```bash
curl -i -X OPTIONS https://razorshield-backend.onrender.com/api/auth/signup -H "Origin: https://YOUR-CONSOLE.vercel.app" -H "Access-Control-Request-Method: POST"
```

Expect `200` and `access-control-allow-origin` echoing your origin.

Then sign in as the operator and confirm `simulator:control` is present:

```bash
curl -s https://razorshield-backend.onrender.com/api/auth/me -H "authorization: Bearer YOUR_TOKEN"
```

---

## Demo walkthrough

1. Open the console and sign in as the operator account.
2. **Dashboard** — figures over the bootstrapped dataset. Every one is a live
   aggregate; none is stored or hardcoded.
3. **Live** — the start controls are present because this account holds
   `simulator:control`.
4. Choose a scenario. `normal` produces a realistic mix; `high_fraud` and
   `coordinated_fraud` drive traffic into review. A scenario sets transaction
   *behaviour* only — amounts, devices, IPs, velocity, location. It never sets a
   probability, a score, or a decision.
5. Start it. Transactions arrive in the feed as they are generated.
6. Each one passes through the real pipeline, and the feed shows every stage:
   `transaction_received → risk_scored → anomaly_detected →
   investigation_started → investigation_completed → decision_created`.
7. Return to the **Dashboard**: the counts have moved, because they are queries.
8. **Transactions** — the generated payments are there, with the probability,
   anomaly score and decision that the pipeline computed for each.
9. **Reviews** — cases the policy opened, each linked to the decision that
   opened it and the rules that matched.
10. **Audit Log** — the append-only record of every decision and investigation.

### Showing a BLOCK

The policy blocks only above `fraud_probability >= 0.90`, with two independent
high-severity sources and a usable investigation. The simulator's generated
behaviour tops out below that threshold, so a live run will show APPROVE,
STEP_UP and REVIEW but generally not BLOCK.

That is not a bug and it should not be tuned around. Blocks exist in the
bootstrapped dataset, where the model does reach that band — filter the
Transaction Explorer by decision `block` to show one, and open it to see the
rule, the reason codes and the investigation that justified it.
