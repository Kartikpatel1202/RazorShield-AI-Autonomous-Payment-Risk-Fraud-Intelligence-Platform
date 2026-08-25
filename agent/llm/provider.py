"""Builds the configured LLM provider.

Selection is entirely environment-driven, so switching providers never requires
a code change. The API key is read once here and handed to the provider; it is
never logged, never persisted, and never placed in an investigation record.
"""

from __future__ import annotations

import logging

from agent.config import AgentSettings, LLMProviderName, get_agent_settings
from agent.llm.base import LLMProvider, LLMUnavailableError
from agent.llm.mock import MockBehaviour, MockLLMProvider

logger = logging.getLogger(__name__)


def build_provider(settings: AgentSettings | None = None) -> LLMProvider:
    """Construct the provider named by ``LLM_PROVIDER``."""
    settings = settings or get_agent_settings()

    match settings.provider:
        case LLMProviderName.MOCK:
            logger.warning(
                "Using the deterministic MOCK LLM provider - investigations will be "
                "marked is_mock=true and are not backed by a real model"
            )
            # Deliberately ignores LLM_MODEL: the mock is not running that
            # model, and a trace claiming it was would be misleading.
            return MockLLMProvider(behaviour=MockBehaviour(settings.mock_behaviour))

        case LLMProviderName.ANTHROPIC:
            from agent.llm.anthropic_provider import AnthropicProvider

            return AnthropicProvider(
                api_key=settings.api_key,
                model=settings.model,
                timeout_seconds=settings.timeout_seconds,
            )

        case LLMProviderName.OPENAI_COMPATIBLE:
            from agent.llm.openai_compatible import OpenAICompatibleProvider

            if not settings.api_key:
                raise LLMUnavailableError(
                    "LLM_PROVIDER=openai_compatible requires LLM_API_KEY to be set"
                )
            return OpenAICompatibleProvider(
                api_key=settings.api_key,
                model=settings.model,
                base_url=settings.base_url,
                timeout_seconds=settings.timeout_seconds,
            )

    raise LLMUnavailableError(f"unknown LLM provider {settings.provider!r}")
