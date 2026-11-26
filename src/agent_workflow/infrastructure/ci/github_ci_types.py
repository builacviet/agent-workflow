"""Layer: Infrastructure
Responsibility: Define GitHub Actions and Checks API wire values.
Dependencies: Python standard library.

Only decisive GitHub conclusions can become CI verdict evidence.
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


DECISIVE_CONCLUSIONS: frozenset[str] = frozenset(
    {
        GitHubCheckConclusion.SUCCESS,
        GitHubCheckConclusion.FAILURE,
    }
)
