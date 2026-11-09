# PR #31 independent-review corrections — manual Docker verification (Owner)

## State and bounds

This guide verifies the isolated review-corrections branch `codex/pr31-r3-corrections`, derived from PR #31 HEAD `696c2ac93485b129f3aaa197a6c23f6b974db02f`. It is **not** evidence that original PR #31 is approved. Do not merge/deploy or run credentialed model calls based on these tests. Only a disposable cached Python 3 Linux Docker image is used. No `docker system prune`, image pull, credential mount, GitHub App change or production Controller invocation is required.

**Limits:** the watchdog is an independent host process, but still requires a reachable original Docker daemon. File-count/logical-byte limits prevent unbounded **host digest allocation**; they do **not** provide hard filesystem quota to the host bind mount. Wrong-daemon regression is mocked; real two-daemon switching is not performed by this runbook. These gaps remain review blockers.

## Run from Windows PowerShell

1. Verify existing checkout and create a test-only detached worktree. Never reset someone else's worktree.

```powershell
Set-Location -LiteralPath 'D:\WORK\Project\agent-workflow'
git status --short --branch
git worktree list
git fetch origin
git worktree add .worktrees/pr31-r3 origin/codex/pr31-r3-corrections
Set-Location -LiteralPath '.worktrees/pr31-r3'
git rev-parse HEAD
```

2. Confirm Python 3.11+, Docker Desktop Linux engine is running, and a **previously cached trusted** Python Linux image is available. Use the same opt-in fixture image that Codex used for its earlier S01–S08 conformance run, without pulling a new image. Avoid entering any secrets in terminal/chat.

```powershell
python --version
docker info --format '{{.ID}} {{.OSType}}'
docker context show
$env:WFC_SANDBOX_TEST_IMAGE = '<YOUR_EXISTING_CACHED_PYTHON_IMAGE>'
docker image inspect $env:WFC_SANDBOX_TEST_IMAGE --format '{{.Id}}'
```

3. Use a disposable Python environment containing this repo and pytest (if required: `python -m pip install -e ".[test]"`). Capture deterministic test output:

```powershell
New-Item -ItemType Directory -Force -Path '.runtime/pr31-r3-evidence' | Out-Null
python -m pytest -q -rs -p no:cacheprovider tests/test_universal_sandbox.py tests/test_runtime_plugins.py tests/test_architecture_boundary.py 2>&1 | Tee-Object -FilePath '.runtime/pr31-r3-evidence/pytest-docker.log'
$code = $LASTEXITCODE
"PYTEST_EXIT_CODE=$code"
git rev-parse HEAD
docker ps -a --filter 'name=my-wfc-sandbox-'
```

Expected: all tests PASS (including opt-in S01–S08 and new Controller-death watchdog test), no unexpected owned containers, `PYTEST_EXIT_CODE=0`. A failed/blocked run is evidence, not an instruction to bypass guards. If there are unexpected leftover containers, preserve output and report it; do **not** run global cleanup commands.

4. Send ChatGPT Tech Lead these three items: full `git rev-parse HEAD`, `PYTEST_EXIT_CODE` and the final pytest summary with any traceback (or attach `.runtime/pr31-r3-evidence/pytest-docker.log`). Include Docker engine type and whether the watchdog crash test passed. No tokens, config secrets, `.codex` files, Docker auth contents or full environment dumps.

## Before future merge

Obtain independent review of the new exact SHA and resolve outstanding hard writable-workspace quota, terminal-rejected Docker-create recovery, host watchdog reliability/target Linux and any environment/provenance gaps. A green GitHub CI job alone is **not** proof of these security requirements.
