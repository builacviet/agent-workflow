# TASK-001: Add idempotent order creation

Status: PROPOSED — ChatGPT Tech Lead must review this task and its acceptance criteria before authorizing Codex implementation.

Risk level: high (request identity and state consistency).
Requested review effort: `high`. Verified High review is required for automatic approval; absent verification, hold for Tech Lead.
Maximum Tech Lead review rounds: 2.

## Business context and scope

Clients may retry an order request after losing its response. Within one `OrderService` instance, an optional idempotency key should let retries recover the original order without creating duplicates. Keep the current no-key API behavior. Add only in-memory key tracking and domain tests; persistence, concurrent callers, HTTP/MCP integration, scheduling, dashboards and orchestration remain out of scope.

## Acceptance criteria

1. A valid request with a new nonblank string key creates exactly one order. Repeating the same key with the same ordered item data returns that original order and ID, without consuming an ID or adding an order.
2. Reusing a key with conflicting data raises `ValueError` and leaves both orders and key tracking unchanged. Compare product ID, quantity and Decimal price for each item, including sequence and duplicate lines. Numerically equal Decimal prices (e.g. `1.0` and `1.00`) are equal; do not normalize product IDs or reorder lines.
3. Validate every request before changing state, including retries. Empty items, invalid items/quantities/prices, blank keys or nonstring keys must create no order, consume no ID and reserve no key. A corrected valid request using a previously invalid request's key must succeed. Existing exception conventions apply.
4. Omitting the key (or passing `None`) retains baseline behavior: each valid call creates a distinct order. Existing totals, validation, immutable snapshots, lookup and instance isolation remain correct.
5. Keys are case-sensitive exact strings; reject whitespace-only values. Different keys may create distinct orders for identical items. Keys belong only to their service instance; no cross-instance deduplication or restart guarantees.

## Edge cases and test expectations

Cover identical retries, changed product/quantity/price, reordered items, duplicate lines, equivalent Decimal representations, different keys with identical data, empty/whitespace/nonstring keys, invalid first attempts followed by valid retry, and invalid retries against existing keys. Confirm rejected requests do not change the original order or next ID. Use deterministic tests and preserve the baseline regression suite. Concurrent calls are explicitly unsupported.

## Definition of Done

- Tech Lead has authorized this proposed task and acceptance criteria before coding begins.
- Implement on a task-specific worktree and branch based on the recorded baseline SHA.
- All acceptance criteria have meaningful tests; `python -m pytest -q` passes with actual evidence recorded.
- Update architecture and handoff for the final behavior; commit and push the task branch, record base/head SHAs and remaining risks.
- ChatGPT Tech Lead reviews the exact current head SHA against this task and review policy within two rounds. No Codex self-approval and no merge without explicit authorization.
