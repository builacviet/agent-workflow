# Agent Workflow V1 — Visual Architecture & Workflow

> **Design status:** V1 scope is Owner-approved ([PR #22](https://github.com/builacviet/agent-workflow/pull/22), [scope clarification PR #23](https://github.com/builacviet/agent-workflow/pull/23)). Diagrams show the **target V1 flow**, not evidence that all steps are deployed.
>
> **Target host:** Owner's **Ubuntu on WSL2 / Windows**. Linux/systemd/Docker access, credential readiness, WSL2 start/restart behavior and 24/7 availability remain to be verified in **V1-01**; systemd deployment is in **V1-13**.
>
> **Out of V1:** Executor, Podman, Controller-to-Executor IPC, epic/parallel branches, auto-merge.

## 1. System architecture (target V1)

```mermaid
flowchart TB
  OWNER["Owner<br/>Approval authority"]
  PLANNER["ChatGPT<br/>TL Planner"]
  PC["Windows PC<br/>Primary Git checkout + manual worktrees"]

  subgraph UB["Ubuntu WSL2 — proposed Controller host"]
    CT["Trusted Controller<br/>systemd target (V1-13)"]
    DB[("SQLite / WFC<br/>state and gate evidence")]
    MCP["Existing MCP /mcp<br/>9 compatible tools"]
    HOOK["Trusted GitHub Webhook receiver<br/>HMAC verified + durable event<br/>target V1-03, not deployed"]
    DC["Docker CLI<br/>trusted host invocation"]
    ENG["Docker Engine<br/>availability to verify (V1-01)"]

    subgraph JOBS["Isolated workflow stages (Docker)"]
      DEV["Codex CLI<br/>Dev"]
      QC["Codex CLI<br/>QC"]
      REVIEW["Codex CLI<br/>Independent TL Reviewer"]
      WORKER["Trusted Worker<br/>Prepare / Publish + GitHub App"]
    end
  end

  subgraph SERVICES["External systems"]
    GH["GitHub<br/>Task branch / Draft PR / merge"]
    CI["GitHub Actions<br/>exact-HEAD CI"]
    TELE["Telegram<br/>Notifications + Owner gate (V1-12)"]
  end

  PLANNER -->|"Plan + atomic tasks + AC"| CT
  OWNER -->|"Planning and PR approvals"| CT
  PC -.->|"Manual repo maintenance"| GH
  CT <-->|"Workflow state"| DB
  MCP <-->|"Authenticated WFC calls"| DB
  CT -->|"Controlled stages"| DC
  DC --> ENG
  ENG --> DEV
  ENG --> QC
  ENG --> REVIEW
  ENG --> WORKER
  WORKER -->|"Commit / push / Draft PR"| GH
  GH -->|"PR checks"| CI
  GH -->|"Signed CI completion event"| HOOK
  HOOK -->|"Notify pending CI task"| CT
  CT <-->|"Re-query exact HEAD/checks and reconcile"| GH
  CT -->|"Notify + request approval"| TELE
  TELE -->|"Owner-identity-gated decision"| CT
  OWNER -.->|"Approve and perform merge"| GH
```

**Webhook signal:** GitHub emits a signed `workflow_run.completed` notification; the receiver validates and durably records it. Controller must re-query GitHub's **current PR HEAD** and named exact-HEAD checks before advancing. After WSL2 sleep/restart, reconcile in-flight CI tasks to recover missed notifications; do not poll every 60 seconds as the primary mechanism. Webhook/adapter are **not yet implemented**.

**Trust boundary:** Only the **trusted Controller** invokes the existing Docker CLI, and only the **trusted publishing Worker** uses GitHub App credentials. Untrusted Codex stages do not receive GitHub secrets, WFC database, arbitrary host paths or Docker engine access. The host Docker authority is broad and remains an acknowledged risk, **not** proof of production isolation. Diagram edges are design intent, not verified installed integrations.

## 2. Planning — one Owner approval for a complete package

```mermaid
flowchart TD
  START([Planning starts]) --> PLAN["ChatGPT TL Planner<br/>Plan + atomic tasks + AC<br/>QC required/skip + reason"]
  PLAN --> QCQ{"QC required<br/>for any task?"}
  QCQ -->|Yes| MTP["Codex QC<br/>Prepare pre-Dev MTP<br/>traced to AC"]
  QCQ -->|No| REV["Independent Codex TL Reviewer<br/>Review Plan / AC / MTP if needed"]
  MTP --> REV
  REV --> RV{"Review accepted?"}
  RV -->|No: revise/version| FIX["Artifact author repairs<br/>deficient Plan / AC / MTP"]
  FIX --> REV
  RV -->|Unresolved| ESC["ESCALATED<br/>human resolution"]
  RV -->|Yes| PIN["Pin immutable<br/>planning package version/hash"]
  PIN --> APPROVE{"Owner approves<br/>entire package?"}
  APPROVE -->|No| HOLD["STOP / return for revision"]
  APPROVE -->|Yes| READY(["Approved package<br/>tasks may be dispatched sequentially"])
```

Owner **planning approval does not authorize automatic PR merges**. Changed approved content must be reviewed and approved again. Unresolved plan revisions escalate rather than looping indefinitely.

## 3. Execution — sequential atomic PR and exact-SHA gates

```mermaid
flowchart TD
  READY(["Approved package + next atomic task"]) --> DEV["Codex Dev<br/>Scoped change"]
  DEV --> PR["Trusted Worker<br/>Commit / push / Draft PR to main"]
  PR --> CI["GitHub Actions<br/>CI on exact latest PR HEAD"]
  CI --> CIPASS{"Exact-HEAD CI<br/>passed?"}
  CIPASS -->|Failed checks| FIXCI["Dev fixes failing checks<br/>new PR HEAD"]
  FIXCI --> PR
  CIPASS -->|Pending / stale / unknown| BLOCKED["BLOCKED<br/>missing or uncertain evidence<br/>stop and reconcile"]
  CIPASS -->|Yes| TL["Independent Codex TL Reviewer<br/>Code diff + AC + security"]
  TL --> TLPASS{"Code accepted?"}
  TLPASS -->|No, first round| REWORK["Dev corrections<br/>new PR HEAD invalidates evidence"]
  REWORK --> PR
  TLPASS -->|No, second round| ESC["ESCALATED<br/>no silent third round"]
  TLPASS -->|Yes| QCQ{"Task QC policy?"}
  QCQ -->|required| TEST["Codex QC executes<br/>preapproved MTP on reviewed HEAD"]
  TEST --> QCRESULT["Independent TL Reviewer<br/>checks QC artifacts"]
  QCRESULT --> QCPASS{"QC evidence accepted<br/>for exact HEAD?"}
  QCPASS -->|No| HOLD["HOLD<br/>resolve QC findings or escalate<br/>without bypassing code gate"]
  QCPASS -->|Uncertain evidence| BLOCKED
  QCPASS -->|Yes| OWNER
  QCQ -->|skip| OWNER{"Owner explicitly approves<br/>merge for this PR + HEAD?"}
  OWNER -->|No| WAIT["WAIT<br/>no auto merge"]
  OWNER -->|Yes| MERGE["Owner performs merge<br/>on GitHub"]
  MERGE --> VERIFY{"Controller verified<br/>PR merged to main?"}
  VERIFY -->|No / uncertain| BLOCKED
  VERIFY -->|Yes, record merge SHA| NEXT(["Next atomic task"])
```

**Failure semantics:** Infrastructure uncertainty is **BLOCKED** and does not consume a code-review round. Two **failed code reviews** mean **ESCALATED**. A changed PR HEAD invalidates earlier CI/review/QC approvals; fresh checks and applicable review are required. QC failures do not silently authorize a third code-review round. No parallel task dispatch or automatic merge.

## 4. Where to read next

- [V1 Scope & Gate Contract](v1-scope-and-gates.md): authoritative detailed rules.
- [Current Phase 3.1 architecture](architecture.md) and [runbook](phase-3.1.md): what is presently implemented, **not** proof of WSL2 deployment.
- [Notion V1-01 readiness task](https://app.notion.com/412f92014abd812486e2f8a7e3145850): verify the Ubuntu WSL2 environment and startup availability.
- [Notion V1 backlog](https://app.notion.com/p/ad495e0645a74aebb326d29c1818ede7): execution order and Owner gates.
