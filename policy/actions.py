"""The four possible outcomes, and how they rank against each other."""

from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    """The only decisions the engine can produce.

    An enum rather than a string so an arbitrary value can never become a
    decision, whatever a configuration file or an upstream component says.
    """

    APPROVE = "APPROVE"
    STEP_UP = "STEP_UP"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


#: Most restrictive first. When several rules match, the most restrictive action
#: wins - but every rule still has to match on its own explicit conditions, so
#: precedence alone can never produce a block.
DEFAULT_PRECEDENCE: tuple[Action, ...] = (
    Action.BLOCK,
    Action.REVIEW,
    Action.STEP_UP,
    Action.APPROVE,
)


def rank(action: Action, precedence: tuple[Action, ...] = DEFAULT_PRECEDENCE) -> int:
    """Position in the precedence order. Lower is more restrictive."""
    return precedence.index(action)


def most_restrictive(
    actions: list[Action], precedence: tuple[Action, ...] = DEFAULT_PRECEDENCE
) -> Action:
    """The winning action from a set of matched rules."""
    if not actions:
        raise ValueError("cannot choose an action from an empty set")
    return min(actions, key=lambda action: rank(action, precedence))
