"""Opt-in Dev/Code dispatch ledger over the shared PostgreSQL schema."""

import re
import time
import uuid
from contextlib import contextmanager
from typing import Callable

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agent_workflow.budget import check_deadline, operation_timeout
from agent_workflow.storage.dispatch_repository import (
    REPOSITORY,
    canonical_repository,
    validate_sha,
)
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_models import (
    AgentDispatch,
    AgentExecutionEvidence,
    Task,
)
from agent_workflow.storage.repository import WorkflowError

_PRE_LAUNCH = "PRE_LAUNCH"
_BLOCKED = "Operator reconciliation required; do not retry automatically"


class DevClaim(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    dispatch_id: str
    task_id: str
    input_sha: str
    deadline: float
    round_number: int


class PostgresDispatchLedger:
    """Host-created ledger. Role and phase are trusted orchestration inputs."""

    def __init__(
        self,
        factory: PostgresFactory,
        *,
        role: str,
        phase: str,
        clock: Callable = time.time,
    ):
        if (role, phase) != ("dev", "code"):
            raise WorkflowError("unsupported dispatch role or phase")
        self.factory, self.role, self.phase, self.clock = factory, role, phase, clock

    @staticmethod
    def _timeout(session: Session) -> None:
        milliseconds = max(1, int(operation_timeout(30) * 1000))
        session.connection().execute(
            text(
                "SELECT set_config('lock_timeout', :timeout, true), set_config('statement_timeout', :timeout, true)"
            ),
            {"timeout": f"{milliseconds}ms"},
        )

    @contextmanager
    def _transaction(self):
        with self.factory.transaction() as connection:
            with Session(bind=connection) as session:
                self._timeout(session)
                yield session
                check_deadline()
                self._timeout(session)
                session.commit()
                check_deadline()

    def _locked(self, session: Session, dispatch_id: str):
        self._timeout(session)
        return session.execute(
            select(AgentDispatch)
            .where(
                AgentDispatch.dispatch_id == dispatch_id,
                AgentDispatch.role == self.role,
                AgentDispatch.phase == self.phase,
            )
            .with_for_update()
        ).scalar_one_or_none()

    def claim(
        self, task_ids: set[str], timeout: int, *, track_pre_launch: bool = False
    ) -> dict | None:
        if type(timeout) is not int or not 60 <= timeout <= 3600:
            raise WorkflowError("runtime timeout must be 60..3600 seconds")
        with self._transaction() as session:
            # Serialize the empty-slot check across independent host connections.
            session.execute(text("SELECT pg_advisory_xact_lock(5727811, 2)"))
            self._timeout(session)
            if session.execute(
                select(AgentDispatch.dispatch_id).where(AgentDispatch.slot == 1)
            ).first():
                return None
            self._timeout(session)
            candidates = session.execute(
                select(Task)
                .where(
                    Task.task_id.in_(task_ids),
                    Task.status.in_(["AWAITING_HANDOFF", "CHANGES_REQUESTED"]),
                    Task.completed_review_rounds < 2,
                )
                .order_by(Task.task_id)
                .with_for_update()
            ).scalars()
            for task in candidates:
                if canonical_repository(task.repository) != REPOSITORY:
                    continue
                sha = task.current_head_sha or task.base_sha
                validate_sha(sha)
                round_number = task.completed_review_rounds
                self._timeout(session)
                exists = session.execute(
                    select(AgentDispatch.dispatch_id).where(
                        AgentDispatch.task_id == task.task_id,
                        AgentDispatch.round_number == round_number,
                        AgentDispatch.input_sha == sha,
                    )
                ).first()
                if exists:
                    continue
                dispatch = AgentDispatch(
                    dispatch_id=uuid.uuid4().hex,
                    role=self.role,
                    phase=self.phase,
                    subject_type="task",
                    subject_id=task.task_id,
                    subject_version=f"{round_number}:{sha}",
                    task_id=task.task_id,
                    round_number=round_number,
                    input_sha=sha,
                    status="ACTIVE",
                    slot=1,
                    deadline=self.clock() + timeout,
                    detail=_PRE_LAUNCH if track_pre_launch else "",
                )
                session.add(dispatch)
                self._timeout(session)
                session.flush()
                return DevClaim.model_validate(dispatch).model_dump()
        return None

    def is_pre_launch(self, dispatch_id: str) -> bool:
        with self._transaction() as session:
            return (
                session.execute(
                    select(AgentDispatch.dispatch_id).where(
                        AgentDispatch.dispatch_id == dispatch_id,
                        AgentDispatch.role == self.role,
                        AgentDispatch.phase == self.phase,
                        AgentDispatch.slot == 1,
                        AgentDispatch.detail == _PRE_LAUNCH,
                    )
                ).first()
                is not None
            )

    def retire_pre_launch(self, dispatch_id: str) -> None:
        with self._transaction() as session:
            row = self._locked(session, dispatch_id)
            if row is not None and row.slot == 1 and row.detail == _PRE_LAUNCH:
                if row.status != "ACTIVE":
                    raise WorkflowError("could not retire dispatch pre-launch evidence")
                row.detail = ""
                self._timeout(session)
                session.flush()

    def record_runtime_evidence(
        self,
        dispatch_id: str,
        task_id: str,
        input_sha: str,
        runtime_name: str,
        declared_version: str,
        image_id: str | None,
        telemetry: str,
        tested_digest: str,
        published_head_sha: str,
    ) -> None:
        validate_sha(input_sha)
        validate_sha(published_head_sha)
        if (
            not re.fullmatch(r"[0-9a-f]{32}", dispatch_id)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", runtime_name)
            or not isinstance(declared_version, str)
            or not declared_version.strip()
            or (
                image_id is not None
                and not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
            )
            or telemetry != "UNVERIFIED"
            or not re.fullmatch(r"[a-f0-9]{64}", tested_digest)
        ):
            raise WorkflowError("invalid runtime evidence")
        with self._transaction() as session:
            row = self._locked(session, dispatch_id)
            if (
                row is None
                or row.slot != 1
                or row.status != "ACTIVE"
                or row.task_id != task_id
                or row.input_sha != input_sha
            ):
                raise WorkflowError("runtime evidence has no active matching dispatch")
            session.add(
                AgentExecutionEvidence(
                    dispatch_id=dispatch_id,
                    evidence_kind="dev_runtime",
                    trusted_writer="host",
                    task_id=task_id,
                    input_sha=input_sha,
                    runtime_name=runtime_name,
                    declared_runtime_version=declared_version,
                    observed_image_id=image_id,
                    inference_telemetry=telemetry,
                    tested_digest=tested_digest,
                    published_head_sha=published_head_sha,
                    recorded_at=self.clock(),
                )
            )
            self._timeout(session)
            session.flush()

    def finish(self, dispatch_id: str, sha: str) -> None:
        validate_sha(sha)
        with self._transaction() as session:
            row = self._locked(session, dispatch_id)
            if row is None or row.status != "ACTIVE" or row.slot != 1:
                raise WorkflowError("dispatch lease lost")
            self._timeout(session)
            evidence = session.get(AgentExecutionEvidence, dispatch_id)
            if evidence is None:
                raise WorkflowError(
                    "runtime evidence required before dispatch completion"
                )
            if (
                evidence.evidence_kind,
                evidence.trusted_writer,
                evidence.task_id,
                evidence.input_sha,
            ) != ("dev_runtime", "host", row.task_id, row.input_sha):
                raise WorkflowError("runtime evidence identity mismatch")
            if evidence.published_head_sha != sha:
                raise WorkflowError("runtime evidence HEAD mismatch")
            row.status, row.slot, row.result_sha = "DONE", None, sha
            self._timeout(session)
            session.flush()

    def block(self, dispatch_id: str) -> None:
        with self._transaction() as session:
            row = self._locked(session, dispatch_id)
            if row is not None and row.slot == 1:
                row.status = "BLOCKED"
                if row.detail != _PRE_LAUNCH:
                    row.detail = _BLOCKED
                self._timeout(session)
                session.flush()

    def acknowledge(self, dispatch_id: str) -> None:
        """Called only after operator reconciliation of external effects."""
        with self._transaction() as session:
            row = self._locked(session, dispatch_id)
            if row is None or row.slot != 1:
                raise WorkflowError("unknown active dispatch")
            row.status, row.slot = "ACKNOWLEDGED", None
            if row.detail == _PRE_LAUNCH:
                row.detail = ""
            self._timeout(session)
            session.flush()
