"""Pure, read-only validation of a GitHub publication receipt.

A trusted Controller fetches branch, commit and candidate PR objects using its
read-only GitHub App and passes the complete responses here. Missing/ambiguous
remote evidence fails closed. No network, DB, credential, or mutation access.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from agent_workflow.integrations.github import REPOSITORY, WorkflowError, validate_sha


_BOT_LOGIN = "builacviet-wfc-coder[bot]"


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    head_sha: str
    pr_number: int
    pr_url: str


def verify_remote_publication(
    intent: Mapping,
    branch: Mapping,
    commit: Mapping,
    pull_requests: Sequence[Mapping],
) -> PublicationReceipt:
    """Prove a pinned, previously prepared commit and one owned Draft PR.

    This is a read-only reconciliation verdict, NOT approval to release a
    blocked dispatch slot, retry GitHub writes, review, or merge.
    """
    try:
        expected = intent["expected_head_sha"]
        input_sha = intent["input_sha"]
        task_branch = intent["branch"]
        validate_sha(expected)
        validate_sha(input_sha)
        if (
            intent["state"] != "UNCERTAIN"
            or intent["repository"] != REPOSITORY
            or expected == input_sha
            or not isinstance(task_branch, str)
            or not task_branch.startswith("codex/")
            or not isinstance(branch, Mapping)
            or not isinstance(commit, Mapping)
            or isinstance(pull_requests, (str, bytes))
            or not isinstance(pull_requests, Sequence)
            or len(pull_requests) != 1
        ):
            raise ValueError
        branch_commit = branch.get("commit")
        if not isinstance(branch_commit, Mapping) or branch_commit.get("sha") != expected:
            raise ValueError
        parents = commit.get("parents")
        if (
            commit.get("sha") != expected
            or not isinstance(parents, list)
            or len(parents) != 1
            or not isinstance(parents[0], Mapping)
            or parents[0].get("sha") != input_sha
        ):
            raise ValueError
        pr = pull_requests[0]
        if not isinstance(pr, Mapping):
            raise ValueError
        user = pr.get("user")
        head, base = pr.get("head"), pr.get("base")
        if not all(isinstance(x, Mapping) for x in (user, head, base)):
            raise ValueError
        head_repo, base_repo = head.get("repo"), base.get("repo")
        if not isinstance(head_repo, Mapping) or not isinstance(base_repo, Mapping):
            raise ValueError
        number, url = pr.get("number"), pr.get("html_url")
        if (
            type(number) is not int or number <= 0
            or url != f"https://github.com/{REPOSITORY}/pull/{number}"
            or pr.get("draft") is not True or pr.get("state") != "open"
            or user.get("login") != _BOT_LOGIN
            or head.get("sha") != expected or head.get("ref") != task_branch
            or head_repo.get("full_name") != REPOSITORY
            or base.get("ref") != "main"
            or base_repo.get("full_name") != REPOSITORY
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, IndexError, WorkflowError):
        # Do not echo attacker-controlled remote response or credentials.
        raise WorkflowError("publication remote receipt unverified; dispatch stays blocked") from None
    return PublicationReceipt(expected, number, url)
