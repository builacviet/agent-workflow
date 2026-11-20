"""Workflow repository identity, errors and SHA validation."""

import re

from agent_workflow.domain.exceptions import DomainError

REPOSITORY = "builacviet/agent-workflow"
LEGACY_REPOSITORY = "builacviet/techlead-review-poc"


def canonical_repository(repository: str) -> str:
    """Treat the former slug as an alias for this repository."""
    if repository == LEGACY_REPOSITORY:
        return REPOSITORY
    return repository


class WorkflowError(DomainError):
    """Invalid workflow input/state."""


def validate_sha(sha: str) -> None:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise WorkflowError("a full lowercase 40-character commit SHA is required")
