"""GitHub Actions/Checks wire values shared by trusted CI adapters.

These are GitHub's lowercase wire values, not the Controller's uppercase
SUCCESS/FAILURE/BLOCKED decision. Keep unknown future conclusions fail-closed.
"""
from __future__ import annotations

from enum import StrEnum


class GitHubRunStatus(StrEnum):
    COMPLETED = "completed"


class GitHubCheckConclusion(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    NEUTRAL = "neutral"
    ACTION_REQUIRED = "action_required"
    STALE = "stale"


DECISIVE_CONCLUSIONS: frozenset[str] = frozenset({
    GitHubCheckConclusion.SUCCESS,
    GitHubCheckConclusion.FAILURE,
})
