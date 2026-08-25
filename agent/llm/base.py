"""The provider-neutral LLM interface.

The investigation agent depends on this module only. No vendor SDK is imported
outside ``agent/llm/``, so swapping providers is a configuration change rather
than a code change.

**The interface is deliberately narrow: structured JSON in, validated Pydantic
model out.** The agent never receives free text from the model and never grants
it execution power. The model returns a *decision document* - which tool to call
next, or what it concluded - and the application decides what to do with it.
That single design choice removes a whole class of prompt-injection outcomes:
there is no channel through which model output can become an action.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMError(RuntimeError):
    """Base class for every provider failure the agent must survive."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached, or is not configured."""


class LLMTimeoutError(LLMError):
    """The provider did not answer inside the configured timeout."""


class LLMRateLimitedError(LLMError):
    """The provider refused the request because of rate limits."""


class LLMInvalidOutputError(LLMError):
    """The provider answered, but not with output matching the requested schema."""


class LLMRefusalError(LLMError):
    """The provider declined to answer."""


@dataclass(frozen=True)
class LLMUsage:
    """Token accounting for one call. Never contains request content."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class LLMCallRecord:
    """One provider call, for the investigation trace.

    Records shape and cost, never prompt or completion text: the trace is
    persisted, and prompts can contain merchant and customer data.
    """

    provider: str
    model: str
    purpose: str
    latency_ms: float
    usage: LLMUsage = field(default_factory=LLMUsage)
    succeeded: bool = True
    error_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "purpose": self.purpose,
            "latency_ms": round(self.latency_ms, 2),
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "succeeded": self.succeeded,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class StructuredResult(Generic[SchemaT]):
    """A validated model response plus its accounting."""

    value: SchemaT
    usage: LLMUsage
    latency_ms: float


class LLMProvider(abc.ABC):
    """What the agent needs from a language model, and nothing more."""

    #: Stable identifier recorded in traces, e.g. ``anthropic`` or ``mock``.
    name: str

    #: Model identifier recorded in traces and in the investigation record.
    model: str

    @property
    def is_mock(self) -> bool:
        """True for test doubles.

        Surfaced in the investigation record so a mock-produced investigation
        can never be mistaken for one backed by a real model.
        """
        return False

    @abc.abstractmethod
    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        purpose: str,
        max_tokens: int = 4096,
    ) -> StructuredResult[SchemaT]:
        """Ask the model for a response matching ``schema``.

        Implementations must raise one of the :class:`LLMError` subclasses on
        any failure - never return a partially valid object, and never invent a
        fallback answer.
        """
