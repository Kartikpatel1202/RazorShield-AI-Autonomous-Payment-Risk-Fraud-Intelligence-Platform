"""Location features, comparing this payment against where the customer has paid before.

All location data is the simulated country/city already stored on the rows. No
geolocation service is called anywhere in this project.
"""

from __future__ import annotations

from typing import Any

from ml.features.history import (
    CustomerHistory,
    CustomerProfile,
    IpProfile,
    TransactionView,
    safe_ratio,
)


def build(
    transaction: TransactionView,
    profile: CustomerProfile,
    history: CustomerHistory,
    ip_profile: IpProfile | None,
) -> dict[str, Any]:
    """Movement since the last payment, familiarity, and IP/transaction agreement."""
    country = transaction.country
    city = transaction.city

    country_seen = history.country_counts.get(country, 0)
    city_seen = history.city_counts.get(city, 0)

    # With no prior transaction there is nothing to have changed; 0 is the
    # honest answer rather than a guess.
    country_changed = int(history.last_country is not None and history.last_country != country)
    city_changed = int(history.last_city is not None and history.last_city != city)

    return {
        "transaction_country": country,
        "country_changed": country_changed,
        "city_changed": city_changed,
        "location_changed": int(bool(country_changed or city_changed)),
        "is_home_country": int(country == profile.home_country),
        "is_home_city": int(city == profile.home_city),
        "previous_country_count": len(history.country_counts),
        "previous_city_count": len(history.city_counts),
        "country_frequency": safe_ratio(country_seen, history.transaction_count),
        "city_frequency": safe_ratio(city_seen, history.transaction_count),
        "is_new_country_for_customer": int(history.has_history and country_seen == 0),
        "is_new_city_for_customer": int(history.has_history and city_seen == 0),
        # A payment claiming one country from an IP registered in another is
        # worth surfacing; both values are stored, neither is looked up.
        "ip_country_matches_transaction": int(
            ip_profile is not None and ip_profile.country == country
        ),
    }
