"""Rebuild the RazorShield simulation dataset.

Run from the ``backend/`` directory:

    python scripts/seed_data.py
    python scripts/seed_data.py --transactions 5000 --seed 7
    python scripts/seed_data.py --reference-time 2026-08-01T12:00:00Z

The whole run happens inside one transaction: if validation fails, nothing is
committed and the existing dataset is left intact.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow `python scripts/seed_data.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.seed import SeedConfig, seed_database  # noqa: E402
from app.seed.runner import format_summary  # noqa: E402
from app.seed.validation import SeedValidationError  # noqa: E402

logger = logging.getLogger("seed")


def _parse_reference_time(raw: str) -> datetime:
    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return stamp.astimezone(UTC) if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    defaults = SeedConfig()
    parser.add_argument("--seed", type=int, default=defaults.random_seed, help="random seed")
    parser.add_argument("--merchants", type=int, default=defaults.merchants)
    parser.add_argument("--customers", type=int, default=defaults.customers)
    parser.add_argument("--ip-addresses", type=int, default=defaults.ip_addresses)
    parser.add_argument("--transactions", type=int, default=defaults.transactions)
    parser.add_argument("--history-days", type=int, default=defaults.history_days)
    parser.add_argument("--fraud-rate", type=float, default=defaults.fraud_rate)
    parser.add_argument(
        "--reference-time",
        type=_parse_reference_time,
        default=None,
        help="pin every timestamp to this instant for byte-identical reruns",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="required when ENVIRONMENT is production",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    if settings.environment == "production" and not args.force:
        logger.error(
            "Refusing to reseed: ENVIRONMENT is production. "
            "Re-run with --force if this is intended."
        )
        return 2

    overrides = {
        "random_seed": args.seed,
        "merchants": args.merchants,
        "customers": args.customers,
        "ip_addresses": args.ip_addresses,
        "transactions": args.transactions,
        "history_days": args.history_days,
        "fraud_rate": args.fraud_rate,
    }
    if args.reference_time is not None:
        overrides["reference_time"] = args.reference_time

    try:
        config = SeedConfig(**overrides)
    except ValueError as exc:
        logger.error("Invalid configuration: %s", exc)
        return 2

    session = SessionLocal()
    try:
        result = seed_database(session, config)
        session.commit()
    except SeedValidationError as exc:
        session.rollback()
        logger.error("%s", exc)
        return 1
    except Exception:
        session.rollback()
        logger.exception("Seeding failed; the database was left unchanged")
        return 1
    finally:
        session.close()

    print()
    print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
