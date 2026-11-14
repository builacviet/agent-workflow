"""Notion task reads and test-page comments using the dedicated TL integration."""

from __future__ import annotations

import httpx

from agent_workflow.app_config import TechLeadNotionConfig, notion_uuid
from agent_workflow.mcp.techlead_github import (
    MUTATION_UNVERIFIED,
    TechLeadError,
    bearer_token,
    object_field,
    provider_client,
    read_secret,
    request_json,
    validate_body,
)

_TEXT_BLOCKS = {
    "bulleted_list_item",
    "callout",
    "code",
    "heading_1",
    "heading_2",
    "heading_3",
    "numbered_list_item",
    "paragraph",
    "quote",
    "to_do",
    "toggle",
}


def _plain_text(rich_text: object, token: str) -> str:
    if not isinstance(rich_text, list):
        raise TechLeadError("Unexpected Notion rich text")
    parts = []
    for item in rich_text:
        text = object_field(item).get("plain_text")
        if not isinstance(text, str):
            raise TechLeadError("Unexpected Notion rich text")
        parts.append(text)
    return "".join(parts).replace(token, "[redacted]")


class TechLeadNotion:
    def __init__(
        self,
        config: TechLeadNotionConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return provider_client(
            "https://api.notion.com",
            {"Notion-Version": "2022-06-28"},
            self.transport,
        )

    async def _authenticate(self, client: httpx.AsyncClient) -> str:
        try:
            token = bearer_token(
                read_secret(self.config.token_file, 4096).decode("ascii")
            )
        except UnicodeError:
            raise TechLeadError("Invalid Tech Lead Notion token") from None
        user = await request_json(client, "GET", "/v1/users/me", token)
        bot = user.get("bot")
        owner = bot.get("owner") if isinstance(bot, dict) else None
        if (
            user.get("object") != "user"
            or user.get("type") != "bot"
            or notion_uuid(user.get("id")) != self.config.bot_id
            or not isinstance(owner, dict)
            or owner.get("type") != "workspace"
        ):
            raise TechLeadError("Tech Lead Notion internal bot identity mismatch")
        return token

    async def identity(self) -> dict:
        async with self._client() as client:
            await self._authenticate(client)
        return {"id": self.config.bot_id, "type": "bot"}

    def _page_id(self, page_id: str, allowlist: frozenset[str]) -> str:
        normalized = notion_uuid(page_id)
        if normalized is None or normalized not in allowlist:
            raise TechLeadError("Notion page is outside the allowlist")
        return normalized

    async def get_task(self, page_id: str) -> dict:
        page_id = self._page_id(page_id, self.config.task_pages)
        async with self._client() as client:
            token = await self._authenticate(client)
            page = await request_json(client, "GET", f"/v1/pages/{page_id}", token)
            if page.get("object") != "page" or notion_uuid(page.get("id")) != page_id:
                raise TechLeadError("Unexpected Notion task identity")
            children = await request_json(
                client, "GET", f"/v1/blocks/{page_id}/children?page_size=20", token
            )

        title_property = next(
            (
                prop
                for prop in object_field(page.get("properties")).values()
                if object_field(prop).get("type") == "title"
            ),
            {},
        )
        title = _plain_text(title_property.get("title", []), token)
        blocks = children.get("results")
        if (
            children.get("object") != "list"
            or not isinstance(blocks, list)
            or len(blocks) > 20
            or type(children.get("has_more")) is not bool
        ):
            raise TechLeadError("Unexpected Notion task blocks")
        preview = []
        truncated = children["has_more"] or len(title) > 300
        for block in blocks:
            block = object_field(block)
            kind = block.get("type")
            if not isinstance(kind, str) or kind not in _TEXT_BLOCKS:
                truncated = True
                continue
            text = _plain_text(object_field(block.get(kind)).get("rich_text"), token)
            preview.append({"type": kind, "text": text[:400]})
            if len(text) > 400 or block.get("has_children"):
                truncated = True
        return {
            "id": page_id,
            "title": title[:300],
            "blocks": preview,
            "truncated": truncated,
        }

    async def comment(self, page_id: str, body: str) -> dict:
        page_id = self._page_id(page_id, self.config.test_pages)
        validate_body(body)
        async with self._client() as client:
            token = await self._authenticate(client)
            result = await request_json(
                client,
                "POST",
                "/v1/comments",
                token,
                payload={
                    "parent": {"page_id": page_id},
                    "rich_text": [{"type": "text", "text": {"content": body}}],
                },
                mutation=True,
            )

        comment_id = notion_uuid(result.get("id"))
        creator = result.get("created_by")
        # Notion versions can identify a top-level comment's parent as its page
        # or as the root block (which shares that page's ID).
        parent = result.get("parent")
        if (
            result.get("object") != "comment"
            or comment_id is None
            or not isinstance(creator, dict)
            or notion_uuid(creator.get("id")) != self.config.bot_id
            or not isinstance(parent, dict)
            or parent.get("type") not in ("page_id", "block_id")
            or notion_uuid(parent.get(parent["type"])) != page_id
        ):
            raise TechLeadError(MUTATION_UNVERIFIED)
        return {"id": comment_id, "page_id": page_id, "author_id": self.config.bot_id}
