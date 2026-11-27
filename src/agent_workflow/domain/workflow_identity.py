"""Workflow repository identity and SHA validation."""

import re

from agent_workflow.domain.exceptions import BusinessRuleError

REPOSITORY = "builacviet/agent-workflow"


def validate_sha(sha: str) -> None:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise BusinessRuleError("a full lowercase 40-character commit SHA is required")
