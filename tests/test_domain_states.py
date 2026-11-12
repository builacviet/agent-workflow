"""Contract tests: new enums must not change existing DB/MCP wire values."""

import json
import sqlite3

import pytest

from agent_workflow.domain.states import (
    CiVerdict,
    DispatchStatus,
    WORKFLOW_TRANSITIONS,
    WorkflowStatus,
    permits_workflow_transition,
)


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    [
        (
            WorkflowStatus,
            {
                "AWAITING_HANDOFF", "READY_IN_REVIEW", "IN_REVIEW",
                "CHANGES_REQUESTED", "APPROVED", "ESCALATED",
            },
        ),
        (
            DispatchStatus,
            {"ACTIVE", "BLOCKED", "DONE", "ACKNOWLEDGED"},
        ),
        (CiVerdict, {"BLOCKED", "FAILURE", "SUCCESS"}),
    ],
)
def test_states_preserve_exact_string_contract(enum_type, expected):
    assert {member.value for member in enum_type} == expected
    for member in enum_type:
        assert isinstance(member, str)
        assert member == member.value
        assert enum_type(member.value) is member
        assert json.loads(json.dumps({"status": member})) == {
            "status": member.value
        }
    with pytest.raises(ValueError):
        enum_type("UNRECOGNIZED")


def test_sqlite_binds_enum_as_existing_text_value():
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE states(status TEXT NOT NULL)")
        for member in (WorkflowStatus.READY_IN_REVIEW, CiVerdict.BLOCKED):
            db.execute("INSERT INTO states VALUES(?)", (member,))
        rows = db.execute("SELECT status FROM states ORDER BY rowid").fetchall()
    assert rows == [("READY_IN_REVIEW",), ("BLOCKED",)]


def test_workflow_transition_contract_is_closed_and_immutable():
    assert set(WORKFLOW_TRANSITIONS) == set(WorkflowStatus)
    assert all(isinstance(next_states, frozenset)
               for next_states in WORKFLOW_TRANSITIONS.values())
    assert permits_workflow_transition(
        WorkflowStatus.AWAITING_HANDOFF, WorkflowStatus.READY_IN_REVIEW
    )
    assert permits_workflow_transition(
        WorkflowStatus.READY_IN_REVIEW, WorkflowStatus.IN_REVIEW
    )
    assert permits_workflow_transition(
        WorkflowStatus.IN_REVIEW, WorkflowStatus.READY_IN_REVIEW
    )
    assert permits_workflow_transition(
        WorkflowStatus.IN_REVIEW, WorkflowStatus.CHANGES_REQUESTED
    )
    assert permits_workflow_transition(
        WorkflowStatus.CHANGES_REQUESTED, WorkflowStatus.READY_IN_REVIEW
    )
    for outcome in (WorkflowStatus.APPROVED, WorkflowStatus.ESCALATED):
        assert permits_workflow_transition(WorkflowStatus.IN_REVIEW, outcome)
        assert not WORKFLOW_TRANSITIONS[outcome]
    # A same-state repair is only structurally possible. Trusted CI-failure
    # evidence and the two-repair budget must still be checked by R1-02A.
    assert permits_workflow_transition(
        WorkflowStatus.READY_IN_REVIEW, WorkflowStatus.READY_IN_REVIEW
    )
    assert not permits_workflow_transition(
        WorkflowStatus.AWAITING_HANDOFF, WorkflowStatus.APPROVED
    )
    with pytest.raises(TypeError):
        WORKFLOW_TRANSITIONS[WorkflowStatus.APPROVED] = frozenset()


@pytest.mark.parametrize(
    ("current", "following"),
    [
        ("AWAITING_HANDOFF", WorkflowStatus.READY_IN_REVIEW),
        (WorkflowStatus.AWAITING_HANDOFF, "READY_IN_REVIEW"),
        (None, WorkflowStatus.APPROVED),
    ],
)
def test_transition_helper_rejects_raw_or_untrusted_values(current, following):
    with pytest.raises(TypeError):
        permits_workflow_transition(current, following)
