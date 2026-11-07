# Architecture — CURRENT code and Phase 3.1 baseline

> **Scope:** This document describes the existing source/package and Phase 3.1 POC behavior; a particular running deployment requires separate verification. The **Owner-selected TARGET design** is in [CL-12 consolidated ADR](adr/CL-12-architecture.md) with [security](adr/CL-12-executor-security.md) and [deployment](adr/CL-12-deployment.md) details. Rootless Podman/Linux-host Executor, four isolated stage containers, GHCR and Grafana are **not claimed implemented here**. Read the [agent documentation map](INDEX.md) to select only task-relevant sections.

`models.py` contains frozen `OrderItem` and `Order` values with validation and computed `Decimal` totals. `service.py` owns a per-instance dictionary, creates validated orders with sequential string IDs and looks them up. `__init__.py` exports the public API. Tests depend only on that API and pytest.

## Invariants and boundaries

- Product/order IDs must be nonblank strings. Quantities are positive integers; booleans are rejected. Prices must be finite, nonnegative `Decimal` values; free items are allowed.
- An order contains at least one valid item in an immutable tuple. The service copies the incoming iterable before storing an order. Invalid creation does not store anything or consume an ID.
- Line total is quantity times unit price; order total is the sum of line totals. No taxes, discounts, currency conversion or quantization are implied. Calculations use the caller's Decimal context (default precision 28); extreme magnitudes are outside this small POC's guarantees.
- Domain models do not depend on the service or external systems. The service depends on models and Python's standard library only. There is no persistence, network access or workflow implementation.
- IDs and storage are scoped to one service instance. Data is lost on restart; concurrent access is unsupported. Repeated valid creation calls currently produce distinct orders. Idempotency is a proposed future task only.

## Errors and verification

Use `TypeError` for unsupported input types, `ValueError` for invalid values and `KeyError` for unknown order IDs. Do not suppress errors or mutate storage before validation succeeds.

Deterministic unit tests cover totals, validation (including NaN/infinities and booleans), immutable snapshots, independent storage, missing IDs and failure atomicity. Review must preserve these invariants and dependency boundaries. No integration infrastructure is needed for Phase 1.

## Consolidated my_wfc package (Phase 2.2)

The preceding application invariants describe order_service only; its source and baseline tests are unchanged. my_wfc is a separate package with an optional MCP SDK dependency and no imports to/from order_service. It preserves the Phase 2 service, five-table SQLite schema version 1, atomic transitions, lease recovery, exact remote-SHA verification, two completed-review-round limit and immutable review/finding history. Seven workflow tools require authenticated transport; ping/info remain public. Verified-effort approval stays fail-closed without trusted evidence.

See my-wfc.md for the complete workflow architecture and migration-my-wfc.md for provenance, stable data paths, packaged resources and fixture strategy. SQLite belongs to ignored local data, never the Git repository. TASK-001 is imported ESCALATED at 2/2 rounds with its original references; no task implementation changes are part of migration. The new synthetic seed pins its real baseline through poc/review-baseline so advancing main does not invalidate a new fixture. Existing copied metadata is never silently retargeted.

## Current Phase 3.1 boundaries (owner-authorized 2026-11-05)

- `order_service` remains independent and unchanged.
- `WorkflowService` implements domain rules using `WorkflowRepository` / `WorkflowUnit` Protocols. SQL, schema initialization and SQLite transaction/connection management live in `repository.py`. Tests can inject a memory implementation without SQLite. Existing schema/data/history remain compatible; no new evidence table.
- `dispatch_repository.py` owns atomic SQLite dispatch storage. Controller coordinates one task at a time, deadlines, Docker phase lifecycle and local result validation; it never runs Git or calls GitHub.
- `coder_worker` owns GitHub App auth, cancellable HTTP, Git clone/commit/ordinary push and draft PR. It runs only inside the independent worker image in prepare/publish phases. Source editing and credential-free tests see only a snapshot, not Git metadata, result files, PEM or DB. No Docker socket is mounted.
- WFC authenticates handoff/verdict submissions and matches their SHA against task/claim/round in SQLite. Tech Lead independently checks remote SHA via GitHub plugin. WFC never claims current remote approval freshness and has no GitHub credential or adapter dependency.
- GitHub App/model live checks remain operator work when credentials are provisioned. No v2, Notion, OAuth, automatic verdict or merge.

See docs/phase-3.1.md for current operator instructions. Earlier remote-verification descriptions above are historical migration evidence superseded by this boundary and docs/review-policy.md.
