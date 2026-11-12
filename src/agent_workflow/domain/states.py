"""Canonical state values shared by workflow, dispatch and trusted CI.

Existing SQLite and MCP representations remain plain strings. These enums are
constants for closed internal state machines, not provider/role plugin IDs.

A transition listed here is *structurally* possible, not an authorization.
The use case and trusted persistence adapter must enforce CI evidence, SHA,
review-round, repair-budget and concurrency requirements before changing state.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class WorkflowStatus(StrEnum):
    AWAITING_HANDOFF = "AWAITING_HANDOFF"
    READY_IN_REVIEW = "READY_IN_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    ESCALATED = "ESCALATED"


class DispatchStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class CiVerdict(StrEnum):
    BLOCKED = "BLOCKED"
    FAILURE = "FAILURE"
    SUCCESS = "SUCCESS"


# Mirrors existing workflow transitions. READY_IN_REVIEW -> READY_IN_REVIEW
# is reserved for an authenticated, trusted CI-failure repair; it must never
# bypass the independent exact-SHA CI and repair-budget guards.
WORKFLOW_TRANSITIONS: Mapping[
    WorkflowStatus, frozenset[WorkflowStatus]
] = MappingProxyType({
    WorkflowStatus.AWAITING_HANDOFF: frozenset({
        WorkflowStatus.READY_IN_REVIEW,
    }),
    WorkflowStatus.READY_IN_REVIEW: frozenset({
        WorkflowStatus.IN_REVIEW,
        WorkflowStatus.READY_IN_REVIEW,
    }),
    WorkflowStatus.IN_REVIEW: frozenset({
        WorkflowStatus.READY_IN_REVIEW,
        WorkflowStatus.CHANGES_REQUESTED,
        WorkflowStatus.APPROVED,
        WorkflowStatus.ESCALATED,
    }),
    WorkflowStatus.CHANGES_REQUESTED: frozenset({
        WorkflowStatus.READY_IN_REVIEW,
    }),
    WorkflowStatus.APPROVED: frozenset(),
    WorkflowStatus.ESCALATED: frozenset(),
})


def permits_workflow_transition(
    current: WorkflowStatus, following: WorkflowStatus
) -> bool:
    """Check the state graph only; callers must separately authorize actions."""
    if not isinstance(current, WorkflowStatus) or not isinstance(
        following, WorkflowStatus
    ):
        raise TypeError("workflow transitions require WorkflowStatus values")
    return following in WORKFLOW_TRANSITIONS[current]
