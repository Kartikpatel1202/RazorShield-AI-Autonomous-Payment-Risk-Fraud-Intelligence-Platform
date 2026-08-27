# RazorShield AI - behavioral anomaly engine evaluation

*Generated from `ml/models/anomaly_metrics.json` and `ml/models/signal_matrix.json`
by `python -m ml.anomaly.report` on 2026-08-22 16:55 UTC. Every figure
comes from a real run; none is typed by hand.*

> RazorShield AI is a Real-Time Risk Intelligence. The dataset is synthetic with
> deliberately learnable structure, so these numbers measure the pipeline, not
> real-world anomaly detection performance.

## 1. What this engine is for

The supervised model in Phase 3 answers *"does this look like fraud I have seen
labelled before?"*. This one answers a different question:

> **How unusual is this transaction's behaviour compared with normal behaviour?**

It never sees the fraud label as an input. It is therefore able to flag patterns
that were too rare in the training labels for supervised learning to separate -
which is exactly what Phase 3's report identified as its blind spot.

The two signals are kept **independent**. Nothing here reads a fraud probability,
and nothing in Phase 3 reads an anomaly score. Combining them is a later phase.

## 2. Where the fraud label is and is not used

| Stage | Label used? |
| --- | --- |
| Behavioral features | **No** - the label is not in the contract |
| Model input at fit time | **No** |
| Model input at inference | **No** |
| Constructing the fitting population | **Yes** - known fraud excluded, disclosed below |
| Threshold and severity band selection | **Yes** - on validation only |
| Evaluation | **Yes** - validation and test |

The forest is fitted on the training fold **with known fraudulent transactions
removed**, so it learns the shape of normal behaviour rather than a blend of
both classes. That is a training-population filter, not a feature. This engine is
accurately described as *label-blind in its inputs* but *label-informed in its
fitting population* - not as purely unsupervised.

## 3. Data

| Fold | Rows | Fraud | Prevalence | From | To |
| --- | ---: | ---: | ---: | --- | --- |
| train | 14,000 | 186 | 1.33% | 2026-05-24 10:11 | 2026-07-26 14:30 |
| validation | 3,000 | 49 | 1.63% | 2026-07-26 14:31 | 2026-08-09 07:52 |
| test | 3,000 | 60 | 2.00% | 2026-08-09 08:04 | 2026-08-22 09:58 |

The split configuration is read from `ml/training/config.yaml`, so these folds
are byte-identical to Phase 3's. Two engines evaluated on different folds could
not be compared.

| Fitting population | |
| --- | --- |
| Source | train fold, known fraud removed |
| Rows fitted | **13,814** |
| Known fraud excluded | 186 |
| From | 2026-05-24 10:11 |
| To | 2026-07-26 14:30 |

## 4. Behavioral features

**48 features**, an explicit subset of the 74 point-in-time
features built in Phase 3 - not a second, incompatible history system. Every
temporal-leakage guarantee from Phase 3 therefore applies unchanged.

| Group | Count | Features |
| --- | ---: | --- |
| transaction | 5 | `amount`, `log_amount`, `amount_vs_historical_average`, `amount_vs_historical_max`, `amount_zscore_vs_history` |
| customer | 8 | `previous_transaction_count`, `previous_success_count`, `previous_failure_count`, `historical_failure_rate`, `historical_average_amount`, `historical_amount_std`, `historical_max_amount`, `seconds_since_previous_transaction` |
| velocity | 8 | `transactions_last_5m`, `transactions_last_1h`, `transactions_last_24h`, `transactions_last_7d`, `failed_transactions_last_1h`, `failed_transactions_last_24h`, `amount_last_1h`, `amount_last_24h` |
| device | 8 | `device_transaction_count`, `device_customer_count`, `device_transactions_last_1h`, `device_transactions_last_24h`, `device_failed_last_1h`, `device_age_hours`, `is_new_device`, `is_new_device_for_customer` |
| ip | 8 | `ip_transaction_count`, `ip_customer_count`, `ip_transactions_last_1h`, `ip_transactions_last_24h`, `ip_failed_last_1h`, `ip_age_hours`, `is_new_ip`, `is_new_ip_for_customer` |
| location | 11 | `location_changed`, `country_changed`, `city_changed`, `country_frequency`, `city_frequency`, `is_home_country`, `is_home_city`, `previous_country_count`, `previous_city_count`, `is_new_country_for_customer`, `is_new_city_for_customer` |

32 heavy-tailed columns are `log1p`
transformed so one very large payment cannot dominate every random split. Ratios,
rates and indicators are left untouched.

Excluded on purpose: the fraud label, all identifiers, the four categoricals,
calendar features, and static entity attributes such as `ip_reputation_score`.
The full reasoning is in `ml/anomaly/schema.py`.

## 5. Isolation Forest

| Parameter | Value |
| --- | --- |
| `n_estimators` | `300` |
| `max_samples` | `256` |
| `contamination` | `0.01` |
| `max_features` | `1.0` |
| `bootstrap` | `False` |
| `n_jobs` | `4` |
| `random_state` | `20260101` |

Training time: **1.91s** on 13,814 rows.

`contamination` is pinned at `0.01` rather than left to
`auto`. The fitting population already has known fraud removed, and this engine
reports a percentile score rather than the library's `predict()` label, so the
value only sets an internal offset the scoring path does not use. It is set
explicitly so the artifact is fully reproducible.

## 6. Score normalization

Raw `score_samples` output is meaningless outside the fitted forest, so it is
converted to an empirical percentile of the fitting population:

```
p             = fraction of fitted normal transactions scoring <= this one
anomaly_score = round(100 * (1 - p))
```

`anomaly_score` therefore answers exactly one question: **this transaction is
more unusual than N% of known-normal behaviour**. It is bounded, monotone in the
raw score, and stable across retrains because it is a rank.

It is **not** a fraud probability. The reference population is normal traffic, so
ordinary payments spread across the whole range and a perfectly typical payment
sits near 50. Severity, not the raw number, is what should be read at a glance.

## 7. Severity bands

Read off the **validation** score distribution at percentiles
90/97/99,
not chosen as round numbers:

| Band | Score range |
| --- | --- |
| LOW | < 92.4 |
| MEDIUM | 92.4 - 97.9 |
| HIGH | 97.9 - 99.4 |
| CRITICAL | >= 99.4 |

Measured fraud rate inside each band on the **test** fold - the bands are only
useful if the fraud rate actually rises across them:

| Severity | Transactions | Fraud | Fraud rate |
| --- | ---: | ---: | ---: |
| LOW | 2,681 | 9 | 0.34% |
| MEDIUM | 222 | 15 | 6.76% |
| HIGH | 57 | 13 | 22.81% |
| CRITICAL | 40 | 23 | 57.50% |

## 8. Threshold

Selected on **validation** by maximising F2, the same
recall-weighted objective Phase 3 uses, subject to a precision floor.

- Selected threshold: **96.10**
- Validation at this threshold: precision **0.2096**,
  recall **0.7143**, F1 **0.3241**
- F1-optimal alternative: 97.50
  (F1 0.3558)

Test labels were not used for any selection.

## 9. Results

| Fold | Precision | Recall | F1 | PR-AUC | Random PR-AUC | Lift | ROC-AUC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| validation | 0.2096 | 0.7143 | 0.3241 | **0.2256** | 0.0163 | 13.8x | 0.9540 |
| test | 0.2629 | 0.7667 | 0.3915 | **0.5481** | 0.0200 | 27.4x | 0.9611 |

PR-AUC is reported against the random baseline because at ~2% prevalence an
absolute PR-AUC is hard to read: the test figure is
**27.4x** better than random ranking.

### Confusion matrix - test fold, threshold 96.10

| | Predicted normal | Predicted anomalous |
| --- | ---: | ---: |
| **Actually legitimate** | 2,811 | 129 |
| **Actually fraud** | 14 | 46 |

### Score distribution - test fold

| Group | Count | Mean | p5 | p25 | p50 | p75 | p90 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| legitimate | 2,940 | 53.6 | 21.6 | 36.4 | 50.4 | 66.0 | 91.4 | 95.7 | 98.9 |
| fraud | 60 | 95.8 | 76.9 | 96.9 | 98.9 | 99.9 | 100.0 | 100.0 | 100.0 |

## 10. Feature sensitivity

Isolation Forest has no native feature importance, so none is invented. This is
permutation sensitivity: shuffle a behavioral column, rescore, and measure how
much the anomaly ranking's agreement with ground truth degrades. Labels are used
for measurement only.

| Rank | Behavioral feature | Validation PR-AUC drop | Std |
| ---: | --- | ---: | ---: |
| 1 | `amount_vs_historical_average` | +0.04928 | 0.00169 |
| 2 | `amount_zscore_vs_history` | +0.02816 | 0.00064 |
| 3 | `amount` | +0.02434 | 0.00161 |
| 4 | `log_amount` | +0.02240 | 0.00417 |
| 5 | `amount_vs_historical_max` | +0.01391 | 0.00225 |
| 6 | `device_customer_count` | +0.00895 | 0.00826 |
| 7 | `is_new_ip_for_customer` | +0.00684 | 0.00505 |
| 8 | `previous_transaction_count` | +0.00453 | 0.00241 |
| 9 | `historical_failure_rate` | +0.00432 | 0.00091 |
| 10 | `city_frequency` | +0.00422 | 0.00462 |
| 11 | `previous_country_count` | +0.00321 | 0.00096 |
| 12 | `ip_customer_count` | +0.00309 | 0.00413 |

## 11. Phase 3 vs Phase 4 - the signal matrix

Both engines scored on the test fold at their own selected thresholds. **The
scores are not combined** - that is the decision engine's job in a later phase.

| Quadrant | Transactions | Fraud | Fraud rate |
| --- | ---: | ---: | ---: |
| low fraud / low anomaly | 2,831 | 5 | 0.2% |
| low fraud / **high anomaly** | 123 | 11 | 8.9% |
| **high fraud** / low anomaly | 12 | 10 | 83.3% |
| **high fraud** / **high anomaly** | 34 | 34 | 100.0% |

Spearman rank correlation between the two signals:
**0.435** - they rank transactions differently,
which is the point.

| Fraud caught by | Count |
| --- | ---: |
| both engines | 34 |
| supervised only | 10 |
| **anomaly only** | **11** |
| neither | 5 |

Of 60 fraudulent transactions in the test fold, the
supervised model alone flags
44,
and the anomaly engine alone flags
45.
Between them they reach
55.

## 12. Demo scenarios

| Transaction | Label | XGBoost probability | Anomaly score | Severity |
| --- | ---: | ---: | ---: | --- |
| `TXN_SCENARIO_A_CURRENT` | legitimate | 0.000049 | **58** | LOW |
| `TXN_SCENARIO_B_CURRENT` | fraud | 0.999644 | **100** | CRITICAL |
| `TXN_SCENARIO_C_CURRENT_1` | fraud | 0.200291 | **100** | CRITICAL |
| `TXN_SCENARIO_C_CURRENT_2` | fraud | 0.995550 | **100** | CRITICAL |
| `TXN_SCENARIO_C_CURRENT_3` | fraud | 0.996483 | **100** | CRITICAL |

### Measured local deviations

Where each standout behaviour sits relative to the fitted normal population.
This is directional, not a risk claim: a long-standing customer legitimately has
an unusually high `previous_transaction_count`.

**`TXN_SCENARIO_A_CURRENT`** - anomaly 58 (LOW), customer deviation 48 driven by `amount_vs_historical_average`

* `previous_transaction_count` = 60.00 (exceeds 100% of normal)
* `previous_success_count` = 60.00 (exceeds 100% of normal)
* `ip_age_hours` = 2,143.47 (exceeds 100% of normal)
* `device_transaction_count` = 60.00 (exceeds 100% of normal)
* `device_age_hours` = 2,143.47 (exceeds 100% of normal)

**`TXN_SCENARIO_B_CURRENT`** - anomaly 100 (CRITICAL), customer deviation 100 driven by `amount_vs_historical_average`

* `transactions_last_1h` = 3.00 (exceeds 100% of normal)
* `previous_transaction_count` = 48.00 (exceeds 100% of normal)
* `previous_success_count` = 45.00 (exceeds 100% of normal)
* `log_amount` = 11.35 (exceeds 100% of normal)
* `ip_transactions_last_1h` = 3.00 (exceeds 100% of normal)

**`TXN_SCENARIO_C_CURRENT_1`** - anomaly 100 (CRITICAL), customer deviation 100 driven by `transactions_last_1h`

* `transactions_last_1h` = 4.00 (exceeds 100% of normal)
* `ip_transactions_last_24h` = 12.00 (exceeds 100% of normal)
* `ip_transactions_last_1h` = 12.00 (exceeds 100% of normal)
* `ip_failed_last_1h` = 3.00 (exceeds 100% of normal)
* `device_transactions_last_24h` = 12.00 (exceeds 100% of normal)

**`TXN_SCENARIO_C_CURRENT_2`** - anomaly 100 (CRITICAL), customer deviation 100 driven by `transactions_last_1h`

* `transactions_last_1h` = 4.00 (exceeds 100% of normal)
* `ip_transactions_last_24h` = 13.00 (exceeds 100% of normal)
* `ip_transactions_last_1h` = 13.00 (exceeds 100% of normal)
* `ip_failed_last_1h` = 3.00 (exceeds 100% of normal)
* `device_transactions_last_24h` = 13.00 (exceeds 100% of normal)

**`TXN_SCENARIO_C_CURRENT_3`** - anomaly 100 (CRITICAL), customer deviation 100 driven by `transactions_last_1h`

* `transactions_last_1h` = 4.00 (exceeds 100% of normal)
* `ip_transactions_last_24h` = 14.00 (exceeds 100% of normal)
* `ip_transactions_last_1h` = 14.00 (exceeds 100% of normal)
* `ip_failed_last_1h` = 3.00 (exceeds 100% of normal)
* `device_transactions_last_24h` = 14.00 (exceeds 100% of normal)

## 13. Limitations

* The dataset is synthetic with deliberately learnable fraud structure. These
  numbers measure the pipeline, not real-world performance.
* Precision at the selected threshold is low by design - a recall-weighted
  objective on a signal that flags *unusual*, not *fraudulent*, behaviour will
  surface legitimate outliers. The engine is built to feed an investigation, not
  to block payments.
* Anomaly score is a rank within normal behaviour, so calibration metrics
  (Brier, ECE) are deliberately not reported: they would treat a rank as a
  probability.
* The severity bands and threshold are fitted to this dataset's validation fold
  and would need re-deriving on real traffic.
