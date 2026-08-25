"""Point-in-time feature engineering for the RazorShield risk engine.

Two rules hold everywhere in this package:

1. A feature may only use information available strictly before the transaction
   being scored, in the total order ``(transaction_timestamp, id)``.
2. ``is_fraud`` is never an input. It is carried alongside the features as the
   training label and nothing else.

See ``docs/ml-methodology.md`` for how those rules are enforced and tested.
"""

from ml.features.builder import build_features
from ml.features.history import (
    CustomerHistory,
    CustomerProfile,
    DeviceProfile,
    EntityHistory,
    HistoryWindow,
    IpProfile,
    TransactionView,
)

__all__ = [
    "CustomerHistory",
    "CustomerProfile",
    "DeviceProfile",
    "EntityHistory",
    "HistoryWindow",
    "IpProfile",
    "TransactionView",
    "build_features",
]
