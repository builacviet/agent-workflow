"""CI Controller presentation transports typed request/response objects."""

from unittest.mock import Mock

import pytest

from agent_workflow.interfaces.cli import ci_controller


def test_one_shot_passes_typed_request_and_renders_typed_response(capsys):
    reconcile = Mock(return_value=ci_controller.CiRunResponse(1, 0))
    ci_controller.run_ci_once(reconcile)
    reconcile.assert_called_once_with(ci_controller.CiRunRequest(startup=True))
    assert capsys.readouterr().out == "CI reconciliation: 1 tracked observations\n"


def test_one_shot_error_is_not_disguised_as_success(capsys):
    reconcile = Mock(side_effect=PermissionError("leadership lost"))
    with pytest.raises(PermissionError, match="leadership lost"):
        ci_controller.run_ci_once(reconcile)
    assert capsys.readouterr().out == ""


def test_watch_bounded_backoff_and_startup_semantics():
    blocked = [1] * 7 + [0, 0]
    reconcile = Mock(
        side_effect=[
            ci_controller.CiRunResponse(observed_count=1, blocked_count=n)
            for n in blocked
        ]
    )
    delays = []
    ci_controller.run_ci_watch(
        reconcile,
        sleep=delays.append,
        stop=lambda: reconcile.call_count == len(blocked),
    )
    assert [c.args for c in reconcile.call_args_list] == [
        (ci_controller.CiRunRequest(startup=True),),
        *[(ci_controller.CiRunRequest(startup=False),)] * 8,
    ]
    assert delays == [4, 8, 16, 32, 60, 60, 60, 2]


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError("leadership lost"),
        OSError("database unavailable"),
        KeyboardInterrupt(),
    ],
)
def test_watch_does_not_consume_runtime_errors(failure):
    reconcile = Mock(side_effect=failure)
    with pytest.raises(type(failure)):
        ci_controller.run_ci_watch(reconcile, sleep=Mock())
    reconcile.assert_called_once_with(ci_controller.CiRunRequest(startup=True))
