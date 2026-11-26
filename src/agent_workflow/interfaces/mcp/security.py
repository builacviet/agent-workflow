"""Boundary authentication: transport headers, never LLM tool arguments."""

import hmac
import os


def authorize_request(request) -> str:
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


class TechLeadScopeRequired(PermissionError):
    """Verified workflow identity needs the additional Tech Lead scope."""


def authorize_techlead() -> None:
    """Require the Auth0 middleware's verified identity and a distinct TL scope."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    if os.environ.get("WFC_AUTH_MODE", "bearer").strip() != "auth0":
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
    ):
        raise PermissionError("Tech Lead OAuth authorization required")
    if TECHLEAD_SCOPE not in verified.scopes:
        raise TechLeadScopeRequired("Tech Lead OAuth authorization required")
