# Coder / agent task handoff template

Fill only with observed evidence for the **current authorized task**. Submit in PR description or an explicit handoff message. Do not append new task reports to this template or to `docs/handoff.md`.

```text
Task ID and approved scope:
Role / responsibility (CURRENT implementation vs TARGET design):
Base commit SHA / branch / worktree:
Head commit SHA / pushed remote SHA / PR URL:
Changed files and why (path allowlist):
Acceptance criteria checked (PASS / FAIL / BLOCKED / NOT RUN):
Actual tests/commands, environment and observed results:
Security/trust-boundary changes and negative checks:
Risks, regressions, unknowns and limitations:
External effects / credentials / DB/host/containers touched (or NONE):
Diff review / CI evidence (or NOT RUN):
Requested review effort (do not claim unverifiable setting):
Status: awaiting independent Tech Lead / Owner review
```

**Rules:** Never fabricate a test, merge, engine hardening or model-effort result. Exact HEAD changes invalidate older review evidence. Codex cannot approve its own work. Tech Lead review allows at most two rounds, with separate Owner merge authority. See [`review-policy.md`](../review-policy.md), [`AGENTS.md`](../../AGENTS.md), and dated [historical handoffs](../history/handoff-log.md) if needed.
