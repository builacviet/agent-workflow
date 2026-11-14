"""Offline tests for the dedicated MCP Tech Lead trust boundary."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError

from agent_workflow.app_config import (
    AppConfig,
    TechLeadGitHubConfig,
    TechLeadNotionConfig,
)
from agent_workflow.mcp.server import create_mcp_server, get_server_info, ping
from agent_workflow.mcp.techlead_github import TechLeadError, TechLeadGitHub
from agent_workflow.mcp.techlead_notion import TechLeadNotion
from agent_workflow.mcp.tools import register_tools

REPO = "builacviet/agent-workflow"
PULL = f"/repos/{REPO}/pulls/42"
HEAD = "a" * 40
BOT_ID = "29a9989d-472f-4470-a550-d67a62b63cc3"
PAGE = "418f9201-4abd-814f-bb4a-f8e2e7d135bf"
OTHER = "b55c9c91-384d-452b-81db-d1ef79372b75"
COMMENT = "3c90c3cc-0d44-4b50-8888-8dd25736052a"
GH_TOKEN = "synthetic_installation.header.signature"
NOTION_TOKEN = "synthetic_notion_token"
APP_ASSERTION = "synthetic.app.assertion"
PERMISSIONS = {"metadata": "read", "pull_requests": "write"}
LEGACY = {
    "ping",
    "get_server_info",
    "list_review_queue",
    "get_review_context",
    "claim_review",
    "submit_review",
    "get_review_result",
    "register_task_handoff",
    "get_coder_assignment",
}
NEW = {
    "tl_github_identity": {},
    "tl_github_get_pr": {"pr_number": 42},
    "tl_github_comment_review": {
        "pr_number": 42,
        "expected_head_sha": HEAD,
        "body": "x",
    },
    "tl_notion_identity": {},
    "tl_notion_get_task": {"page_id": PAGE},
    "tl_notion_comment": {"page_id": PAGE, "body": "x"},
}


@pytest.fixture
def auth_env(monkeypatch):
    for setting in (
        "WFC_OAUTH_EXPECTED_CLIENT_ID",
        *("TECHLEAD_" + name for name in ("GITHUB_APP_SLUG", "NOTION_BOT_ID")),
    ):
        monkeypatch.delenv(setting, raising=False)
    for setting, value in {
        "WFC_AUTH_MODE": "auth0",
        "WFC_POC_NO_AUTH": "0",
        "WFC_PUBLIC_HOST": "test.example.org",
        "WFC_AUTH0_ISSUER": "https://example.us.auth0.com/",
        "WFC_AUTH0_ALLOWED_SUB": "auth0|test",
        "WFC_AUTH0_CLIENT_ID": "https://chatgpt.com/oauth/client.json",
        "WFC_AUTH0_SCOPE": "wfc:workflow",
    }.items():
        monkeypatch.setenv(setting, value)


def _server():
    mcp = create_mcp_server()
    mcp.tool()(ping)
    mcp.tool()(get_server_info)
    register_tools(mcp)
    return mcp


def test_optional_tools_preserve_nine_legacy_schemas(auth_env, monkeypatch):
    oauth = asyncio.run(_server().list_tools())
    assert {tool.name for tool in oauth} == LEGACY | NEW.keys()
    monkeypatch.setenv("WFC_AUTH_MODE", "bearer")
    legacy = asyncio.run(_server().list_tools())
    assert {tool.name for tool in legacy} == LEGACY
    assert {tool.name: tool.inputSchema for tool in legacy} == {
        tool.name: tool.inputSchema for tool in oauth if tool.name in LEGACY
    }
    for tool in oauth:
        if tool.name in NEW:
            assert set(tool.inputSchema["properties"]) == set(NEW[tool.name])


@pytest.mark.parametrize(
    "case, message",
    [
        ("missing_identity", "OAuth authorization required"),
        ("wrong_scope", "OAuth authorization required"),
        ("wrong_subject", "OAuth authorization required"),
        ("scope_collision", "OAuth authorization required"),
        ("bearer", "require Auth0"),
        ("anonymous", "require Auth0"),
        ("disabled", "connection disabled or invalid"),
    ],
)
def test_every_tool_checks_auth0_and_config_before_io(
    auth_env, monkeypatch, case, message
):
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    server = _server()
    scopes = ["wfc:workflow", "wfc:techlead"]
    subject = "auth0|test"
    if case == "wrong_scope":
        scopes = ["wfc:workflow", "wfc:techlead:other"]
    if case == "scope_collision":
        monkeypatch.setenv("WFC_AUTH0_SCOPE", "wfc:techlead")
    if case == "wrong_subject":
        subject = "auth0|someone"
    if case == "bearer":
        monkeypatch.setenv("WFC_AUTH_MODE", "bearer")
    if case == "anonymous":
        monkeypatch.setenv("WFC_POC_NO_AUTH", "1")
    verified = AccessToken(
        token="synthetic", client_id="test", scopes=scopes, subject=subject
    )

    async def exercise():
        reset = auth_context_var.set(
            None if case == "missing_identity" else AuthenticatedUser(verified)
        )
        try:
            for tool, args in NEW.items():
                with pytest.raises(ToolError, match=message):
                    await server.call_tool(tool, args)
        finally:
            auth_context_var.reset(reset)

    def no_http(*args, **kwargs):
        pytest.fail("rejected invocation must not create any HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", no_http)
    asyncio.run(exercise())


def test_git_config_does_not_accept_coder_or_controller(tmp_path):
    values = {
        "TECHLEAD_GITHUB_APP_ID": "11",
        "TECHLEAD_GITHUB_INSTALLATION_ID": "22",
        "TECHLEAD_GITHUB_APP_SLUG": "fixture-techlead",
        "TECHLEAD_GITHUB_BOT_ID": "33",
        "TECHLEAD_GITHUB_KEY_FILE": str(tmp_path / "key.pem"),
        "TECHLEAD_GITHUB_TEST_PRS": "42, 43",
    }
    assert AppConfig.techlead_github(values).test_prs == {42, 43}
    for changes in (
        {"TECHLEAD_GITHUB_APP_SLUG": "builacviet-wfc-coder"},
        {"WFC_CONTROLLER_INSTALLATION_ID": "22"},
        {"TECHLEAD_GITHUB_INSTALLATION_ID": "0"},
        {"TECHLEAD_GITHUB_KEY_FILE": "relative.pem"},
        {"TECHLEAD_GITHUB_TEST_PRS": "42,/anything"},
    ):
        with pytest.raises(ValueError, match="connection disabled or invalid"):
            AppConfig.techlead_github(values | changes)


def test_notion_config_requires_explicit_test_subset(tmp_path):
    values = {
        "TECHLEAD_NOTION_BOT_ID": BOT_ID,
        "TECHLEAD_NOTION_TOKEN_FILE": str(tmp_path / "token"),
        "TECHLEAD_NOTION_TASK_PAGES": PAGE.replace("-", "").upper(),
    }
    assert AppConfig.techlead_notion(values).task_pages == {PAGE}
    assert AppConfig.techlead_notion(values).test_pages == set()
    for changes in (
        {"TECHLEAD_NOTION_TEST_PAGES": OTHER},
        {"TECHLEAD_NOTION_TEST_PAGES": f"https://notion.so/{PAGE}"},
        {"TECHLEAD_NOTION_BOT_ID": ""},
        {"TECHLEAD_NOTION_TOKEN_FILE": "relative"},
    ):
        with pytest.raises(ValueError, match="connection disabled or invalid"):
            AppConfig.techlead_notion(values | changes)


@pytest.fixture
def github(tmp_path):
    pem = tmp_path / "fixture.pem"
    pem.write_bytes(b"synthetic PEM fixture")
    config = TechLeadGitHubConfig(11, 22, "fixture-techlead", 33, pem, frozenset({42}))
    bot = {"id": 33, "login": config.bot_login, "type": "Bot"}
    app = {"id": 11, "slug": config.app_slug, "permissions": PERMISSIONS}
    installation = {
        "id": 22,
        "app_id": 11,
        "app_slug": config.app_slug,
        "suspended_at": None,
        "permissions": PERMISSIONS,
    }
    scoped = {"repositories": [{"full_name": REPO}]}
    issued = {
        **scoped,
        "token": GH_TOKEN,
        "expires_at": "2023-11-14T22:30:00Z",
        "permissions": PERMISSIONS,
    }
    pr = {
        "number": 42,
        "base": {"repo": {"full_name": REPO}},
        "head": {"sha": HEAD},
        "state": "open",
        "draft": True,
        "body": GH_TOKEN,
    }
    paths = {
        "/app": httpx.Response(200, json=app),
        f"/repos/{REPO}/installation": httpx.Response(200, json=installation),
        "/app/installations/22/access_tokens": httpx.Response(201, json=issued),
        "/installation/repositories": httpx.Response(
            200, json={**scoped, "total_count": 1}
        ),
        "/users/fixture-techlead[bot]": httpx.Response(200, json=bot),
        PULL: httpx.Response(200, json=pr),
        f"{PULL}/reviews": httpx.Response(
            200, json={"id": 50, "commit_id": HEAD, "state": "COMMENTED", "user": bot}
        ),
    }
    calls = []

    def handle(request):
        assert request.url.scheme == "https" and request.url.host == "api.github.com"
        calls.append(request)
        path = request.url.path
        expected = (
            APP_ASSERTION
            if path.startswith("/app") or path.endswith("/installation")
            else GH_TOKEN
        )
        assert request.headers["Authorization"] == f"Bearer {expected}"
        return paths[path]

    def signer(claims, key, *, algorithm):
        assert key == b"synthetic PEM fixture"
        assert claims == {"iat": 1_699_999_940, "exp": 1_700_000_480, "iss": "11"}
        assert algorithm == "RS256"
        return APP_ASSERTION

    client = TechLeadGitHub(
        config,
        transport=httpx.MockTransport(handle),
        signer=signer,
        clock=lambda: 1_700_000_000,
    )
    return client, paths, calls


def test_github_identity_and_minimal_pr_projection(github):
    client, _, calls = github
    actor = asyncio.run(client.identity())
    assert actor == {
        "app_id": 11,
        "app_slug": "fixture-techlead",
        "installation_id": 22,
        "login": "fixture-techlead[bot]",
        "id": 33,
        "type": "Bot",
    }
    assert json.loads(calls[2].content) == {
        "repositories": ["agent-workflow"],
        "permissions": PERMISSIONS,
    }
    calls.clear()
    assert asyncio.run(client.get_pr(42)) == {
        "repository": REPO,
        "number": 42,
        "head_sha": HEAD,
        "state": "open",
        "draft": True,
        "url": f"https://github.com/{REPO}/pull/42",
    }
    assert calls[-1].url.path == PULL


def test_github_review_only_comment_pinned_to_fresh_head(github):
    client, _, calls = github
    result = asyncio.run(client.comment_review(42, HEAD, "fixture comment"))
    assert [request.method for request in calls[-2:]] == ["GET", "POST"]
    assert [request.url.path for request in calls[-2:]] == [PULL, f"{PULL}/reviews"]
    assert json.loads(calls[-1].content) == {
        "event": "COMMENT",
        "commit_id": HEAD,
        "body": "fixture comment",
    }
    assert result["author"] == "fixture-techlead[bot]"
    assert result["url"] == f"https://github.com/{REPO}/pull/42#pullrequestreview-50"


@pytest.mark.parametrize(
    "path, patch, error",
    [
        ("/app", {"id": 12}, "App identity"),
        ("/app", {"permissions": {**PERMISSIONS, "contents": "write"}}, "permissions"),
        (f"/repos/{REPO}/installation", {"id": 23}, "installation identity"),
        (
            f"/repos/{REPO}/installation",
            {"suspended_at": "2026-01-01"},
            "installation identity",
        ),
        (
            "/app/installations/22/access_tokens",
            {"token": ""},
            "Invalid Tech Lead token",
        ),
        (
            "/app/installations/22/access_tokens",
            {"expires_at": "2023-11-01T00:00:00Z"},
            "expired",
        ),
        (
            "/app/installations/22/access_tokens",
            {"permissions": {"metadata": "read"}},
            "scope mismatch",
        ),
        (
            "/app/installations/22/access_tokens",
            {"repositories": [{"full_name": "another/repo"}]},
            "scope mismatch",
        ),
        ("/installation/repositories", {"total_count": 2}, "repository mismatch"),
        ("/users/fixture-techlead[bot]", {"type": "User"}, "bot identity"),
        ("/users/fixture-techlead[bot]", {"id": 34}, "bot identity"),
    ],
)
def test_github_fails_closed_before_review(github, path, patch, error):
    client, paths, calls = github
    original = paths[path]
    paths[path] = httpx.Response(original.status_code, json=original.json() | patch)
    with pytest.raises(TechLeadError, match=error):
        asyncio.run(client.comment_review(42, HEAD, "fixture comment"))
    assert all(not request.url.path.endswith("/reviews") for request in calls)


@pytest.mark.parametrize(
    "patch, error",
    [
        ({"head": {"sha": "b" * 40}}, "HEAD changed"),
        ({"state": "closed"}, "HEAD changed"),
        ({"base": {"repo": {"full_name": "unapproved/repo"}}}, "Unexpected GitHub"),
    ],
)
def test_invalid_or_stale_pr_prevents_write(github, patch, error):
    client, paths, calls = github
    paths[PULL] = httpx.Response(200, json=paths[PULL].json() | patch)
    with pytest.raises(TechLeadError, match=error):
        asyncio.run(client.comment_review(42, HEAD, "fixture comment"))
    assert calls[-1].url.path == PULL


@pytest.mark.parametrize(
    "number, sha, body",
    [
        (41, HEAD, "x"),
        (True, HEAD, "x"),
        ("42", HEAD, "x"),
        (42, "main", "x"),
        (42, HEAD, " "),
        (42, HEAD, "x" * 2001),
        (42, HEAD, "\0"),
    ],
)
def test_invalid_review_arguments_never_contact_provider(github, number, sha, body):
    client, _, calls = github
    with pytest.raises(TechLeadError):
        asyncio.run(client.comment_review(number, sha, body))
    assert calls == []


@pytest.fixture
def notion(tmp_path):
    secret = tmp_path / "notion"
    secret.write_text(NOTION_TOKEN, encoding="ascii")
    config = TechLeadNotionConfig(
        BOT_ID, secret, frozenset({PAGE, OTHER}), frozenset({PAGE})
    )
    user = {
        "object": "user",
        "id": BOT_ID,
        "type": "bot",
        "bot": {"owner": {"type": "workspace"}},
    }
    text = [{"plain_text": "Example task"}]
    paths = {
        "/v1/users/me": httpx.Response(200, json=user),
        f"/v1/pages/{PAGE}": httpx.Response(
            200,
            json={
                "object": "page",
                "id": PAGE,
                "properties": {"Name": {"type": "title", "title": text}},
            },
        ),
        f"/v1/blocks/{PAGE}/children": httpx.Response(
            200,
            json={
                "object": "list",
                "has_more": True,
                "next_cursor": "discard",
                "results": [
                    {
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {
                            "rich_text": [{"plain_text": NOTION_TOKEN + " test"}]
                        },
                    }
                ],
            },
        ),
        "/v1/comments": httpx.Response(
            200,
            json={
                "object": "comment",
                "id": COMMENT,
                "created_by": {"object": "user", "id": BOT_ID},
                "parent": {"type": "page_id", "page_id": PAGE},
            },
        ),
    }
    calls = []

    async def handle(request):
        assert request.url.scheme == "https" and request.url.host == "api.notion.com"
        assert request.headers["Authorization"] == f"Bearer {NOTION_TOKEN}"
        calls.append(request)
        response = paths[request.url.path]
        if isinstance(response, Exception):
            raise response
        return response

    client = TechLeadNotion(config, transport=httpx.MockTransport(handle))
    return client, paths, calls


def test_notion_identity_and_task_projection(notion):
    client, _, calls = notion
    assert asyncio.run(client.identity()) == {"id": BOT_ID, "type": "bot"}
    task = asyncio.run(client.get_task(PAGE.replace("-", "").upper()))
    assert task == {
        "id": PAGE,
        "title": "Example task",
        "truncated": True,
        "blocks": [{"type": "paragraph", "text": "[redacted] test"}],
    }
    assert dict(calls[-1].url.params) == {"page_size": "20"}
    assert len(calls) == 4


def test_notion_comment_returns_bound_author_and_parent(notion):
    client, _, calls = notion
    assert asyncio.run(client.comment(PAGE, "test comment")) == {
        "id": COMMENT,
        "page_id": PAGE,
        "author_id": BOT_ID,
    }
    assert [request.method for request in calls] == ["GET", "POST"]
    assert json.loads(calls[-1].content) == {
        "parent": {"page_id": PAGE},
        "rich_text": [{"type": "text", "text": {"content": "test comment"}}],
    }


@pytest.mark.parametrize(
    "patch", [{"id": OTHER}, {"type": "person"}, {"bot": {"owner": None}}]
)
def test_wrong_notion_identity_prevents_comment(notion, patch):
    client, paths, calls = notion
    paths["/v1/users/me"] = httpx.Response(
        200, json=paths["/v1/users/me"].json() | patch
    )
    with pytest.raises(TechLeadError, match="bot identity mismatch"):
        asyncio.run(client.comment(PAGE, "x"))
    assert [call.url.path for call in calls] == ["/v1/users/me"]


def test_notion_input_and_secret_checks_precede_io(notion, tmp_path):
    client, _, calls = notion
    for page_id in ("https://notion.so/" + PAGE, "f" * 32):
        with pytest.raises(TechLeadError, match="allowlist"):
            asyncio.run(client.get_task(page_id))
    with pytest.raises(TechLeadError, match="allowlist"):
        asyncio.run(client.comment(OTHER, "x"))
    with pytest.raises(TechLeadError, match="body"):
        asyncio.run(client.comment(PAGE, ""))
    with pytest.raises(TechLeadError, match="secret file"):
        asyncio.run(
            TechLeadNotion(
                replace(client.config, token_file=tmp_path / "missing"),
                transport=client.transport,
            ).identity()
        )
    client.config.token_file.write_bytes(b"")
    with pytest.raises(TechLeadError, match="secret file"):
        asyncio.run(client.identity())
    assert calls == []


@pytest.mark.parametrize(
    "response, message",
    [
        (
            httpx.Response(302, headers={"Location": "https://another.example"}),
            "HTTP 302",
        ),
        (httpx.Response(401, text=NOTION_TOKEN), "HTTP 401"),
        (httpx.Response(429, text=NOTION_TOKEN), "HTTP 429"),
        (httpx.Response(503, text=NOTION_TOKEN), "HTTP 503"),
        (httpx.ReadTimeout(NOTION_TOKEN), "unavailable or invalid"),
        (httpx.Response(200, content=b"x" * 256_001), "unavailable or invalid"),
        (
            httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=httpx.ByteStream(b"{}"),
            ),
            "unavailable or invalid",
        ),
    ],
)
def test_transport_rejects_and_sanitizes_failures(notion, response, message):
    client, paths, calls = notion
    paths["/v1/users/me"] = response
    with pytest.raises(TechLeadError, match=message) as failure:
        asyncio.run(client.identity())
    assert NOTION_TOKEN not in str(failure.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.ReadTimeout(NOTION_TOKEN),
        httpx.Response(503, text=NOTION_TOKEN),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"id": COMMENT, "created_by": {"id": OTHER}}),
    ],
)
def test_uncertain_notion_write_requires_reconciliation_without_retry(notion, response):
    client, paths, calls = notion
    paths["/v1/comments"] = response
    with pytest.raises(TechLeadError, match="BLOCKED.*reconcile") as failure:
        asyncio.run(client.comment(PAGE, "fixture"))
    assert NOTION_TOKEN not in str(failure.value)
    assert [request.method for request in calls] == ["GET", "POST"]


def test_uncertain_github_review_requires_reconciliation_without_retry(github):
    client, paths, calls = github
    paths[f"{PULL}/reviews"] = httpx.Response(502, text=GH_TOKEN)
    with pytest.raises(TechLeadError, match="BLOCKED.*reconcile") as failure:
        asyncio.run(client.comment_review(42, HEAD, "fixture"))
    assert GH_TOKEN not in str(failure.value)
    assert [request.url.path for request in calls].count(f"{PULL}/reviews") == 1


def test_verified_scope_can_call_notion_tool(auth_env, monkeypatch, notion):
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    client, _, calls = notion
    monkeypatch.setenv("TECHLEAD_NOTION_BOT_ID", BOT_ID)
    monkeypatch.setenv("TECHLEAD_NOTION_TOKEN_FILE", str(client.config.token_file))
    monkeypatch.setattr(
        "agent_workflow.mcp.tools.TechLeadNotion",
        lambda config: TechLeadNotion(config, transport=client.transport),
    )
    server = _server()
    assert calls == []

    async def exercise():
        verified = AccessToken(
            token="synthetic",
            client_id="test",
            scopes=["wfc:workflow", "wfc:techlead"],
            subject="auth0|test",
        )
        reset = auth_context_var.set(AuthenticatedUser(verified))
        try:
            output = await server.call_tool("tl_notion_identity", {})
            assert json.loads(output[0].text) == {"id": BOT_ID, "type": "bot"}
        finally:
            auth_context_var.reset(reset)

    asyncio.run(exercise())
    assert [request.url.path for request in calls] == ["/v1/users/me"]
