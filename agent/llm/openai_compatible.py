"""OpenAI-compatible chat-completions provider.

Talks to any endpoint implementing ``POST /chat/completions`` with JSON-schema
response formatting - OpenAI itself, Azure OpenAI, OpenRouter, Together, vLLM,
llama.cpp and friends. Raw HTTP rather than a vendor SDK, because "compatible"
here means the wire format, not any one company's client library.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from agent.llm.base import (
    LLMInvalidOutputError,
    LLMProvider,
    LLMRateLimitedError,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMUsage,
    SchemaT,
    StructuredResult,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAICompatibleProvider(LLMProvider):
    """Structured completions over the OpenAI chat-completions wire format."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailableError(
                "the OpenAI-compatible provider requires LLM_API_KEY to be set"
            )
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._api_key = api_key
        # Injectable so tests can supply a stub transport without patching a
        # global, and so a caller can pass a proxy-configured client.
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=timeout_seconds))

    def _headers(self) -> dict[str, str]:
        # Built per request and never logged: the key must not reach a trace.
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _json_schema(schema: type[SchemaT]) -> dict[str, Any]:
        """Strict JSON schema for the response_format field."""
        payload = schema.model_json_schema()
        payload["additionalProperties"] = False
        return {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": False, "schema": payload},
        }

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        purpose: str,
        max_tokens: int = 4096,
    ) -> StructuredResult[SchemaT]:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": self._json_schema(schema),
        }

        started = time.perf_counter()
        try:
            with self._client_factory() as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"the LLM timed out during {purpose}") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"the LLM could not be reached during {purpose}") from exc

        if response.status_code == 429:
            raise LLMRateLimitedError(f"the LLM rate limited the request during {purpose}")
        if response.status_code >= 400:
            # The body can echo request content, so only the status is surfaced.
            raise LLMUnavailableError(
                f"the LLM returned status {response.status_code} during {purpose}"
            )

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMInvalidOutputError(
                f"the LLM returned an unreadable envelope during {purpose}"
            ) from exc

        if not content:
            raise LLMInvalidOutputError(f"the LLM returned an empty response during {purpose}")

        try:
            value = schema.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMInvalidOutputError(
                f"the LLM output failed {schema.__name__} validation during {purpose}"
            ) from exc

        usage = payload.get("usage") or {}
        return StructuredResult(
            value=value,
            usage=LLMUsage(
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
            ),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
