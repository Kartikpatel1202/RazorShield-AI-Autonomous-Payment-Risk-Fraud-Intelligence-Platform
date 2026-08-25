"""Score normalization, severity bands and customer-relative deviation."""

from __future__ import annotations

import numpy as np
import pytest

from ml.anomaly.explain import top_deviations
from ml.anomaly.scoring import (
    AnomalySeverity,
    CustomerRelativeNormalizer,
    PercentileNormalizer,
    SeverityBands,
)


def _normalizer(values: np.ndarray | None = None) -> PercentileNormalizer:
    raw = values if values is not None else np.linspace(-0.7, -0.35, 1000)
    return PercentileNormalizer.fit(raw)


# --- normalization ----------------------------------------------------------


def test_normalization_is_bounded() -> None:
    normalizer = _normalizer()
    scores = normalizer.to_anomaly_score(np.array([-10.0, -0.5, 10.0]))
    assert scores.min() >= 0.0
    assert scores.max() <= 100.0


def test_lower_raw_scores_are_more_anomalous() -> None:
    """Isolation Forest scores are higher for normal points; the mapping inverts."""
    normalizer = _normalizer()
    scores = normalizer.to_anomaly_score(np.array([-0.70, -0.50, -0.35]))
    assert scores[0] > scores[1] > scores[2]


def test_normalization_is_monotone() -> None:
    normalizer = _normalizer()
    raw = np.linspace(-0.8, -0.3, 50)
    scores = normalizer.to_anomaly_score(raw)
    assert np.all(np.diff(scores) <= 0)


def test_an_extreme_outlier_scores_at_the_top() -> None:
    normalizer = _normalizer()
    assert normalizer.to_anomaly_score(np.array([-5.0]))[0] == pytest.approx(100.0)


def test_the_most_normal_point_scores_at_the_bottom() -> None:
    normalizer = _normalizer()
    assert normalizer.to_anomaly_score(np.array([5.0]))[0] == pytest.approx(0.0)


def test_the_median_of_the_fitting_population_scores_near_fifty() -> None:
    """Documented behaviour: the score is a percentile, not a fraud probability."""
    raw = np.linspace(-0.7, -0.35, 1001)
    normalizer = PercentileNormalizer.fit(raw)
    median_score = normalizer.to_anomaly_score(np.array([float(np.median(raw))]))[0]
    assert 45.0 <= median_score <= 55.0


def test_normalizer_round_trips_through_the_artifact() -> None:
    normalizer = _normalizer()
    restored = PercentileNormalizer.from_dict(normalizer.as_dict())
    raw = np.array([-0.65, -0.5, -0.4])
    np.testing.assert_allclose(normalizer.to_anomaly_score(raw), restored.to_anomaly_score(raw))


def test_fitting_on_nothing_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty population"):
        PercentileNormalizer.fit(np.array([]))


# --- severity ---------------------------------------------------------------


def _bands() -> SeverityBands:
    return SeverityBands(
        medium=90.0, high=97.0, critical=99.0, source_percentiles=(90.0, 97.0, 99.0)
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, AnomalySeverity.LOW),
        (50.0, AnomalySeverity.LOW),
        (89.9, AnomalySeverity.LOW),
        (90.0, AnomalySeverity.MEDIUM),
        (96.9, AnomalySeverity.MEDIUM),
        (97.0, AnomalySeverity.HIGH),
        (98.9, AnomalySeverity.HIGH),
        (99.0, AnomalySeverity.CRITICAL),
        (100.0, AnomalySeverity.CRITICAL),
    ],
)
def test_severity_boundaries_are_inclusive_at_the_bottom(
    score: float, expected: AnomalySeverity
) -> None:
    assert _bands().classify(score) is expected


def test_severity_is_monotone() -> None:
    bands = _bands()
    order = {
        AnomalySeverity.LOW: 0,
        AnomalySeverity.MEDIUM: 1,
        AnomalySeverity.HIGH: 2,
        AnomalySeverity.CRITICAL: 3,
    }
    ranks = [order[bands.classify(score)] for score in np.linspace(0, 100, 200)]
    assert ranks == sorted(ranks)


def test_bands_are_derived_from_a_distribution_not_assumed() -> None:
    scores = np.linspace(0.0, 100.0, 1001)
    bands = SeverityBands.from_scores(scores, (90.0, 97.0, 99.0))
    assert bands.medium == pytest.approx(90.0, abs=0.2)
    assert bands.high == pytest.approx(97.0, abs=0.2)
    assert bands.critical == pytest.approx(99.0, abs=0.2)


def test_out_of_order_bands_are_rejected() -> None:
    with pytest.raises(ValueError, match="ascending"):
        SeverityBands(medium=99.0, high=90.0, critical=95.0, source_percentiles=(9, 9, 9))


def test_tied_percentiles_do_not_collapse_the_bands() -> None:
    """A heavily tied distribution must still yield ordered bands."""
    bands = SeverityBands.from_scores(np.full(1000, 50.0), (90.0, 97.0, 99.0))
    assert bands.medium <= bands.high <= bands.critical


def test_bands_round_trip_through_the_artifact() -> None:
    bands = _bands()
    restored = SeverityBands.from_dict(bands.as_dict())
    assert restored.medium == bands.medium
    assert restored.critical == bands.critical
    assert restored.classify(98.0) is bands.classify(98.0)


# --- customer-relative deviation --------------------------------------------


def test_the_modal_value_is_not_reported_as_a_deviation() -> None:
    """A value most of the population shares must score 0, not 100.

    These features are discrete and massed at zero - most customers make no
    payment in a given five minutes - so a rank that counted ties would report
    the most ordinary possible value as a 100% deviation.
    """
    population = np.zeros(1000)
    population[-5:] = [1, 2, 3, 4, 5]
    normalizer = CustomerRelativeNormalizer.fit({"transactions_last_5m": population})

    score, _ = normalizer.deviation({"transactions_last_5m": 0.0})
    assert score == pytest.approx(0.0)


def test_an_extreme_value_scores_high() -> None:
    population = np.zeros(1000)
    population[-5:] = [1, 2, 3, 4, 5]
    normalizer = CustomerRelativeNormalizer.fit({"transactions_last_5m": population})

    score, driver = normalizer.deviation({"transactions_last_5m": 99.0})
    assert score > 99.0
    assert driver == "transactions_last_5m"


def test_the_strongest_feature_drives_the_deviation() -> None:
    normalizer = CustomerRelativeNormalizer.fit(
        {
            "amount_vs_historical_average": np.linspace(0.0, 2.0, 1000),
            "transactions_last_1h": np.zeros(1000),
        }
    )
    score, driver = normalizer.deviation(
        {"amount_vs_historical_average": 1.9, "transactions_last_1h": 0.0}
    )
    assert driver == "amount_vs_historical_average"
    assert score > 90.0


def test_customer_relative_normalizer_round_trips() -> None:
    normalizer = CustomerRelativeNormalizer.fit(
        {"amount_vs_historical_average": np.linspace(0.0, 5.0, 500)}
    )
    restored = CustomerRelativeNormalizer.from_dict(normalizer.as_dict())
    features = {"amount_vs_historical_average": 4.0}
    assert restored.deviation(features) == normalizer.deviation(features)


# --- local explanation ------------------------------------------------------


def test_top_deviations_report_only_extreme_values() -> None:
    grids = {
        "velocity": list(np.linspace(0.0, 10.0, 101)),
        "ordinary": list(np.linspace(0.0, 10.0, 101)),
    }
    deviations = top_deviations(grids, {"velocity": 9.9, "ordinary": 1.0})

    names = [d.feature for d in deviations]
    assert "velocity" in names
    assert "ordinary" not in names


def test_top_deviations_are_ranked_by_percentile() -> None:
    grids = {name: list(np.linspace(0.0, 10.0, 101)) for name in ("a", "b", "c")}
    deviations = top_deviations(grids, {"a": 9.9, "b": 9.5, "c": 9.7})
    assert [d.feature for d in deviations] == ["a", "c", "b"]


def test_top_deviations_respect_the_limit() -> None:
    grids = {f"f{i}": list(np.linspace(0.0, 10.0, 101)) for i in range(20)}
    features = {f"f{i}": 9.9 for i in range(20)}
    assert len(top_deviations(grids, features, limit=5)) == 5


def test_top_deviations_ignore_features_without_a_grid() -> None:
    deviations = top_deviations({"known": list(np.linspace(0, 1, 101))}, {"unknown": 5.0})
    assert deviations == []
