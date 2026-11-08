"""Explicit local-only provision/import/reset commands; no automatic remote dispatch."""
import argparse
import json
from pathlib import Path
from importlib.resources import files

from agent_workflow.github import REPOSITORY as REPO, WorkflowError
from agent_workflow.service import encode
from agent_workflow.tools import default_service

BASE = "015ccc535b85ce1d0ad00da787b827970b9bf39a"
ROUND1 = "6352c8ff5ea83450a93cd1077d6447170c9dedaa"
ROUND2 = "ff1458fb98dec3a321ca9b96ed8723e40ba882a9"
PATHS = ["AGENTS.md", "docs/architecture.md", "docs/handoff.md", "docs/review-policy.md", "docs/tasks/TASK-001.md"]


def historical_spec():
    text = files("agent_workflow").joinpath("resources/TASK-001-reference.md").read_text(encoding="utf-8-sig")
    ac = text.split("## Acceptance criteria\n", 1)[1].split("## Edge cases", 1)[0]
    return dict(task_id="TASK-001", title="Add idempotent order creation", description=text,
                acceptance_criteria=[line for line in ac.splitlines() if line and line[0].isdigit()],
                definition_of_done=text.split("## Definition of Done", 1)[1].strip(), repository=REPO,
                branch="task/TASK-001", base_sha=BASE, requested_effort="high", risk_level="high",
                require_verified_effort=True, context_paths=PATHS, synthetic=False)


def import_history(engine):
    engine.provision_task(historical_spec())
    with engine.transaction() as db:
        task = engine.task(db, "TASK-001")
        if task["status"] == "ESCALATED" and task["completed_review_rounds"] == 2:
            return
        if task["status"] != "AWAITING_HANDOFF" or task["completed_review_rounds"]:
            raise WorkflowError("refusing to overwrite real task history")
        imported = engine.clock()
        records = [
            (1, ROUND1, "CHANGES_REQUESTED", {}, "F-001", "AC3", "Dynamic subclass dispatch could bypass domain validation.", "Call the base domain validator directly."),
            (2, ROUND2, "ESCALATED", {"F-001": "FIXED"}, "F-002", "AC2", "Subclass-defined equality can bypass request conflict detection.", "Not provided in historical evidence."),
        ]
        for number, sha, verdict, resolutions, fid, ac, description, fix in records:
            rid = f"historical-TASK-001-{number}"
            db.insert_review(
                       (rid, "TASK-001", number, BASE, sha, verdict, description, "high", None,
                        "unverified", None, None, imported, encode(resolutions)))
            engine.insert_findings(db, rid, "TASK-001", [dict(finding_id=fid, severity="medium",
                acceptance_criterion=ac, file_path="src/order_service/service.py", line=None,
                description=description, suggested_correction=fix,
                required_verification="Not provided in historical evidence.")])
            db.import_historical_head((sha, number))
            task["current_head_sha"] = sha
            engine.event(db, task, verdict, "HISTORICAL_IMPORT", {"round": number, "original_timestamp": None, "original_reviewer": None})
            task["status"] = verdict


def seed_synthetic(engine):
    spec = dict(task_id="POC-REVIEW-001", title="SYNTHETIC POC-only baseline inspection",
                description="Disposable metadata exercise using a real baseline commit; not a real implementation assignment.",
                acceptance_criteria=["Inspect the baseline order domain and confirm documented Decimal validation."],
                definition_of_done="Record a synthetic review; do not modify code or merge.", repository=REPO,
                branch="poc/review-baseline", base_sha=BASE, requested_effort="medium", risk_level="low",
                require_verified_effort=False, context_paths=PATHS, synthetic=True)
    engine.provision_task(spec)
    task = engine.get_review_context(spec["task_id"])
    if task["status"] == "AWAITING_HANDOFF":
        engine.register_task_handoff(spec["task_id"], REPO, "poc/review-baseline", BASE, BASE, "docs/handoff.md",
                                     "SYNTHETIC fixture registration only; no test execution claimed by this seed.")


def reset_synthetic(engine):
    with engine.transaction() as db:
        task = engine.task(db, "POC-REVIEW-001")
        if not json.loads(task["metadata"])["synthetic"]:
            raise WorkflowError("refusing to reset real history")
        for table in ("review_findings", "reviews", "review_attempts", "task_events", "tasks"):
            db.delete_synthetic_history((table, "POC-REVIEW-001"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed-history", "seed-synthetic", "reset-synthetic", "provision"])
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    engine = default_service()
    if args.action == "seed-history":
        import_history(engine)
    elif args.action == "seed-synthetic":
        seed_synthetic(engine)
    elif args.action == "reset-synthetic":
        reset_synthetic(engine)
        seed_synthetic(engine)
    else:
        if not args.file:
            parser.error("provision requires --file containing authorized TaskSpec JSON")
        engine.provision_task(json.loads(args.file.read_text()))
    print("Local operation completed; no review approval or AI effort claimed.")


if __name__ == "__main__":
    main()
