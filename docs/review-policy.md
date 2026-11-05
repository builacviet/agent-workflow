# Proposed Tech Lead review contract

This document proposes a POC contract; it does not implement orchestration or prove that ChatGPT automation can dynamically select models or reasoning effort.

- Every task declares requested review effort: `instant`, `medium` or `high`. Suggested use: small low-risk edits, normal changes, and high-risk/stateful changes respectively. All levels must cover the acceptance criteria.
- ChatGPT Tech Lead reviews task requirements, acceptance criteria, `AGENTS.md`, architecture, handoff, test evidence and the Git diff from the recorded base SHA to the exact head SHA.
- At most two Tech Lead review rounds per task. Round 1 failure is `CHANGES_REQUESTED`; the Coder may submit fixes for round 2. Round 2 failure is `ESCALATED`; stop automatic rework and await a Tech Lead decision. Approval is `APPROVED`.
- Each review record includes task ID, round (1 or 2), requested effort, verifiable model/effort evidence or `unverified`, base SHA, reviewed head SHA, findings mapped to acceptance criteria, test evidence, decision, Tech Lead identity and timestamp.
- ChatGPT Tech Lead must use its GitHub plugin immediately before recording approval to verify that the task branch head still equals the reviewed SHA. A stale SHA must not be approved. Any later commit invalidates approval for the new head; review must target the new SHA within the two-round limit, otherwise escalate.
- Only ChatGPT Tech Lead records approval and escalation. Codex submits implementation and evidence, never self-approval. Approval does not authorize merging; merging requires explicit authorization.
- Never claim required model/reasoning effort was applied unless it can be verified. Tasks requiring verified High review must not be automatically approved when verification is missing; hold for the Tech Lead to resolve the missing evidence or explicitly revise the requirement. Never silently downgrade the task.

Review records may initially be explicit handoff/review messages. A future storage/orchestration mechanism is outside Phase 1.


## Owner-approved local WFC boundary (2026-11-05)

WFC performs no Git/GitHub calls. Authenticated handoff and review clients own remote verification and report the full SHA. WFC checks SHA syntax and exact equality against stored authorization, task head and claim, plus round/lease/owner/history rules, within a repository transaction. A stored approval covers that SHA only; remote freshness is explicitly unknown (`approval_current=null`, `remote_verification=not_performed_by_wfc`). Tech Lead must verify branch head/base ancestry with its GitHub plugin before verdict and again before an authorized merge. Worker owns GitHub App credentials, Git push and draft PR. Controller only dispatches bounded worker phases and records the reported result. No DB migration or new evidence fields are introduced in this revision. The two-round limit and verified-effort requirement are unchanged; no third formal round or automatic merge is authorized.
