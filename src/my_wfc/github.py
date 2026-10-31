"""Trusted GitHub branch verification through the operator's existing gh sign-in."""
import json
import re
import subprocess
from urllib.parse import quote


class WorkflowError(ValueError):
    """Policy or operational rejection; no review round was consumed."""


def validate_sha(sha: str) -> None:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise WorkflowError("a full lowercase 40-character commit SHA is required")


class GitHubVerifier:
    def __init__(self, allowed_repositories: set[str]):
        self.allowed = allowed_repositories

    def _get(self, endpoint: str):
        try:
            result = subprocess.run(
                ["gh", "api", endpoint], capture_output=True, text=True,
                timeout=15, check=True,
            )
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            # Do not return credential-bearing subprocess output or headers.
            raise WorkflowError("GitHub verification unavailable") from None

    def __call__(self, repository: str, branch: str, base: str, head: str) -> None:
        if repository not in self.allowed:
            raise WorkflowError("repository is not authorized")
        validate_sha(base)
        validate_sha(head)
        branch_data = self._get(f"repos/{repository}/branches/{quote(branch, safe='')}")
        if branch_data.get("commit", {}).get("sha") != head:
            raise WorkflowError("remote branch head is stale or does not match")
        comparison = self._get(f"repos/{repository}/compare/{base}...{head}")
        if comparison.get("status") not in {"ahead", "identical"}:
            raise WorkflowError("base is not an ancestor of remote head")
