"""Render ``docs/ml-evaluation.md`` from ``ml/models/metrics.json``.

    python -m ml.training.report

Every number in the report is read out of the metrics file produced by a real
training run. Nothing is typed by hand, so the document cannot drift away from
what the models actually scored.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from typing import Any

from app.core.logging import configure_logging
from ml.config import EVALUATION_REPORT_PATH, METRICS_PATH

logger = logging.getLogger(__name__)

BASELINE_VERSION = "logistic-regression-v1"
PRIMARY_VERSION = "xgboost-v1"


def _stamp(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def _fold_rows(metrics: dict[str, Any]) -> str:
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


def _comparison_table(metrics: dict[str, Any], fold: str) -> str:
    lines = [
        "| Model | Precision | Recall | F1 | PR-AUC | ROC-AUC | Brier | ECE |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for version in (BASELINE_VERSION, PRIMARY_VERSION):
        entry = metrics["models"][version]["metrics"][fold]
        lines.append(
            f"| `{version}` | {entry['precision']:.4f} | {entry['recall']:.4f} | "
            f"{entry['f1']:.4f} | **{entry['pr_auc']:.4f}** | {entry['roc_auc']:.4f} | "
            f"{entry['brier_score']:.5f} | {entry['expected_calibration_error']:.4f} |"
        )
    return "\n".join(lines)


def _confusion_block(metrics: dict[str, Any], version: str, fold: str) -> str:
    entry = metrics["models"][version]["metrics"][fold]
    matrix = entry["confusion"]
    return (
        f"| | Predicted legitimate | Predicted fraud |\n"
        f"| --- | ---: | ---: |\n"
        f"| **Actually legitimate** | {matrix['true_negatives']:,} | "
        f"{matrix['false_positives']:,} |\n"
        f"| **Actually fraud** | {matrix['false_negatives']:,} | {matrix['true_positives']:,} |"
    )


def _threshold_block(metrics: dict[str, Any], version: str) -> str:
    choice = metrics["models"][version]["threshold"]
    return (
        f"- Selected threshold: **{choice['threshold']:.6f}**\n"
        f"- Objective: maximise F{choice['beta']:.0f} (recall weighted "
        f"{choice['beta'] ** 2:.0f}x precision), subject to a precision floor\n"
        f"- Validation at this threshold: precision **{choice['precision']:.4f}**, "
        f"recall **{choice['recall']:.4f}**, F1 **{choice['f1']:.4f}**, "
        f"F{choice['beta']:.0f} **{choice['fbeta']:.4f}**\n"
        f"- For reference, the F1-optimal threshold would be "
        f"{choice['f1_optimal_threshold']:.6f} (F1 {choice['f1_optimal_f1']:.4f})"
    )


def _importance_table(metrics: dict[str, Any], kind: str, limit: int = 10) -> str:
    entries = metrics["feature_importance"][kind][:limit]
    if kind == "permutation":
        lines = [
            "| Rank | Feature | Validation PR-AUC drop | Std |",
            "| ---: | --- | ---: | ---: |",
        ]
        for rank, entry in enumerate(entries, start=1):
            lines.append(
                f"| {rank} | `{entry['feature']}` | {entry['mean_pr_auc_drop']:+.5f} | "
                f"{entry['std']:.5f} |"
            )
    else:
        lines = ["| Rank | Feature | Gain |", "| ---: | --- | ---: |"]
        for rank, entry in enumerate(entries, start=1):
            lines.append(f"| {rank} | `{entry['feature']}` | {entry['gain']:.4f} |")
    return "\n".join(lines)


def _calibration_block(metrics: dict[str, Any]) -> str:
    report = metrics["models"][PRIMARY_VERSION]["calibration"]
    raw = report["raw_expected_calibration_error"]
    limit = report["limit"]

    if not report["applied"]:
        return (
            f"Measured expected calibration error on validation: **{raw:.4f}**, against a "
            f"limit of {limit:.4f}.\n\n"
            "The raw probabilities are already well calibrated, so **no calibration layer was "
            "applied**. Adding isotonic regression here would have been unnecessary complexity "
            "for no measurable gain."
        )
    return (
        f"Measured expected calibration error on validation: **{raw:.4f}**, above the "
        f"{limit:.4f} limit, so **{report['method']} calibration was applied**, fitted on the "
        f"validation fold. Post-calibration validation ECE: "
        f"**{report['calibrated_expected_calibration_error']:.4f}**."
    )


def _grid_table(metrics: dict[str, Any]) -> str:
    trials = metrics["models"][PRIMARY_VERSION].get("grid_trials", [])
    lines = ["| # | Parameters | Validation PR-AUC |", "| ---: | --- | ---: |"]
    best = max((trial["validation_pr_auc"] for trial in trials), default=0.0)
    for index, trial in enumerate(trials, start=1):
        params = ", ".join(f"{key}={value}" for key, value in trial["params"].items())
        marker = " **(selected)**" if trial["validation_pr_auc"] == best else ""
        lines.append(f"| {index} | `{params}` | {trial['validation_pr_auc']:.4f}{marker} |")
    return "\n".join(lines)


def render(metrics: dict[str, Any]) -> str:
    """Build the full evaluation report."""
    dataset = metrics["dataset"]
    config = metrics["config"]
    primary = metrics["models"][PRIMARY_VERSION]
    baseline = metrics["models"][BASELINE_VERSION]

    return f"""# RazorShield AI - fraud risk engine evaluation

*Generated from `ml/models/metrics.json` by `python -m ml.training.report` on
{_stamp(metrics["generated_at"])}. Every figure below comes from a real training
run; none is typed by hand.*

> RazorShield AI is a hackathon simulation. The dataset is synthetic, so these
> numbers measure the pipeline on generated data with deliberately learnable
> fraud structure - they are not a claim about real-world fraud detection
> performance.

## 1. Dataset

| | |
| --- | --- |
| Dataset version | `{dataset["dataset_version"]}` |
| Feature version | `{dataset["feature_version"]}` |
| Transactions | {dataset["rows"]:,} |
| Fraud | {dataset["fraud_rows"]:,} |
| Legitimate | {dataset["legitimate_rows"]:,} |
| Fraud prevalence | **{dataset["fraud_prevalence"]:.2%}** |
| Features | {dataset["feature_count"]} ({len(dataset["numeric_features"])} numeric, {len(dataset["categorical_features"])} categorical) |
| Time range | {dataset["time_range"]["start"][:16].replace("T", " ")} to {dataset["time_range"]["end"][:16].replace("T", " ")} |
| Build time | {dataset["build_seconds"]:.2f}s |

## 2. Chronological split

Train is strictly older than validation, which is strictly older than test. No
random shuffling is used anywhere: a random split would let the model learn from
transactions that occur *after* the ones it is judged on.

{_fold_rows(metrics)}

Prevalence differs between folds because fraud is not uniform over time. That is
the realistic case and is left uncorrected.

## 3. Class imbalance strategy

At {dataset["fraud_prevalence"]:.2%} prevalence, predicting "legitimate" for
everything scores {1 - dataset["fraud_prevalence"]:.2%} accuracy and catches no
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

{_grid_table(metrics)}

## 5. Threshold selection

0.5 was not assumed. The operating point was swept across every threshold the
validation data distinguishes.

{_threshold_block(metrics, PRIMARY_VERSION)}

RazorShield routes elevated risk to a human review queue: a false positive costs
an analyst a few minutes, a false negative costs a chargeback. F2 encodes that
asymmetry by weighting recall four times as heavily as precision.

## 6. Calibration

{_calibration_block(metrics)}

## 7. Results

### Validation

{_comparison_table(metrics, "validation")}

### Test (held out, used only here)

{_comparison_table(metrics, "test")}

### Confusion matrix - `{PRIMARY_VERSION}` on test, at threshold {primary["threshold"]["threshold"]:.4f}

{_confusion_block(metrics, PRIMARY_VERSION, "test")}

### Confusion matrix - `{BASELINE_VERSION}` on test, at threshold {baseline["threshold"]["threshold"]:.4f}

{_confusion_block(metrics, BASELINE_VERSION, "test")}

**Selected model: `{metrics["selected_model"]}`**, chosen on validation PR-AUC.

## 8. Feature importance

Both measures are computed, neither assumed.

### Permutation importance (validation, {metrics["feature_importance"]["permutation_repeats"]} repeats, scored by average precision)

How much validation PR-AUC actually degrades when a column is shuffled - a
direct measure of what the model relies on.

{_importance_table(metrics, "permutation")}

### Gain importance (training)

How much each split improved the objective while fitting.

{_importance_table(metrics, "gain")}

## 9. Reproducibility

| | |
| --- | --- |
| Random seed | `{config["random_seed"]}` |
| Feature version | `{config["feature_version"]}` |
| Dataset version | `{config["dataset_version"]}` |
| Split | {config["split"]["train"]:.0%} / {config["split"]["validation"]:.0%} / {config["split"]["test"]:.0%} |
| Configuration | `ml/training/config.yaml` |
| Baseline training time | {baseline["training_seconds"]:.2f}s |
| Grid search time | {primary["training_seconds"]:.2f}s |

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
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    parser.parse_args(argv)
    configure_logging("INFO")

    if not METRICS_PATH.exists():
        logger.error("No metrics found; run `python -m ml.training.train` first")
        return 1

    metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    EVALUATION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVALUATION_REPORT_PATH.write_text(render(metrics), encoding="utf-8")
    logger.info("Report -> %s", EVALUATION_REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
