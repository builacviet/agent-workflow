"""Validate the TaskSpec contract without any transport or persistence."""

from dataclasses import replace

import pytest

from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)
from agent_workflow.domain.workflow_identity import (
    LEGACY_REPOSITORY,
    REPOSITORY,
    WorkflowError,
)


@pytest.fixture
def spec() -> TaskAuthorizationSpec:
    return TaskAuthorizationSpec(
        task_id="R2-EXAMPLE",
        title="  Preserve original whitespace  ",
        description="Beschreibung",
        acceptance_criteria=("", "second"),
        definition_of_done="Observed and documented",
        repository=LEGACY_REPOSITORY,
        branch="codex/example",
        base_sha="a" * 40,
        requested_effort="high",
        risk_level="",
        context_paths=("docs/one.md", "AGENTS.md"),
    )


@pytest.mark.parametrize("effort", ["instant", "medium", "high"])
def test_canonical_alias_defaults_and_exact_spec_semantics(spec, effort):
    spec = replace(spec, requested_effort=effort)
    assert spec.repository == REPOSITORY
    assert spec.title == "  Preserve original whitespace  "
    assert spec.acceptance_criteria == ("", "second")
    assert spec.risk_level == ""
    assert spec.require_verified_effort is True
    assert spec.synthetic is False
    assert TaskAuthorization(spec, 10, 11).status is WorkflowStatus.AWAITING_HANDOFF


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task_id", None, "task_id must be a string"),
        ("task_id", "", "task_id must be nonempty"),
        ("task_id", "task\0id", "must not contain NUL"),
        ("branch", "\ud800", "valid UTF-8"),
        ("title", 12, "title must be a string"),
        ("description", "", "description must be nonempty"),
        ("definition_of_done", "", "definition_of_done must be nonempty"),
        ("branch", "", "branch must be nonempty"),
        ("repository", "other/repository", "unauthorized repository"),
        ("repository", " " + REPOSITORY, "unauthorized repository"),
        ("requested_effort", "low", "unsupported effort"),
        ("requested_effort", "high ", "unsupported effort"),
        ("base_sha", "A" * 40, "lowercase"),
        ("base_sha", "a" * 39, "40-character"),
        ("acceptance_criteria", (), "nonempty tuple of strings"),
        ("acceptance_criteria", (1,), "nonempty tuple of strings"),
        ("context_paths", ["AGENTS.md"], "nonempty tuple of strings"),
        ("context_paths", (), "nonempty tuple of strings"),
        ("context_paths", ("",), "repository-relative"),
        ("context_paths", ("/AGENTS.md",), "repository-relative"),
        ("context_paths", ("\\AGENTS.md",), "repository-relative"),
        ("context_paths", ("docs/../AGENTS.md",), "repository-relative"),
        ("context_paths", ("docs\\..\\AGENTS.md",), "repository-relative"),
        ("context_paths", ("C:AGENTS.md",), "repository-relative"),
        ("context_paths", ("docs/a\0.md",), "repository-relative"),
        ("require_verified_effort", 1, "must be booleans"),
        ("synthetic", "false", "must be booleans"),
    ],
)
def test_rejects_invalid_task_specs(spec, field, value, message):
    with pytest.raises(WorkflowError, match=message):
        replace(spec, **{field: value})
