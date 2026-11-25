"""PostgreSQL Dev dispatch and role-neutral persistence integration tests."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pg_task_fixtures import authorize_task
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_agent_workflow import HEAD, NEXT, spec

from agent_workflow import budget
from agent_workflow.app_config import AppConfig
from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.exceptions import (
    CorruptedPersistenceDataError,
    PersistenceUnavailableError,
)
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    GetCoderAssignmentUseCase,
)
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.database.repositories import dispatch_ledger
from agent_workflow.infrastructure.database.repositories.dispatch_ledger import (
    PostgresDispatchLedger,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)


@pytest.fixture
def setup(request):
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    if make_url(dsn).database not in {"workflow_test", "workflow_ci"}:
        pytest.fail("dispatch tests require workflow_test or workflow_ci")
    task_id = "PG02C-" + uuid.uuid4().hex
    task_keys = {"task": task_id, "other": task_id + "-NEXT"}
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        authorize_task(factory, spec(task_id))
        ledger = PostgresDispatchLedger(
            factory, role="dev", phase="code", clock=lambda: 1000.0
        )
        try:
            if getattr(request, "param", False):
                authorize_task(factory, spec(task_keys["other"]))
            yield ledger, factory, task_id
        finally:
            with factory.transaction() as conn:
                conn.execute(
                    text(
                        "DELETE FROM agent_execution_evidence WHERE dispatch_id IN (SELECT dispatch_id FROM agent_dispatches WHERE task_id IN (:task, :other))"
                    ),
                    task_keys,
                )
                conn.execute(
                    text(
                        "DELETE FROM agent_dispatches WHERE task_id IN (:task, :other)"
                    ),
                    task_keys,
                )
                conn.execute(
                    text("DELETE FROM task_events WHERE task_id IN (:task, :other)"),
                    task_keys,
                )
                conn.execute(
                    text("DELETE FROM tasks WHERE task_id IN (:task, :other)"),
                    task_keys,
                )


def test_concurrent_claim_block_restart_and_replay(setup, monkeypatch):
    ledger, factory, task_id = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: ledger.claim({task_id}, 60, track_pre_launch=True), range(2)
            )
        )
    claims = [result for result in results if result is not None]
    assert len(claims) == 1
    claim = claims[0]
    assert isinstance(claim, DispatchClaim)
    assert claim.task_id == task_id
    assert claim.round_number == 0
    assert claim.deadline == 1060.0
    restarted = PostgresDispatchLedger(
        factory, role="dev", phase="code", clock=lambda: 1000.0
    )
    engine = factory._engine
    checkouts = []
    reads = []
    rollbacks = []
    commits = []
    deadline = time.time() + 15
    expire_after_select = False

    def observe_checkout(*_args):
        checkouts.append(object())

    def observe_read(connection, _cursor, statement, _params, _context, _many):
        if "from agent_dispatches" not in statement.lower():
            return
        reads.append(connection)
        isolation, read_only, statement_ms, lock_ms = connection.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only'), "
                "(SELECT setting::int FROM pg_settings "
                "WHERE name = 'statement_timeout'), "
                "(SELECT setting::int FROM pg_settings WHERE name = 'lock_timeout')"
            )
        ).one()
        assert isolation == "read committed"
        assert read_only == "on"
        assert 0 < statement_ms == lock_ms <= 15_000
        if expire_after_select:
            # The SQL succeeded, but the result must not escape an expired budget.
            patch.setattr(
                budget,
                "time",
                SimpleNamespace(time=lambda: deadline + 1, monotonic=time.monotonic),
            )

    listeners = (
        ("checkout", observe_checkout),
        ("after_cursor_execute", observe_read),
        ("rollback", rollbacks.append),
        ("commit", commits.append),
    )
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        with monkeypatch.context() as patch, dispatch_budget(deadline):
            assert restarted.is_pre_launch(claim.dispatch_id)
            assert not restarted.is_pre_launch(uuid.uuid4().hex)
            expire_after_select = True
            with pytest.raises(TimeoutError, match="dispatch deadline exceeded"):
                restarted.is_pre_launch(claim.dispatch_id)
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)

    assert len(checkouts) == len(reads) == 3
    assert len({id(connection) for connection in reads}) == 3
    assert rollbacks == reads
    assert all(connection.closed for connection in reads)
    assert commits == []
    assert restarted.is_pre_launch(claim.dispatch_id)
    restarted.retire_pre_launch(claim.dispatch_id)
    assert not restarted.is_pre_launch(claim.dispatch_id)
    restarted.block(claim.dispatch_id)
    assert restarted.claim({task_id}, 60) is None
    with pytest.raises(BusinessRuleError):
        restarted.finish(claim.dispatch_id, HEAD)
    restarted.acknowledge(claim.dispatch_id)
    assert restarted.claim({task_id}, 60) is None
    with pytest.raises(BusinessRuleError):
        restarted.acknowledge(claim.dispatch_id)


def test_ambiguous_claim_reserves_slot_across_new_controller_owner(setup):
    ledger, factory, task_id = setup
    claim = ledger.claim({task_id}, 60, track_pre_launch=True)
    assert claim is not None
    simulated_publish = Mock()

    def next_owner_attempt():
        successor = PostgresDispatchLedger(
            factory, role="dev", phase="code", clock=lambda: 1000.0
        )
        leadership = PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER)
        return RunDispatchPassUseCase(
            leadership,
            successor,
            GetCoderAssignmentUseCase(create_get_review_context_use_case(factory)),
            simulated_publish,
        ).execute({task_id})

    assert not next_owner_attempt()
    assert ledger.is_pre_launch(claim.dispatch_id)
    ledger.block(claim.dispatch_id)
    assert ledger.is_pre_launch(claim.dispatch_id)
    assert not next_owner_attempt()
    ledger.acknowledge(claim.dispatch_id)
    assert not ledger.is_pre_launch(claim.dispatch_id)
    assert not next_owner_attempt()
    simulated_publish.assert_not_called()


@pytest.mark.parametrize("setup", [True], indirect=True)
@pytest.mark.parametrize("fault", ["uncertain-publish", "late-result"])
def test_dispatch_failure_keeps_durable_slot_across_restart(setup, monkeypatch, fault):
    _, factory, task_id = setup
    ledger = PostgresDispatchLedger(factory, role="dev", phase="code")
    leadership = PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER)
    finish_cleanup = Mock()
    diagnostic = "private publication diagnostic"

    def execute(claim, context, guard):
        assert context["task_id"] == task_id
        guard()
        dispatch_id = claim.dispatch_id
        ledger.retire_pre_launch(dispatch_id)
        if fault == "uncertain-publish":
            raise BusinessRuleError(diagnostic)
        ledger.record_runtime_evidence(
            dispatch_id,
            task_id,
            claim.input_sha,
            "codex",
            "1.0",
            None,
            "UNVERIFIED",
            "d" * 64,
            HEAD,
        )
        monkeypatch.setattr(
            budget,
            "time",
            SimpleNamespace(time=lambda: claim.deadline + 1, monotonic=time.monotonic),
        )
        return HEAD

    worker = Mock(side_effect=execute)
    dispatch = RunDispatchPassUseCase(
        leadership,
        ledger,
        GetCoderAssignmentUseCase(create_get_review_context_use_case(factory)),
        worker,
        on_finished=finish_cleanup,
    )
    if fault == "late-result":
        with pytest.raises(TimeoutError, match="dispatch deadline exceeded"):
            dispatch.execute({task_id}, 60)
    else:
        with pytest.raises(BusinessRuleError, match=diagnostic):
            dispatch.execute({task_id}, 60)

    with PostgresFactory.open(
        AppConfig(pg_runtime_dsn=os.environ["PG_TEST_DSN"])
    ) as fresh:
        successor = RunDispatchPassUseCase(
            PostgresLeadershipLock(fresh, LeadershipDomain.CONTROLLER),
            PostgresDispatchLedger(fresh, role="dev", phase="code"),
            GetCoderAssignmentUseCase(create_get_review_context_use_case(fresh)),
            worker,
            on_finished=finish_cleanup,
        )
        with fresh.transaction() as connection:
            row = connection.execute(
                text(
                    "SELECT status, slot, result_sha, detail FROM agent_dispatches "
                    "WHERE task_id=:task_id"
                ),
                {"task_id": task_id},
            ).one()
        assert tuple(row[:3]) == ("BLOCKED", 1, None)
        assert diagnostic not in row.detail
        assert not successor.execute({task_id}, 60)
        assert not successor.execute({task_id + "-NEXT"}, 60)

    context = create_get_review_context_use_case(factory).execute(
        GetReviewContextInput(task_id)
    )
    assert context.task.status == "AWAITING_HANDOFF"
    assert [event.action for event in context.events] == ["LOCAL_AUTHORIZATION"]
    worker.assert_called_once()
    finish_cleanup.assert_not_called()


def test_evidence_identity_and_finish(setup):
    ledger, factory, task_id = setup
    claim = ledger.claim({task_id}, 60)
    evidence = (
        claim.dispatch_id,
        task_id,
        claim.input_sha,
        "codex",
        "1.0",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    with pytest.raises(BusinessRuleError):
        ledger.record_runtime_evidence(claim.dispatch_id, task_id, NEXT, *evidence[3:])
    with pytest.raises(BusinessRuleError):
        ledger.record_runtime_evidence(claim.dispatch_id, "wrong", *evidence[2:])
    ledger.record_runtime_evidence(*evidence)
    with pytest.raises(
        PersistenceUnavailableError, match="^PostgreSQL unit of work failed$"
    ) as error:
        ledger.record_runtime_evidence(*evidence)
    assert error.value.__suppress_context__
    cause = error.value.__context__
    assert isinstance(cause, IntegrityError)
    assert getattr(cause.orig, "sqlstate", None) == "23505"
    with pytest.raises(BusinessRuleError, match="HEAD mismatch"):
        ledger.finish(claim.dispatch_id, NEXT)
    assert ledger.claim({task_id}, 60) is None
    ledger.finish(claim.dispatch_id, HEAD)
    # Replays and terminal no-op operations must roll back, not commit.
    outcomes = []

    def on_rollback(connection):
        outcomes.append(("rollback", connection))

    def on_commit(connection):
        outcomes.append(("commit", connection))

    engine = factory._engine
    event.listen(engine, "rollback", on_rollback)
    event.listen(engine, "commit", on_commit)
    try:
        assert ledger.claim({task_id}, 60) is None
        ledger.retire_pre_launch(claim.dispatch_id)
        ledger.block(claim.dispatch_id)
    finally:
        event.remove(engine, "commit", on_commit)
        event.remove(engine, "rollback", on_rollback)
    assert [outcome for outcome, _ in outcomes] == ["rollback"] * 3
    assert all(connection.closed for _, connection in outcomes)


def test_unsupported_role_and_invalid_timeout(setup):
    ledger, factory, task_id = setup
    for role, phase in [("reviewer", "code"), ("dev", "test"), ("", "")]:
        with pytest.raises(BusinessRuleError):
            PostgresDispatchLedger(factory, role=role, phase=phase)
    for timeout in (0, 59, 3601, True, 1.5):
        with pytest.raises(BusinessRuleError):
            ledger.claim({task_id}, timeout)
    assert ledger.claim({task_id}, 60) is not None


def test_claim_rejects_ineligible_status_round_and_task(setup):
    ledger, factory, task_id = setup
    assert ledger.claim({"UNKNOWN"}, 60) is None
    for status, rounds in (
        ("APPROVED", 1),
        ("ESCALATED", 2),
        ("READY_IN_REVIEW", 0),
        ("IN_REVIEW", 0),
        ("CHANGES_REQUESTED", 2),
    ):
        with factory.transaction() as conn:
            conn.execute(
                text(
                    "UPDATE tasks SET status=:status, completed_review_rounds=:rounds WHERE task_id=:task"
                ),
                {"status": status, "rounds": rounds, "task": task_id},
            )
        assert ledger.claim({task_id}, 60) is None


def test_lock_wait_is_bounded_and_retries(setup):
    ledger, factory, task_id = setup
    with factory.transaction() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(5727811, 2)"))
        started = time.monotonic()
        with pytest.raises(
            PersistenceUnavailableError, match="^PostgreSQL unit of work failed$"
        ) as error:
            with dispatch_budget(time.time() + 0.6):
                ledger.claim({task_id}, 60)
        assert error.value.__suppress_context__
        cause = error.value.__context__
        assert isinstance(cause, DBAPIError)
        assert getattr(cause.orig, "sqlstate", None) in {"55P03", "57014"}
        assert time.monotonic() - started < 3
    assert ledger.claim({task_id}, 60) is not None


def test_new_round_and_sha_are_new_identity(setup):
    ledger, factory, task_id = setup
    first = ledger.claim({task_id}, 60)
    ledger.record_runtime_evidence(
        first.dispatch_id,
        task_id,
        first.input_sha,
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    ledger.finish(first.dispatch_id, HEAD)
    with factory.transaction() as conn:
        conn.execute(
            text(
                "UPDATE tasks SET status='CHANGES_REQUESTED', completed_review_rounds=1, current_head_sha=:sha WHERE task_id=:task"
            ),
            {"sha": HEAD, "task": task_id},
        )
    second = ledger.claim({task_id}, 60)
    assert second.round_number == 1 and second.input_sha == HEAD
    assert second.dispatch_id != first.dispatch_id


def test_wrong_dispatch_and_role_cannot_record_evidence(setup):
    ledger, factory, task_id = setup
    claim = ledger.claim({task_id}, 60)
    evidence = (
        task_id,
        claim.input_sha,
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    with pytest.raises(BusinessRuleError, match="no active matching dispatch"):
        ledger.record_runtime_evidence("f" * 32, *evidence)
    with factory.transaction() as conn:
        conn.execute(
            text("UPDATE agent_dispatches SET role='other' WHERE dispatch_id=:id"),
            {"id": claim.dispatch_id},
        )
    with pytest.raises(BusinessRuleError, match="no active matching dispatch"):
        ledger.record_runtime_evidence(claim.dispatch_id, *evidence)
    with pytest.raises(BusinessRuleError, match="lease lost"):
        ledger.finish(claim.dispatch_id, HEAD)
    with factory.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM agent_dispatches WHERE dispatch_id=:id"),
                {"id": claim.dispatch_id},
            ).scalar_one()
            == "ACTIVE"
        )


def test_claim_failure_rolls_back_and_allows_retry(setup, monkeypatch):
    ledger, factory, task_id = setup
    project = dispatch_ledger._project_claim

    def fail_validation(row):
        return project(
            SimpleNamespace(
                dispatch_id=row.dispatch_id,
                task_id=None,
                input_sha=row.input_sha,
                deadline=row.deadline,
                round_number=row.round_number,
            )
        )

    with monkeypatch.context() as patch:
        patch.setattr(dispatch_ledger, "_project_claim", fail_validation)
        with pytest.raises(
            CorruptedPersistenceDataError, match="invalid persisted dispatch claim"
        ):
            ledger.claim({task_id}, 60)
    with factory.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM agent_dispatches WHERE task_id=:task"),
                {"task": task_id},
            ).scalar_one()
            == 0
        )
    assert ledger.claim({task_id}, 60) is not None


@pytest.mark.parametrize(
    "checkpoint", ["before-session-commit", "after-session-commit"]
)
def test_claim_deadline_before_outer_commit_releases_slot(
    setup, monkeypatch, checkpoint
):
    ledger, factory, task_id = setup
    clock = SimpleNamespace(now=1000.0)
    fake_time = SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now)
    engine = factory._engine
    outcomes = []
    session_commits = []

    def on_flush(session, _context):
        if checkpoint == "before-session-commit":
            clock.now = 1002.0

    def on_session_commit(session):
        session_commits.append(True)
        if checkpoint == "after-session-commit":
            clock.now = 1002.0

    def on_rollback(connection):
        outcomes.append(("rollback", connection))

    def on_commit(connection):
        outcomes.append(("commit", connection))

    listeners = (
        (Session, "after_flush", on_flush),
        (Session, "after_commit", on_session_commit),
        (engine, "rollback", on_rollback),
        (engine, "commit", on_commit),
    )
    for target, name, listener in listeners:
        event.listen(target, name, listener)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(budget, "time", fake_time)
            with dispatch_budget(1001.0):
                with pytest.raises(TimeoutError, match="dispatch deadline exceeded"):
                    ledger.claim({task_id}, 60)
    finally:
        for target, name, listener in listeners:
            event.remove(target, name, listener)

    assert session_commits == ([True] if checkpoint == "after-session-commit" else [])
    assert [outcome for outcome, _ in outcomes] == ["rollback"]
    assert all(connection.closed for _, connection in outcomes)
    with factory.transaction() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM agent_dispatches WHERE task_id=:task_id"),
                {"task_id": task_id},
            )
            == 0
        )
    assert ledger.claim({task_id}, 60) is not None


def test_finish_without_evidence_fails_and_retains_slot(setup):
    ledger, factory, task_id = setup
    claim = ledger.claim({task_id}, 60)
    with pytest.raises(BusinessRuleError, match="runtime evidence required"):
        ledger.finish(claim.dispatch_id, HEAD)
    with factory.transaction() as conn:
        row = conn.execute(
            text(
                "SELECT status,slot,result_sha FROM agent_dispatches WHERE dispatch_id=:id"
            ),
            {"id": claim.dispatch_id},
        ).one()
        assert tuple(row) == ("ACTIVE", 1, None)
    assert ledger.claim({task_id}, 60) is None
    ledger.record_runtime_evidence(
        claim.dispatch_id,
        task_id,
        claim.input_sha,
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    ledger.finish(claim.dispatch_id, HEAD)
    with factory.transaction() as conn:
        row = conn.execute(
            text(
                "SELECT status,slot,result_sha FROM agent_dispatches WHERE dispatch_id=:id"
            ),
            {"id": claim.dispatch_id},
        ).one()
        assert tuple(row) == ("DONE", None, HEAD)


def test_tampered_evidence_identity_keeps_slot(setup):
    ledger, factory, task_id = setup
    claim = ledger.claim({task_id}, 60)
    ledger.record_runtime_evidence(
        claim.dispatch_id,
        task_id,
        claim.input_sha,
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    with factory.transaction() as conn:
        conn.execute(
            text(
                "UPDATE agent_execution_evidence SET input_sha=:sha WHERE dispatch_id=:id"
            ),
            {"sha": NEXT, "id": claim.dispatch_id},
        )
    with pytest.raises(BusinessRuleError, match="identity mismatch"):
        ledger.finish(claim.dispatch_id, HEAD)
    assert ledger.claim({task_id}, 60) is None


@pytest.mark.parametrize("fault", [None, "finish", "cleanup", "ownership"])
def test_controller_entrypoint_fake_worker_pg_lifecycle(
    setup, monkeypatch, tmp_path, fault
):
    """Hosted PG16 module: real composition/state; synthetic external effects only."""
    from types import SimpleNamespace

    from test_agent_runtime import FakeHost

    from agent_workflow.composition import postgres_dispatch_deps as deps_wiring
    from agent_workflow.composition import postgres_dispatch_entrypoint as entry
    from agent_workflow.interfaces.cli import dispatch_controller as dispatch_cli
    from agent_workflow.runners import coder_runtime

    _, observer, task_id = setup
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "fences"))
    app = AppConfig(pg_runtime_dsn=os.environ["PG_TEST_DSN"])
    # Admission settings are covered offline with inert credential fixtures.
    # This PG test targets the real factory/guard/adapters and fake Worker seam.
    monkeypatch.setattr(entry, "_validate_worker_config", lambda _: None)
    opened = []
    original_open = PostgresFactory.open
    original_compose = deps_wiring.compose_postgres_worker
    original_controller = deps_wiring.compose_postgres_controller
    calls = []
    pins = set()
    dispatch_ids = []

    def open_factory(settings):
        factory = original_open(settings)
        opened.append(factory)
        return factory

    def compose_controller(factory):
        dependencies = original_controller(factory)
        if fault == "finish":
            monkeypatch.setattr(
                dependencies.dispatch,
                "finish",
                Mock(side_effect=RuntimeError("uncertain finish")),
            )
        return dependencies

    def compose_worker(config, service, deps, *, leadership_guard):
        runtime = original_compose(
            config, service, deps, leadership_guard=leadership_guard
        )
        assert service is deps.handoff_provider
        assert runtime.agent_runtime.dispatch is deps.dispatch
        assert runtime.agent_runtime.ci is deps.ci_tracking
        host = FakeHost(calls)
        host._validated_result = lambda *_: {
            "head_sha": HEAD,
            "pr_url": "https://github.com/builacviet/agent-workflow/pull/7",
        }

        def handoff(context, result, summary):
            leadership_guard()
            calls.append("handoff")
            runtime._register_handoff(context, result, summary)

        host._register_handoff = handoff
        runtime.agent_runtime.host = host

        def execute(claim, context):
            leadership_guard()
            with observer.transaction() as conn:
                persisted = (
                    conn.execute(
                        text(
                            "SELECT dispatch_id,task_id,input_sha,deadline,round_number "
                            "FROM agent_dispatches WHERE dispatch_id=:id"
                        ),
                        {"id": claim["dispatch_id"]},
                    )
                    .mappings()
                    .one()
                )
            assert claim == dict(persisted)
            dispatch_ids.append(claim["dispatch_id"])
            assert claim["task_id"] == task_id
            assert deps.dispatch.is_pre_launch(claim["dispatch_id"])
            deps.dispatch.retire_pre_launch(claim["dispatch_id"])
            assert not deps.dispatch.is_pre_launch(claim["dispatch_id"])
            pins.add(claim["dispatch_id"])
            result = runtime.agent_runtime.execute_bound(
                claim,
                context,
                SimpleNamespace(spec=SimpleNamespace(runtime="fake")),
                SimpleNamespace(),
                None,
            )
            if fault == "ownership":
                # Simulate abrupt session loss after evidence/handoff/CI commit.
                deps.leadership.release()
            return result

        def clear(dispatch_id):
            leadership_guard()
            with observer.transaction() as conn:
                assert (
                    conn.execute(
                        text(
                            "SELECT status FROM agent_dispatches WHERE dispatch_id=:id"
                        ),
                        {"id": dispatch_id},
                    ).scalar_one()
                    == "DONE"
                )
            calls.append("clear")
            if fault == "cleanup":
                raise RuntimeError("uncertain cleanup")
            pins.remove(dispatch_id)

        monkeypatch.setattr(runtime, "execute", execute)
        monkeypatch.setattr(runtime, "release_engine", clear)
        return runtime

    monkeypatch.setattr(entry.PostgresFactory, "open", open_factory)
    monkeypatch.setattr(deps_wiring, "compose_postgres_controller", compose_controller)
    monkeypatch.setattr(deps_wiring, "compose_postgres_worker", compose_worker)
    monkeypatch.setattr(
        coder_runtime, "docker_command", lambda *_: pytest.fail("real Docker forbidden")
    )
    monkeypatch.setattr(
        coder_runtime, "run_container", lambda *_: pytest.fail("real Worker forbidden")
    )
    config = {"tasks": {task_id: {"handoff_path": "docs/handoff.md"}}}

    def run_dispatch():
        return dispatch_cli.run_dispatch_pass(
            config, app, compose=entry.compose_dispatch_execution
        ).worker_completed

    try:
        if fault:
            with pytest.raises((RuntimeError, BusinessRuleError, PermissionError)):
                run_dispatch()
        else:
            assert run_dispatch()
        assert len(opened) == 1 and opened[0]._closed
        assert len(dispatch_ids) == 1
        with observer.transaction() as conn:
            row = conn.execute(
                text(
                    "SELECT status,slot,result_sha FROM agent_dispatches WHERE dispatch_id=:id"
                ),
                {"id": dispatch_ids[0]},
            ).one()
            expected = (
                "BLOCKED"
                if fault == "finish"
                else "ACTIVE"
                if fault == "ownership"
                else "DONE"
            )
            assert tuple(row) == (
                expected,
                1 if expected != "DONE" else None,
                HEAD if expected == "DONE" else None,
            )
            binding = conn.execute(
                text(
                    "SELECT head_sha,verdict FROM controller_ci_tasks WHERE task_id=:task"
                ),
                {"task": task_id},
            ).one()
            assert tuple(binding) == (HEAD, "BLOCKED")
            assert (
                conn.execute(
                    text(
                        "SELECT published_head_sha FROM agent_execution_evidence WHERE dispatch_id=:id"
                    ),
                    {"id": dispatch_ids[0]},
                ).scalar_one()
                == HEAD
            )
        assert bool(pins) == bool(fault)
        assert ("clear" in calls) == (fault in {None, "cleanup"})
        # A fresh owner and factory must never replay the synthetic publication,
        # even when the first owner crashed after the handoff or cleanup failed.
        assert not run_dispatch()
        assert len(dispatch_ids) == 1
        assert calls.count("publish") == 1
        assert all(factory._closed for factory in opened)
    finally:
        with observer.transaction() as conn:
            conn.execute(
                text("DELETE FROM controller_ci_tasks WHERE task_id=:task"),
                {"task": task_id},
            )
