# Phase 2.2 source migration

Source: private builacviet/my-wfc-mcp, commit b1a3d684162c3d9f32b3caec8116b85f7770a1c3, local D:\STUDY\TESTING\my-mcp. Target: private builacviet/techlead-review-poc. Target base main/origin-main: 015ccc535b85ce1d0ad00da787b827970b9bf39a. Migration branch: infra/integrate-my-wfc; dedicated checkout D:\WORK\Project\techlead-review-poc-my-wfc. Files were read from the source commit, rather than copied from runtime directories. The original checkout, Git repository, server and database are retained unchanged. No merge, rebase or force-push of main/TASK-001 is part of this migration.

## Migration changes

workflow becomes src/my_wfc with import namespaces adjusted; mcp_server.py becomes my_wfc.server. Pure workflow contracts, transaction/state-machine logic, schema version 1, GitHub verification and security are preserved. Packaged SQL and historical task snapshot are included in wheels; the snapshot is loaded using importlib.resources so seed-history does not depend on a checkout docs directory. paths.py supplies stable default state paths. pyproject.toml exposes optional mcp and test extras without adding a runtime dependency to order_service. Existing order source and tests remain byte-for-byte unchanged.

Default src/editable state: checkout/data/workflow.sqlite3. A wheel without the source checkout defaults to ~/.my_wfc/data/workflow.sqlite3; use an absolute WFC_DB_PATH for deliberate placement. Relative overrides are rejected to prevent working-directory-dependent state. Empty override selects the default. No runtime database, environment, backup or credentials are migrated into Git.

## Optional carry-over of existing state — operator action only

No state-copy operation was performed. New-checkout seeds create a separate DB and label records as imported history. To carry actual state instead, choose ONE initialization approach before seeding:

1. Keep the original service/database untouched. Make a consistent SQLite snapshot with a read-only source connection and SQLite's backup API. Write a NEW destination path outside source, e.g. the consolidated checkout's ignored data/imported-workflow.sqlite3. Refuse to overwrite any existing destination. Do not copy a live .sqlite file with ordinary file-copy tools: WAL/journal content may be missed.
2. Example, run locally after substituting absolute paths; the script fails if destination already exists:

```python
from pathlib import Path
import sqlite3

source = Path(r"D:\STUDY\TESTING\my-mcp\data\workflow.sqlite3")
destination = Path(r"D:\WORK\Project\techlead-review-poc-my-wfc\data\imported-workflow.sqlite3")
if not source.is_file() or destination.exists():
    raise RuntimeError("Source must exist and destination must be new")
destination.parent.mkdir(parents=True, exist_ok=True)
# Read-only source; all writes go to the new snapshot.
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
    with sqlite3.connect(destination) as snapshot:
        original.backup(snapshot)
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute("PRAGMA user_version").fetchone()[0] == 1
```

3. Configure WFC_DB_PATH to the NEW absolute snapshot path; never point new tools at the old database. Back up the snapshot again before any intentional fixture reset. Inspect TASK-001 result/history before exposing workflow tools. A copied live lease may still be active until expiry; normal recovery applies without consuming a review round. Credentials are not part of the DB copy procedure.
4. Do not seed over actual review execution history. seed-history refuses conflicting task lifecycle states. Unknown historical evidence remains null/unverified.

## Fixture SHA strategy

Preserved real references:

- Baseline 015ccc535b85ce1d0ad00da787b827970b9bf39a.
- Round 1 6352c8ff5ea83450a93cd1077d6447170c9dedaa.
- Round 2 ff1458fb98dec3a321ca9b96ed8723e40ba882a9, still task/TASK-001 head.

POC-REVIEW-001 in a NEW database uses real branch poc/review-baseline at the baseline SHA, verified against GitHub at handoff/claim/submission. That branch was created only after checking whether it existed; a different existing head would stop the procedure. Do not move, merge into or reuse the fixture branch for implementations. The service still verifies its remote head, so accidental movement fails closed. No historical SHA is changed. The fixture's requested effort remains medium and require_verified_effort=false solely to exercise POC outcomes while actual effort stays unverified.

A copied original DB may contain the OLD synthetic authorization for main. It must not be silently retargeted: seed-synthetic will reject conflicting metadata. Inspect and preserve a snapshot first. If POC-REVIEW-001 is disposable, run the explicit `python -m my_wfc.admin reset-synthetic` against the NEW DB to delete only that synthetic fixture and reseed it on the verified fixture branch. This intentionally discards only its synthetic history; TASK-001 is retained. If its records must be kept, leave the copied DB intact and use a separate new DB for fixture testing instead. Never update task/review SHAs manually. Main advancing cannot affect new pinned fixtures; old main-based fixtures fail remote verification rather than silently accept stale SHA.

## Verification and operations

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m my_wfc.admin seed-history
.venv\Scripts\python.exe -m my_wfc.admin seed-synthetic
.venv\Scripts\python.exe tests/live_my_wfc_smoke.py
```

The smoke check starts and stops only its own temporary loopback server processes, reconnects twice and checks state persistence/ping/denied writes. It uses the new configured database; it does not start a tunnel or edit an existing server. Full unit tests include original source tests with namespace changes and migration resource/path checks. Build with `python -m pip wheel --no-deps . -w dist` to inspect packaged schema/reference and both imports in a non-editable install.

## Cutover / rollback

No automatic cutover is performed: the original my-mcp server may keep running on 8000, and the migration server is verified on temporary loopback ports. Starting `python -m my_wfc.server` uses 8000; first confirm the port is available. Do not stop the original project as part of verification. Cloudflare tunnel/public host configuration is preserved in source but no new exposure or connector reconfiguration is performed.

Before any future authorized cutover, snapshot state, configure protected transport credentials securely, stop only the intended old server, start the consolidated module, and reconnect the client. If rollback is needed, stop only the consolidated process, keep both DB snapshots intact, and resume the unchanged original project's documented start command with its original DB/configuration. No schema conversion is required (version 1 in both), but writes made after cutover are not automatically copied back. Do not overwrite the original DB to roll back. Git rollback of a future authorized merge must be decided by Tech Lead; this branch is not merged automatically.
