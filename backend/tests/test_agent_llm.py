"""LLM provider abstraction, factory and error mapping.

The real providers are exercised against a stub transport rather than a live
endpoint - no API key is configured in this project, and a test that silently
needed one would be a test that silently stopped running.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from agent.config import AgentSettings, LLMProviderName
from agent.llm.base import (
    LLMInvalidOutputError,
    LLMProvider,
    LLMRateLimitedError,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMUsage,
)
from agent.llm.mock import MockBehaviour, MockLLMProvider
from agent.llm.openai_compatible import OpenAICompatibleProvider
from agent.llm.provider import build_provider
from agent.schemas.investigation import FinalReport, ToolDecision


class Sample(BaseModel):
    verdict: str
    score: int


# --- the abstraction --------------------------------------------------------


def test_every_provider_implements_the_interface() -> None:
    from agent.llm.anthropic_provider import AnthropicProvider

    for implementation in (MockLLMProvider, OpenAICompatibleProvider, AnthropicProvider):
        assert issubclass(implementation, LLMProvider)


def test_no_vendor_sdk_leaks_outside_the_llm_package() -> None:
    """The agent must depend on the abstraction, not on any one vendor."""
    import pathlib

    agent_root = pathlib.Path(__file__).resolve().parents[2] / "agent"
    offenders: list[str] = []

    for path in agent_root.rglob("*.py"):
        if path.parent.name == "llm":
            continue
        text = path.read_text(encoding="utf-8")
        if "import anthropic" in text or "import openai" in text:
            offenders.append(str(path.relative_to(agent_root)))

    assert offenders == []


def test_usage_accumulates() -> None:
    total = LLMUsage(10, 5) + LLMUsage(3, 2)
    assert total.input_tokens == 13
    assert total.output_tokens == 7


# --- the mock ---------------------------------------------------------------


def test_the_mock_declares_itself() -> None:
    provider = MockLLMProvider()
    assert provider.is_mock is True
    assert provider.name == "mock"
    assert "mock" in provider.model


def test_the_mock_ignores_the_configured_model_name() -> None:
    """A trace claiming a model the mock is not running would be misleading."""
    provider = build_provider(
        AgentSettings(llm_provider=LLMProviderName.MOCK, llm_model="claude-opus-5")
    )
    assert provider.model != "claude-opus-5"
    assert provider.is_mock is True


def test_the_mock_is_deterministic() -> None:
    prompt = "Evidence gathered so far:\nEV-001 [HIGH] (get_velocity) burst\n"
    first = MockLLMProvider().complete_structured(
        system="s", user=prompt, schema=ToolDecision, purpose="tool_selection"
    )
    second = MockLLMProvider().complete_structured(
        system="s", user=prompt, schema=ToolDecision, purpose="tool_selection"
    )
    assert first.value == second.value


def test_the_mock_reads_the_prompt_rather_than_returning_a_fixed_blob() -> None:
    empty = MockLLMProvider().complete_structured(
        system="s",
        user="Evidence gathered so far:\n(none)",
        schema=ToolDecision,
        purpose="tool_selection",
    )
    populated = MockLLMProvider().complete_structured(
        system="s",
        user=(
            "Tools already run:\n  - tool: get_transaction_context\n"
            "  - tool: get_ml_prediction\n  - tool: get_anomaly_result\n"
            "  - tool: get_velocity\n  - tool: get_device_history\n"
            "Evidence gathered so far:\n"
            + "\n".join(f"EV-{i:03d} [HIGH] (get_velocity) x" for i in range(1, 8))
        ),
        schema=ToolDecision,
        purpose="tool_selection",
    )
    assert empty.value.enough_evidence is False
    assert populated.value.enough_evidence is True


def test_the_mock_only_cites_evidence_it_was_shown() -> None:
    result = MockLLMProvider().complete_structured(
        system="s",
        user="Evidence gathered:\nEV-001 [HIGH] (get_velocity) burst\nEV-002 [MEDIUM] (x) y",
        schema=FinalReport,
        purpose="final_report",
    )
    cited = {item for finding in result.value.findings for item in finding.evidence_ids}
    assert cited <= {"EV-001", "EV-002"}


@pytest.mark.parametrize(
    ("behaviour", "expected"),
    [
        (MockBehaviour.TIMEOUT, LLMTimeoutError),
        (MockBehaviour.RATE_LIMIT, LLMRateLimitedError),
        (MockBehaviour.UNAVAILABLE, LLMUnavailableError),
        (MockBehaviour.MALFORMED, LLMInvalidOutputError),
        (MockBehaviour.EMPTY, LLMInvalidOutputError),
    ],
)
def test_the_mock_simulates_each_failure_mode(
    behaviour: MockBehaviour, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        MockLLMProvider(behaviour=behaviour).complete_structured(
            system="s", user="u", schema=ToolDecision, purpose="tool_selection"
        )


def test_the_mock_counts_its_calls() -> None:
    provider = MockLLMProvider()
    for _ in range(3):
        provider.complete_structured(
            system="s", user="u", schema=ToolDecision, purpose="tool_selection"
        )
    assert provider.calls == 3


# --- the factory ------------------------------------------------------------


def test_the_factory_defaults_to_the_mock() -> None:
    """A missing key must never silently become a real, billed call."""
    assert build_provider(AgentSettings()).is_mock is True


def test_the_factory_builds_the_openai_compatible_provider() -> None:
    provider = build_provider(
        AgentSettings(
            llm_provider=LLMProviderName.OPENAI_COMPATIBLE,
            llm_api_key="test-key",  # type: ignore[arg-type]
            llm_model="gpt-4o-mini",
        )
    )
    assert provider.name == "openai_compatible"
    assert provider.is_mock is False


def test_the_openai_provider_requires_a_key() -> None:
    with pytest.raises(LLMUnavailableError, match="LLM_API_KEY"):
        build_provider(AgentSettings(llm_provider=LLMProviderName.OPENAI_COMPATIBLE))


def test_settings_do_not_expose_the_key_in_their_repr() -> None:
    settings = AgentSettings(llm_api_key="super-secret-value")  # type: ignore[arg-type]
    assert "super-secret-value" not in repr(settings)
    assert settings.api_key == "super-secret-value"


def test_the_iteration_cap_is_configurable_and_bounded() -> None:
    assert AgentSettings(agent_max_iterations=3).max_iterations == 3
    with pytest.raises(ValueError):
        AgentSettings(agent_max_iterations=0)
    with pytest.raises(ValueError):
        AgentSettings(agent_max_iterations=999)


# --- the OpenAI-compatible provider -----------------------------------------


def _provider(handler: Any, *, api_key: str = "test-key") -> OpenAICompatibleProvider:
    """A provider whose HTTP calls are answered by ``handler``."""
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleProvider(
        api_key=api_key,
        model="test-model",
        client_factory=lambda: httpx.Client(transport=transport),
    )


def test_the_openai_provider_parses_a_valid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"verdict": "ok", "score": 7})}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            },
        )

    result = _provider(handler).complete_structured(
        system="s", user="u", schema=Sample, purpose="unit"
    )

    assert result.value.verdict == "ok"
    assert result.value.score == 7
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 3


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, LLMRateLimitedError), (500, LLMUnavailableError), (401, LLMUnavailableError)],
)
def test_the_openai_provider_maps_http_errors(status: int, expected: type[Exception]) -> None:
    provider = _provider(lambda request: httpx.Response(status, json={}))
    with pytest.raises(expected):
        provider.complete_structured(system="s", user="u", schema=Sample, purpose="unit")


def test_the_openai_provider_rejects_unparseable_output() -> None:
    provider = _provider(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "not json at all"}}]}
        )
    )
    with pytest.raises(LLMInvalidOutputError):
        provider.complete_structured(system="s", user="u", schema=Sample, purpose="unit")


def test_the_openai_provider_rejects_output_failing_the_schema() -> None:
    provider = _provider(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"verdict": "ok"})}}]},
        )
    )
    with pytest.raises(LLMInvalidOutputError):
        provider.complete_structured(system="s", user="u", schema=Sample, purpose="unit")


def test_the_openai_provider_rejects_an_empty_response() -> None:
    provider = _provider(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
    )
    with pytest.raises(LLMInvalidOutputError):
        provider.complete_structured(system="s", user="u", schema=Sample, purpose="unit")


def test_the_openai_provider_rejects_an_unreadable_envelope() -> None:
    provider = _provider(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMInvalidOutputError):
        provider.complete_structured(system="s", user="u", schema=Sample, purpose="unit")


def test_the_openai_provider_maps_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(LLMTimeoutError):
        _provider(handler).complete_structured(system="s", user="u", schema=Sample, purpose="unit")


def test_the_openai_provider_never_puts_the_key_in_an_error() -> None:
    provider = _provider(
        lambda request: httpx.Response(401, json={"error": "bad key sk-secret123"}),
        api_key="sk-secret123",
    )
    with pytest.raises(LLMUnavailableError) as error:
        provider.complete_structured(system="s", user="u", schema=Sample, purpose="unit")

    assert "sk-secret123" not in str(error.value)


# --- the Anthropic provider -------------------------------------------------


def test_the_anthropic_provider_maps_sdk_errors() -> None:
    """Error translation is verified without contacting the API."""
    import anthropic

    from agent.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="claude-opus-5")

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def raise_timeout(**_kwargs: Any) -> Any:
        raise anthropic.APITimeoutError(request=request)

    provider._client.messages.parse = raise_timeout  # type: ignore[method-assign]
    with pytest.raises(LLMTimeoutError):
        provider.complete_structured(system="s", user="u", schema=Sample, purpose="unit")


def test_the_anthropic_provider_requests_refusal_fallbacks_for_opus() -> None:
    from agent.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider

    opus = AnthropicProvider(api_key="k", model="claude-opus-5")
    assert opus._request_kwargs()["betas"] == [FALLBACK_BETA]

    other = AnthropicProvider(api_key="k", model="claude-haiku-4-5")
    assert other._request_kwargs() == {}
