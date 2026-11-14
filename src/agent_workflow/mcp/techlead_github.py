"""Review-only GitHub App client and bounded HTTP helpers for the TL tools."""

from __future__ import annotations

import asyncio
import json
import re
import stat
import time
from datetime import datetime
from pathlib import Path

import httpx
import jwt

from agent_workflow.app_config import TechLeadGitHubConfig

_REPOSITORY = "builacviet/agent-workflow"
_REPO_PATH = f"/repos/{_REPOSITORY}"
_PERMISSIONS = {"metadata": "read", "pull_requests": "write"}
MUTATION_UNVERIFIED = "BLOCKED: mutation outcome unverified; reconcile before retrying"


class TechLeadError(RuntimeError):
    """Fixed messages safe to surface through MCP."""


def read_secret(path: Path, limit: int) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        if str(resolved).startswith(("//", "\\\\")):
            raise ValueError
        if not stat.S_ISREG(resolved.stat().st_mode) or any(
            (parent / ".git").exists() for parent in resolved.parents
        ):
            raise ValueError
        with resolved.open("rb") as handle:
            data = handle.read(limit + 1)
            value = data.strip(b"\r\n")
            if len(data) > limit or not value:
                raise ValueError
        return value
    except (OSError, RuntimeError, ValueError):
        raise TechLeadError("Tech Lead secret file unavailable") from None


def bearer_token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[!-~]{1,4096}", value):
        raise TechLeadError("Invalid Tech Lead token")
    return value


def validate_body(body: str) -> None:
    if not isinstance(body, str) or not 1 <= len(body) <= 2000 or not body.strip():
        raise TechLeadError("Comment body must contain 1–2000 characters")
    try:
        body.encode("utf-8")
    except UnicodeError:
        raise TechLeadError("Invalid comment body encoding") from None
    if any(ord(char) < 32 and char not in "\r\n\t" for char in body):
        raise TechLeadError("Invalid comment body characters")


def object_field(value: object) -> dict:
    if not isinstance(value, dict):
        raise TechLeadError("Unexpected provider response")
    return value


def positive_id(value: object) -> bool:
    return type(value) is int and 0 < value <= 2**63 - 1


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    token: str,
    *,
    expected_status: int = 200,
    payload: dict | None = None,
    mutation: bool = False,
) -> dict:
    """Use a fixed provider client and make exactly one bounded request."""
    try:
        async with asyncio.timeout(6):
            async with client.stream(
                method,
                path,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            ) as response:
                status = response.status_code
                if status != expected_status:
                    if mutation and (
                        status >= 500 or 200 <= status < 400 or status == 408
                    ):
                        raise TechLeadError(MUTATION_UNVERIFIED)
                    raise TechLeadError(
                        f"Tech Lead provider rejected request (HTTP {status})"
                    )
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    data.extend(chunk)
                    if len(data) > 256_000:
                        raise ValueError
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError
                return result
    except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
        message = (
            MUTATION_UNVERIFIED
            if mutation
            else "Tech Lead provider response unavailable or invalid"
        )
        raise TechLeadError(message) from None


def provider_client(
    origin: str, headers: dict[str, str], transport: httpx.AsyncBaseTransport | None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=origin,
        headers={"Accept-Encoding": "identity", **headers},
        timeout=httpx.Timeout(5.0, connect=2.0),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )


class TechLeadGitHub:
    def __init__(
        self,
        config: TechLeadGitHubConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        signer=jwt.encode,
        clock=time.time,
    ) -> None:
        self.config = config
        self.transport = transport
        self.signer = signer
        self.clock = clock

    def _client(self) -> httpx.AsyncClient:
        return provider_client(
            "https://api.github.com",
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            self.transport,
        )

    def _assertion(self) -> str:
        private_key = read_secret(self.config.key_file, 16_384)
        now = int(self.clock())
        try:
            return self.signer(
                {"iat": now - 60, "exp": now + 480, "iss": str(self.config.app_id)},
                private_key,
                algorithm="RS256",
            )
        except (jwt.PyJWTError, ValueError, TypeError, UnicodeError):
            raise TechLeadError("Invalid Tech Lead GitHub App key") from None

    async def _authenticate(self, client: httpx.AsyncClient) -> tuple[str, dict]:
        config = self.config
        assertion = self._assertion()
        app = await request_json(client, "GET", "/app", assertion)
        if (
            not positive_id(app.get("id"))
            or app["id"] != config.app_id
            or app.get("slug") != config.app_slug
            or app.get("permissions") != _PERMISSIONS
        ):
            raise TechLeadError("Tech Lead GitHub App identity or permissions mismatch")
        installation = await request_json(
            client, "GET", f"{_REPO_PATH}/installation", assertion
        )
        if (
            not positive_id(installation.get("id"))
            or installation["id"] != config.installation_id
            or not positive_id(installation.get("app_id"))
            or installation["app_id"] != config.app_id
            or installation.get("app_slug") != config.app_slug
            or installation.get("suspended_at", False) is not None
            or installation.get("permissions") != _PERMISSIONS
        ):
            raise TechLeadError(
                "Tech Lead GitHub installation identity or permissions mismatch"
            )
        issued = await request_json(
            client,
            "POST",
            f"/app/installations/{config.installation_id}/access_tokens",
            assertion,
            expected_status=201,
            payload={"repositories": ["agent-workflow"], "permissions": _PERMISSIONS},
            mutation=True,
        )
        repositories = issued.get("repositories")
        if (
            issued.get("permissions") != _PERMISSIONS
            or not isinstance(repositories, list)
            or len(repositories) != 1
            or object_field(repositories[0]).get("full_name") != _REPOSITORY
        ):
            raise TechLeadError("Tech Lead GitHub installation token scope mismatch")
        token = bearer_token(issued.get("token"))
        try:
            expires = datetime.fromisoformat(issued["expires_at"])
            if expires.utcoffset() is None or not (
                self.clock() < expires.timestamp() <= self.clock() + 3660
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise TechLeadError(
                "Invalid or expired Tech Lead GitHub installation token"
            ) from None
        # This endpoint requires the installation token. The public bot profile
        # alone cannot verify that the token is accepted or scoped to this repo.
        accessible = await request_json(
            client, "GET", "/installation/repositories", token
        )
        repos = accessible.get("repositories")
        if (
            type(accessible.get("total_count")) is not int
            or accessible["total_count"] != 1
            or not isinstance(repos, list)
            or len(repos) != 1
            or object_field(repos[0]).get("full_name") != _REPOSITORY
        ):
            raise TechLeadError("Tech Lead GitHub installation repository mismatch")
        bot = await request_json(client, "GET", f"/users/{config.bot_login}", token)
        if not self._is_bot(bot):
            raise TechLeadError("Tech Lead GitHub bot identity mismatch")
        return token, {
            "app_id": config.app_id,
            "app_slug": config.app_slug,
            "installation_id": config.installation_id,
            "login": config.bot_login,
            "id": config.bot_id,
            "type": "Bot",
        }

    def _is_bot(self, bot: dict) -> bool:
        return (
            bot.get("login") == self.config.bot_login
            and bot.get("type") == "Bot"
            and positive_id(bot.get("id"))
            and bot["id"] == self.config.bot_id
        )

    async def identity(self) -> dict:
        async with self._client() as client:
            _, identity = await self._authenticate(client)
        return identity

    async def _get_pr(
        self, client: httpx.AsyncClient, token: str, pr_number: int
    ) -> dict:
        pr = await request_json(client, "GET", f"{_REPO_PATH}/pulls/{pr_number}", token)
        repo = object_field(object_field(pr.get("base")).get("repo"))
        sha = object_field(pr.get("head")).get("sha")
        state = pr.get("state")
        if (
            not positive_id(pr.get("number"))
            or pr["number"] != pr_number
            or repo.get("full_name") != _REPOSITORY
            or not _valid_sha(sha)
            or state not in ("open", "closed")
            or type(pr.get("draft")) is not bool
        ):
            raise TechLeadError("Unexpected GitHub pull request metadata")
        return {
            "repository": _REPOSITORY,
            "number": pr_number,
            "head_sha": sha,
            "state": state,
            "draft": pr["draft"],
            "url": f"https://github.com/{_REPOSITORY}/pull/{pr_number}",
        }

    async def get_pr(self, pr_number: int) -> dict:
        if not positive_id(pr_number):
            raise TechLeadError("Invalid GitHub pull request number")
        async with self._client() as client:
            token, _ = await self._authenticate(client)
            return await self._get_pr(client, token, pr_number)

    async def comment_review(
        self, pr_number: int, expected_head_sha: str, body: str
    ) -> dict:
        if not positive_id(pr_number) or pr_number not in self.config.test_prs:
            raise TechLeadError("GitHub pull request is outside the test allowlist")
        if not _valid_sha(expected_head_sha):
            raise TechLeadError("Invalid GitHub HEAD SHA")
        validate_body(body)
        async with self._client() as client:
            token, _ = await self._authenticate(client)
            pr = await self._get_pr(client, token, pr_number)
            if pr["state"] != "open" or pr["head_sha"] != expected_head_sha:
                raise TechLeadError("GitHub pull request is closed or its HEAD changed")
            review = await request_json(
                client,
                "POST",
                f"{_REPO_PATH}/pulls/{pr_number}/reviews",
                token,
                payload={
                    "event": "COMMENT",
                    "commit_id": expected_head_sha,
                    "body": body,
                },
                mutation=True,
            )
        if (
            not positive_id(review.get("id"))
            or review.get("commit_id") != expected_head_sha
            or review.get("state") != "COMMENTED"
            or not isinstance(review.get("user"), dict)
            or not self._is_bot(review["user"])
        ):
            raise TechLeadError(MUTATION_UNVERIFIED)
        return {
            "id": review["id"],
            "commit_id": expected_head_sha,
            "author": self.config.bot_login,
            "url": f"{pr['url']}#pullrequestreview-{review['id']}",
        }


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None
