"""Claude provider, using the official Anthropic SDK.

Structured output goes through ``client.messages.parse``, which validates the
response against a Pydantic schema server-side and hands back a typed instance -
so a malformed answer surfaces as a clean error rather than as a half-parsed
dict the agent has to guess about.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anthropic
from pydantic import ValidationError

from agent.llm.base import (
    LLMInvalidOutputError,
    LLMProvider,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMUsage,
    SchemaT,
    StructuredResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"

#: Models that accept the server-side refusal fallback beta. A safety refusal
#: arrives as HTTP 200 with ``stop_reason="refusal"``; the fallback routes it to
#: another model rather than failing the investigation outright.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset({"claude-opus-5", "claude-fable-5"})


class AnthropicProvider(LLMProvider):
    """Claude via the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self._timeout = timeout_seconds
        # The SDK resolves ANTHROPIC_API_KEY / an `ant auth login` profile when
        # no key is passed, so an unset LLM_API_KEY is not automatically fatal.
        self._client = anthropic.Anthropic(
            api_key=api_key or None,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def _request_kwargs(self) -> dict[str, Any]:
        """Extra parameters that only apply to some models."""
        if self.model in FALLBACK_MODELS:
            return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        return {}

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        purpose: str,
        max_tokens: int = 4096,
    ) -> StructuredResult[SchemaT]:
        started = time.perf_counter()
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                **self._request_kwargs(),
            )
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(f"Claude timed out during {purpose}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitedError(f"Claude rate limited during {purpose}") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailableError("Claude rejected the configured credentials") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("Claude could not be reached") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailableError(
                f"Claude returned status {exc.status_code} during {purpose}"
            ) from exc

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMRefusalError(f"Claude declined to answer during {purpose}")

        parsed = getattr(response, "parsed_output", None)
        if parsed is None or not isinstance(parsed, schema):
            raise LLMInvalidOutputError(
                f"Claude returned no valid {schema.__name__} during {purpose}"
            )

        try:
            value = schema.model_validate(parsed.model_dump())
        except ValidationError as exc:
            raise LLMInvalidOutputError(
                f"Claude output failed {schema.__name__} validation during {purpose}"
            ) from exc

        usage = getattr(response, "usage", None)
        return StructuredResult(
            value=value,
            usage=LLMUsage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            ),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
