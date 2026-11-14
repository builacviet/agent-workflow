# Manual Coder — Draft PR handoff

**Put this in the GitHub PR description, not a new Markdown file.** GitHub owns implementation, test/CI evidence and review findings. The linked **Notion Task owns approved AC, scope/allowlist, base and publish authorization**; do not copy its full requirements here. Notion Comments are only for questions, status and links. Never include credentials or secret-bearing logs.

## Task and change
- **Task:** `<TASK-ID>` — [approved Notion task](<NOTION-URL>)
- **Implementation (2–5 lines):** `<what actually changed and why>`
- **AC coverage by ID:** `AC-01 PASS → code/test reference; AC-02 NOT VERIFIED → reason`. Do not repeat the AC prose.
- **GitHub PR base / tested implementation SHA / final pushed HEAD:** `<base> / <tested-sha> / <head>` (GitHub identifies current HEAD; state any divergence from Notion base).
- **Actual diff against approved base:** `<inspected; paths match Notion allowlist PASS / FAIL / NOT VERIFIED>`.
- **Actor attribution:** `<actual authenticated GitHub author/app; Coder agent role if relevant>`. A role label is not proof of bot authentication.

## Tests and CI (observed evidence only)

| Command or CI run link | Environment / tested SHA / event | Exit / conclusion and pass-fail-skip | Limitations |
| --- | --- | --- | --- |
| `<scoped test / Ruff / architecture>` | `<OS, Python, SHA>` | `<exit; counts>` | `<NONE or NOT VERIFIED + reason>` |
| `<push HEAD / pull_request merge ref>` | `<actual event + SHA>` | `<result>` | `<gaps>` |

- CI **push HEAD** and **pull_request merge-ref** are different evidence. If CI is pending or not observed, say `NOT VERIFIED`, not PASS. New HEAD invalidates old SHA-bound evidence.

## Concise checked self-review

Each checked item means *inspected*, not automatically passed. Mark each relevant area `PASS / FAIL / N/A / NOT VERIFIED` with a short reason or reference; group irrelevant items as `N/A + reason` for small tasks.

- [ ] **AC / regression / compatibility:** `<verdict; evidence>`
- [ ] **Approved write paths / protected paths / remote publish grant:** `<verdict; Notion task and diff check>`
- [ ] **Clean Code / architecture / PostgreSQL if touched:** `<verdict; findings/fixes or N/A>`
- [ ] **Security / external effects:** `<verdict; issues or NONE>`
- [ ] **Tests / CI coverage and skipped checks:** `<verdict; links or NOT VERIFIED>`

**Self-review fixes:** `<brief or NONE>`. **Risks/blockers:** `<brief or NONE>`. **Requested status:** `READY_FOR_TL_REVIEW` or `BLOCKED`.

**Handoff:** Coder may ordinary-push/open Draft PR only if the approved task grants remote publish. After PR evidence is ready, post **one short Notion comment**: `READY_FOR_TL_REVIEW — PR #<n> <url>` (include CI PENDING if applicable). Detailed code findings stay in GitHub review; only Owner merges.
