# The RazorShield simulation dataset

Everything described here is **synthetic**. It is generated locally from a fixed
random seed and does not represent real Razorpay infrastructure, real merchants,
real customers or real transaction data.

## Regenerating

From `backend/`:

```bash
python scripts/seed_data.py
```

```bash
python scripts/seed_data.py --transactions 5000 --customers 400 --seed 7
```

Timestamps are anchored on the moment the generator runs, so velocity windows
("last 5 minutes") always contain rows. Pin the anchor for byte-identical reruns:

```bash
python scripts/seed_data.py --reference-time 2026-08-01T12:00:00Z
```

The run is one transaction. If any validation check fails, nothing is committed
and the previous dataset survives untouched.

## Shape

| Entity              | Count  | Notes                                              |
| ------------------- | ------ | -------------------------------------------------- |
| Merchants           | 4      | Retail, travel, subscription, luxury                |
| Customers           | 1,505  | 1,500 generated + 5 demo-scenario customers         |
| Devices             | ~2,250 | Derived from customers; see *Device sharing* below  |
| IP addresses        | ~907   | 900 generated + 7 demo-scenario addresses           |
| Transactions        | 20,000 | 19,863 generated + 137 demo-scenario               |
| History window      | 90 days | Ending at the run's reference time                 |

Row counts for devices and IPs are approximate because unused device
fingerprints are pruned (see below) and the demo scenarios add a fixed handful.

## Fraud distribution

**Target prevalence: 1.5% of generated transactions.**

Real card-not-present fraud sits well below 1% of volume. A 50/50 split would be
useless — a model trained on it would learn nothing about the class imbalance that
makes fraud detection hard. 1.5% keeps the imbalance realistic while still
yielding ~300 positive examples, enough for a Phase 3 model to learn from.

A typical run produces:

| Class       | Count  | Share  |
| ----------- | ------ | ------ |
| Legitimate  | 19,705 | 98.53% |
| Fraud       | 295    | 1.47%  |

Fraud is not spread evenly. Each behaviour profile carries a multiplier applied
to the base rate, normalised so the volume-weighted mean still lands on 1.5%.
The demo scenarios are hand-crafted and deliberately fraud-heavy, so the
validation band is measured against the generated background stream only.

## Customer behaviour profiles

| Profile      | Share | Typical amount   | Monthly volume | Failure rate | Fraud multiplier |
| ------------ | ----- | ---------------- | -------------- | ------------ | ---------------- |
| `normal`     | 70%   | ₹1,000–₹5,000    | 4–12           | 4%           | 0.35×            |
| `high_value` | 14%   | ₹20,000–₹50,000  | 2–6            | 3%           | 0.9×             |
| `occasional` | 11%   | ₹400–₹2,500      | 0.6–2.5        | 6%           | 0.5×             |
| `risky`      | 5%    | ₹3,000–₹25,000   | 12–34          | 24%          | 9×               |

The great majority of customers and transactions are ordinary. `risky` customers
transact more often, fail more often, travel more and use more devices — but they
are still only one in twenty, and most of their transactions are legitimate.

## Amounts, currency and time

* **Amounts** follow a skewed distribution within each profile's range: most
  payments sit near the low end with a long thin tail. Fraudulent payments are
  multiplied by 2.5–8×.
* **Currency** is INR for every transaction. The schema supports multiple
  currencies, but mixing them would distort the spending-deviation features that
  Phase 3 depends on, so payments settle in INR regardless of payer location.
* **Timestamps** follow an hour-of-day curve — an evening peak, an overnight
  trough — spread across the whole 90-day window. Fraudulent transactions land in
  the 01:00–04:00 dead zone 45% of the time. Nothing is stacked at one instant,
  so velocity over any window is meaningful.

## Locations

A fixed catalogue, no geolocation service is ever called.

* **Domestic (IN):** Mumbai, Delhi, Bengaluru, Hyderabad, Pune, Chennai,
  Kolkata, Ahmedabad
* **International:** Singapore, Dubai, London, New York, Berlin

Each customer has a home city. Ordinary customers pay from it almost always;
travel and foreign origination rates come from their profile.

## Device sharing

Device sharing is one of the strongest coordinated-fraud signals, and it is only
a signal if it is the exception. So:

* Every customer owns **one or more private device fingerprints** used by nobody
  else.
* A small pool (~60) is **deliberately shared** by 3–5 unrelated customers each,
  drawn preferentially from `risky` customers.
* Fingerprints that never carried a transaction are **pruned** at the end of the
  run, so no device row holds an invented first-seen timestamp.

A typical run has ~2,250 devices of which ~61 are shared — under 3%.

> **Deviation from the original brief.** Phase 2 suggested 300–500 devices for
> 1,000–2,000 customers. That ratio forces roughly four customers onto every
> device, which would make a shared device unremarkable and destroy the signal
> entirely. The device count is therefore derived from the customer population
> instead of fixed. `SeedConfig.shared_devices` still controls how many are
> deliberately shared.

## IP sharing

IP sharing is genuinely common in the real world — carrier-grade NAT, offices,
cafés — so the pool is smaller than the customer count on purpose.

* 80% of the pool behaves as **residential** space: one or two customers each.
* 20% behaves as **public/NAT** space seen from many customers; ~15% of those are
  flagged `is_proxy`.
* `reputation_score` is a **simulated** 0–100 value. No external IP-reputation
  service is consulted anywhere in this project.

## Demo scenarios

Three deterministic scenarios exist so later phases have a stable target. Each
one writes **evidence only** — no scenario stores a risk score, a fraud
probability or a risk signal.

### Scenario A — normal payment

| | |
| --- | --- |
| Customer | `CUSTOMER_NORMAL_001` |
| Transaction | `TXN_SCENARIO_A_CURRENT` |
| Evidence | 60 successful payments over 90 days, one device (`dev_scn_normal_001`), one IP (`198.18.100.11`), always Mumbai, ₹2,450 pending payment on the same device |

Expected later verdict: low risk. Not computed here.

### Scenario B — suspicious payment

| | |
| --- | --- |
| Customer | `CUSTOMER_SUSPICIOUS_001` |
| Transaction | `TXN_SCENARIO_B_CURRENT` |
| Evidence | 45 payments averaging ~₹2,450 from Pune on `dev_scn_suspicious_home_001`; then a ₹85,000 attempt from a device first seen 25 minutes ago, a Singapore proxy IP (reputation 23.4), after three failed attempts of ₹70k–₹80k in the preceding 25 minutes |

Every fact a risk engine would need is derivable by query: the baseline average,
the device age, the IP reputation, the country mismatch, the failure count.

### Scenario C — coordinated fraud

| | |
| --- | --- |
| Customers | `CUSTOMER_FRAUD_001`, `CUSTOMER_FRAUD_002`, `CUSTOMER_FRAUD_003` |
| Transactions | `TXN_SCENARIO_C_CURRENT_1/2/3` |
| Shared device | `dev_scn_fraud_shared_001` |
| Shared IP | `198.18.100.31` (proxy, reputation 11.5, Singapore) |
| Evidence | Each customer has a thin ordinary history on their own device, then all three burst 4 transactions each through the one shared device and IP within 40 minutes, minutes apart, mixing successes and failures |

The discovery path — multiple customers → one device → one IP → high velocity —
is fully present in the transaction rows.

## Validation

The seed run aborts unless every check passes:

* every transaction resolves to an existing merchant and customer
* every referenced device and IP address exists
* every customer belongs to an existing merchant
* all amounts are positive and all currencies recognised
* all timestamps fall inside the dataset window
* all statuses and fraud labels are valid
* background fraud prevalence sits inside its expected band
* enough devices and IPs are shared between unrelated customers (both thresholds
  scale with the customer count)
* all three demo scenarios exist with exactly the expected shape

## What is deliberately empty

`risk_predictions`, `risk_signals`, `investigations`, `review_cases`,
`analyst_decisions`, `risk_rules`, `audit_logs` and `model_feedback` contain **no
rows**. Their tables exist so later phases have somewhere to write; populating
them now would mean inventing risk intelligence that has not been computed.

`users.password_hash` is never populated either — no credential, real or fake, is
written by the generator.

## Determinism

The generator uses Python's `random.Random(seed)` rather than Faker. Faker's
output can change between library versions, which would silently break
reproducibility; the standard library's Mersenne Twister is stable across
versions and adds no dependency.

Structure — which customers exist, their profiles, amounts, relationships — is
fully determined by `--seed`. Timestamps additionally depend on
`--reference-time`, which defaults to the run time so recent-window queries work.
Pin both to get an identical dataset.
