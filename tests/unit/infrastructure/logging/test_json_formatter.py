"""Exercise bounded JSON fields and scoped stderr logging."""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

import pytest

from agent_workflow.infrastructure.logging.json_formatter import (
    JsonFormatter,
    admin_log_scope,
    ci_log_scope,
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


def test_overlapping_threads_keep_json_handler_until_both_have_exited(capsys):
    admin = logging.getLogger(LOGGER_NAME)
    authorization = logging.getLogger(
        "agent_workflow.application.task.use_cases.authorize_task"
    )
    loggers = (admin, authorization)
    original = [
        (list(logger.handlers), logger.level, logger.propagate) for logger in loggers
    ]
    second_entered, first_exited = Event(), Event()

    def second_request():
        with correlation_scope(correlation_id="second"), admin_log_scope():
            second_entered.set()
            assert first_exited.wait(timeout=5), "first request did not exit"
            authorization.info("task_authorization_committed")

    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            with correlation_scope(correlation_id="first"), admin_log_scope():
                admin.warning("admin_temporary_failure")
                second = executor.submit(second_request)
                assert second_entered.wait(timeout=5), "second request did not enter"
        finally:
            first_exited.set()
        second.result(timeout=5)

    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [(line["event"], line["correlation_id"]) for line in lines] == [
        ("admin_temporary_failure", "first"),
        ("task_authorization_committed", "second"),
    ]
    assert [
        (logger.handlers, logger.level, logger.propagate) for logger in loggers
    ] == original


def test_ci_scope_emits_only_allowlisted_fields_and_restores_logger(capsys):
    logger = logging.getLogger("agent_workflow.interfaces.cli.ci_controller")
    original = list(logger.handlers), logger.level, logger.propagate
    with correlation_scope(correlation_id="ci-correlation"):
        with ci_log_scope(), ci_log_scope():
            logger.error(
                "ci_cli_failure",
                extra={
                    "mode": "watch",
                    "reason": "permission_failure",
                    "dsn": PRIVATE_DATA,
                    "pem_path": PRIVATE_DATA,
                    "sql_parameters": [PRIVATE_DATA],
                },
            )

    (line,) = capsys.readouterr().err.splitlines()
    payload = json.loads(line)
    assert payload["logger"] == logger.name
    assert payload["event"] == "ci_cli_failure"
    assert payload["message"] == "CI CLI operation ended without success"
    assert payload["mode"] == "watch"
    assert payload["reason"] == "permission_failure"
    assert payload["correlation_id"] == "ci-correlation"
    assert PRIVATE_DATA not in line
    assert "dsn" not in payload and "pem_path" not in payload
    assert (logger.handlers, logger.level, logger.propagate) == original
