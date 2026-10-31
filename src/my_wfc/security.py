"""Boundary authentication: transport headers, never LLM tool arguments."""
import hmac
import os


def authorize_request(request) -> str:
    token = os.environ.get("WFC_AUTH_TOKEN", "")
    reviewer = os.environ.get("WFC_REVIEWER_ID", "")
    if len(token) < 32 or not reviewer or request is None:
        raise PermissionError("workflow access disabled: secure transport credentials are not configured")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise PermissionError("workflow authorization required")
    return reviewer


def authorize_context(ctx) -> str:
    try:
        request = ctx.request_context.request
    except (AttributeError, ValueError):
        request = None
    return authorize_request(request)
