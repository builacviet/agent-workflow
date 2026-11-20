"""Exercise bounded JSON fields and scoped stderr logging."""

import json
import logging
import sys
from datetime import UTC, datetime

import pytest

from agent_workflow.infrastructure.logging.json_formatter import (
    JsonFormatter,
    admin_log_scope,
)
from agent_workflow.observability.correlation import (
    correlation_scope,
    set_correlation_id,
)

LOGGER_NAME = "agent_workflow.admin"
PRIVATE_DATA = "postgresql://user:PRIVATE_SENTINEL@example.invalid/db"


def test_json_timestamp_schema_and_safe_event_fields():
    record = logging.LogRecord(
        LOGGER_NAME,
        logging.WARNING,
        PRIVATE_DATA,
        1,
        "admin_temporary_failure",
        (),
        None,
    )
    record.created = 1_700_000_000.5
    record.action = "provision"
    record.reason = "timeout"
    record.credentials = {"password": PRIVATE_DATA}
    record.sql_parameters = [PRIVATE_DATA]
    record.extra_object = object()
    with correlation_scope(correlation_id="cli-allowed"):
        formatted = JsonFormatter().format(record)

    assert len(formatted.splitlines()) == 1
    assert PRIVATE_DATA not in formatted
    payload = json.loads(formatted)
    timestamp = payload.pop("timestamp")
    assert datetime.fromisoformat(timestamp) == datetime.fromtimestamp(
        record.created, UTC
    )
    assert timestamp.endswith("+00:00")
    assert payload == {
        "level": "WARNING",
        "logger": LOGGER_NAME,
        "correlation_id": "cli-allowed",
        "message": "Local operation failed temporarily",
        "event": "admin_temporary_failure",
        "action": "provision",
        "reason": "timeout",
    }


@pytest.mark.parametrize(
    "message", [PRIVATE_DATA + "\nnew line", "admin_temporary_failure"]
)
def test_formatter_never_renders_free_text_arguments_exception_or_invalid_id(message):
    class Sensitive:
        def __str__(self):
            pytest.fail("log arguments must never be rendered")

        def __repr__(self):
            pytest.fail("log arguments must never be represented")

    try:
        raise ValueError(PRIVATE_DATA)
    except ValueError:
        record = logging.LogRecord(
            LOGGER_NAME,
            logging.ERROR,
            PRIVATE_DATA,
            1,
            message,
            (Sensitive(),),
            sys.exc_info(),
            sinfo=PRIVATE_DATA,
        )
    record.exc_text = PRIVATE_DATA
    record.reason = PRIVATE_DATA
    record.action = Sensitive()
    with correlation_scope():
        set_correlation_id(PRIVATE_DATA + " */")
        formatted = JsonFormatter().format(record)

    payload = json.loads(formatted)
    assert PRIVATE_DATA not in formatted
    assert "PRIVATE_SENTINEL" not in formatted
    assert payload["correlation_id"] is None
    assert "reason" not in payload and "action" not in payload
    assert payload["message"] in {
        "Log message redacted",
        "Local operation failed temporarily",
    }


def test_stderr_setup_is_reentrant_and_restores_logger_configuration(capsys):
    logger = logging.getLogger(LOGGER_NAME)
    original = list(logger.handlers), logger.level, logger.propagate
    for _ in range(2):
        with admin_log_scope(), admin_log_scope():
            logger.warning("admin_temporary_failure")
        (line,) = capsys.readouterr().err.splitlines()
        assert json.loads(line)["event"] == "admin_temporary_failure"
        assert (logger.handlers, logger.level, logger.propagate) == original
