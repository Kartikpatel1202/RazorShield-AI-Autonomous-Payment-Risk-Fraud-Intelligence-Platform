"""The fixed set of tools the agent may run.

The registry is a closed mapping from :class:`ToolName` to a callable. The model
selects a name; the application looks it up here and calls it with a context it
built itself. There is no path by which a name outside this mapping becomes a
call, and no path by which model output becomes a tool argument.
"""

from __future__ import annotations

from collections.abc import Callable

from agent.schemas.investigation import ToolName
from agent.tools.base import ToolContext, ToolResult
from agent.tools.behaviour_tools import get_location_history, get_velocity
from agent.tools.entity_tools import (
    get_customer_history,
    get_device_history,
    get_ip_history,
    get_transaction_context,
)
from agent.tools.model_tools import get_anomaly_result, get_ml_prediction

ToolCallable = Callable[[ToolContext], ToolResult]

TOOL_REGISTRY: dict[ToolName, ToolCallable] = {
    ToolName.GET_TRANSACTION_CONTEXT: get_transaction_context,
    ToolName.GET_CUSTOMER_HISTORY: get_customer_history,
    ToolName.GET_DEVICE_HISTORY: get_device_history,
    ToolName.GET_IP_HISTORY: get_ip_history,
    ToolName.GET_VELOCITY: get_velocity,
    ToolName.GET_LOCATION_HISTORY: get_location_history,
    ToolName.GET_ML_PREDICTION: get_ml_prediction,
    ToolName.GET_ANOMALY_RESULT: get_anomaly_result,
}

TOOL_DESCRIPTIONS: dict[ToolName, str] = {
    ToolName.GET_TRANSACTION_CONTEXT: (
        "The transaction itself plus its merchant, customer, device, IP and location."
    ),
    ToolName.GET_CUSTOMER_HISTORY: (
        "What this customer had done before the payment: volume, spend baseline, "
        "failure rate, recent transactions, devices and places used."
    ),
    ToolName.GET_DEVICE_HISTORY: (
        "Prior activity of the paying device: age, how many distinct customers have "
        "used it, recent volume and failures."
    ),
    ToolName.GET_IP_HISTORY: (
        "Prior activity of the originating IP: reputation, proxy flag, how many "
        "distinct customers have used it, recent volume and failures."
    ),
    ToolName.GET_VELOCITY: (
        "Transaction and failure counts for this customer over 5m/1h/24h/7d windows."
    ),
    ToolName.GET_LOCATION_HISTORY: (
        "Where this customer paid from before, and how unusual the current location is."
    ),
    ToolName.GET_ML_PREDICTION: ("The supervised fraud model's probability for this transaction."),
    ToolName.GET_ANOMALY_RESULT: (
        "The unsupervised behavioural anomaly score for this transaction."
    ),
}


def resolve(name: ToolName) -> ToolCallable:
    """Look up a tool. Raises ``KeyError`` for anything not registered."""
    return TOOL_REGISTRY[name]


def catalogue() -> str:
    """The tool list as it appears in the prompt."""
    return "\n".join(f"- {name}: {TOOL_DESCRIPTIONS[name]}" for name in TOOL_REGISTRY)
