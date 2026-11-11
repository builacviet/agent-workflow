"""V1-03C: CI-only entry point cannot dispatch agents or require worker PEM."""
from argparse import Namespace

from agent_workflow.orchestration import controller


def test_ci_only_mode_never_loads_coder_runtime(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "_ci_operator",
                        lambda config, *, watch: calls.append((config, watch)))
    controller.operate({"database": "unused"}, Namespace(ci_once=True, ci_watch=False))
    assert calls == [({"database": "unused"}, False)]


def test_ci_watch_mode_is_separate_from_dev_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "_ci_operator",
                        lambda config, *, watch: calls.append(watch))
    controller.operate({"database": "unused"}, Namespace(ci_once=False, ci_watch=True))
    assert calls == [True]
