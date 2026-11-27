"""Map the local task authorization aggregate onto the canonical PostgreSQL rows."""

import json
from dataclasses import asdict, fields

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import (
    CorruptedPersistenceDataError,
    InvalidApplicationRequestError,
)
from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)
from agent_workflow.infrastructure.database.lock_keys import (
    TASK_AUTHORIZATION_HASH_SEED,
)
from agent_workflow.infrastructure.database.models import Task, TaskEvent

_SPEC_KEYS = frozenset(field.name for field in fields(TaskAuthorizationSpec))


def _assert_postgres_text(value: str, name: str) -> None:
    """Guard direct repository calls, independently of the CLI schema."""
    if "\0" in value:
        raise InvalidApplicationRequestError(f"{name} must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidApplicationRequestError(f"{name} must be valid UTF-8") from None


def _assert_persistable_spec(spec: TaskAuthorizationSpec) -> None:
    for field in fields(spec):
        value = getattr(spec, field.name)
        values = (
            (value,)
            if isinstance(value, str)
            else value
            if isinstance(value, tuple)
            else ()
        )
        for item in values:
            if isinstance(item, str):
                _assert_postgres_text(item, field.name)


class SqlAlchemyTaskAuthorizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load_for_authorization(self, task_id: str) -> TaskAuthorizationSpec | None:
        _assert_postgres_text(task_id, "task_id")
        session = self._session
        # Use the existing Workflow authorization lock key to serialize both callers.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:task_id, :seed))"),
            {"task_id": task_id, "seed": TASK_AUTHORIZATION_HASH_SEED},
        )
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
            spec = TaskAuthorizationSpec(**values)
            _assert_persistable_spec(spec)
            if spec.task_id != task_id:
                raise ValueError
            return spec
        except (TypeError, ValueError, InvalidApplicationRequestError):
            raise CorruptedPersistenceDataError(
                "Invalid persisted task authorization"
            ) from None

    def add(self, authorization: TaskAuthorization) -> None:
        spec = authorization.spec
        _assert_persistable_spec(spec)
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
        self._session.add(task)
        self._session.flush()
