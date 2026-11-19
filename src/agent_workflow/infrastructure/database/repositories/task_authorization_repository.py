"""Map the local task authorization aggregate onto the canonical PostgreSQL rows."""

import json
from dataclasses import asdict, fields

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)
from agent_workflow.infrastructure.database.models import Task, TaskEvent
from agent_workflow.infrastructure.database.session_factory import (
    PostgresUnavailableError,
)
from agent_workflow.infrastructure.database.unit_of_work import (
    set_workflow_query_timeout,
)

_SPEC_KEYS = frozenset(field.name for field in fields(TaskAuthorizationSpec))


class SqlAlchemyTaskAuthorizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load_for_authorization(self, task_id: str) -> TaskAuthorizationSpec | None:
        session = self._session
        set_workflow_query_timeout(session)
        # Use the existing Workflow authorization lock key to serialize both callers.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:task_id, 5727811))"),
            {"task_id": task_id},
        )
        set_workflow_query_timeout(session)
        metadata = session.scalar(
            select(Task.metadata_text).where(Task.task_id == task_id)
        )
        if metadata is None:
            return None
        try:
            values = json.loads(metadata)
            if not isinstance(values, dict) or values.keys() != _SPEC_KEYS:
                raise ValueError
            for name in ("acceptance_criteria", "context_paths"):
                if not isinstance(values[name], list):
                    raise ValueError
                values[name] = tuple(values[name])
            return TaskAuthorizationSpec(**values)
        except (TypeError, ValueError):
            raise PostgresUnavailableError(
                "Invalid persisted task authorization"
            ) from None

    def add(self, authorization: TaskAuthorization) -> None:
        spec = authorization.spec
        metadata = json.dumps(
            asdict(spec), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        task = Task(
            task_id=spec.task_id,
            metadata_text=metadata,
            repository=spec.repository,
            branch=spec.branch,
            base_sha=spec.base_sha,
            requested_effort=spec.requested_effort,
            require_verified_effort=int(spec.require_verified_effort),
            status=authorization.status.value,
            created_at=authorization.created_at,
            updated_at=authorization.authorized_at,
        )
        task.events.append(
            TaskEvent(
                next_status=authorization.status.value,
                action="LOCAL_AUTHORIZATION",
                timestamp=authorization.authorized_at,
                metadata_text="{}",
            )
        )
        set_workflow_query_timeout(self._session)
        self._session.add(task)
        self._session.flush()
