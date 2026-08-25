# RazorShield AI - fraud risk engine evaluation

*Generated from `ml/models/metrics.json` by `python -m ml.training.report` on
2026-08-22 11:22 UTC. Every figure below comes from a real training
run; none is typed by hand.*

> RazorShield AI is a hackathon simulation. The dataset is synthetic, so these
> numbers measure the pipeline on generated data with deliberately learnable
> fraud structure - they are not a claim about real-world fraud detection
> performance.

## 1. Dataset

| | |
| --- | --- |
| Dataset version | `v1` |
| Feature version | `v1` |
| Transactions | 20,000 |
| Fraud | 295 |
| Legitimate | 19,705 |
| Fraud prevalence | **1.47%** |
| Features | 74 (70 numeric, 4 categorical) |
| Time range | 2026-05-24 10:11 to 2026-08-22 09:58 |
| Build time | 3.25s |

## 2. Chronological split

Train is strictly older than validation, which is strictly older than test. No
random shuffling is used anywhere: a random split would let the model learn from
transactions that occur *after* the ones it is judged on.

| Fold | Rows | Fraud | Prevalence | From | To |
| --- | ---: | ---: | ---: | --- | --- |
| train | 14,000 | 186 | 1.33% | 2026-05-24 10:11 | 2026-07-26 14:30 |
| validation | 3,000 | 49 | 1.63% | 2026-07-26 14:31 | 2026-08-09 07:52 |
| test | 3,000 | 60 | 2.00% | 2026-08-09 08:04 | 2026-08-22 09:58 |

Prevalence differs between folds because fraud is not uniform over time. That is
the realistic case and is left uncorrected.

## 3. Class imbalance strategy

At 1.47% prevalence, predicting "legitimate" for
everything scores 98.52% accuracy and catches no
fraud. **Accuracy is therefore not reported as a headline metric.** PR-AUC is the
primary comparison metric: it summarises performance across all operating points
without being inflated by the large, easy true-negative mass that lifts ROC-AUC
on imbalanced data.

- **Logistic regression** uses `class_weight="balanced"`, re-weighting the loss
  by inverse class frequency.
- **XGBoost** uses `scale_pos_weight` set to the negative/positive ratio of the
  training fold, scaling the rare class's gradient contribution up to match the
  majority class.

## 4. Hyperparameter search

Candidates were scored on **validation** PR-AUC. The test fold was not touched
during selection.

| # | Parameters | Validation PR-AUC |
| ---: | --- | ---: |
| 1 | `n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=1, reg_lambda=1.0, reg_alpha=0.0` | 0.8654 |
| 2 | `n_estimators=300, max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=3, reg_lambda=2.0, reg_alpha=0.0` | 0.8558 |
| 3 | `n_estimators=500, max_depth=4, learning_rate=0.03, subsample=0.9, colsample_bytree=0.7, min_child_weight=5, reg_lambda=2.0, reg_alpha=0.5` | 0.8681 **(selected)** |
| 4 | `n_estimators=500, max_depth=6, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8, min_child_weight=1, reg_lambda=5.0, reg_alpha=0.0` | 0.8631 |
| 5 | `n_estimators=200, max_depth=3, learning_rate=0.1, subsample=1.0, colsample_bytree=1.0, min_child_weight=1, reg_lambda=1.0, reg_alpha=0.0` | 0.8667 |
| 6 | `n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.7, colsample_bytree=0.7, min_child_weight=3, reg_lambda=3.0, reg_alpha=0.5` | 0.8542 |
| 7 | `n_estimators=600, max_depth=3, learning_rate=0.03, subsample=0.8, colsample_bytree=0.6, min_child_weight=10, reg_lambda=10.0, reg_alpha=1.0` | 0.8590 |
| 8 | `n_estimators=400, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.6, min_child_weight=20, reg_lambda=5.0, reg_alpha=1.0` | 0.8600 |
| 9 | `n_estimators=800, max_depth=3, learning_rate=0.02, subsample=0.9, colsample_bytree=0.8, min_child_weight=15, reg_lambda=8.0, reg_alpha=0.5` | 0.8666 |

## 5. Threshold selection

0.5 was not assumed. The operating point was swept across every threshold the
validation data distinguishes.

- Selected threshold: **0.533209**
- Objective: maximise F2 (recall weighted 4x precision), subject to a precision floor
- Validation at this threshold: precision **0.8200**, recall **0.8367**, F1 **0.8283**, F2 **0.8333**
- For reference, the F1-optimal threshold would be 0.861831 (F1 0.8506)

RazorShield routes elevated risk to a human review queue: a false positive costs
an analyst a few minutes, a false negative costs a chargeback. F2 encodes that
asymmetry by weighting recall four times as heavily as precision.

## 6. Calibration

Measured expected calibration error on validation: **0.0068**, against a limit of 0.0200.

The raw probabilities are already well calibrated, so **no calibration layer was applied**. Adding isotonic regression here would have been unnecessary complexity for no measurable gain.

## 7. Results

### Validation

| Model | Precision | Recall | F1 | PR-AUC | ROC-AUC | Brier | ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `logistic-regression-v1` | 0.5634 | 0.8163 | 0.6667 | **0.8063** | 0.9807 | 0.02719 | 0.0391 |
| `xgboost-v1` | 0.8200 | 0.8367 | 0.8283 | **0.8681** | 0.9895 | 0.00559 | 0.0068 |

### Test (held out, used only here)

| Model | Precision | Recall | F1 | PR-AUC | ROC-AUC | Brier | ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `logistic-regression-v1` | 0.5690 | 0.5500 | 0.5593 | **0.6336** | 0.7900 | 0.02790 | 0.0331 |
| `xgboost-v1` | 0.9565 | 0.7333 | 0.8302 | **0.8844** | 0.9950 | 0.00552 | 0.0039 |

### Confusion matrix - `xgboost-v1` on test, at threshold 0.5332

| | Predicted legitimate | Predicted fraud |
| --- | ---: | ---: |
| **Actually legitimate** | 2,938 | 2 |
| **Actually fraud** | 16 | 44 |

### Confusion matrix - `logistic-regression-v1` on test, at threshold 0.9477

| | Predicted legitimate | Predicted fraud |
| --- | ---: | ---: |
| **Actually legitimate** | 2,915 | 25 |
| **Actually fraud** | 27 | 33 |

**Selected model: `xgboost-v1`**, chosen on validation PR-AUC.

## 8. Feature importance

Both measures are computed, neither assumed.

### Permutation importance (validation, 5 repeats, scored by average precision)

How much validation PR-AUC actually degrades when a column is shuffled - a
direct measure of what the model relies on.

| Rank | Feature | Validation PR-AUC drop | Std |
| ---: | --- | ---: | ---: |
| 1 | `amount_vs_historical_average` | +0.19027 | 0.02428 |
| 2 | `amount` | +0.18744 | 0.04164 |
| 3 | `amount_zscore_vs_history` | +0.02848 | 0.01101 |
| 4 | `ip_transaction_count` | +0.02532 | 0.00623 |
| 5 | `city_frequency` | +0.00842 | 0.00258 |
| 6 | `log_amount` | +0.00804 | 0.00550 |
| 7 | `ip_transactions_last_24h` | +0.00521 | 0.00074 |
| 8 | `previous_transaction_count` | +0.00460 | 0.00070 |
| 9 | `previous_failure_count` | +0.00348 | 0.00167 |
| 10 | `is_home_city` | +0.00305 | 0.00162 |

### Gain importance (training)

How much each split improved the objective while fitting.

| Rank | Feature | Gain |
| ---: | --- | ---: |
| 1 | `log_amount` | 0.1035 |
| 2 | `amount` | 0.0983 |
| 3 | `amount_vs_historical_average` | 0.0404 |
| 4 | `is_home_city` | 0.0385 |
| 5 | `ip_country_matches_transaction` | 0.0368 |
| 6 | `failed_transactions_last_1h` | 0.0340 |
| 7 | `is_home_country` | 0.0319 |
| 8 | `amount_zscore_vs_history` | 0.0269 |
| 9 | `device_is_shared` | 0.0248 |
| 10 | `transaction_country_IN` | 0.0201 |

## 9. Reproducibility

| | |
| --- | --- |
| Random seed | `20260101` |
| Feature version | `v1` |
| Dataset version | `v1` |
| Split | 70% / 15% / 15% |
| Configuration | `ml/training/config.yaml` |
| Baseline training time | 0.72s |
| Grid search time | 20.96s |

Rebuild everything with:

```bash
python -m ml.training.build_dataset
```

```bash
python -m ml.training.train
```

```bash
python -m ml.training.report
```
