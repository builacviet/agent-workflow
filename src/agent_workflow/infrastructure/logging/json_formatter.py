"""Bounded JSON logging for local admin operations."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from agent_workflow.observability.correlation import (
    get_correlation_id,
    is_valid_correlation_id,
)

_CLI_LOGGERS = (
    "agent_workflow.admin",
    "agent_workflow.application.task.use_cases.authorize_task",
)
_LEVELS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}
_MESSAGES = {
    "admin_temporary_failure": "Local operation failed temporarily",
    "task_authorization_started": "Task authorization started",
    "task_authorization_skipped": "Task already authorized",
    "task_authorization_committed": "Task authorization committed",
}
_EVENT_FIELDS = {
    "action": frozenset(
        ("provision", "seed-history", "seed-synthetic", "reset-synthetic")
    ),
    "reason": frozenset(("persistence_unavailable", "timeout")),
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        correlation_id = get_correlation_id()
        event = record.msg if type(record.msg) is str else ""
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": _LEVELS.get(record.levelno, "UNKNOWN"),
            "logger": record.name if record.name in _CLI_LOGGERS else "unrecognized",
            "correlation_id": correlation_id
            if is_valid_correlation_id(correlation_id)
            else None,
            # Never interpolate arguments or format exception/stack information.
            "message": _MESSAGES.get(event, "Log message redacted"),
        }
        if event in _MESSAGES:
            payload["event"] = event
        for field, allowed in _EVENT_FIELDS.items():
            value = record.__dict__.get(field)
            if type(value) is str and value in allowed:
                payload[field] = value
        return json.dumps(payload)


@contextmanager
def admin_log_scope() -> Iterator[None]:
    """Attach one stderr handler for the duration of the local CLI call."""
    loggers = tuple(logging.getLogger(name) for name in _CLI_LOGGERS)
    if any(type(handler.formatter) is JsonFormatter for handler in loggers[0].handlers):
        yield
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    previous = [(logger, logger.level, logger.propagate) for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    try:
        yield
    finally:
        for logger, level, propagate in previous:
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate
        handler.close()
