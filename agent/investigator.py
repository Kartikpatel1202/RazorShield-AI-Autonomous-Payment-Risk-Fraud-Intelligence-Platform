"""Top-level entry point for running an investigation."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from agent.config import get_agent_settings
from agent.graph.executor import InvestigationAgent
from agent.llm.base import LLMProvider
from agent.llm.provider import build_provider
from agent.schemas.investigation import Investigation
from app.models import Transaction

logger = logging.getLogger(__name__)


def build_agent(provider: LLMProvider | None = None) -> InvestigationAgent:
    """Construct an agent from configuration, or around a supplied provider."""
    settings = get_agent_settings()
    return InvestigationAgent(
        provider=provider or build_provider(settings),
        max_iterations=settings.max_iterations,
    )


def investigate_transaction(
    session: Session,
    transaction: Transaction,
    *,
    provider: LLMProvider | None = None,
    model_versions: dict[str, str] | None = None,
) -> Investigation:
    """Investigate one transaction and return the structured result."""
    return build_agent(provider).investigate(session, transaction, model_versions or {})
