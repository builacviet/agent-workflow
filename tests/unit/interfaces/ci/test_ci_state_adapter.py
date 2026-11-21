"""CI state interface delegates to Use Cases without opening SQL sessions."""

from unittest.mock import Mock

from agent_workflow.interfaces.ci.state_adapter import CiStateAdapter


def test_wiring_is_injected_and_no_repository_construction():
    reader = Mock()
    tracker = Mock()
    recorder = Mock()
    approval = Mock()
    acknowledger = Mock()
    state = CiStateAdapter(
        reader=reader,
        tracker=tracker,
        recorder=recorder,
        approval=approval,
        acknowledger=acknowledger,
    )
    evidence = object()
    reader.tracked.return_value = [{"task_id": "T"}]
    reader.previous_failure.return_value = True
    reader.pending_events.return_value = [object()]
    approval.execute.return_value = True

    state.track("T", 7, "a" * 40)
    assert state.tracked(3) == [{"task_id": "T"}]
    assert state.previous_failure("T", "a" * 40)
    state.record(evidence)
    assert state.approved("T", "a" * 40)
    assert len(state.pending_events(5)) == 1
    state.mark_processed("delivery")

    tracker.execute.assert_called_once_with("T", 7, "a" * 40)
    recorder.execute.assert_called_once_with(evidence)
    approval.execute.assert_called_once_with("T", "a" * 40)
    acknowledger.execute.assert_called_once_with("delivery")
    reader.tracked.assert_called_once_with(3)
    reader.pending_events.assert_called_once_with(5)
