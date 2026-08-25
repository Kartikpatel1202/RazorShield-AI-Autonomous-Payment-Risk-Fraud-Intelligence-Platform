"""Synthetic identifier generation.

Every identifier is fabricated:

* e-mail domains use the IANA-reserved ``example.*`` names;
* IP addresses come from ranges reserved for documentation (RFC 5737) and
  benchmarking (RFC 2544), so a generated address can never route to a real
  host.
"""

from __future__ import annotations

import random

FIRST_NAMES: tuple[str, ...] = (
    "Aarav",
    "Ananya",
    "Rohan",
    "Ishita",
    "Vikram",
    "Meera",
    "Kabir",
    "Priya",
    "Arjun",
    "Sneha",
    "Rahul",
    "Divya",
    "Aditya",
    "Kavya",
    "Nikhil",
    "Tara",
    "Siddharth",
    "Neha",
    "Manav",
    "Riya",
    "Karan",
    "Pooja",
    "Devansh",
    "Anika",
)

LAST_NAMES: tuple[str, ...] = (
    "Sharma",
    "Iyer",
    "Reddy",
    "Nair",
    "Patel",
    "Banerjee",
    "Kulkarni",
    "Mehta",
    "Gupta",
    "Chauhan",
    "Desai",
    "Menon",
    "Joshi",
    "Rao",
    "Bhat",
    "Kapoor",
)

EMAIL_DOMAINS: tuple[str, ...] = ("example.com", "example.net", "example.org")

# Documentation and benchmarking ranges - never routable to a real host.
_DOC_PREFIXES: tuple[str, ...] = ("192.0.2", "198.51.100", "203.0.113")


def full_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def email_for(name: str, unique_suffix: int, rng: random.Random) -> str:
    """Deterministic synthetic address; the suffix guarantees uniqueness."""
    local = name.lower().replace(" ", ".")
    return f"{local}.{unique_suffix}@{rng.choice(EMAIL_DOMAINS)}"


def device_fingerprint(index: int) -> str:
    return f"dev_{index:06d}"


def ip_address(index: int) -> str:
    """Map a dense index onto a reserved address, without collisions."""
    if index < len(_DOC_PREFIXES) * 254:
        prefix = _DOC_PREFIXES[index // 254]
        return f"{prefix}.{index % 254 + 1}"
    # 198.18.0.0/15 (RFC 2544) provides the remaining headroom.
    offset = index - len(_DOC_PREFIXES) * 254
    return f"198.18.{offset // 254 % 512}.{offset % 254 + 1}"


def transaction_reference(sequence: int) -> str:
    return f"txn_{sequence:08d}"
