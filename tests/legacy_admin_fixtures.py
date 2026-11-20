"""Test-only compatibility helpers for legacy SQLite WorkflowService regressions.

These helpers are not CLI entrypoints. New operator commands use PostgreSQL.
"""

import json

from agent_workflow.admin import (
    BASE,
    REPO,
    ROUND1,
    ROUND2,
    historical_spec,
    synthetic_spec,
)
from agent_workflow.domain.service import encode
from agent_workflow.integrations.github import WorkflowError


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
            (
                1,
                ROUND1,
                "CHANGES_REQUESTED",
                {},
                "F-001",
                "AC3",
                "Dynamic subclass dispatch could bypass domain validation.",
                "Call the base domain validator directly.",
            ),
            (
                2,
                ROUND2,
                "ESCALATED",
                {"F-001": "FIXED"},
                "F-002",
                "AC2",
                "Subclass-defined equality can bypass request conflict detection.",
                "Not provided in historical evidence.",
            ),
        ]
        for number, sha, verdict, resolutions, fid, ac, description, fix in records:
            rid = f"historical-TASK-001-{number}"
            db.insert_review(
                (
                    rid,
                    "TASK-001",
                    number,
                    BASE,
                    sha,
                    verdict,
                    description,
                    "high",
                    None,
                    "unverified",
                    None,
                    None,
                    imported,
                    encode(resolutions),
                )
            )
            engine.insert_findings(
                db,
                rid,
                "TASK-001",
                [
                    dict(
                        finding_id=fid,
                        severity="medium",
                        acceptance_criterion=ac,
                        file_path="src/order_service/service.py",
                        line=None,
                        description=description,
                        suggested_correction=fix,
                        required_verification="Not provided in historical evidence.",
                    )
                ],
            )
            db.import_historical_head((sha, number))
            task["current_head_sha"] = sha
            engine.event(
                db,
                task,
                verdict,
                "HISTORICAL_IMPORT",
                {
                    "round": number,
                    "original_timestamp": None,
                    "original_reviewer": None,
                },
            )
            task["status"] = verdict


def seed_synthetic(engine):
    spec = synthetic_spec()
    engine.provision_task(spec)
    task = engine.get_review_context(spec["task_id"])
    if task["status"] == "AWAITING_HANDOFF":
        engine.register_task_handoff(
            spec["task_id"],
            REPO,
            "poc/review-baseline",
            BASE,
            BASE,
            "docs/handoff.md",
            "SYNTHETIC fixture registration only; no test execution claimed by this seed.",
        )


def reset_synthetic(engine):
    with engine.transaction() as db:
        task = engine.task(db, "POC-REVIEW-001")
        if not json.loads(task["metadata"])["synthetic"]:
            raise WorkflowError("refusing to reset real history")
        for table in (
            "review_findings",
            "reviews",
            "review_attempts",
            "task_events",
            "tasks",
        ):
            db.delete_synthetic_history((table, "POC-REVIEW-001"))
