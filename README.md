# ChatGPT Tech Lead Review POC

Phase 1 is a standalone order-domain baseline for a future workflow:

ChatGPT Tech Lead → MCP Workflow Server → Codex CLI Coder → GitHub → ChatGPT Tech Lead Review → Notification.

Only the order domain and review documents exist here. TASK-001 is proposed, not implemented. No MCP server, dashboard, scheduling or orchestration is included.

## Setup and tests

Requires Python 3.11+. From the repository root (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[test]'
.venv\Scripts\python.exe -m pytest -q
```

After activating the environment, the equivalent test command is `python -m pytest -q`. Runtime dependencies are empty; pytest is a test dependency.

```python
from decimal import Decimal
from order_service import OrderItem, OrderService

service = OrderService()
order = service.create_order([OrderItem("book", 2, Decimal("12.50"))])
assert order.total == Decimal("25.00")
assert service.get_order(order.order_id) is order
```

Storage is in memory and local to one service instance; concurrent use is unsupported. Totals use Decimal with the caller's precision and no currency quantization. Repeated creation currently produces different IDs.

## Review context

- [Project rules](AGENTS.md)
- [Architecture](docs/architecture.md)
- [Handoff format and baseline evidence](docs/handoff.md)
- [Proposed review policy](docs/review-policy.md)
- [TASK-001: proposed idempotent creation](docs/tasks/TASK-001.md)

Future implementation uses a dedicated Git worktree/branch, committed and pushed for exact-head-SHA review by ChatGPT Tech Lead. Review effort is requested per task (`instant`, `medium`, `high`), with at most two rounds. Codex cannot approve its own work. Do not implement TASK-001 before Tech Lead authorization or merge without explicit authorization.

## Initial publication recovery

If GitHub authentication/publication is unavailable during bootstrap, sign in and publish from this new repository only:

```powershell
gh auth login -h github.com
gh repo create techlead-review-poc --private --source . --remote origin --push
gh repo view --json url,isPrivate,defaultBranchRef
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

If creation reports an existing repository, inspect its owner and privacy before attaching any remote; do not overwrite an existing project. GitHub publication is complete only when privacy and matching local/remote commit SHAs are verified.
