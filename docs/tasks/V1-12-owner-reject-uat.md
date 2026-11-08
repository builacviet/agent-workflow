# V1-12 — Owner Reject → ChatGPT Planning round-trip: integration UAT

> **Status: PLANNED, NOT EXECUTED.** This is a joint Owner acceptance scenario for V1-12 and a required compatible read-only planning-feedback integration. **No live Telegram/WFC/ChatGPT end-to-end test has been performed.** Do not mark PASS until the Owner executes and confirms the real round trip.
>
> Authority: [V1 scope/gates](../v1-scope-and-gates.md), [V1 visual flow](../v1-visual-flows.md), [Notion V1-12](https://app.notion.com/412f92014abd8180982bd42746d2b095). No Executor/Podman, no auto merge.

## Acceptance prerequisites and safety

- Owner-approved V1-12 Telegram adapter exists; only an **allowlisted, authenticated Owner identity** may Approve/Reject. Buttons and commands are single-use, expire and bind to the **exact package ID + immutable version/hash**.
- Trusted Controller stores event atomically in durable WFC state, with rejection reason, timestamp, decision identity/reference and event ID. A Reject invalidates pending approval and **blocks dispatch immediately**; request retries/duplicate callbacks are idempotent.
- An **explicitly approved read-only WFC/MCP planning-feedback tool** is connected and authorized for ChatGPT TL Planner. Its response distinguishes the correct package, latest status/reason/version/hash, and any obsolete decision. **None of the nine current MCP tools provides this specific retrieval contract**; backward compatibility of all nine must be preserved. A generic Notion page is not sufficient proof of the authoritative decision.
- Use a disposable **synthetic package**, e.g. \`UAT-PLAN-001\`, with no real developer dispatch/credentials/production DB mutation. Use a test Owner Telegram identity authorized for the environment; log only redacted IDs, event metadata and status transitions. No secret, token, private key or sensitive personal content in logs, messages, or PR.
- Verify end-to-end functionality on the **approved target Ubuntu WSL2 environment** only when Owner has authorized the integration/deployment. ChatGPT cannot autonomously open or insert messages into the old chat; the Owner must navigate back and type the resume request.

## Owner UAT — expected success path

| ID | Owner / system action | Expected evidence |
| --- | --- | --- |
| UAT-01 | TL Planner and Owner agree on synthetic package **v1**, Plan/Tasks/AC and \`qc: required\` with reason; Codex QC prepares MTP; independent TL Reviewer accepts package | Frozen package ID, version/hash, MTP reference, independent review verdict |
| UAT-02 | Controller sends Telegram planning approval with summary, full-document links and exact **v1 hash** | Telegram message/approval-request ID; no secrets; status \`PENDING_OWNER\` |
| UAT-03 | **Owner presses Reject** and provides a concrete reason: “Split task X and add API timeout AC” | WFC records \`REJECTED\`, identity-validated action, reason, exact v1 hash, event ID, timestamp; **no Dev dispatch** |
| UAT-04 | Owner returns to the **same ChatGPT Planning conversation** and types: **“Em tiếp tục planning vừa bị reject nhé. Kiểm tra WFC, lấy lý do Reject và package version/hash mới nhất; chưa gửi duyệt lại.”** | Planner queries trusted MCP read-only tool, shows correct package ID, v1 hash, reason and status. No guessed facts; no auto-dispatch |
| UAT-05 | Owner discusses changes with TL Planner; planner drafts **v2** and highlights v1 → v2 diff | New immutable v2 hash, updated Plan/Tasks/AC/QC policy; v1 preserved, not overwritten |
| UAT-06 | Codex QC updates MTP where AC/test scope changed; independent Codex TL Reviewer reviews **v2** | MTP traceability, reviewer verdict tied to v2 hash, no silent reuse of stale review |
| UAT-07 | Telegram sends **new v2** approval request; Owner approves v2 using allowlisted identity | WFC \`APPROVED\` for v2 only; v1 Reject remains in audit; only now may controlled sequential dispatch begin |
| UAT-08 | Owner reviews final evidence with TL Planner | Owner signoff with actual test timestamp, environment, recorded package IDs/versions (redacted as needed), PASS/FAIL |

## Negative / recovery tests (must fail closed)

| ID | Scenario | Expected |
| --- | --- | --- |
| NEG-01 | Unauthorized Telegram account attempts Approve/Reject | Rejected; no state mutation, no dispatch |
| NEG-02 | v1 button replayed after v2 exists; duplicate callback/delivery | Idempotent or stale rejection, without applying to v2 |
| NEG-03 | Rejection recorded, but ChatGPT MCP unavailable or wrong package selected | Planner says **NOT VERIFIED**, asks Owner for exact feedback/context; never fabricates a WFC read or advances |
| NEG-04 | Owner does not provide reason or feedback storage fails | Explicit actionable failure; remains unapproved; no Dev |
| NEG-05 | Controller restart / WSL2 sleep, then recovery | Durable Reject still retrievable and gates closed; reconcile, no duplicate dispatch |
| NEG-06 | v2 changes after Owner approval or MTP/review evidence belongs to v1 | Prior approval/evidence invalid; new review + Owner approval required |
| NEG-07 | A Reject is entered while package approval or dispatch is racing | Atomic authorization check prevents unauthorized dispatch; record reconciliation/blocker |

## Evidence / signoff contract

- Record **actual** environment and package IDs/hashes, review and Telegram event IDs, tool name/sanitized response, WFC status, times, exact pass/fail observations, and any blocker. No simulated PASS for an unimplemented component.
- The Owner explicitly confirms UAT-03 (Reject in Telegram), UAT-04 (feedback independently retrieved in the existing ChatGPT planning conversation) and UAT-07 (Approve v2). TL Planner independently compares recorded state and reports the outcome.
- **PASS** only when the entire real round trip and negative safety checks succeed. Otherwise **BLOCKED / NOT VERIFIED** with remediation. Integration UAT does not approve PR merge or alter unrelated environments.

## Current status

**Design + documented acceptance test only.** The WFC rejection store, Telegram callback handling, approved read-only MCP feedback query, ChatGPT connector authorization and Owner end-to-end execution are future implementation and UAT work. Existing Phase 3.1 nine MCP tools must continue to behave compatibly.
