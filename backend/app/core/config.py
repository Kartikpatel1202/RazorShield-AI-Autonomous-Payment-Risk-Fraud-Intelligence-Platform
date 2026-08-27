"""Application configuration.

All configuration is sourced from environment variables (optionally loaded from a
local ``.env`` file). No secret is ever hardcoded in this module.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "development", "staging", "production"]

#: This module's own default. Refused outside local development so a deployment
#: cannot accidentally sign tokens with a value published in the source tree.
PLACEHOLDER_JWT_SECRET = "change-me-in-local-development-only"

#: Every signing key that is public knowledge and must therefore never be used
#: anywhere real. The second entry is the one printed in ``.env.example``: it is
#: long enough to pass the length check below, which is exactly why it needs to
#: be named here - a length rule alone would wave through anyone who copied the
#: template and deployed it. ``test_security_secrets`` keeps this set in step
#: with what the template actually says.
WEAK_JWT_SECRETS: frozenset[str] = frozenset(
    {
        PLACEHOLDER_JWT_SECRET,
        "replace-with-a-long-random-string",
        "change-me",
        "changeme",
        "secret",
    }
)

#: Below this a brute-force search of the signing key is cheap.
MINIMUM_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Service identity -------------------------------------------------
    service_name: str = "razorshield-backend"
    environment: Environment = "local"
    log_level: str = "INFO"

    # --- Database ---------------------------------------------------------
    # SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/dbname
    database_url: str = "postgresql+psycopg://razorshield:razorshield@localhost:5432/razorshield"
    database_echo: bool = False
    database_pool_size: int = 5
    database_max_overflow: int = 10
    #: Seconds to wait for a free pooled connection before failing the request.
    #: Without this a saturated pool blocks the worker indefinitely.
    database_pool_timeout: int = 10
    #: Recycle connections older than this (seconds) so a proxy or database
    #: restart cannot hand us a socket the server has already forgotten.
    database_pool_recycle: int = 1_800
    #: PostgreSQL ``statement_timeout`` in milliseconds. Ignored by SQLite,
    #: which has no equivalent. 0 disables it.
    database_statement_timeout_ms: int = 15_000

    # --- Authentication ---------------------------------------------------
    jwt_secret: str = PLACEHOLDER_JWT_SECRET
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "razorshield-ai"
    jwt_audience: str = "razorshield-console"
    access_token_ttl_minutes: int = 60

    # --- Rate limiting ----------------------------------------------------
    # Fixed-window counters held in this process. See `app.core.ratelimit` for
    # the multi-worker caveat.
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 10
    #: Signup is the one public write endpoint, so it is capped tightly: it is
    #: both the account-creation flood surface and, because a duplicate address
    #: is reported, the only enumeration surface in the auth flow.
    rate_limit_signup_per_minute: int = 5
    #: Reset requests. Low, because each one invalidates the account's previous
    #: token - an unthrottled endpoint would let anyone keep a user's inbox full
    #: and their links perpetually stale.
    rate_limit_password_reset_per_minute: int = 5
    rate_limit_ingest_per_minute: int = 600
    rate_limit_simulator_per_minute: int = 30
    rate_limit_feedback_per_minute: int = 60
    rate_limit_review_per_minute: int = 60

    #: Return the password-reset link in the API response instead of emailing
    #: it. There is no SMTP integration in this project, so without this the
    #: reset flow cannot be exercised at all locally.
    #:
    #: Refused outright in production by the validator below - not merely
    #: defaulted off. A flag that hands a password-reset capability to whoever
    #: asked for it is not something to leave one environment variable away from
    #: being on in the deployment that matters.
    auth_expose_dev_reset_token: bool = True
    #: Origin the reset link points at. The console, not the API.
    frontend_base_url: str = "http://localhost:3000"

    # --- HTTP security ----------------------------------------------------
    #: Emit ``Strict-Transport-Security``. Off by default because the compose
    #: stack serves plain HTTP, and HSTS on an http:// origin is both ignored
    #: and, if it ever were honoured, a foot-gun.
    hsts_enabled: bool = False
    hsts_max_age_seconds: int = 31_536_000
    #: Maximum accepted request body, in bytes. Larger requests are refused with
    #: 413 before the body is read into memory.
    max_request_bytes: int = 1_048_576

    # --- LLM / AI agent (wired up in a later phase) -----------------------
    llm_api_key: str | None = None
    llm_model: str = "claude-sonnet-5"

    # --- HTTP -------------------------------------------------------------
    # Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:5173"
    api_prefix: str = "/api"

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """Treat an empty ``LLM_API_KEY=`` entry as "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _reject_weak_secret_outside_local(self) -> Settings:
        """A deployed environment may not run on the example signing key.

        Checked here rather than at first use so the process fails at startup -
        loudly, before it can mint a single token an attacker could forge.
        """
        # Order matters. A deployment running on a published signing key is a
        # worse problem than one exposing the dev reset link, and a validator
        # reports only its first failure - so the signing key is checked first
        # and the operator is told about that one.
        if self.environment == "local":
            return self
        if self.jwt_secret in WEAK_JWT_SECRETS:
            raise ValueError(
                f"JWT_SECRET is still a placeholder value in environment "
                f"'{self.environment}'. Generate one with "
                f'`python -c "import secrets; print(secrets.token_urlsafe(48))"`.'
            )
        if len(self.jwt_secret) < MINIMUM_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be at least {MINIMUM_JWT_SECRET_LENGTH} characters "
                f"in environment '{self.environment}'."
            )
        if self.auth_expose_dev_reset_token and self.environment == "production":
            raise ValueError(
                "AUTH_EXPOSE_DEV_RESET_TOKEN must be false in production: it returns a "
                "password-reset capability to anyone who asks for one."
            )
        return self

    @property
    def dev_reset_token_enabled(self) -> bool:
        """Whether the reset endpoint may return the link in its response.

        The validator above already refuses to construct a production Settings
        with the flag on, so this can only ever be redundant. It is here anyway
        because the cost is one comparison and the failure it guards against -
        a password-reset capability handed to an anonymous caller - is the worst
        one in this file.
        """
        return self.auth_expose_dev_reset_token and self.environment != "production"

    @property
    def cors_origin_list(self) -> list[str]:
        """``CORS_ORIGINS`` split into a list of trimmed, non-empty origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def docs_enabled(self) -> bool:
        """Interactive API docs are served everywhere except production.

        The schema is a map of the attack surface; there is no reason to publish
        it from the deployment that matters.
        """
        return self.environment != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
