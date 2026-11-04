"""SQLite transactions own all workflow transitions; no AI review is performed here."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from my_wfc.budget import check_deadline, operation_timeout
from my_wfc.contracts import Submission, TaskSpec
from my_wfc.github import WorkflowError, validate_sha


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class WorkflowService:
    def __init__(self, db_path, verify_remote, *, clock=time.time, lease_seconds=900,
                 allowed_repositories=None, effort_verifier=None,
                 efforts=("instant", "medium", "high")):
        self.db_path = str(db_path)
        self.verify_remote = verify_remote
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.allowed = set(allowed_repositories or {"builacviet/techlead-review-poc"})
        self.effort_verifier = effort_verifier  # trusted server-side adapter only
        self.efforts = efforts
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise WorkflowError("unsupported database schema version")
            db.executescript(Path(__file__).with_name("schema.sql").read_text())

    @contextmanager
    def connection(self):
        db = sqlite3.connect(
            self.db_path, timeout=operation_timeout(30), isolation_level=None
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                check_deadline()
                db.execute(
                    "PRAGMA busy_timeout=%d" % int(operation_timeout(30) * 1000)
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

    def task(self, db, task_id):
        task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None:
            raise WorkflowError("unknown authorized task")
        return dict(task)

    def event(self, db, task, next_status, action, metadata=None):
        now = self.clock()
        db.execute("INSERT INTO task_events(task_id,previous_status,next_status,action,sha,timestamp,metadata) VALUES(?,?,?,?,?,?,?)",
                   (task["task_id"], task.get("status"), next_status, action,
                    task.get("current_head_sha"), now, encode(metadata or {})))
        db.execute("UPDATE tasks SET status=?,updated_at=? WHERE task_id=?",
                   (next_status, now, task["task_id"]))

    def provision_task(self, spec: dict):
        """Local operator-only authorization entry point; deliberately not an MCP tool."""
        spec = TaskSpec.model_validate(spec).model_dump()
        if spec["repository"] not in self.allowed or spec["requested_effort"] not in self.efforts:
            raise WorkflowError("unauthorized repository or unsupported effort")
        validate_sha(spec["base_sha"])
        if any(not path or path.startswith(("/", "\\")) or ".." in path.split("/") or ":" in path
               for path in spec["context_paths"]):
            raise WorkflowError("context paths must be repository-relative")
        with self.transaction() as db:
            existing = db.execute("SELECT metadata FROM tasks WHERE task_id=?", (spec["task_id"],)).fetchone()
            if existing:
                if existing[0] != encode(spec):
                    raise WorkflowError("task authorization conflicts with stored metadata")
                return
            now = self.clock()
            db.execute("INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,requested_effort,require_verified_effort,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (spec["task_id"], encode(spec), spec["repository"], spec["branch"], spec["base_sha"],
                        spec["requested_effort"], int(spec["require_verified_effort"]), "AWAITING_HANDOFF", now, now))
            self.event(db, {"task_id": spec["task_id"]}, "AWAITING_HANDOFF", "LOCAL_AUTHORIZATION")

    def register_task_handoff(self, task_id, repository, branch, base_sha, new_head_sha,
                              handoff_file_path, test_summary):
        validate_sha(new_head_sha)
        if not isinstance(test_summary, str) or not test_summary.strip():
            raise WorkflowError("test summary required")
        with self.transaction() as db:
            task = self.task(db, task_id)
            spec = json.loads(task["metadata"])
            if (repository, branch, base_sha) != (task["repository"], task["branch"], task["base_sha"]):
                raise WorkflowError("handoff differs from authorized task")
            if handoff_file_path not in spec["context_paths"]:
                raise WorkflowError("handoff path is not authorized")
            if task["status"] not in {"AWAITING_HANDOFF", "CHANGES_REQUESTED", "READY_IN_REVIEW"} or task["completed_review_rounds"] >= 2:
                raise WorkflowError("handoff is not permitted in this state")
            self.verify_remote(repository, branch, base_sha, new_head_sha)
            if task["status"] == "READY_IN_REVIEW":
                if new_head_sha != task["current_head_sha"]:
                    raise WorkflowError("pending handoff cannot be replaced")
                return {"task_id": task_id, "status": "READY_IN_REVIEW", "head_sha": new_head_sha}
            if task["status"] == "CHANGES_REQUESTED" and new_head_sha == task["current_head_sha"]:
                raise WorkflowError("correction requires a new pushed SHA")
            db.execute("UPDATE tasks SET current_head_sha=? WHERE task_id=?", (new_head_sha, task_id))
            task["current_head_sha"] = new_head_sha
            self.event(db, task, "READY_IN_REVIEW", "HANDOFF", {"handoff_file_path": handoff_file_path, "test_summary": test_summary})
            return {"task_id": task_id, "status": "READY_IN_REVIEW", "head_sha": new_head_sha}

    def recover_expired(self, db, task):
        active = db.execute("SELECT * FROM review_attempts WHERE task_id=? AND status='ACTIVE'", (task["task_id"],)).fetchone()
        if active and active["expires_at"] <= self.clock():
            db.execute("UPDATE review_attempts SET status='EXPIRED' WHERE token=?", (active["token"],))
            self.event(db, task, "READY_IN_REVIEW", "LEASE_EXPIRED")
            task["status"] = "READY_IN_REVIEW"

    def list_review_queue(self, requested_effort=None):
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM tasks WHERE status='IN_REVIEW'").fetchall():
                self.recover_expired(db, dict(row))
            rows = db.execute("SELECT * FROM tasks WHERE status='READY_IN_REVIEW' ORDER BY task_id").fetchall()
            return [{"task_id": t["task_id"], "title": json.loads(t["metadata"])["title"],
                     "repository": t["repository"], "branch": t["branch"], "head_sha": t["current_head_sha"],
                     "requested_effort": t["requested_effort"], "review_round": t["completed_review_rounds"] + 1}
                    for t in rows if requested_effort is None or t["requested_effort"] == requested_effort]

    def claim_review(self, task_id, expected_head_sha, reviewer):
        with self.transaction() as db:
            task = self.task(db, task_id)
            self.recover_expired(db, task)
            if task["status"] != "READY_IN_REVIEW" or task["completed_review_rounds"] >= 2:
                raise WorkflowError("task is not reviewable")
            if expected_head_sha != task["current_head_sha"]:
                raise WorkflowError("stale claim SHA")
            self.verify_remote(task["repository"], task["branch"], task["base_sha"], expected_head_sha)
            token = uuid.uuid4().hex
            now = self.clock()
            expires = now + self.lease_seconds
            db.execute("INSERT INTO review_attempts(token,task_id,expected_sha,reviewer,claimed_at,expires_at,status) VALUES(?,?,?,?,?,?,'ACTIVE')",
                       (token, task_id, expected_head_sha, reviewer, now, expires))
            self.event(db, task, "IN_REVIEW", "CLAIM")
            return {"claim_token": token, "task_id": task_id, "reviewed_sha": expected_head_sha,
                    "review_round": task["completed_review_rounds"] + 1, "lease_expiration": expires}

    def history(self, db, task_id):
        reviews = []
        for row in db.execute("SELECT * FROM reviews WHERE task_id=? ORDER BY round_number", (task_id,)):
            review = dict(row)
            review["prior_resolutions"] = json.loads(review["prior_resolutions"])
            review["findings"] = [dict(f) for f in db.execute("SELECT * FROM review_findings WHERE review_id=? ORDER BY finding_id", (row["review_id"],))]
            reviews.append(review)
        return reviews

    def get_review_context(self, task_id):
        with self.connection() as db:
            db.execute("BEGIN")  # consistent read snapshot
            task = self.task(db, task_id)
            history = self.history(db, task_id)
            spec = json.loads(task.pop("metadata"))
            result = {**spec, **task, "review_round": min(task["completed_review_rounds"] + 1, 2),
                      "rounds_remaining": 2 - task["completed_review_rounds"], "previous_reviews": history}
            previous = history[-1] if history else None
            result["previous_reviewed_sha"] = previous["reviewed_head_sha"] if previous else None
            result["previous_verdict"] = previous["verdict"] if previous else None
            root = f"https://github.com/{task['repository']}"
            head = task["current_head_sha"]
            result["github_references"] = {
                "complete_diff": f"{root}/compare/{task['base_sha']}...{head}" if head else None,
                "correction_diff": f"{root}/compare/{previous['reviewed_head_sha']}...{head}" if previous and head else None,
                "context_files": {p: f"{root}/blob/{head}/{quote(p, safe='/')}" for p in spec["context_paths"]} if head else {},
            }
            result["events"] = [dict(row) for row in db.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY event_id", (task_id,))]
            return result

    def submit_review(self, payload: dict, reviewer: str):
        submission = Submission.model_validate(payload)
        data = submission.model_dump()
        canonical = encode(data)
        with self.transaction() as db:
            task = self.task(db, data["task_id"])
            attempt = db.execute("SELECT * FROM review_attempts WHERE token=? AND task_id=?",
                                 (data["claim_token"], data["task_id"])).fetchone()
            if not attempt or attempt["reviewer"] != reviewer:
                raise WorkflowError("unknown claim or claim owner mismatch")
            if attempt["status"] == "SUBMITTED":
                if attempt["submitted_payload"] != canonical:
                    raise WorkflowError("conflicting duplicate submission")
                if data["verdict"] == "APPROVED":
                    self.verify_remote(task["repository"], task["branch"], task["base_sha"], data["reviewed_head_sha"])
                return {"task_id": task["task_id"], "duplicate": True, "reviewed_head_sha": data["reviewed_head_sha"], "verdict": data["verdict"]}
            if attempt["status"] != "ACTIVE" or attempt["expires_at"] <= self.clock():
                raise WorkflowError("claim expired; reclaim without consuming a round")
            if task["status"] != "IN_REVIEW" or task["completed_review_rounds"] >= 2:
                raise WorkflowError("task is not in an active review")
            if data["reviewed_head_sha"] != task["current_head_sha"] or data["reviewed_head_sha"] != attempt["expected_sha"]:
                raise WorkflowError("stale submission SHA")
            round_number = task["completed_review_rounds"] + 1
            if data["review_round"] != round_number or data["requested_effort"] != task["requested_effort"]:
                raise WorkflowError("round or requested effort mismatch")
            history = self.history(db, task["task_id"])
            prior = {f["finding_id"] for r in history for f in r["findings"]}
            if set(data["prior_resolutions"]) != prior:
                raise WorkflowError("all prior findings require explicit resolution")
            new_ids = [f["finding_id"] for f in data["findings"]]
            if len(set(new_ids)) != len(new_ids) or prior.intersection(new_ids):
                raise WorkflowError("finding identities must be new and unique")
            expected_failure = "CHANGES_REQUESTED" if round_number == 1 else "ESCALATED"
            if data["verdict"] not in {"APPROVED", expected_failure}:
                raise WorkflowError("verdict violates two-round policy")
            if data["verdict"] == "APPROVED" and (data["findings"] or any(v != "FIXED" for v in data["prior_resolutions"].values())):
                raise WorkflowError("approval requires no new findings and all prior findings fixed")
            if data["verdict"] != "APPROVED" and not data["findings"] and not any(v != "FIXED" for v in data["prior_resolutions"].values()):
                raise WorkflowError("failure verdict requires a code finding; operational limitations are not verdicts")
            actual, evidence = None, "unverified"
            if self.effort_verifier:
                verified = self.effort_verifier(reviewer, task, data)
                if verified:
                    actual, evidence = verified
            if data["verdict"] == "APPROVED" and task["require_verified_effort"] and (actual != task["requested_effort"] or not evidence or evidence == "unverified"):
                raise WorkflowError("required effort cannot be verified; approval blocked without consuming a round")
            # Verify all submissions, especially approval, immediately before writes.
            self.verify_remote(task["repository"], task["branch"], task["base_sha"], data["reviewed_head_sha"])
            review_id = uuid.uuid4().hex
            db.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (review_id, task["task_id"], round_number, task["base_sha"], data["reviewed_head_sha"], data["verdict"],
                        data["summary"], task["requested_effort"], actual, evidence, reviewer, self.clock(), None, encode(data["prior_resolutions"])))
            self.insert_findings(db, review_id, task["task_id"], data["findings"])
            db.execute("UPDATE tasks SET completed_review_rounds=? WHERE task_id=?", (round_number, task["task_id"]))
            db.execute("UPDATE review_attempts SET status='SUBMITTED',submitted_payload=? WHERE token=?", (canonical, data["claim_token"]))
            self.event(db, task, data["verdict"], "REVIEW_SUBMITTED", {"review_id": review_id, "round": round_number})
            return {"task_id": task["task_id"], "review_id": review_id, "verdict": data["verdict"],
                    "review_round": round_number, "reviewed_head_sha": data["reviewed_head_sha"], "actual_effort": actual}

    def insert_findings(self, db, review_id, task_id, findings):
        for f in findings:
            db.execute("INSERT INTO review_findings VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (review_id, f["finding_id"], task_id, f["severity"], f["acceptance_criterion"], f["file_path"],
                        f.get("line"), f["description"], f["suggested_correction"], f["required_verification"]))

    def get_review_result(self, task_id):
        context = self.get_review_context(task_id)
        history = context["previous_reviews"]
        context["latest_reviewed_sha"] = context["previous_reviewed_sha"]
        context["actual_effort"] = history[-1]["actual_effort"] if history else None
        context["escalation_reason"] = history[-1]["summary"] if context["status"] == "ESCALATED" else None
        context["approval_current"] = None
        if context["status"] == "APPROVED":
            try:
                self.verify_remote(context["repository"], context["branch"], context["base_sha"], context["latest_reviewed_sha"])
                context["approval_current"] = True
            except WorkflowError:
                context["approval_current"] = False
                context["approval_warning"] = "approval is stale or remote verification unavailable; do not merge"
        return context

    def get_coder_assignment(self, task_id):
        context = self.get_review_context(task_id)
        resolutions = {k: v for r in context["previous_reviews"] for k, v in r["prior_resolutions"].items()}
        context["unresolved_findings"] = [{**f, "resolution_status": resolutions.get(f["finding_id"], "NOT_VERIFIED")}
                                          for r in context["previous_reviews"] for f in r["findings"]
                                          if resolutions.get(f["finding_id"]) != "FIXED"]
        return context
