"""Build the point-in-time training dataset.

    python -m ml.training.build_dataset

Walks every transaction once in chronological order, computes its features from
history that existed strictly before it, validates the schema, and writes a
versioned CSV plus a metadata sidecar recording dtypes, versions and row counts.

Identifiers and the transaction's own outcome are written to the metadata
columns so the dataset stays auditable, but they are excluded from
``FEATURE_COLUMNS`` and can never reach a model.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from ml.config import DATASET_METADATA_PATH, DATASET_PATH, DATASET_VERSION, ensure_directories
from ml.features.batch_provider import iter_feature_rows
from ml.features.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    METADATA_COLUMNS,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    validate_row,
)

logger = logging.getLogger(__name__)


class DatasetValidationError(RuntimeError):
    """The generated dataset failed its own consistency checks."""


def build_dataframe(session: Session) -> pd.DataFrame:
    """Generate the full feature matrix, oldest transaction first."""
    records: list[dict[str, Any]] = []
    validated = False

    for transaction, features in iter_feature_rows(session):
        if not validated:
            # One schema check is enough: every row comes from the same builder.
            validate_row(features)
            validated = True

        record: dict[str, Any] = dict(features)
        record.update(
            {
                "transaction_id": transaction.transaction_id,
                "transaction_db_id": transaction.id,
                "merchant_id": transaction.merchant_id,
                "customer_id": transaction.customer_id,
                "device_id": transaction.device_id,
                "ip_address_id": transaction.ip_address_id,
                "transaction_timestamp": transaction.timestamp,
                "status": transaction.status,
                TARGET_COLUMN: int(transaction.is_fraud),
            }
        )
        records.append(record)

    if not records:
        raise DatasetValidationError("no transactions found; seed the database first")

    frame = pd.DataFrame.from_records(records)
    ordered = [*METADATA_COLUMNS, *FEATURE_COLUMNS, TARGET_COLUMN]
    return frame[ordered]


def validate_dataframe(frame: pd.DataFrame) -> None:
    """Fail loudly on anything that would poison training."""
    problems: list[str] = []

    missing = [column for column in FEATURE_COLUMNS if column not in frame.columns]
    if missing:
        # Report and stop: the dtype checks below would raise KeyError on the
        # very columns we already know are absent.
        raise DatasetValidationError(
            f"dataset validation failed: missing feature columns {missing}"
        )

    if TARGET_COLUMN not in frame.columns:
        problems.append(f"missing target column {TARGET_COLUMN!r}")
    elif set(frame[TARGET_COLUMN].unique()) - {0, 1}:
        problems.append("target contains values outside {0, 1}")

    numeric = frame[list(NUMERIC_FEATURES)]
    if numeric.isna().any().any():
        offending = sorted(numeric.columns[numeric.isna().any()].tolist())
        problems.append(f"numeric features contain NaN: {offending}")

    non_numeric = [
        column for column in NUMERIC_FEATURES if not pd.api.types.is_numeric_dtype(frame[column])
    ]
    if non_numeric:
        problems.append(f"numeric features with a non-numeric dtype: {non_numeric}")

    if not frame["transaction_timestamp"].is_monotonic_increasing:
        problems.append("rows are not in chronological order")

    if frame["transaction_id"].duplicated().any():
        problems.append("duplicate transaction ids in the dataset")

    positives = int(frame[TARGET_COLUMN].sum())
    if positives == 0:
        problems.append("no positive examples; the dataset cannot train a classifier")

    if problems:
        raise DatasetValidationError(
            "dataset validation failed:\n" + "\n".join(f"  - {p}" for p in problems)
        )


def build_metadata(frame: pd.DataFrame, elapsed_seconds: float) -> dict[str, Any]:
    """Sidecar describing the dataset, so it can be reloaded with exact dtypes."""
    positives = int(frame[TARGET_COLUMN].sum())
    total = len(frame)

    return {
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "build_seconds": round(elapsed_seconds, 3),
        "rows": total,
        "fraud_rows": positives,
        "legitimate_rows": total - positives,
        "fraud_prevalence": positives / total if total else 0.0,
        "time_range": {
            "start": frame["transaction_timestamp"].min().isoformat(),
            "end": frame["transaction_timestamp"].max().isoformat(),
        },
        "feature_count": len(FEATURE_COLUMNS),
        "numeric_features": list(NUMERIC_FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "metadata_columns": list(METADATA_COLUMNS),
        "target_column": TARGET_COLUMN,
    }


def write_dataset(frame: pd.DataFrame, metadata: dict[str, Any]) -> None:
    ensure_directories()
    frame.to_csv(DATASET_PATH, index=False)
    DATASET_METADATA_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def load_dataset(path: Path | None = None) -> pd.DataFrame:
    """Read a previously built dataset back with the correct dtypes."""
    dataset_path = path or DATASET_PATH
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"{dataset_path} does not exist; run `python -m ml.training.build_dataset` first"
        )

    frame = pd.read_csv(dataset_path, parse_dates=["transaction_timestamp"])
    for column in CATEGORICAL_FEATURES:
        frame[column] = frame[column].astype("string").fillna("unknown")
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    started = time.perf_counter()
    with SessionLocal() as session:
        frame = build_dataframe(session)
    elapsed = time.perf_counter() - started

    validate_dataframe(frame)
    metadata = build_metadata(frame, elapsed)
    write_dataset(frame, metadata)

    logger.info(
        "Built %d rows x %d features in %.2fs (%.2f%% fraud)",
        metadata["rows"],
        metadata["feature_count"],
        elapsed,
        100 * metadata["fraud_prevalence"],
    )
    logger.info("Dataset  -> %s", DATASET_PATH)
    logger.info("Metadata -> %s", DATASET_METADATA_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
