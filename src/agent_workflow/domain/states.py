"""Canonical fixed status constants for workflow, dispatch and trusted CI.

Values match existing SQLite TEXT and MCP wire representations exactly.
These are internal state names, not extensible role/provider/plugin IDs.

This module intentionally does not declare workflow transitions: ESCALATED
hands control to the human Owner, and the complete Human-in-the-Loop decision
contract has not yet been implemented. In particular, an automatic third code
review is forbidden. Application use cases must enforce trusted CI evidence,
exact SHA, review limits, approval and persistence atomicity.
"""

from __future__ import annotations

from enum import StrEnum


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
