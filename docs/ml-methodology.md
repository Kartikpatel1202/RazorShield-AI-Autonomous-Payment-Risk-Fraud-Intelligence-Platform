# Phase 3 - ML fraud risk engine: methodology

How the risk engine is built, and specifically how it is prevented from cheating.

> RazorShield AI is a Real-Time Risk Intelligence. The dataset is synthetic; nothing
> here represents real Razorpay infrastructure or real transaction data.

## The question the model answers

> For a transaction being evaluated at time **T**, using only information that
> would have been available before or at T, what is the probability that this
> transaction is fraudulent?

The probability is produced by a trained gradient-boosting model. No probability
is hardcoded anywhere, and no rule-based score is presented as a model output.

## 1. Preventing temporal leakage

This is the most important property of the system. A model that can see the
future scores brilliantly in evaluation and is worthless in production.

### The boundary

"Before" means strictly earlier in the total order `(transaction_timestamp, id)`:

```
timestamp < T  OR  (timestamp = T AND id < T_id)
```

The surrogate id breaks ties, so the boundary is unambiguous when two payments
share a timestamp and a transaction can never count itself as its own history.

### Structural enforcement, not filtering

Feature functions receive exactly two things: the transaction, and a
`HistoryWindow` describing what preceded it. They have no database session and
no ORM access, so they *cannot* reach a future row even by mistake.

Two providers build that window:

| Provider | Used by | How |
| --- | --- | --- |
| `HistoryAccumulator` | dataset building | One chronological pass. Each row's features are taken from the accumulator state, and only then is the row folded in. At the moment of scoring, no later row has been observed at all. |
| `point_in_time` | live inference | SQL aggregates filtered by the boundary predicate above. |

The accumulator also raises if transactions arrive out of order, so the
guarantee cannot be broken by a mis-sorted query.

### Provider parity

The two must agree, or the model would be trained on one distribution and served
another. `test_batch_and_sql_providers_agree` builds features both ways for a
sample of transactions and asserts every one of the 74 features matches to
floating-point precision.

### Columns excluded because they encode the future

Phase 2's seed generator recomputes several columns across the *whole* dataset.
At any time T they already summarise transactions that have not happened yet, so
they are banned from the feature set and their point-in-time equivalents are
rebuilt from the transaction stream instead:

| Excluded column | Why | Point-in-time replacement |
| --- | --- | --- |
| `customers.average_transaction_amount` | Whole-dataset mean | `historical_average_amount` |
| `customers.successful_transaction_count` | Whole-dataset count | `previous_success_count` |
| `customers.failed_transaction_count` | Whole-dataset count | `previous_failure_count` |
| `customers.chargeback_count` | Whole-dataset count | *(none - no prior-only source)* |
| `customers.historical_risk_level` | Derived from the above | *(none)* |
| `devices.is_trusted` | Derived from full device history | `device_age_hours`, `device_transaction_count` |
| `devices.first_seen_at` / `last_seen_at` | Full-stream min/max | `device_age_hours` (measured before T) |
| `ip_addresses.first_seen_at` / `last_seen_at` | Full-stream min/max | `ip_age_hours` (measured before T) |

`ip_addresses.reputation_score` and `is_proxy` **are** used: they are assigned
when an address is created and are not derived from transaction outcomes.

`transactions.status` is also excluded - it is the outcome of the very
transaction being scored, known only after processing. Using it would be scoring
the answer.

`transactions.failed_attempts` **is** used. It counts the customer's consecutive
failures immediately *before* this attempt, which a real payment flow genuinely
knows at authorisation time.

### The anti-cheating tests

`backend/tests/test_ml_leakage.py` asserts:

1. Inserting a transaction **after** T leaves every feature of T unchanged.
2. A burst of ten later transactions still changes nothing.
3. A transaction at the **same instant** with a higher id is not history either.
4. A transaction never counts itself.
5. The label, every identifier, `status`, and every contaminated aggregate are
   absent from the feature set.
6. **Inserting a transaction *before* T *does* change its features.** Without
   this last test the others could pass vacuously - a builder that ignored
   history entirely would satisfy every "nothing changed" assertion.

## 2. Feature set

74 features (70 numeric, 4 categorical) in six groups:

| Group | Count | Examples |
| --- | --- | --- |
| Transaction | 9 | `amount`, `log_amount`, `hour_of_day`, `is_night`, `payment_method`, `merchant_category`, `failed_attempts` |
| Customer history | 17 | `customer_account_age_days`, `previous_transaction_count`, `historical_failure_rate`, `historical_amount_std`, `amount_vs_historical_average`, `amount_zscore_vs_history` |
| Velocity | 10 | `transactions_last_5m/1h/24h/7d`, `failed_transactions_last_1h/24h`, `amount_last_1h/24h` |
| Device | 12 | `is_new_device`, `is_new_device_for_customer`, `device_age_hours`, `device_customer_count`, `device_is_shared` |
| IP | 13 | `is_new_ip`, `ip_age_hours`, `ip_customer_count`, `ip_is_shared`, `ip_reputation_score`, `ip_is_proxy` |
| Location | 13 | `country_changed`, `is_home_country`, `country_frequency`, `is_new_country_for_customer`, `ip_country_matches_transaction` |

Identifiers are kept as **metadata columns** for joins and auditing, and are
excluded from the model input by `FEATURE_COLUMNS`.

## 3. Missing history

A customer's first payment has no baseline; a device seen for the first time has
no age. Substituting a population average would hand the model information the
transaction never had.

Instead, every undefined quantity gets an explicit default **plus a companion
indicator**, so "no history" is a state the model can learn rather than something
confused with "history that happens to be zero":

| Situation | Defaults | Indicator |
| --- | --- | --- |
| First transaction | counts, sums and ratios `0.0` | `customer_is_first_transaction`, `customer_has_history` |
| Unseen device | age `0.0`, counts `0` | `is_new_device`, `is_new_device_for_customer` |
| Unseen IP | age `0.0`, counts `0` | `is_new_ip`, `is_new_ip_for_customer` |
| No device on the transaction | `device_type="unknown"`, counts `0` | `has_device` |
| No IP on the transaction | reputation `50.0` (scale midpoint) | `has_ip` |
| Fewer than two prior payments | `historical_amount_std = 0.0` | `previous_transaction_count` |

Ratios with a zero denominator return `0.0` rather than infinity or NaN, via a
single shared `safe_ratio` helper.

## 4. Chronological split

70% train / 15% validation / 15% test, cut by time, never shuffled. Train is
strictly older than validation, which is strictly older than test. The split
refuses to produce a fold that is empty or contains no fraud.

Exact date ranges are in [ml-evaluation.md](ml-evaluation.md), regenerated with
every training run.

## 5. Class imbalance

Fraud prevalence is ~1.47%. A model predicting "legitimate" for everything is
98.5% accurate and catches nothing, so **accuracy is not a headline metric**.

- **PR-AUC** is the primary comparison metric - it ignores the huge, easy
  true-negative mass that inflates ROC-AUC on imbalanced data.
- Logistic regression uses `class_weight="balanced"`.
- XGBoost uses `scale_pos_weight = negatives / positives` computed on the
  **training fold only**.

## 6. Threshold

0.5 is not assumed. The operating point is swept across every threshold the
validation fold distinguishes, maximising **F2** subject to a precision floor.

F2 weights recall four times as heavily as precision because RazorShield routes
elevated risk to a human review queue: a false positive costs an analyst a few
minutes, a false negative costs a chargeback. The F1-optimal threshold is
reported alongside for reference.

The test fold is used **only** for the final evaluation - never for threshold
selection, hyperparameter search or calibration.

## 7. Calibration

Expected calibration error is measured on validation with 10 equal-width bins.
Isotonic calibration is applied **only if** that error exceeds the configured
limit. The measured value is reported either way, including when no calibration
is applied - see [ml-evaluation.md](ml-evaluation.md).

## 8. Score mapping

```
risk_score = clamp(round(fraud_probability * 100), 0, 100)
```

A linear restatement of the probability, nothing more. It applies no banding, no
policy and no business rule. Deciding what to *do* at a given score belongs to
the decision engine in a later phase. (`round()` breaks exact `.5` ties towards
even; this is pinned by a test rather than left accidental.)

## 9. Reproducibility

Everything that affects the model lives in `ml/training/config.yaml`: random
seed, split ratios, model grid, threshold objective, calibration limit, feature
version.

The artifact stamps `feature_version`, `dataset_version`, `feature_columns`,
`threshold`, `random_seed`, training timestamp and parameters. The predictor
**refuses to load** a model whose feature contract differs from the running code,
so a stale model can never be silently served against changed features.

Rebuild from scratch:

```bash
python -m ml.training.build_dataset
```

```bash
python -m ml.training.train
```

```bash
python -m ml.training.report
```

## 10. Known limitation: coordinated fraud

The supervised model learns amount deviation very strongly. It does **not**
reliably flag the coordinated-fraud scenario, and the reason is measurable rather
than mysterious.

In the training fold:

| Signal | Fraud rate | Legitimate rate | Useful? |
| --- | --- | --- | --- |
| `device_is_shared` | 0.312 | 0.068 | Weak - 785 legitimate transactions also sit on devices used by 3+ customers |
| `ip_is_shared` | 0.790 | 0.809 | **None** - IP sharing is normal behaviour in this data |

Whereas `amount_vs_historical_average` is decisive:

| Ratio band | Rows | Fraud rate |
| --- | ---: | ---: |
| 2-3x | 822 | 3.5% |
| 3-4x | 153 | 14.4% |
| 4-6x | 75 | 49.3% |
| 6x+ | 55 | 92.7% |

Scenario C's transactions sit at ~3.2-3.6x, in the 14% band - and the model
scores one of them at 0.20, which is close to that historical base rate. The
model is behaving correctly and is well calibrated; it simply has too few
training examples of device-sharing fraud to separate it from legitimate device
sharing.

**This is precisely the gap Phase 4 exists to fill.** Unsupervised anomaly
detection does not need labelled examples of a pattern to find it, so
coordinated behaviour that supervised learning cannot separate is exactly the
kind of thing an Isolation Forest is for. The two signals are meant to be
complementary, and Phase 5's agent will weigh both.
