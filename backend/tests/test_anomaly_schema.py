"""The behavioral feature contract."""

from __future__ import annotations

import pytest

from ml.anomaly.schema import (
    BEHAVIORAL_FEATURE_VERSION,
    BEHAVIORAL_FEATURES,
    CUSTOMER_RELATIVE_FEATURES,
    FEATURE_GROUPS,
    FORBIDDEN_BEHAVIORAL_FEATURES,
    LOG1P_FEATURES,
    BehavioralSchemaError,
    select,
    validate_row,
)
from ml.features.schema import CATEGORICAL_FEATURES, FEATURE_COLUMNS, TARGET_COLUMN


def test_behavioral_features_are_a_subset_of_the_phase_3_contract() -> None:
    """Phase 4 must reuse Phase 3's pipeline, not invent a second one."""
    assert set(BEHAVIORAL_FEATURES) <= set(FEATURE_COLUMNS)


def test_the_subset_is_strictly_smaller_than_the_full_contract() -> None:
    assert len(BEHAVIORAL_FEATURES) < len(FEATURE_COLUMNS)


def test_no_feature_is_listed_twice() -> None:
    assert len(BEHAVIORAL_FEATURES) == len(set(BEHAVIORAL_FEATURES))


def test_groups_partition_the_contract() -> None:
    from_groups = [name for group in FEATURE_GROUPS.values() for name in group]
    assert from_groups == list(BEHAVIORAL_FEATURES)


def test_the_label_is_not_a_behavioral_feature() -> None:
    assert TARGET_COLUMN not in BEHAVIORAL_FEATURES
    assert TARGET_COLUMN in FORBIDDEN_BEHAVIORAL_FEATURES


@pytest.mark.parametrize(
    "identifier",
    ["transaction_id", "customer_id", "device_id", "ip_address_id", "merchant_id"],
)
def test_no_identifier_is_a_behavioral_feature(identifier: str) -> None:
    assert identifier not in BEHAVIORAL_FEATURES
    assert identifier in FORBIDDEN_BEHAVIORAL_FEATURES


def test_the_transactions_own_outcome_is_not_a_behavioral_feature() -> None:
    assert "status" not in BEHAVIORAL_FEATURES


def test_categoricals_are_excluded() -> None:
    """One-hot columns would let the forest isolate rare categories, not behaviour."""
    assert not set(CATEGORICAL_FEATURES) & set(BEHAVIORAL_FEATURES)


def test_static_entity_attributes_are_excluded() -> None:
    """Reputation and account age describe an entity, not the behaviour scored."""
    for name in ("ip_reputation_score", "ip_is_proxy", "customer_account_age_days"):
        assert name not in BEHAVIORAL_FEATURES


def test_calendar_features_are_excluded() -> None:
    for name in ("hour_of_day", "day_of_week", "is_weekend", "is_night"):
        assert name not in BEHAVIORAL_FEATURES


def test_every_group_contributes_features() -> None:
    for name, group in FEATURE_GROUPS.items():
        assert group, f"group {name} is empty"


def test_log1p_targets_are_all_in_the_contract() -> None:
    assert LOG1P_FEATURES <= set(BEHAVIORAL_FEATURES)


def test_already_logged_features_are_not_logged_twice() -> None:
    assert "log_amount" not in LOG1P_FEATURES


def test_customer_relative_features_are_in_the_contract() -> None:
    assert set(CUSTOMER_RELATIVE_FEATURES) <= set(BEHAVIORAL_FEATURES)


# --- select / validate ------------------------------------------------------


def _full_row() -> dict[str, float]:
    return {name: 1.0 for name in FEATURE_COLUMNS}


def test_select_narrows_a_full_feature_row() -> None:
    narrowed = select(_full_row())
    assert set(narrowed) == set(BEHAVIORAL_FEATURES)


def test_select_rejects_an_incomplete_row() -> None:
    row = _full_row()
    del row[BEHAVIORAL_FEATURES[0]]
    with pytest.raises(BehavioralSchemaError, match="missing behavioral feature"):
        select(row)


def test_validate_accepts_a_narrowed_row() -> None:
    validate_row(select(_full_row()))


def test_validate_rejects_extra_columns() -> None:
    row = select(_full_row())
    row["surprise"] = 1.0
    with pytest.raises(BehavioralSchemaError, match="unexpected"):
        validate_row(row)


def test_validate_rejects_a_row_carrying_the_label() -> None:
    row = select(_full_row())
    row[TARGET_COLUMN] = 1
    with pytest.raises(BehavioralSchemaError):
        validate_row(row)


def test_feature_version_is_declared() -> None:
    assert BEHAVIORAL_FEATURE_VERSION
