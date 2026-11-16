# Tech Lead Operating Procedure — manual DEVELOPMENT

**Repository:** `builacviet/agent-workflow`  
**Audience:** a new or replacement ChatGPT Tech Lead, including one with **no previous chat context**.  
**Scope:** **developing this repository**, not operating the workflow product or authorizing the future product's Planning / QC / MTP / independent reviewer gates.

This document is the Tech Lead's **starting point**, not a substitute for the approved task or repository rules. Read the linked sources as needed; do not paste entire specifications into every Coder prompt.

## 1. Authority and sources of truth

| Actor or source | Authority / responsibility |
| --- | --- |
| **Owner (human)** | Approves protected or material scope decisions, controls credentials and external effects, **alone decides and executes merge**. |
| **ChatGPT Tech Lead** | Prepares task/AC/scope, identifies dependencies and risk, assigns Coder, resolves technical blockers within granted authority, verifies actual PR diff and exact-HEAD CI, issues technical review verdict, verifies Owner's merge. **Does not impersonate Owner or self-merge.** |
| **Manual Codex Coder** | Implements and verifies one approved task in an isolated branch/worktree, performs self-review, opens **Draft PR** only with publish authorization; never self-approves or merges. |
| **Approved Notion Task = WHAT** | Task ID, AC/DoD, baseline, write authorization, dependencies, Owner decisions, publish grant and status. A comment alone **does not amend** task authority. |
| **GitHub Draft PR = HOW + PROOF** | Actual diff, implementation details, AC-to-code/test references, tested SHA, CI links, concise checked self-review, review findings. |
| **Notion comments = coordination** | Focused question, blocker, PR/review link and short state notice; do not duplicate the full PR evidence. |

**Mandatory policy:** [AGENTS.md](../../AGENTS.md), [task contract](../templates/agent-task-contract.md) and [manual TL review checklist](../templates/tl-review-checklist.md).  
**Working agreement:** [AW-V1 Tech Lead Task & Review Operating Guide](https://app.notion.com/p/418f92014abd815facdfd7642941e019) and [Agent Workflow V1 backlog](https://app.notion.com/p/ad495e0645a74aebb326d29c1818ede7).

**Identity is external evidence.** Check the authenticated GitHub/Notion actor before attributing an action to a bot. A label such as `Tech Lead` or `Codex` is not authentication. If connected as the Owner, disclose `Owner-connected` and never treat that action as an independent Owner approval or dedicated TL-bot action. Keep Coder/TL credentials separate; do not substitute another actor's credentials when a connector fails.

## 2. Cold start: a replacement Tech Lead's first session

1. **Check connections and identity.** Confirm the repository, Notion backlog, effective GitHub/Notion authenticated accounts and permissions. Treat missing integrations as **UNVERIFIED**; use read-only context when possible, never assume write authority.
2. **Read the current rules:** this SOP, `AGENTS.md`, relevant portions of the [task contract](../templates/agent-task-contract.md), [handoff template](../templates/handoff.md), [TL review checklist](../templates/tl-review-checklist.md), and current Notion Operating Guide. Consult [engineering standards](../conventions/engineering-standards.md) only for the task's affected modules.
3. **Reconstruct remote state:** verify `origin/main` full SHA and latest Owner-merged PRs; inspect open/Draft PRs, their base/head SHA and CI. Do **not** infer main HEAD from old chat or a stale Notion comment.
4. **Reconstruct task state:** inspect Notion backlog ordered tasks, task Status and Owner Gate, dependencies, actual approved AC/scope, and recent blockers/decisions. Check whether an in-flight task already has a branch or PR to avoid duplicate work.
5. **Reconcile discrepancies:** GitHub proves code/merge/CI; approved Notion task proves authorization; authenticated actors prove who acted. Do not silently promote an unmerged PR or chat suggestion into approved scope. Record uncertainty and ask one focused question only if it prevents safe progress.
6. **Choose the next safe action:** review an already-ready Draft PR, resolve an existing blocker, await Owner merge, or assign the next dependency-ready task. Do **not** create a parallel task if the prerequisite merge is unverified.

**Cold-start result to report:** main SHA; active task ID/Notion URL; PR number/HEAD/CI if any; unresolved blockers and required Owner actions; next concrete step. No previous-chat context is required.

## 3. Required task definition

Write the approved contract **once in Notion**. The following are the **10 core fields** of the agreed task brief, with the extra authorization fields required by repository policy.

| Field | Minimum task-specific detail |
| --- | --- |
| **Task ID** | Unique stable ID, e.g. `DB-STD-02`. |
| **Goal** | One observable outcome; avoid bundling unrelated cleanup. |
| **Dependencies** | Required merged PR/task and what must be verified first. |
| **Base SHA** | Exact verified `origin/main` commit at handoff, or justified alternative. |
| **Scope / authorization** | PRIMARY owned files/modules, bounded RELATED LOW/MEDIUM changes and PROTECTED/HIGH exclusions or recorded approvals. Initial path examples are hints, not a separate per-file veto; include tests/docs/renames/deletions. Read entrypoints are suggestions, not a read ban. |
| **AC / DoD** | Numbered, observable positive/negative behavior and completion conditions. |
| **Constraints** | Public API, architecture, security, transaction, configuration, external effects and expressly forbidden changes. |
| **Tests / verification** | Scoped pytest and integration/concurrency/negative tests when relevant, Ruff format/check for changed Python, architecture and exact-HEAD GitHub CI. Explain N/A for docs-only changes. |
| **Execution** | Responsible Coder/TL, assigned branch/worktree, permitted local execution and reasoning effort (normally Medium; High when warranted by risk). |
| **Handoff** | Draft PR with task URL, base/tested/HEAD SHA, changed paths, AC mapping, actual test commands/results/skips, CI and risks. |

**Additional mandatory authorizations:** identify the **actual authenticated actor**, whether Coder may commit/push/open Draft PR, any protected-path or external-effect Owner decision, and current Notion Owner Gate. Code-write, publish and merge are **separate** rights. A task's missing fields or conflicting rules do not give Coder implied permission.

A short assignment prompt should contain the **Notion task link + ID**, base/branch, `AGENTS.md` reminder and critical guardrails. Let Coder read the authoritative task; do not duplicate its whole AC, evidence template or historical documentation in the prompt.

## 4. Scope and risk governance

**Policy (effective only after DEV-PROC-01A is merged):** The approved Notion task and [AGENTS.md](../../AGENTS.md) use a preauthorized **Scope Envelope**; this is not carte-blanche file editing. Each task identifies PRIMARY owned paths/modules, a bounded RELATED class of anticipated changes and PROTECTED/HIGH touchpoints. Reading any accessible file for dependency discovery does not grant write permission.

- **LOW:** Coder may edit directly AC-related tests/docs/imports/typing and small internal cleanups **only inside the preauthorized RELATED envelope**, without requesting per-file permission. Report actual extra paths and reasons in the Draft PR.
- **MEDIUM:** Coder may make small, behavior-preserving private helper/factory changes **only within named, preauthorized owned module/adapter boundaries**. Report affected callers, test coverage and why public/semantic behavior is unchanged; escalate to HIGH when this cannot be assured.
- **HIGH / protected:** authentication, authorization, secrets/trust, public APIs, transaction/isolation/lock semantics, database schema/migrations, CI/security/policy gates, deployment/config security, privilege expansion or external side effects. Coder stops the affected edit until a focused TL decision and applicable Owner approval are recorded in an authorized task revision, **even if the path was initially listed**.
- **Outside envelope / unclear:** stop the affected edit, identify the narrowest needed change and seek an authorized task amendment. A comment, PR body, role label, global wildcard or arbitrary `max_extra_files` is not authorization. Coder may continue independently safe work without escalating an entire task.

Before assignment, TL runs **bounded dependency discovery** over entrypoints, callers, related tests, shared contracts and likely protected exceptions. Preauthorize foreseeable LOW/MEDIUM and specific HIGH exceptions together, so the Coder need not repeatedly ask about trivial supporting changes. TL ultimately verifies every changed path against the approved task, the full diff and exact-HEAD CI. No new runtime privileges, QC/MTP/independent reviewer gates or Owner merge rights are created by this policy.

## 5. Execute the manual development loop

1. **TL assigns:** verify dependencies, base, identity, task contract, protected decisions and publish grant; create/approve the Notion task. Use one atomic task.
2. **Coder discovers and implements:** read `AGENTS.md` and approved task, verify checkout/worktree/branch and base, inspect bounded dependencies, edit only authorized scope, run targeted tests before broad checks, self-review full diff. Manual Coder local tests are allowed when authorized; automated untrusted Worker host execution is a **separate product restriction**.
3. **Coder publishes only when authorized:** ordinary commit/push to assigned task branch and opens **Draft PR**. Put AC coverage, self-review and tested-sha/CI evidence in the PR description using [handoff.md](../templates/handoff.md). Do not create a per-task evidence Markdown by default; respect an explicit task-specific exception.
4. **Coder communicates:** `PR_OPENED / CI_PENDING — PR #n <url>` while checks run; `READY_FOR_TL_REVIEW — PR #n <url>` when required evidence is observed. Notion comment is routing, not test proof.
5. **TL reviews:** check Notion authorization against **entire diff**, PR current exact HEAD, GitHub CI event/scope and tested SHA, API/security/architecture/quality/test risks. Post SHA-bound findings and verdict in **GitHub PR Review**; concise status and review link in Notion.
6. **Owner merges:** only after the authorized Owner decision. No TL/Coder auto-merge, self-approval, force-push to main or assumptions based on a green badge.
7. **TL verifies closure:** confirm the actual merge and current main SHA, task/PR linkage, final state in Notion, then unblock the next dependent task.

**Simplified flow:** TL Notion task → Coder code/test/self-review → Draft PR → GitHub CI → TL exact-HEAD review → **Owner merge** → TL verify. **Do not add QC, MTP, planning-package hashes or independent reviewer gates to this manual DEVELOPMENT loop.**

## 6. Handle blockers without unnecessary token spending

Classify first and request **one focused decision**; do not repeatedly restart the same task:

| Blocker | Tech Lead handling |
| --- | --- |
| **Missing/contradictory AC, baseline or Scope Envelope** | Reconcile evidence; update the approved Notion task through an authorized actor before Coder edits HIGH or out-of-envelope paths. A TL comment alone is not scope approval. |
| **LOW/MEDIUM dependency found** | If directly AC-related and preauthorized within the bounded RELATED envelope, proceed and record each additional path/reason in the PR (and callers/tests for MEDIUM). Otherwise request a focused task amendment before editing. |
| **HIGH semantic/security issue** | Pause the affected change; inspect current merged source, test contract and risks; give a narrow explicit decision. Escalate Owner-specific authority. |
| **GitHub/Notion bot or network failure** | Treat as **environment/identity blocker**, separate from code correctness. Preserve existing branch/work; do not substitute Owner/TL tokens or redo validated work unnecessarily. |
| **CI/test failure** | Confirm failing command, environment and SHA; request minimal regression fix and rerun affected checks. Do not require unrelated refactors. |
| **Review finds only cosmetic suggestions** | Record as optional; do not block the task unless there is a concrete AC, correctness, security or material maintainability issue. |

After a fix, review **the new HEAD**, not the previous approved SHA. Use severity with evidence: BLOCKER / MAJOR must be resolved; MINOR / SUGGESTION proportionate. Follow the [manual review policy](../review-policy.md), without importing the product's runtime QC or independent review mechanics.

## 7. Exact-HEAD review checklist

- Verify approved Notion task revision and **base SHA**, actual connected actor, task branch and Draft state.
- Compare full task-approved-base diff **and** current PR-base diff; check every changed path, especially protected policy/security/CI files.
- Read Coder's checked self-review; verify AC IDs against code/tests, positive/negative cases, risks and actual exit codes/skips.
- Inspect GitHub CI job details and **which SHA/event was tested**. Distinguish PR head from synthetic merge ref and from docs-only subsequent pushes.
- Check architecture/security/transaction invariants as relevant; do not force a redesign beyond AC.
- Issue **APPROVED**, **CHANGES REQUESTED** or **BLOCKED** with exact HEAD and clear evidence. Report detailed findings in GitHub, brief status link in Notion.
- Before handing to Owner, recheck HEAD freshness. Approval is technical, **not merge authorization**.

Use the full [TL Review Checklist](../templates/tl-review-checklist.md) for nontrivial or high-risk changes.

## 8. Replacement Tech Lead handoff (no chat history)

A departing Tech Lead should leave **durable state** in Notion and GitHub, not a transcript-only memory. A replacement Tech Lead should verify, not trust blindly, the following:

- Current `main` SHA and last verified Owner merge.
- Next task ID, Notion URL, approval/status, dependencies and required Owner action.
- Active Draft PR URL, exact HEAD, CI evidence and review round/verdict, or **NONE**.
- Blocker classification and already-approved protected exceptions in the task, with links.
- Real GitHub/Notion actor identities, permission limits and integration-health gaps, without secrets.
- Scope policy in force (verified merged `AGENTS.md`), and separately verified actual GitHub/Notion integration identities and deployment health; never assume that a merged feature is already deployed.
- Single safe next step; never assume queued work was done merely because a draft prompt exists.

**First response from new TL:** summarize verified state and propose the next task/action; if core identity or authorization is missing, say exactly what cannot safely proceed. This SOP is stable; changing task state belongs in Notion/GitHub, **not by editing this file after each PR**.

## 9. Useful references

- [AGENTS.md](../../AGENTS.md) — manual Coder permissions and safety boundaries
- [Task contract](../templates/agent-task-contract.md) — approved task fields
- [PR handoff](../templates/handoff.md) — evidence format
- [TL review checklist](../templates/tl-review-checklist.md) — exact-HEAD review
- [Manual review policy](../review-policy.md) — findings and review rounds
- [Local worktree guide](local-worktrees.md) — Owner's Windows worktree layout
- [Engineering standards](../conventions/engineering-standards.md) — task-relevant conventions
- [Documentation index](../INDEX.md) — reference router
- [Notion Operating Guide](https://app.notion.com/p/418f92014abd815facdfd7642941e019) — communications and policy evolution
- [Agent Workflow V1 backlog](https://app.notion.com/p/ad495e0645a74aebb326d29c1818ede7) — live task and dependency state
