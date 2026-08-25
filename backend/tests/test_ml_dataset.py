"""Dataset construction, provider parity and chronological splitting."""

from __future__ import annotations

import random
from dataclasses import replace

import pandas as pd
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models import Transaction
from ml.features.batch_provider import iter_feature_rows
from ml.features.builder import build_features
from ml.features.loader import get_merchant_profile
from ml.features.point_in_time import build_history_window
from ml.features.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    METADATA_COLUMNS,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
)
from ml.training.build_dataset import (
    DatasetValidationError,
    build_metadata,
    validate_dataframe,
)
from ml.training.settings import SplitConfig, load_config
from ml.training.split import SplitError, split_chronologically

# Both providers use the same variance formula but accumulate in different
# orders, so parity is asserted to floating-point precision, not bit equality.
RELATIVE_TOLERANCE = 1e-6
ABSOLUTE_TOLERANCE = 1e-6


# --- dataset shape ----------------------------------------------------------


def test_dataset_has_one_row_per_transaction(
    ml_dataset: pd.DataFrame, seeded_engine: Engine
) -> None:
    with Session(seeded_engine) as session:
        total = len(list(session.scalars(select(Transaction.id))))
    assert len(ml_dataset) == total
    assert ml_dataset["transaction_id"].is_unique


def test_dataset_columns_are_features_metadata_and_target(ml_dataset: pd.DataFrame) -> None:
    expected = {*METADATA_COLUMNS, *FEATURE_COLUMNS, TARGET_COLUMN}
    assert set(ml_dataset.columns) == expected


def test_dataset_is_chronological(ml_dataset: pd.DataFrame) -> None:
    assert ml_dataset["transaction_timestamp"].is_monotonic_increasing


def test_dataset_target_is_binary_and_imbalanced(ml_dataset: pd.DataFrame) -> None:
    values = set(ml_dataset[TARGET_COLUMN].unique())
    assert values <= {0, 1}
    prevalence = ml_dataset[TARGET_COLUMN].mean()
    assert 0 < prevalence < 0.15


def test_numeric_features_are_numeric_and_complete(ml_dataset: pd.DataFrame) -> None:
    numeric = ml_dataset[list(NUMERIC_FEATURES)]
    assert not numeric.isna().any().any()
    for column in NUMERIC_FEATURES:
        assert pd.api.types.is_numeric_dtype(ml_dataset[column])


def test_categorical_features_have_no_missing_values(ml_dataset: pd.DataFrame) -> None:
    for column in CATEGORICAL_FEATURES:
        assert not ml_dataset[column].isna().any()


def test_dataset_validation_rejects_a_broken_frame(ml_dataset: pd.DataFrame) -> None:
    broken = ml_dataset.drop(columns=[NUMERIC_FEATURES[0]])
    with pytest.raises(DatasetValidationError, match="missing feature columns"):
        validate_dataframe(broken)


def test_dataset_validation_rejects_unsorted_rows(ml_dataset: pd.DataFrame) -> None:
    shuffled = ml_dataset.iloc[::-1].copy()
    with pytest.raises(DatasetValidationError, match="chronological"):
        validate_dataframe(shuffled)


def test_metadata_reports_the_real_counts(ml_dataset: pd.DataFrame) -> None:
    metadata = build_metadata(ml_dataset, 1.0)
    assert metadata["rows"] == len(ml_dataset)
    assert metadata["fraud_rows"] == int(ml_dataset[TARGET_COLUMN].sum())
    assert metadata["feature_count"] == len(FEATURE_COLUMNS)
    assert metadata["fraud_prevalence"] == pytest.approx(ml_dataset[TARGET_COLUMN].mean())


# --- provider parity --------------------------------------------------------


def test_batch_and_sql_providers_agree(seeded_engine: Engine) -> None:
    """The training path and the serving path must build identical features.

    If they diverged, the model would be trained on one distribution and served
    another - a silent, hard-to-detect production failure.
    """
    with Session(seeded_engine) as session:
        batch_rows = {
            view.transaction_id: (view, features) for view, features in iter_feature_rows(session)
        }

    rng = random.Random(11)
    sample = rng.sample(sorted(batch_rows), 25)

    with Session(seeded_engine) as session:
        for reference in sample:
            view, expected = batch_rows[reference]
            window = build_history_window(session, view)
            actual = build_features(view, window, get_merchant_profile(session, view.merchant_id))

            for name in FEATURE_COLUMNS:
                if isinstance(expected[name], float) or isinstance(actual[name], float):
                    assert float(actual[name]) == pytest.approx(
                        float(expected[name]), rel=RELATIVE_TOLERANCE, abs=ABSOLUTE_TOLERANCE
                    ), f"{reference}: {name}"
                else:
                    assert actual[name] == expected[name], f"{reference}: {name}"


def test_batch_provider_is_deterministic(seeded_engine: Engine) -> None:
    with Session(seeded_engine) as session:
        first = [features for _, features in iter_feature_rows(session)]
    with Session(seeded_engine) as session:
        second = [features for _, features in iter_feature_rows(session)]
    assert first == second


# --- chronological split ----------------------------------------------------


def test_split_is_strictly_time_ordered(ml_dataset: pd.DataFrame) -> None:
    config = load_config()
    split = split_chronologically(ml_dataset, config.split)

    assert split.train.end <= split.validation.start
    assert split.validation.end <= split.test.start
    assert split.train.rows + split.validation.rows + split.test.rows == len(ml_dataset)


def test_split_folds_all_contain_fraud(ml_dataset: pd.DataFrame) -> None:
    split = split_chronologically(ml_dataset, load_config().split)
    for fold in (split.train, split.validation, split.test):
        assert fold.positives > 0, f"{fold.name} fold has no positives"


def test_split_proportions_match_the_configuration(ml_dataset: pd.DataFrame) -> None:
    config = load_config()
    split = split_chronologically(ml_dataset, config.split)
    total = len(ml_dataset)
    assert split.train.rows == int(total * config.split.train)
    assert abs(split.validation.rows - int(total * config.split.validation)) <= 1


def test_split_rejects_unsorted_input(ml_dataset: pd.DataFrame) -> None:
    with pytest.raises(SplitError, match="sorted"):
        split_chronologically(ml_dataset.iloc[::-1].copy(), load_config().split)


def test_split_ratios_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1.0"):
        SplitConfig(train=0.7, validation=0.2, test=0.2)


def test_no_transaction_appears_in_two_folds(ml_dataset: pd.DataFrame) -> None:
    split = split_chronologically(ml_dataset, load_config().split)
    ids = [
        set(fold.frame["transaction_id"]) for fold in (split.train, split.validation, split.test)
    ]
    assert not ids[0] & ids[1]
    assert not ids[1] & ids[2]
    assert not ids[0] & ids[2]


def test_split_summary_reports_real_ranges(ml_dataset: pd.DataFrame) -> None:
    split = split_chronologically(ml_dataset, load_config().split)
    summary = split.summary()
    assert summary["train"]["rows"] == split.train.rows
    assert summary["test"]["fraud_rows"] == split.test.positives
    assert summary["train"]["start"] <= summary["validation"]["start"]


def test_a_fold_without_fraud_is_rejected(ml_dataset: pd.DataFrame) -> None:
    """A fold with no positives cannot be scored, so the split must refuse it."""
    frame = ml_dataset.copy()
    frame[TARGET_COLUMN] = 0
    frame.iloc[:5, frame.columns.get_loc(TARGET_COLUMN)] = 1

    with pytest.raises(SplitError, match="no fraud examples"):
        split_chronologically(frame, load_config().split)


def test_an_empty_fold_is_rejected(ml_dataset: pd.DataFrame) -> None:
    tiny = replace(load_config().split, train=0.9999, validation=0.00005, test=0.00005)
    with pytest.raises(SplitError, match="empty"):
        split_chronologically(ml_dataset, tiny)
