"""A small, fixed catalogue of locations.

No geolocation service is called anywhere. Cities are hardcoded so the dataset
stays reproducible and offline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    country: str
    city: str


DOMESTIC_LOCATIONS: tuple[Location, ...] = (
    Location("IN", "Mumbai"),
    Location("IN", "Delhi"),
    Location("IN", "Bengaluru"),
    Location("IN", "Hyderabad"),
    Location("IN", "Pune"),
    Location("IN", "Chennai"),
    Location("IN", "Kolkata"),
    Location("IN", "Ahmedabad"),
)

INTERNATIONAL_LOCATIONS: tuple[Location, ...] = (
    Location("SG", "Singapore"),
    Location("AE", "Dubai"),
    Location("GB", "London"),
    Location("US", "New York"),
    Location("DE", "Berlin"),
)

ALL_LOCATIONS: tuple[Location, ...] = DOMESTIC_LOCATIONS + INTERNATIONAL_LOCATIONS

# The schema supports multiple currencies. The dataset deliberately uses a
# single one: every payment settles in INR regardless of where the payer is, so
# `amount` stays directly comparable across the whole dataset. Later phases
# model spending deviation, which mixed currencies would distort.
DATASET_CURRENCY = "INR"

# Currencies accepted by the validation pass.
VALID_CURRENCIES: frozenset[str] = frozenset({"INR", "USD", "AED", "SGD", "GBP", "EUR"})
