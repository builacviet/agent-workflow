"""Contract tests: new enums must not change existing DB/MCP wire values."""

import json
import sqlite3

import pytest

from agent_workflow.domain.states import (
    CiVerdict,
    DispatchStatus,
    WorkflowStatus,
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


def test_escalated_is_a_valid_status_not_a_terminality_policy():
    # Owner/Human-in-the-Loop routing is an application workflow decision,
    # not a transition to silently infer from a fixed enum's presence.
    assert WorkflowStatus("ESCALATED") is WorkflowStatus.ESCALATED
    assert WorkflowStatus.ESCALATED.value == "ESCALATED"
    # Avoid reintroducing the incomplete auto-only state graph.
    from agent_workflow.domain import states
    assert not hasattr(states, "WORKFLOW_TRANSITIONS")
    assert not hasattr(states, "permits_workflow_transition")
