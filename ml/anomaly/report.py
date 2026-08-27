"""Render ``docs/anomaly-evaluation.md`` from the measured metrics.

    python -m ml.anomaly.report

Every number is read out of ``ml/models/anomaly_metrics.json`` and
``ml/models/signal_matrix.json``, both produced by real runs. Nothing is typed
by hand, so the document cannot drift from what the model actually scored.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from typing import Any

from app.core.logging import configure_logging
from ml.anomaly.paths import ANOMALY_METRICS_PATH, ANOMALY_REPORT_PATH, SIGNAL_MATRIX_PATH

logger = logging.getLogger(__name__)


def _stamp(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def _fold_table(metrics: dict[str, Any]) -> str:
    lines = [
        "| Fold | Rows | Fraud | Prevalence | From | To |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for name in ("train", "validation", "test"):
        fold = metrics["split"][name]
        lines.append(
            f"| {name} | {fold['rows']:,} | {fold['fraud_rows']:,} | "
            f"{fold['fraud_prevalence']:.2%} | {fold['start'][:16].replace('T', ' ')} | "
            f"{fold['end'][:16].replace('T', ' ')} |"
        )
    return "\n".join(lines)


def _results_table(metrics: dict[str, Any]) -> str:
    lines = [
        "| Fold | Precision | Recall | F1 | PR-AUC | Random PR-AUC | Lift | ROC-AUC |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for fold in ("validation", "test"):
        entry = metrics["metrics"][fold]
        lines.append(
            f"| {fold} | {entry['precision']:.4f} | {entry['recall']:.4f} | "
            f"{entry['f1']:.4f} | **{entry['pr_auc']:.4f}** | {entry['pr_auc_baseline']:.4f} | "
            f"{entry['lift_over_random']:.1f}x | {entry['roc_auc']:.4f} |"
        )
    return "\n".join(lines)


def _confusion(metrics: dict[str, Any], fold: str) -> str:
    matrix = metrics["metrics"][fold]["confusion"]
    return (
        "| | Predicted normal | Predicted anomalous |\n"
        "| --- | ---: | ---: |\n"
        f"| **Actually legitimate** | {matrix['true_negatives']:,} | "
        f"{matrix['false_positives']:,} |\n"
        f"| **Actually fraud** | {matrix['false_negatives']:,} | {matrix['true_positives']:,} |"
    )


def _distribution_table(metrics: dict[str, Any], fold: str) -> str:
    entry = metrics["metrics"][fold]
    legitimate = entry["legitimate_scores"]
    fraud = entry["fraud_scores"]
    percentiles = list(legitimate["percentiles"])

    header = "| Group | Count | Mean | " + " | ".join(percentiles) + " |"
    divider = "| --- | ---: | ---: | " + " | ".join("---:" for _ in percentiles) + " |"
    rows = [header, divider]
    for label, group in (("legitimate", legitimate), ("fraud", fraud)):
        cells = " | ".join(f"{group['percentiles'][p]:.1f}" for p in percentiles)
        rows.append(f"| {label} | {group['count']:,} | {group['mean']:.1f} | {cells} |")
    return "\n".join(rows)


def _severity_table(metrics: dict[str, Any], fold: str) -> str:
    breakdown = metrics["severity_breakdown"][fold]
    lines = ["| Severity | Transactions | Fraud | Fraud rate |", "| --- | ---: | ---: | ---: |"]
    for band in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        cell = breakdown[band]
        lines.append(
            f"| {band} | {cell['rows']:,} | {cell['fraud_rows']:,} | {cell['fraud_rate']:.2%} |"
        )
    return "\n".join(lines)


def _sensitivity_table(metrics: dict[str, Any], limit: int = 12) -> str:
    lines = [
        "| Rank | Behavioral feature | Validation PR-AUC drop | Std |",
        "| ---: | --- | ---: | ---: |",
    ]
    for rank, entry in enumerate(metrics["sensitivity"]["features"][:limit], start=1):
        lines.append(
            f"| {rank} | `{entry['feature']}` | {entry['mean_pr_auc_drop']:+.5f} | "
            f"{entry['std']:.5f} |"
        )
    return "\n".join(lines)


def _quadrant_table(matrix: dict[str, Any]) -> str:
    labels = {
        "low_fraud_low_anomaly": "low fraud / low anomaly",
        "low_fraud_high_anomaly": "low fraud / **high anomaly**",
        "high_fraud_low_anomaly": "**high fraud** / low anomaly",
        "high_fraud_high_anomaly": "**high fraud** / **high anomaly**",
    }
    lines = ["| Quadrant | Transactions | Fraud | Fraud rate |", "| --- | ---: | ---: | ---: |"]
    for key, label in labels.items():
        cell = matrix["quadrants"][key]
        lines.append(
            f"| {label} | {cell['transactions']:,} | {cell['fraud_rows']:,} | "
            f"{cell['fraud_rate']:.1%} |"
        )
    return "\n".join(lines)


def _demo_table(matrix: dict[str, Any]) -> str:
    lines = [
        "| Transaction | Label | XGBoost probability | Anomaly score | Severity |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in matrix["demo_scenarios"]:
        label = "fraud" if row["is_fraud"] else "legitimate"
        lines.append(
            f"| `{row['transaction_id']}` | {label} | {row['fraud_probability']:.6f} | "
            f"**{row['anomaly_score']}** | {row['severity']} |"
        )
    return "\n".join(lines)


def _demo_deviations(matrix: dict[str, Any]) -> str:
    blocks: list[str] = []
    for row in matrix["demo_scenarios"]:
        deviations = row.get("top_deviations", [])
        if not deviations:
            continue
        items = "\n".join(
            f"* `{d['feature']}` = {d['value']:,.2f} (exceeds {d['percentile']:.0f}% of normal)"
            for d in deviations
        )
        blocks.append(
            f"**`{row['transaction_id']}`** - anomaly {row['anomaly_score']} "
            f"({row['severity']}), customer deviation {row['customer_deviation_score']} "
            f"driven by `{row['customer_deviation_driver']}`\n\n{items}"
        )
    return "\n\n".join(blocks)


def render(metrics: dict[str, Any], matrix: dict[str, Any]) -> str:
    """Build the full anomaly evaluation report."""
    fitting = metrics["fitting_population"]
    features = metrics["behavioral_features"]
    forest = metrics["config"]["isolation_forest"]
    threshold = metrics["threshold"]
    bands = metrics["severity_bands"]
    test = metrics["metrics"]["test"]
    test_matrix = matrix["test_fold"]
    complementarity = test_matrix["complementarity"]

    parameters = "\n".join(f"| `{key}` | `{value}` |" for key, value in forest.items())
    groups = "\n".join(
        f"| {name} | {len(group)} | {', '.join(f'`{f}`' for f in group)} |"
        for name, group in features["groups"].items()
    )

    return f"""# RazorShield AI - behavioral anomaly engine evaluation

*Generated from `ml/models/anomaly_metrics.json` and `ml/models/signal_matrix.json`
by `python -m ml.anomaly.report` on {_stamp(metrics["generated_at"])}. Every figure
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

{_fold_table(metrics)}

The split configuration is read from `ml/training/config.yaml`, so these folds
are byte-identical to Phase 3's. Two engines evaluated on different folds could
not be compared.

| Fitting population | |
| --- | --- |
| Source | {fitting["source"]} |
| Rows fitted | **{fitting["rows"]:,}** |
| Known fraud excluded | {fitting["excluded_known_fraud"]:,} |
| From | {fitting["start"][:16].replace("T", " ")} |
| To | {fitting["end"][:16].replace("T", " ")} |

## 4. Behavioral features

**{features["count"]} features**, an explicit subset of the 74 point-in-time
features built in Phase 3 - not a second, incompatible history system. Every
temporal-leakage guarantee from Phase 3 therefore applies unchanged.

| Group | Count | Features |
| --- | ---: | --- |
{groups}

{len(features["log1p_transformed"])} heavy-tailed columns are `log1p`
transformed so one very large payment cannot dominate every random split. Ratios,
rates and indicators are left untouched.

Excluded on purpose: the fraud label, all identifiers, the four categoricals,
calendar features, and static entity attributes such as `ip_reputation_score`.
The full reasoning is in `ml/anomaly/schema.py`.

## 5. Isolation Forest

| Parameter | Value |
| --- | --- |
{parameters}
| `random_state` | `{metrics["config"]["random_seed"]}` |

Training time: **{metrics["training_seconds"]:.2f}s** on {fitting["rows"]:,} rows.

`contamination` is pinned at `{forest["contamination"]}` rather than left to
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
{bands["source_percentiles"][0]:.0f}/{bands["source_percentiles"][1]:.0f}/{bands["source_percentiles"][2]:.0f},
not chosen as round numbers:

| Band | Score range |
| --- | --- |
| LOW | < {bands["medium"]:.1f} |
| MEDIUM | {bands["medium"]:.1f} - {bands["high"]:.1f} |
| HIGH | {bands["high"]:.1f} - {bands["critical"]:.1f} |
| CRITICAL | >= {bands["critical"]:.1f} |

Measured fraud rate inside each band on the **test** fold - the bands are only
useful if the fraud rate actually rises across them:

{_severity_table(metrics, "test")}

## 8. Threshold

Selected on **validation** by maximising F{threshold["beta"]:.0f}, the same
recall-weighted objective Phase 3 uses, subject to a precision floor.

- Selected threshold: **{threshold["threshold"]:.2f}**
- Validation at this threshold: precision **{threshold["precision"]:.4f}**,
  recall **{threshold["recall"]:.4f}**, F1 **{threshold["f1"]:.4f}**
- F1-optimal alternative: {threshold["f1_optimal_threshold"]:.2f}
  (F1 {threshold["f1_optimal_f1"]:.4f})

Test labels were not used for any selection.

## 9. Results

{_results_table(metrics)}

PR-AUC is reported against the random baseline because at ~2% prevalence an
absolute PR-AUC is hard to read: the test figure is
**{test["lift_over_random"]:.1f}x** better than random ranking.

### Confusion matrix - test fold, threshold {test["threshold"]:.2f}

{_confusion(metrics, "test")}

### Score distribution - test fold

{_distribution_table(metrics, "test")}

## 10. Feature sensitivity

Isolation Forest has no native feature importance, so none is invented. This is
permutation sensitivity: shuffle a behavioral column, rescore, and measure how
much the anomaly ranking's agreement with ground truth degrades. Labels are used
for measurement only.

{_sensitivity_table(metrics)}

## 11. Phase 3 vs Phase 4 - the signal matrix

Both engines scored on the test fold at their own selected thresholds. **The
scores are not combined** - that is the decision engine's job in a later phase.

{_quadrant_table(test_matrix)}

Spearman rank correlation between the two signals:
**{test_matrix["rank_correlation"]:.3f}** - they rank transactions differently,
which is the point.

| Fraud caught by | Count |
| --- | ---: |
| both engines | {complementarity["fraud_caught_by_both"]} |
| supervised only | {complementarity["fraud_caught_only_by_supervised"]} |
| **anomaly only** | **{complementarity["fraud_caught_only_by_anomaly"]}** |
| neither | {complementarity["fraud_missed_by_both"]} |

Of {test_matrix["fraud_rows"]} fraudulent transactions in the test fold, the
supervised model alone flags
{complementarity["fraud_caught_by_both"] + complementarity["fraud_caught_only_by_supervised"]},
and the anomaly engine alone flags
{complementarity["fraud_caught_by_both"] + complementarity["fraud_caught_only_by_anomaly"]}.
Between them they reach
{complementarity["fraud_caught_by_both"] + complementarity["fraud_caught_only_by_supervised"] + complementarity["fraud_caught_only_by_anomaly"]}.

## 12. Demo scenarios

{_demo_table(matrix)}

### Measured local deviations

Where each standout behaviour sits relative to the fitted normal population.
This is directional, not a risk claim: a long-standing customer legitimately has
an unusually high `previous_transaction_count`.

{_demo_deviations(matrix)}

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
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    configure_logging("INFO")

    for path, command in (
        (ANOMALY_METRICS_PATH, "python -m ml.anomaly.train"),
        (SIGNAL_MATRIX_PATH, "python -m ml.anomaly.compare"),
    ):
        if not path.exists():
            logger.error("%s is missing; run `%s` first", path.name, command)
            return 1

    metrics = json.loads(ANOMALY_METRICS_PATH.read_text(encoding="utf-8"))
    matrix = json.loads(SIGNAL_MATRIX_PATH.read_text(encoding="utf-8"))

    ANOMALY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANOMALY_REPORT_PATH.write_text(render(metrics, matrix), encoding="utf-8")
    logger.info("Report -> %s", ANOMALY_REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
