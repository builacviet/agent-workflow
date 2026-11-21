from dataclasses import replace

import pytest

from agent_workflow.application.exceptions import ApplicationError, InvalidApplicationRequestError
from agent_workflow.application.webhook.receive_webhook import ReceiveWebhookUseCase
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult


class FakeWebhookInbox:
    def __init__(self, result=InboxResult.INSERTED):
        self.result = result
        self.calls = []

    def record(self, notification, body_digest):
        self.calls.append((notification, body_digest))
        return self.result


def valid_notification():
    return WebhookNotification(
        "0b989ba4-242f-11e5-81e1-c7b6966d2516",
        "workflow_run",
        "completed",
        "builacviet/agent-workflow",
        123,
        456,
        "a" * 40,
    )


@pytest.mark.parametrize(
    "result", [InboxResult.INSERTED, InboxResult.DUPLICATE, InboxResult.CONFLICT]
)
def test_receive_webhook_returns_repository_outcome(result):
    inbox = FakeWebhookInbox(result)
    notification = valid_notification()
    digest = "b" * 64

    assert ReceiveWebhookUseCase(inbox).execute(notification, digest) is result
    assert inbox.calls == [(notification, digest)]


@pytest.mark.parametrize(
    ("notification", "digest"),
    [
        (object(), "b" * 64),
        (replace(valid_notification(), delivery_id="invalid"), "b" * 64),
        (replace(valid_notification(), event=[]), "b" * 64),
        (replace(valid_notification(), action="requested"), "b" * 64),
        (replace(valid_notification(), repository="bad"), "b" * 64),
        (replace(valid_notification(), installation_id=True), "b" * 64),
        (replace(valid_notification(), subject_id=0), "b" * 64),
        (replace(valid_notification(), head_sha="A" * 40), "b" * 64),
        (valid_notification(), "not-a-digest"),
    ],
)
def test_invalid_direct_call_is_rejected_before_persistence(notification, digest):
    inbox = FakeWebhookInbox()

    with pytest.raises(InvalidApplicationRequestError, match="invalid webhook"):
        ReceiveWebhookUseCase(inbox).execute(notification, digest)

    assert inbox.calls == []


def test_untyped_repository_outcome_is_rejected():
    inbox = FakeWebhookInbox("inserted")

    with pytest.raises(ApplicationError, match="invalid webhook inbox result"):
        ReceiveWebhookUseCase(inbox).execute(valid_notification(), "b" * 64)

    assert len(inbox.calls) == 1
