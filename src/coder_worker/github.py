"""Trusted GitHub branch verification through the operator's existing gh sign-in."""

import json
import subprocess
from urllib.parse import quote


from agent_workflow.github import WorkflowError, canonical_repository, validate_sha


class GitHubVerifier:
    def __init__(self, allowed_repositories: set[str]):
        self.allowed = {canonical_repository(name) for name in allowed_repositories}

    def _get(self, endpoint: str):
        try:
            result = subprocess.run(
                ["gh", "api", endpoint],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            # Do not return credential-bearing subprocess output or headers.
            raise WorkflowError("GitHub verification unavailable") from None

    def __call__(
        self, repository: str, branch: str, base: str, head: str
    ) -> None:
        repository = canonical_repository(repository)
        if repository not in self.allowed:
            raise WorkflowError("repository is not authorized")
        validate_sha(base)
        validate_sha(head)
        branch_data = self._get(
            f"repos/{repository}/branches/{quote(branch, safe='')}"
        )
        if branch_data.get("commit", {}).get("sha") != head:
            raise WorkflowError(
                "remote branch head is stale or does not match"
            )
        comparison = self._get(f"repos/{repository}/compare/{base}...{head}")
        if comparison.get("status") not in {"ahead", "identical"}:
            raise WorkflowError("base is not an ancestor of remote head")
