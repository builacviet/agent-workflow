"""Boundary authentication: transport headers, never LLM tool arguments."""

import hmac
import os
from pathlib import Path


def poc_unauthenticated_enabled() -> bool:
    """Explicitly unsafe POC mode, restricted to a dedicated disposable SQLite file.

    This does NOT make an Internet-exposed MCP endpoint private. Anyone holding
    the tunnel URL may invoke all seven workflow tools while this mode is active.
    """
    if os.environ.get("WFC_POC_NO_AUTH") != "1":
        return False
    raw_path = os.environ.get("WFC_DB_PATH", "")
    path = Path(raw_path).expanduser() if raw_path else None
    if (
        path is None
        or not path.is_absolute()
        or path.name != "workflow-poc-unauth.sqlite3"
    ):
        raise PermissionError(
            "WFC_POC_NO_AUTH requires WFC_DB_PATH to be an absolute "
            "path named workflow-poc-unauth.sqlite3"
        )
    return True


def authorize_request(request) -> str:
    if poc_unauthenticated_enabled():
        return "poc-unauthenticated"
    token = os.environ.get("WFC_AUTH_TOKEN", "")
    reviewer = os.environ.get("WFC_REVIEWER_ID", "")
    if len(token) < 32 or not reviewer or request is None:
        raise PermissionError(
            "workflow access disabled: secure transport credentials are not configured"
        )
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise PermissionError("workflow authorization required")
    return reviewer


def authorize_context(ctx) -> str:
    mode = os.environ.get("WFC_AUTH_MODE", "bearer")
    if mode == "auth0":
        # The SDK verifies the bearer token at the HTTP boundary. Never use
        # an unverified tool argument or request header for OAuth identity.
        from mcp.server.auth.middleware.auth_context import get_access_token

        verified = get_access_token()
        allowed_sub = os.environ.get("WFC_AUTH0_ALLOWED_SUB", "")
        scope = os.environ.get("WFC_AUTH0_SCOPE", "wfc:workflow")
        if (
            verified is None
            or not allowed_sub
            or verified.subject != allowed_sub
            or scope not in verified.scopes
        ):
            raise PermissionError("workflow OAuth authorization required")
        return "auth0:" + allowed_sub
    if mode != "bearer":
        raise PermissionError("unsupported workflow authentication mode")
    try:
        request = ctx.request_context.request
    except (AttributeError, ValueError):
        request = None
    return authorize_request(request)


TECHLEAD_SCOPE = "wfc:techlead"


def authorize_techlead() -> None:
    """Require the Auth0 middleware's verified identity and a distinct TL scope."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    if (
        os.environ.get("WFC_AUTH_MODE", "bearer").strip() != "auth0"
        or os.environ.get("WFC_POC_NO_AUTH") == "1"
    ):
        raise PermissionError("Tech Lead tools require Auth0 OAuth")
    verified = get_access_token()
    subject = os.environ.get("WFC_AUTH0_ALLOWED_SUB", "").strip()
    workflow_scope = os.environ.get("WFC_AUTH0_SCOPE", "wfc:workflow").strip()
    if (
        verified is None
        or not subject
        or verified.subject != subject
        or workflow_scope == TECHLEAD_SCOPE
        or workflow_scope not in verified.scopes
        or TECHLEAD_SCOPE not in verified.scopes
    ):
        raise PermissionError("Tech Lead OAuth authorization required")
