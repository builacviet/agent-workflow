# my_wfc consolidated operator guide

This extends the existing MCP SDK 1.30.0 FastMCP server. It does not perform AI reviews, start a scheduler, dispatch Codex, manage worktrees or merge code. The existing streamable-http transport, loopback bind, port 8000, /mcp path, DNS-rebinding host list and public ping/get_server_info are preserved. Only server_info version/mode describe the new capabilities.

## Files and responsibilities

- src/my_wfc/server.py: original endpoint and public connectivity tools; registers workflow adapters.
- src/my_wfc/contracts.py: strict task, finding and submission schemas.
- src/my_wfc/service.py: SQLite transactions, leases, authorization metadata checks, two-round transitions, context/history and results.
- src/my_wfc/schema.sql: schema version 1; standard-library sqlite3; foreign keys and uniqueness constraints.
- src/my_wfc/github.py: trusted read-only gh API branch-head and base-ancestor verification; timeouts and sanitized errors.
- src/my_wfc/security.py: constant-time Bearer-header validation and configured operator identity.
- src/my_wfc/tools.py: authenticated MCP adapter, injected SDK request context, lazy service initialization.
- src/my_wfc/admin.py: local-only task authorization, historical import, disposable seed/reset. No remote provision/reset tools.
- tests/test_my_wfc.py: deterministic unit/concurrency/transaction/security tests and real loopback HTTP/MCP test with mocked GitHub.
- tests/live_my_wfc_smoke.py: actual server subprocess restart/reconnect check against seeded SQLite, without submitting verdicts.
- src/my_wfc/resources/TASK-001-reference.md: copied real task document from the reference repository; not invented source contents.
- pyproject.toml: mcp optional extra pins SDK 1.30.0; test extra supplies pytest; package data includes SQL and historical reference.
- .gitignore: excludes local environment, credentials, SQLite and backup files.
- data/workflow.sqlite3: local persistent state; never include in source publication.

## Schema

Five tables:

1. tasks: task identity, JSON immutable authorization metadata (title/description/AC/DoD/risk/context paths/synthetic marker), repository/branch/base/current head, requested effort, verified-effort requirement, max/completed rounds, status and timestamps.
2. review_attempts: opaque claim token, task, expected SHA, authenticated reviewer, claim/expiry time, status and canonical submitted payload. One ACTIVE attempt per task. A claim is not a round.
3. reviews: unique task/round and task/reviewed SHA, exact base/head, verdict/summary, requested/verified actual effort, trusted evidence, reviewer/time or null for unavailable historical evidence, separate imported_at, and JSON prior-finding resolutions.
4. review_findings: immutable original finding identity, review/task, severity, AC, path/line, description, suggested correction and required verification. Finding identity unique per task.
5. task_events: previous/next status, action, task/SHA, operation/import timestamp and JSON metadata. Historical event timestamps are import times, not invented original review times.

Resolution statuses FIXED, NOT_FIXED, PARTIALLY_FIXED and NOT_VERIFIED are recorded on the later review, keyed by original finding ID. Original findings/reviews are never overwritten. Context returns all completed reviews and all original findings, plus both base-to-head and previous-reviewed-head-to-current-head compare URLs and exact-SHA file URLs. Results also return latest effort, escalation reason, events and approval validity; coder assignment derives unresolved findings from the latest resolution history.

## MCP signatures

The SDK injects Context; clients never supply it. Workflow tools require transport authentication, including reads because repository metadata is private.

```text
list_review_queue(requested_effort: str | null = null) -> {tasks: [...]}
get_review_context(task_id: str) -> dict
claim_review(task_id: str, expected_head_sha: str) -> dict
submit_review(submission: Submission) -> dict
get_review_result(task_id: str) -> dict
register_task_handoff(task_id: str, repository: str, branch: str,
    base_sha: str, new_head_sha: str, handoff_file_path: str,
    test_summary: str) -> dict
get_coder_assignment(task_id: str) -> dict
```

Submission fields: task_id, claim_token, reviewed_head_sha, review_round (1 or 2), verdict, summary, findings, prior_resolutions, requested_effort, optional actual_effort and verification_evidence. Claim token is the submission idempotency identity. Identical replay returns duplicate=true without incrementing a round; conflicting payload is rejected. Approved replays reverify GitHub and fail if stale.

Finding fields: finding_id, severity (low/medium/high/critical), acceptance_criterion, file_path, optional positive line, description, suggested_correction, required_verification. prior_resolutions maps every prior finding ID to a supported resolution. A later finding requires a new ID.

## Transitions and operational failures

Local operator authorization creates AWAITING_HANDOFF. Verified handoff makes READY_IN_REVIEW. An atomic claim makes IN_REVIEW. Round 1 failure must be CHANGES_REQUESTED; a new verified pushed SHA enables round 2. Round 2 failure must be ESCALATED. A valid pass may produce APPROVED in either round, with no new findings and every prior finding FIXED. Terminal tasks cannot be reopened by these APIs. The client cannot choose an arbitrary failure transition. Never begin round 3.

Expired claims recover to READY_IN_REVIEW without spending a round; queue polling and claiming recover them. Active duplicate claims fail. An expired/stale/invalid claim cannot submit. A valid review, findings, completed-round count, attempt status and event are committed together under BEGIN IMMEDIATE. Failures roll back. Operational GitHub/effort/storage failures do not create failed reviews or consume rounds. Failures due solely to missing effort evidence cannot be disguised as code findings.

GitHub verifies the authorized branch equals the exact full SHA and base is its ancestor at handoff, claim and immediately before review writes. Fail closed on missing gh auth, timeout or mismatched head. An APPROVED historical status is not a perpetual merge permission: get_review_result rechecks remote and returns approval_current=false if moved/unverifiable. There is no atomic lock spanning GitHub and SQLite; a branch can move just after verification. Always revalidate before any future authorized merge. Never merge based solely on status. Pending handoffs cannot silently switch SHA; movement outside the prescribed correction cycle needs operator intervention.

## Security and effort

Public-safe tools are ONLY ping() and get_server_info(). All seven workflow tools require an Authorization: Bearer header, with a minimum-32-character WFC_AUTH_TOKEN configured on the server and matching client transport configuration. WFC_REVIEWER_ID is a server-configured actor label; do not treat it as independent proof of a human identity. Configure an operator-controlled dedicated credential for the Tech Lead client; protect it like write access. Multi-user roles/OAuth are future work.

No auth token is an LLM tool argument. Missing/short token, absent reviewer label, missing request or wrong header denies workflow access. Loopback is not an auth bypass because Cloudflare also forwards from loopback. Binding/Cloudflare settings are unchanged. HTTPS must terminate at the existing trusted tunnel/gateway; do not expose this bearer scheme over public HTTP. Tool discovery and ping remain public. Do not log transport headers, print credentials, put them in source/commands/history or return them in tool context. DB claim tokens are sensitive; keep data/ local and OS-protected. gh must be signed in as an authorized read-capable repository account; its credential output is never returned by tools.

The runtime has NO trusted reasoning-effort attestation provider. Client actual_effort/evidence never establishes verification. Stored actual_effort is null and verification_evidence is unverified unless a trusted server-side adapter independently verifies it. Verified High tasks cannot automatically approve in the default runtime; rejection leaves round count unchanged. Requested effort remains metadata/filtering. A future attestation adapter is an explicit integration point, not a model switch. Supported effort registry is injectable; no model versions are hardcoded.

## Start / restart / reconnect

From the consolidated repository root:

```powershell
.venv\Scripts\python.exe -m pip install -e '.[mcp,test]'
.venv\Scripts\python.exe -m my_wfc.admin seed-history
.venv\Scripts\python.exe -m my_wfc.admin seed-synthetic
.venv\Scripts\python.exe -m my_wfc.server
```

Editable/src installs use the checkout data/workflow.sqlite3 independent of working directory. Wheel installs without a checkout use ~/.my_wfc/data/workflow.sqlite3. An explicit WFC_DB_PATH must be absolute; blank behaves as unset. Optional WFC_DB_PATH selects an operator-controlled persistent file. Do not change it between restarts unless intentionally using a different POC database. Back up SQLite using its backup API or with the process stopped; do not delete the real DB to reset a fixture.

For authenticated workflow use, first configure WFC_AUTH_TOKEN and WFC_REVIEWER_ID in the server process environment using a secret manager or secure operator session, and configure the same token in the client's HTTP Authorization header. Restart the local server to apply environment changes. The public ping remains usable without credentials. The current plugin's ability to supply protected transport credentials has not been verified; if it cannot, keep writes disabled until an appropriate authenticated gateway/client is available. Do not paste the token into chat or a tool call.

Stop only the local server process started for this project, restart the same command, and reconnect/reinitialize the MCP client. If the tunnel is still running it continues pointing to the same local port. This phase does not restart/create a tunnel or change its public hostname. Quick Tunnel hostname changes require an operator-reviewed host allowlist update and client reconnect. Refresh/reconnect the plugin tool schema after server update; end-to-end ChatGPT connector reconnect is a manual integration step, not claimed by loopback tests.

## Safe manual Tech Lead POC

1. Confirm local ping and server_info. With no configured credentials, confirm claim/submit/handoff fail. Configure authenticated client transport before testing workflow tools.
2. Call list_review_queue('medium'): expect POC-REVIEW-001, marked synthetic in full context. TASK-001 must never appear. This fixture references the real baseline commit through the dedicated poc/review-baseline branch, verified by gh at seed time; it makes no claim that seed executed tests.
3. Call get_review_context('TASK-001'): expect ESCALATED, two rounds, F-001 then F-002, F-001 FIXED in round 2, unknown original reviewers/times and unverified effort. Do not claim or submit this task.
4. Call get_review_context('POC-REVIEW-001'), then retrieve its actual GitHub files/compare URL separately. Synthetic metadata is for exercising the protocol, not claiming a genuine implementation assignment or real review evidence.
5. Claim POC-REVIEW-001 using the exact current_head_sha. Repeat claim: it must fail. Use returned token/round/head in a structured submission. Do not invent findings: review actual code, or clearly mark a synthetic test exercise. No automatic LLM review is performed by the server.
6. The fixture requests medium and explicitly does not require verified effort so a manual POC approval can exercise the transition while storing actual_effort=null. A task requiring verified High must remain blocked without trusted attestation.
7. For a two-round local synthetic test, use the deterministic tests with mocked remote SHAs. For a live correction test, locally provision a new synthetic TaskSpec for a disposable authorized branch you explicitly create and push; do not use TASK-001 or pretend an arbitrary hash exists. No worktree/branch automation is provided.
8. Check get_review_result and get_coder_assignment. Repeat the exact submission: no new round. Change its payload with the same token: reject. Stop/start the server and confirm history persists.

Manual real-GitHub verification (read-only): `gh api repos/builacviet/techlead-review-poc/branches/task%2FTASK-001 --jq .commit.sha`; compare with ff1458fb98dec3a321ca9b96ed8723e40ba882a9. The synthetic fixture uses `branches/poc%2Freview-baseline` and 015ccc535b85ce1d0ad00da787b827970b9bf39a. `gh api repos/builacviet/techlead-review-poc/compare/015ccc535b85ce1d0ad00da787b827970b9bf39a...ff1458fb98dec3a321ca9b96ed8723e40ba882a9 --jq .status` must report ahead. For stale-head behavior on a separately authorized disposable branch, push a real new commit after claiming; submission must fail without changing completed rounds. Network/auth failure must also fail closed. Automated tests cover these failures without modifying remote branches.

## Seed/reset and tests

seed-history is repeatable and refuses to overwrite nonmatching real lifecycle state. Original review times and reviewer identities are null; imported_at explicitly records local import time. Historical High effort remains unverified. seed-synthetic is repeatable and uses a real verified baseline reference. Explicit reset-synthetic deletes ONLY POC-REVIEW-001 after checking its synthetic marker, then reseeds it; real TASK-001 history is untouched. Reset is destructive only to the explicitly disposable fixture, never the default startup operation.

```powershell
.venv\Scripts\python.exe -m my_wfc.admin reset-synthetic
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tests/live_my_wfc_smoke.py
```

Local authorization of a future task is `python -m my_wfc.admin provision --file authorized-task.json` with TaskSpec fields defined in contracts.py; transport cannot create/retarget authorizations. Review effort, task AC and repository identity are immutable once provisioned. The default allowed repository is builacviet/techlead-review-poc.

## Remaining limits / Phase 3 points

Single-host SQLite and bounded synchronous gh reads serialize writes; no high-throughput claim. Token rotation/role-based authorization/OAuth, independently trusted effort attestation, safe controller dispatch, scheduler routing, notifications and UI are future integration points. No automatic merge, restart of external tunnel, production deployment or TASK-001 application changes were performed. Default workflow access is intentionally disabled pending protected client configuration. A round-2 failure is final automatic escalation; further work needs Tech Lead/human authorization.

SDK reference: https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0 (implementation was checked against the locally installed 1.30.0 API).


See [migration instructions](migration-my-wfc.md) for source provenance, safe state-copy and fixture migration, and rollback without touching the original project.
