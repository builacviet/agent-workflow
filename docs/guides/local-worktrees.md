# Local Git worktrees for manual tasks

Use this workflow for Owner-authorized manual development. The Owner’s Windows primary checkout is `D:\WORK\Project\agent-workflow`. On another host, start from the designated primary checkout and use the same relative layout.

Controller-managed dispatch workspaces follow the [Phase 3.1 operator runbook](../phase-3.1.md). For runtime workspace or publish incidents, use the [dispatch recovery guide](dispatch-recovery.md). The Windows path here applies to the Owner’s local development checkout.

## 1. Verify the checkout and base

From the primary checkout in PowerShell, run each command in order and stop on a failure or an unexpected result.

```powershell
Set-Location -LiteralPath 'D:\WORK\Project\agent-workflow'
git status --short --branch
git remote get-url origin
git remote get-url --push origin
git worktree list
```

Confirm that `main` is checked out and clean and that both `origin` URLs refer to `builacviet/agent-workflow` on GitHub. Then fetch and record the full SHA before branching.

```powershell
git fetch origin
$base = git rev-parse --verify 'origin/main^{commit}'
$base
```

If the task explicitly approves a different base, also record that ref and its verified full commit SHA and use it as `$base`. Keep local `main` free of task commits. If it needs updating and is still clean, use `git merge --ff-only origin/main` from `main`. Stop if `main` has local commits or has diverged.

## 2. Create or safely reuse one worktree

Choose a task slug and use it consistently for the directory and branch. The Owner’s checkout ignores `/.worktrees/` through `.git/info/exclude`. In another checkout, verify that the directory is ignored and add `/.worktrees/` to the repository’s local exclude file if needed.

```powershell
$task = 'example-task'
$branch = "codex/$task"
$worktree = Join-Path (Get-Location).Path ".worktrees\$task"
git check-ignore -- ".worktrees/$task"
```

The Owner’s layout places each task at `D:\WORK\Project\agent-workflow\.worktrees\<task>`. Check the registered worktrees, target directory and branch before creating anything. For a new task with an unused path and branch, create the worktree explicitly from the recorded SHA.

```powershell
git worktree add -b $branch -- $worktree $base
```

Reuse a worktree only at the expected path, on the expected branch, with the same recorded base in its history and a clean or explicitly reconciled working state. A shared ancestor alone does not establish the recorded base. If anything conflicts, preserve the existing worktree and resolve the discrepancy before editing. Never delete or reset someone else’s worktree or create a second worktree for the same task.

Before editing, verify the actual path, branch and working state and read the task checkout’s instructions.

```powershell
Set-Location -LiteralPath $worktree
git worktree list
git rev-parse --show-toplevel
git status --short --branch
Get-Content -LiteralPath AGENTS.md
```

Codex automatic Worktree mode can choose an app-configured location. Check its actual path before editing. Use the explicit `git worktree add` workflow for the Owner’s layout. If the expected layout cannot be used safely, leave the active checkout intact and report the blocker and any isolated alternative used.

## 3. Verify and hand off

- Use disposable test configuration and state. Never copy an active database or credentials from the primary checkout.
- Commit on the task branch, push it with `git push -u origin $branch`, and verify that the remote branch points to the tested `git rev-parse HEAD` SHA. Open a Draft PR when requested and verify its head against the pushed SHA. Only the Owner decides whether to merge.
- Use the [handoff template](../templates/handoff.md) and [review policy](../review-policy.md). Record the absolute worktree path, branch, base SHA, head SHA, actual test results, and PR URL when available.
