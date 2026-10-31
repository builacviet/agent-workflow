# Proposed Tech Lead review contract

This document proposes a POC contract; it does not implement orchestration or prove that ChatGPT automation can dynamically select models or reasoning effort.

- Every task declares requested review effort: `instant`, `medium` or `high`. Suggested use: small low-risk edits, normal changes, and high-risk/stateful changes respectively. All levels must cover the acceptance criteria.
- ChatGPT Tech Lead reviews task requirements, acceptance criteria, `AGENTS.md`, architecture, handoff, test evidence and the Git diff from the recorded base SHA to the exact head SHA.
- At most two Tech Lead review rounds per task. Round 1 failure is `CHANGES_REQUESTED`; the Coder may submit fixes for round 2. Round 2 failure is `ESCALATED`; stop automatic rework and await a Tech Lead decision. Approval is `APPROVED`.
- Each review record includes task ID, round (1 or 2), requested effort, verifiable model/effort evidence or `unverified`, base SHA, reviewed head SHA, findings mapped to acceptance criteria, test evidence, decision, Tech Lead identity and timestamp.
- Immediately before recording approval, verify that the task branch head still equals the reviewed SHA. A stale SHA must not be approved. Any later commit invalidates approval for the new head; review must target the new SHA within the two-round limit, otherwise escalate.
- Only ChatGPT Tech Lead records approval and escalation. Codex submits implementation and evidence, never self-approval. Approval does not authorize merging; merging requires explicit authorization.
- Never claim required model/reasoning effort was applied unless it can be verified. Tasks requiring verified High review must not be automatically approved when verification is missing; hold for the Tech Lead to resolve the missing evidence or explicitly revise the requirement. Never silently downgrade the task.

Review records may initially be explicit handoff/review messages. A future storage/orchestration mechanism is outside Phase 1.
