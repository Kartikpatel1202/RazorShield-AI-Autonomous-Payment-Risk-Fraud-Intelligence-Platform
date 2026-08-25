"""Agent configuration, sourced entirely from the environment.

No key, endpoint or model is hardcoded. ``AgentSettings`` never exposes the API
key in its repr, and nothing in the agent writes it to a log or a trace.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderName(StrEnum):
    """Providers the agent can be pointed at."""

    MOCK = "mock"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class AgentSettings(BaseSettings):
    """Everything that determines how an investigation runs."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    #: Defaults to the mock so a fresh checkout runs end to end without a key,
    #: and so a missing key can never silently become a real billed call.
    llm_provider: LLMProviderName = LLMProviderName.MOCK
    llm_model: str = "claude-opus-5"
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)

    #: Hard cap on tool iterations. The agent always terminates.
    agent_max_iterations: int = Field(default=8, ge=1, le=25)
    #: Behaviour of the mock provider, for tests and demos.
    agent_mock_behaviour: str = "normal"

    @property
    def provider(self) -> LLMProviderName:
        return self.llm_provider

    @property
    def model(self) -> str:
        return self.llm_model

    @property
    def api_key(self) -> str | None:
        """The raw key, read only at provider construction."""
        return self.llm_api_key.get_secret_value() if self.llm_api_key else None

    @property
    def base_url(self) -> str:
        return self.llm_base_url

    @property
    def timeout_seconds(self) -> float:
        return self.llm_timeout_seconds

    @property
    def max_iterations(self) -> int:
        return self.agent_max_iterations

    @property
    def mock_behaviour(self) -> str:
        return self.agent_mock_behaviour


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    """Process-wide agent settings."""
    return AgentSettings()
