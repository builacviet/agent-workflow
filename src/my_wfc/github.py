"""Local workflow errors and SHA syntax validation. No remote operations."""
import re


class WorkflowError(ValueError):
    """Invalid workflow input/state."""


def validate_sha(sha: str) -> None:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise WorkflowError("a full lowercase 40-character commit SHA is required")
