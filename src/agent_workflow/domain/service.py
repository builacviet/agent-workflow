"""SQLite transactions own all workflow transitions; no AI review is performed here."""

import json
import time
import uuid
from urllib.parse import quote

from agent_workflow.domain.contracts import Submission, TaskSpec
from agent_workflow.integrations.github import (
    REPOSITORY,
    WorkflowError,
    canonical_repository,
    validate_sha,
)
from agent_workflow.paths import database_path
from agent_workflow.storage.repository import SQLiteWorkflowRepository, WorkflowRepository


def default_service() -> "WorkflowService":
    return WorkflowService(database_path())


def encode(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class WorkflowService:
    def __init__(
        self,
        db_path=None,
        *,
        repository: WorkflowRepository | None = None,
        clock=time.time,
        lease_seconds=900,
        allowed_repositories=None,
        effort_verifier=None,
        efforts=("instant", "medium", "high"),
    ):
        self.db_path = str(db_path)
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.allowed = {
            canonical_repository(name)
            for name in (allowed_repositories or {REPOSITORY})
        }
        self.effort_verifier = effort_verifier
        self.efforts = efforts
        self.repository = repository if repository is not None else SQLiteWorkflowRepository(db_path)

    def transaction(self):
        return self.repository.transaction()

    def task(self, db, task_id):
        task = db.find_task((task_id,))
        if task is None:
            raise WorkflowError("unknown authorized task")
        return dict(task)

    def event(self, db, task, next_status, action, metadata=None):
        now = self.clock()
        db.insert_event(
            (
                task["task_id"],
                task.get("status"),
                next_status,
                action,
                task.get("current_head_sha"),
                now,
                encode(metadata or {}),
            )
        )
        db.set_status((next_status, now, task["task_id"]))

    def provision_task(self, spec: dict):
        """Local operator-only authorization entry point; deliberately not an MCP tool."""
        spec = TaskSpec.model_validate(spec).model_dump()
        spec["repository"] = canonical_repository(spec["repository"])
        if (
            spec["repository"] not in self.allowed
            or spec["requested_effort"] not in self.efforts
        ):
            raise WorkflowError(
                "unauthorized repository or unsupported effort"
            )
        validate_sha(spec["base_sha"])
        if any(
            (
                not path
                or path.startswith(("/", "\\"))
                or ".." in path.split("/")
                or (":" in path)
                for path in spec["context_paths"]
            )
        ):
            raise WorkflowError("context paths must be repository-relative")
        with self.transaction() as db:
            existing = db.find_authorization((spec["task_id"],))
            if existing:
                stored_spec = json.loads(existing["metadata"])
                stored_spec["repository"] = canonical_repository(
                    stored_spec["repository"]
                )
                if encode(stored_spec) != encode(spec):
                    raise WorkflowError(
                        "task authorization conflicts with stored metadata"
                    )
                return
            now = self.clock()
            db.insert_task(
                (
                    spec["task_id"],
                    encode(spec),
                    spec["repository"],
                    spec["branch"],
                    spec["base_sha"],
                    spec["requested_effort"],
                    int(spec["require_verified_effort"]),
                    "AWAITING_HANDOFF",
                    now,
                    now,
                )
            )
            self.event(
                db,
                {"task_id": spec["task_id"]},
                "AWAITING_HANDOFF",
                "LOCAL_AUTHORIZATION",
            )

    def register_task_handoff(
        self,
        task_id,
        repository,
        branch,
        base_sha,
        new_head_sha,
        handoff_file_path,
        test_summary,
    ):
        validate_sha(new_head_sha)
        if not isinstance(test_summary, str) or not test_summary.strip():
            raise WorkflowError("test summary required")
        with self.transaction() as db:
            task = self.task(db, task_id)
            spec = json.loads(task["metadata"])
            if (canonical_repository(repository), branch, base_sha) != (
                canonical_repository(task["repository"]),
                task["branch"],
                task["base_sha"],
            ):
                raise WorkflowError("handoff differs from authorized task")
            if handoff_file_path not in spec["context_paths"]:
                raise WorkflowError("handoff path is not authorized")
            if (
                task["status"]
                not in {
                    "AWAITING_HANDOFF",
                    "CHANGES_REQUESTED",
                    "READY_IN_REVIEW",
                }
                or task["completed_review_rounds"] >= 2
            ):
                raise WorkflowError("handoff is not permitted in this state")
            if task["status"] == "READY_IN_REVIEW":
                if new_head_sha != task["current_head_sha"]:
                    raise WorkflowError("pending handoff cannot be replaced")
                return {
                    "task_id": task_id,
                    "status": "READY_IN_REVIEW",
                    "head_sha": new_head_sha,
                }
            if (
                task["status"] == "CHANGES_REQUESTED"
                and new_head_sha == task["current_head_sha"]
            ):
                raise WorkflowError("correction requires a new pushed SHA")
            db.set_head((new_head_sha, task_id))
            task["current_head_sha"] = new_head_sha
            self.event(
                db,
                task,
                "READY_IN_REVIEW",
                "HANDOFF",
                {
                    "handoff_file_path": handoff_file_path,
                    "test_summary": test_summary,
                    "verification": "reporter_supplied_not_remotely_verified",
                },
            )
            return {
                "task_id": task_id,
                "status": "READY_IN_REVIEW",
                "head_sha": new_head_sha,
            }

    def recover_expired(self, db, task):
        active = db.find_active_claim((task["task_id"],))
        if active and active["expires_at"] <= self.clock():
            db.expire_claim((active["token"],))
            self.event(db, task, "READY_IN_REVIEW", "LEASE_EXPIRED")
            task["status"] = "READY_IN_REVIEW"

    def list_review_queue(self, requested_effort=None):
        with self.transaction() as db:
            for row in db.list_claimed_tasks():
                self.recover_expired(db, dict(row))
            rows = db.list_ready_tasks()
            return [
                {
                    "task_id": t["task_id"],
                    "title": json.loads(t["metadata"])["title"],
                    "repository": canonical_repository(t["repository"]),
                    "branch": t["branch"],
                    "head_sha": t["current_head_sha"],
                    "requested_effort": t["requested_effort"],
                    "review_round": t["completed_review_rounds"] + 1,
                }
                for t in rows
                if requested_effort is None
                or t["requested_effort"] == requested_effort
            ]

    def claim_review(self, task_id, expected_head_sha, reviewer):
        with self.transaction() as db:
            task = self.task(db, task_id)
            self.recover_expired(db, task)
            if (
                task["status"] != "READY_IN_REVIEW"
                or task["completed_review_rounds"] >= 2
            ):
                raise WorkflowError("task is not reviewable")
            if expected_head_sha != task["current_head_sha"]:
                raise WorkflowError("stale claim SHA")
            token = uuid.uuid4().hex
            now = self.clock()
            expires = now + self.lease_seconds
            db.insert_claim(
                (token, task_id, expected_head_sha, reviewer, now, expires)
            )
            self.event(db, task, "IN_REVIEW", "CLAIM")
            return {
                "claim_token": token,
                "task_id": task_id,
                "reviewed_sha": expected_head_sha,
                "review_round": task["completed_review_rounds"] + 1,
                "lease_expiration": expires,
            }

    def history(self, db, task_id):
        reviews = []
        for row in db.list_reviews((task_id,)):
            review = dict(row)
            review["prior_resolutions"] = json.loads(
                review["prior_resolutions"]
            )
            review["findings"] = [
                dict(f) for f in db.list_findings((row["review_id"],))
            ]
            reviews.append(review)
        return reviews

    def get_review_context(self, task_id):
        with self.repository.read() as db:
            task = self.task(db, task_id)
            history = self.history(db, task_id)
            spec = json.loads(task.pop("metadata"))
            result = {
                **spec,
                **task,
                "repository": canonical_repository(task["repository"]),
                "review_round": min(task["completed_review_rounds"] + 1, 2),
                "rounds_remaining": 2 - task["completed_review_rounds"],
                "previous_reviews": history,
            }
            previous = history[-1] if history else None
            result["previous_reviewed_sha"] = (
                previous["reviewed_head_sha"] if previous else None
            )
            result["previous_verdict"] = (
                previous["verdict"] if previous else None
            )
            root = f"https://github.com/{result['repository']}"
            head = task["current_head_sha"]
            result["github_references"] = {
                "complete_diff": f"{root}/compare/{task['base_sha']}...{head}"
                if head
                else None,
                "correction_diff": f"{root}/compare/{previous['reviewed_head_sha']}...{head}"
                if previous and head
                else None,
                "context_files": {
                    p: f"{root}/blob/{head}/{quote(p, safe='/')}"
                    for p in spec["context_paths"]
                }
                if head
                else {},
            }
            result["events"] = [
                dict(row) for row in db.list_events((task_id,))
            ]
            return result

    def submit_review(self, payload: dict, reviewer: str):
        submission = Submission.model_validate(payload)
        data = submission.model_dump()
        canonical = encode(data)
        with self.transaction() as db:
            task = self.task(db, data["task_id"])
            attempt = db.find_claim((data["claim_token"], data["task_id"]))
            if not attempt or attempt["reviewer"] != reviewer:
                raise WorkflowError("unknown claim or claim owner mismatch")
            if attempt["status"] == "SUBMITTED":
                if attempt["submitted_payload"] != canonical:
                    raise WorkflowError("conflicting duplicate submission")
                return {
                    "task_id": task["task_id"],
                    "duplicate": True,
                    "reviewed_head_sha": data["reviewed_head_sha"],
                    "verdict": data["verdict"],
                }
            if (
                attempt["status"] != "ACTIVE"
                or attempt["expires_at"] <= self.clock()
            ):
                raise WorkflowError(
                    "claim expired; reclaim without consuming a round"
                )
            if (
                task["status"] != "IN_REVIEW"
                or task["completed_review_rounds"] >= 2
            ):
                raise WorkflowError("task is not in an active review")
            if (
                data["reviewed_head_sha"] != task["current_head_sha"]
                or data["reviewed_head_sha"] != attempt["expected_sha"]
            ):
                raise WorkflowError("stale submission SHA")
            round_number = task["completed_review_rounds"] + 1
            if (
                data["review_round"] != round_number
                or data["requested_effort"] != task["requested_effort"]
            ):
                raise WorkflowError("round or requested effort mismatch")
            history = self.history(db, task["task_id"])
            prior = {f["finding_id"] for r in history for f in r["findings"]}
            if set(data["prior_resolutions"]) != prior:
                raise WorkflowError(
                    "all prior findings require explicit resolution"
                )
            new_ids = [f["finding_id"] for f in data["findings"]]
            if len(set(new_ids)) != len(new_ids) or prior.intersection(
                new_ids
            ):
                raise WorkflowError(
                    "finding identities must be new and unique"
                )
            expected_failure = (
                "CHANGES_REQUESTED" if round_number == 1 else "ESCALATED"
            )
            if data["verdict"] not in {"APPROVED", expected_failure}:
                raise WorkflowError("verdict violates two-round policy")
            if data["verdict"] == "APPROVED" and (
                data["findings"]
                or any(
                    (v != "FIXED" for v in data["prior_resolutions"].values())
                )
            ):
                raise WorkflowError(
                    "approval requires no new findings and all prior findings fixed"
                )
            if (
                data["verdict"] != "APPROVED"
                and (not data["findings"])
                and (
                    not any(
                        (
                            v != "FIXED"
                            for v in data["prior_resolutions"].values()
                        )
                    )
                )
            ):
                raise WorkflowError(
                    "failure verdict requires a code finding; operational limitations are not verdicts"
                )
            actual, evidence = (None, "unverified")
            if self.effort_verifier:
                verified = self.effort_verifier(reviewer, task, data)
                if verified:
                    actual, evidence = verified
            if (
                data["verdict"] == "APPROVED"
                and task["require_verified_effort"]
                and (
                    actual != task["requested_effort"]
                    or not evidence
                    or evidence == "unverified"
                )
            ):
                raise WorkflowError(
                    "required effort cannot be verified; approval blocked without consuming a round"
                )
            review_id = uuid.uuid4().hex
            db.insert_review(
                (
                    review_id,
                    task["task_id"],
                    round_number,
                    task["base_sha"],
                    data["reviewed_head_sha"],
                    data["verdict"],
                    data["summary"],
                    task["requested_effort"],
                    actual,
                    evidence,
                    reviewer,
                    self.clock(),
                    None,
                    encode(data["prior_resolutions"]),
                )
            )
            self.insert_findings(
                db, review_id, task["task_id"], data["findings"]
            )
            db.set_rounds((round_number, task["task_id"]))
            db.submit_claim((canonical, data["claim_token"]))
            self.event(
                db,
                task,
                data["verdict"],
                "REVIEW_SUBMITTED",
                {"review_id": review_id, "round": round_number},
            )
            return {
                "task_id": task["task_id"],
                "review_id": review_id,
                "verdict": data["verdict"],
                "review_round": round_number,
                "reviewed_head_sha": data["reviewed_head_sha"],
                "actual_effort": actual,
            }

    def insert_findings(self, db, review_id, task_id, findings):
        for f in findings:
            db.insert_finding(
                (
                    review_id,
                    f["finding_id"],
                    task_id,
                    f["severity"],
                    f["acceptance_criterion"],
                    f["file_path"],
                    f.get("line"),
                    f["description"],
                    f["suggested_correction"],
                    f["required_verification"],
                )
            )

    def get_review_result(self, task_id):
        context = self.get_review_context(task_id)
        history = context["previous_reviews"]
        context["latest_reviewed_sha"] = context["previous_reviewed_sha"]
        context["actual_effort"] = (
            history[-1]["actual_effort"] if history else None
        )
        context["escalation_reason"] = (
            history[-1]["summary"]
            if context["status"] == "ESCALATED"
            else None
        )
        context["approval_current"] = None
        context["remote_verification"] = "not_performed_by_wfc"
        if context["status"] == "APPROVED":
            context["approval_warning"] = (
                "Recorded approval covers only the stored SHA; Tech Lead must verify GitHub freshness before merge"
            )
        return context

    def get_coder_assignment(self, task_id):
        context = self.get_review_context(task_id)
        resolutions = {
            k: v
            for r in context["previous_reviews"]
            for k, v in r["prior_resolutions"].items()
        }
        context["unresolved_findings"] = [
            {
                **f,
                "resolution_status": resolutions.get(
                    f["finding_id"], "NOT_VERIFIED"
                ),
            }
            for r in context["previous_reviews"]
            for f in r["findings"]
            if resolutions.get(f["finding_id"]) != "FIXED"
        ]
        return context
