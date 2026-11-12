"""R1-04B1 offline durability and remote-receipt negative tests.

No GitHub connection, Docker engine, process spawn, or production secrets.
These tests do not claim the worker-to-Controller publish handshake is wired.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.integrations.publication_receipt import (
    verify_remote_publication,
)
from agent_workflow.storage.dispatch_repository import DispatchLedger
from agent_workflow.storage.publication_intent import PublicationError, PublicationIntentStore


BASE = "a" * 40
HEAD = "b" * 40
DIGEST = "c" * 64
BRANCH = "codex/r1-04b1-fixture"
TASK = "R104B1"


def _spec(task_id=TASK):
    return {
        "task_id": task_id,
        "title": "Publication fault isolation",
        "description": "Offline synthetic fixture",
        "acceptance_criteria": ["No retry of uncertain external effects"],
        "definition_of_done": "Durable intent and independent receipt",
        "repository": REPOSITORY,
        "branch": BRANCH,
        "base_sha": BASE,
        "requested_effort": "medium",
        "risk_level": "low",
        "require_verified_effort": False,
        "context_paths": ["docs/handoff.md"],
        "synthetic": True,
    }


@pytest.fixture
def publication(tmp_path):
    path = tmp_path / "workflow.db"
    service = WorkflowService(path)
    service.provision_task(_spec())
    service.provision_task(_spec("NEXT"))
    ledger = DispatchLedger(service)
    claim = ledger.claim({TASK}, 60)
    assert claim is not None
    store = PublicationIntentStore(path)
    return path, ledger, claim, store


def _prepare(store, claim):
    store.prepare(
        claim["dispatch_id"], TASK, BRANCH, BASE, BASE, HEAD, DIGEST,
    )


def _remote(intent):
    branch = {"commit": {"sha": HEAD}}
    commit = {"sha": HEAD, "parents": [{"sha": BASE}]}
    prs = [{
        "number": 7,
        "html_url": f"https://github.com/{REPOSITORY}/pull/7",
        "state": "open", "draft": True,
        "user": {"login": "builacviet-wfc-coder[bot]"},
        "head": {
            "sha": HEAD, "ref": BRANCH, "repo": {"full_name": REPOSITORY},
        },
        "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
    }]
    return branch, commit, prs


def test_intent_survives_restart_before_any_remote_side_effect(publication):
    path, ledger, claim, store = publication
    _prepare(store, claim)
    restarted = PublicationIntentStore(path)
    assert restarted.get(claim["dispatch_id"])["state"] == "PREPARED"
    # A crash before a response is not permission to reattempt a push.
    with pytest.raises(PublicationError, match="never retry"):
        _prepare(restarted, claim)
    assert ledger.claim({"NEXT"}, 60) is None


def test_uncertain_remote_write_remains_blocked_even_after_valid_receipt(publication):
    path, ledger, claim, store = publication
    dispatch = claim["dispatch_id"]
    _prepare(store, claim)
    store.mark_uncertain(dispatch)
    store.mark_uncertain(dispatch)  # Idempotent state, no repeated external effect.
    restarted = PublicationIntentStore(path)
    intent = restarted.get(dispatch)
    assert intent["state"] == "UNCERTAIN"
    assert ledger.claim({"NEXT"}, 60) is None
    receipt = verify_remote_publication(intent, *_remote(intent))
    restarted.record_verified(dispatch, receipt.head_sha, receipt.pr_number, receipt.pr_url)
    assert PublicationIntentStore(path).get(dispatch)["state"] == "VERIFIED"
    assert ledger.claim({"NEXT"}, 60) is None
    with ledger.service.repository.raw_connection() as db:
        row = db.execute(
            "SELECT status,slot FROM coder_dispatches WHERE dispatch_id=?",
            (dispatch,),
        ).fetchone()
    assert (row["status"], row["slot"]) == ("BLOCKED", 1)
    with pytest.raises(PublicationError, match="receipt"):
        restarted.record_verified(dispatch, receipt.head_sha, receipt.pr_number, receipt.pr_url)


def test_receipt_cannot_be_recorded_without_uncertainty_and_held_slot(publication):
    _, ledger, claim, store = publication
    _prepare(store, claim)
    intent = store.get(claim["dispatch_id"])
    with pytest.raises(WorkflowError, match="unverified"):
        verify_remote_publication(intent, *_remote(intent))
    store.mark_uncertain(claim["dispatch_id"])
    intent = store.get(claim["dispatch_id"])
    receipt = verify_remote_publication(intent, *_remote(intent))
    # Simulate an unexpected external operator release; never trust stale slot.
    ledger.acknowledge(claim["dispatch_id"])
    with pytest.raises(PublicationError, match="blocked matching intent"):
        store.record_verified(claim["dispatch_id"], receipt.head_sha, receipt.pr_number, receipt.pr_url)


@pytest.mark.parametrize("mutation", [
    "branch_changed", "commit_parent", "missing_commit", "two_prs",
    "wrong_pr_sha", "wrong_repo", "not_draft", "not_bot", "wrong_base",
    "closed_pr", "bad_url", "missing_pr",
])
def test_remote_receipt_fails_closed_on_untrusted_evidence(publication, mutation):
    _, _, claim, store = publication
    _prepare(store, claim)
    store.mark_uncertain(claim["dispatch_id"])
    intent = store.get(claim["dispatch_id"])
    branch, commit, prs = deepcopy(_remote(intent))
    if mutation == "branch_changed":
        branch["commit"]["sha"] = "e" * 40
    elif mutation == "commit_parent":
        commit["parents"] = [{"sha": "e" * 40}]
    elif mutation == "missing_commit":
        commit = {}
    elif mutation == "two_prs":
        prs.append(deepcopy(prs[0]))
    elif mutation == "wrong_pr_sha":
        prs[0]["head"]["sha"] = "e" * 40
    elif mutation == "wrong_repo":
        prs[0]["head"]["repo"]["full_name"] = "other/repo"
    elif mutation == "not_draft":
        prs[0]["draft"] = False
    elif mutation == "not_bot":
        prs[0]["user"]["login"] = "someone-else"
    elif mutation == "wrong_base":
        prs[0]["base"]["ref"] = "work"
    elif mutation == "closed_pr":
        prs[0]["state"] = "closed"
    elif mutation == "bad_url":
        prs[0]["html_url"] = "https://other.invalid/7"
    else:
        prs = []
    with pytest.raises(WorkflowError, match="unverified"):
        verify_remote_publication(intent, branch, commit, prs)
    assert store.get(claim["dispatch_id"])["state"] == "UNCERTAIN"


@pytest.mark.parametrize("invalid", [
    "mismatch_task", "mismatch_branch", "same_head", "bad_digest", "missing_dispatch",
])
def test_only_active_authorized_dispatch_can_prepare_intent(publication, invalid):
    _, _, claim, store = publication
    args = [
        claim["dispatch_id"], TASK, BRANCH, BASE, BASE, HEAD, DIGEST,
    ]
    if invalid == "mismatch_task":
        args[1] = "NEXT"
    elif invalid == "mismatch_branch":
        args[2] = "codex/unauthorized"
    elif invalid == "same_head":
        args[5] = BASE
    elif invalid == "bad_digest":
        args[6] = "short"
    elif invalid == "missing_dispatch":
        args[0] = "0" * 32
    with pytest.raises(PublicationError):
        store.prepare(*args)
    with pytest.raises(PublicationError, match="unknown publication intent"):
        store.get(claim["dispatch_id"])


def test_unexpected_no_intent_cannot_mark_dispatch_uncertain(publication):
    _, ledger, claim, store = publication
    with pytest.raises(PublicationError, match="unavailable"):
        store.mark_uncertain(claim["dispatch_id"])
    # The original active slot remains held.
    assert ledger.claim({"NEXT"}, 60) is None


def test_persistence_rejects_unregistered_shadow_intent_table(tmp_path):
    import sqlite3

    path = tmp_path / "workflow.db"
    WorkflowService(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE publication_intents (dispatch_id TEXT PRIMARY KEY)")
    with pytest.raises(sqlite3.DatabaseError, match="unregistered"):
        PublicationIntentStore(path)
