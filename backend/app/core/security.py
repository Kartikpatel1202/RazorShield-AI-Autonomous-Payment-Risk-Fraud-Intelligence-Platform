"""Password hashing and access-token minting.

Two deliberately boring primitives, both delegated to libraries that specialise
in them:

* **bcrypt** for passwords. Salted and adaptive; the cost factor is stored in
  the hash, so raising it later does not invalidate existing credentials.
* **PyJWT** for tokens. Signed HS256, with issuer and audience pinned so a token
  minted for something else cannot be replayed here, and with the algorithm
  fixed at verification time so ``alg: none`` and HS/RS confusion are not
  reachable.

Nothing in this module logs, and nothing returns a secret.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import Settings, get_settings

#: bcrypt hashes at most the first 72 bytes of a password and silently ignores
#: the rest. Truncating would mean two different passwords authenticating the
#: same account, so over-long passwords are rejected instead.
MAX_PASSWORD_BYTES = 72

#: Bcrypt work factor. 12 is roughly 250ms on commodity hardware in 2026 - slow
#: enough to make offline cracking expensive, fast enough for an interactive
#: login.
BCRYPT_ROUNDS = 12

#: A valid bcrypt hash of a password no one holds. Verified against when the
#: account does not exist, so a missing account and a wrong password take the
#: same time and cannot be told apart by a stopwatch.
_DUMMY_HASH = bcrypt.hashpw(b"razorshield-timing-equaliser", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


class PasswordTooLongError(ValueError):
    """The supplied password exceeds what bcrypt can hash without truncation."""


class TokenError(Exception):
    """An access token was absent, malformed, expired or not ours."""


def hash_password(password: str) -> str:
    """Return a bcrypt hash suitable for storing in ``users.password_hash``."""
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password exceeds {MAX_PASSWORD_BYTES} bytes, which bcrypt cannot hash intact."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check ``password`` against a stored hash, in constant-ish time.

    ``password_hash`` is ``None`` for every seeded account - the seed generator
    writes no credentials - and for any account whose password was never set.
    Those still pay the cost of a bcrypt comparison so that "no such user" and
    "wrong password" are not distinguishable by response time.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        # Refuse rather than truncate; a 200-byte guess must not be able to
        # authenticate an account whose password shares its first 72 bytes.
        bcrypt.checkpw(b"", _DUMMY_HASH)
        return False
    if not password_hash:
        bcrypt.checkpw(encoded, _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except ValueError:
        # A stored value that is not a bcrypt hash at all: treat as no
        # credential rather than crashing the login endpoint.
        return False


@dataclass(frozen=True)
class TokenClaims:
    """The parts of a verified token the application actually uses."""

    subject: int
    email: str
    role: str
    token_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_at: datetime


def create_access_token(
    *,
    user_id: int,
    email: str,
    role: str,
    settings: Settings | None = None,
    now: datetime | None = None,
    ttl: timedelta | None = None,
) -> IssuedToken:
    """Mint a signed access token for ``user_id``.

    ``now`` and ``ttl`` are injectable so the expiry tests can produce a genuinely
    expired token rather than sleeping through one.
    """
    settings = settings or get_settings()
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    lifetime = ttl if ttl is not None else timedelta(minutes=settings.access_token_ttl_minutes)
    expires_at = issued_at + lifetime

    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return IssuedToken(token=token, expires_at=expires_at)


def decode_access_token(token: str, settings: Settings | None = None) -> TokenClaims:
    """Verify a token and return its claims, or raise :class:`TokenError`.

    Every failure mode - bad signature, wrong algorithm, expired, wrong issuer,
    wrong audience, missing claim, not a JWT at all - collapses to one exception
    type carrying a short reason. The caller turns that into a 401 without
    echoing it, so a probe learns only that the token was not accepted.
    """
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            # A list of exactly one algorithm. PyJWT will not consider any
            # other, which is what closes off `alg: none` and HS/RS confusion.
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token invalid") from exc

    try:
        subject = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("token subject invalid") from exc

    email = payload.get("email")
    role = payload.get("role")
    if not isinstance(email, str) or not isinstance(role, str):
        raise TokenError("token claims incomplete")

    return TokenClaims(
        subject=subject,
        email=email,
        role=role,
        token_id=str(payload.get("jti", "")),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )


def bearer_token(header_value: str | None) -> str:
    """Pull the credential out of an ``Authorization: Bearer <token>`` header."""
    if not header_value:
        raise TokenError("missing credentials")
    scheme, _, credential = header_value.partition(" ")
    # Case-insensitive per RFC 7235, compared without leaking length via early
    # exit - the scheme is not secret, but the habit is cheap.
    if not hmac.compare_digest(scheme.lower(), "bearer") or not credential.strip():
        raise TokenError("unsupported authorization scheme")
    return credential.strip()
