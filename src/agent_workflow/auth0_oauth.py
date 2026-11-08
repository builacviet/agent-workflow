"""Auth0 JWT verifier for the isolated MCP resource server.

This module never redirects users, registers OAuth clients or stores secrets.
Auth0 handles those jobs. The MCP SDK handles HTTP bearer challenges and
RFC 9728 protected-resource discovery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Auth0Config:
    issuer: str
    resource: str
    allowed_sub: str
    client_id: str
    scope: str


def load_auth0_config(public_host: str) -> Auth0Config:
    """Reject partial OAuth configuration before opening the HTTP listener."""
    if os.environ.get("WFC_POC_NO_AUTH") == "1":
        raise ValueError("OAuth and public no-auth mode are mutually exclusive")
    issuer = os.environ.get("WFC_AUTH0_ISSUER", "").strip()
    allowed_sub = os.environ.get("WFC_AUTH0_ALLOWED_SUB", "").strip()
    # The expected OAuth client identity may be a CIMD HTTPS URL, not an
    # Auth0-internal tpc_ application record. Migrate without a forced restart.
    expected_client_id = os.environ.get("WFC_OAUTH_EXPECTED_CLIENT_ID", "").strip()
    legacy_client_id = os.environ.get("WFC_AUTH0_CLIENT_ID", "").strip()
    if expected_client_id and legacy_client_id and expected_client_id != legacy_client_id:
        raise ValueError("conflicting WFC_OAUTH_EXPECTED_CLIENT_ID and WFC_AUTH0_CLIENT_ID")
    client_id = expected_client_id or legacy_client_id
    scope = os.environ.get("WFC_AUTH0_SCOPE", "wfc:workflow").strip()
    parsed = urlsplit(issuer)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.hostname)
        or ".." in parsed.hostname
    ):
        raise ValueError("WFC_AUTH0_ISSUER must be an HTTPS domain issuer")
    if not (1 <= len(allowed_sub) <= 256) or any(x.isspace() for x in allowed_sub):
        raise ValueError("WFC_AUTH0_ALLOWED_SUB must be the exact Auth0 user ID")
    # Auth0 may display an internal tpc_ ID, while CIMD uses a metadata URL.
    # The operator pins ONE exact expected ID: no wildcard client trust.
    cimd_url = re.fullmatch(
        r"https://chatgpt[.]com/oauth/(?:[A-Za-z0-9_-]{1,128}/)?client[.]json",
        client_id,
    )
    auth0_record_id = re.fullmatch(r"tpc_[A-Za-z0-9_-]{6,256}", client_id)
    if not (cimd_url or auth0_record_id):
        raise ValueError(
            "WFC_OAUTH_EXPECTED_CLIENT_ID must be the exact ChatGPT CIMD metadata URL "
            "or Auth0 third-party application ID"
        )
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", scope):
        raise ValueError("WFC_AUTH0_SCOPE must be a single OAuth permission")
    if not public_host:
        raise ValueError("WFC_PUBLIC_HOST is required for OAuth resource discovery")
    issuer = f"https://{parsed.hostname.lower()}/"
    return Auth0Config(
        issuer=issuer,
        # Auth0 Resource Parameter Compatibility Profile maps this URL to the
        # custom API's configured identifier (aud). An exact match is required.
        resource=f"https://{public_host}/mcp",
        allowed_sub=allowed_sub,
        client_id=client_id,
        scope=scope,
    )


class Auth0TokenVerifier:
    """Async JWKS verifier with a bounded fetch, strict claims and subject allowlist."""

    def __init__(self, config: Auth0Config, *, clock=None):
        self.config = config
        self.clock = clock or time.monotonic
        self._jwks: dict[str, object] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._diagnostic_events = 0

    def _diagnose(self, code: str) -> None:
        """Opt-in fixed reason codes only; never log credentials or JWT claims.

        Hard cap protects log volume for a public POC endpoint. Disabled by default.
        """
        if os.environ.get("WFC_AUTH0_DIAGNOSTICS") != "1":
            return
        if self._diagnostic_events >= 40:
            return
        self._diagnostic_events += 1
        _LOG.warning("auth0_oauth_validation code=%s", code)

    def _reject(self, code: str) -> None:
        self._diagnose(code)
        return None

    async def _fetch_jwks(self) -> dict[str, object]:
        url = self.config.issuer + ".well-known/jwks.json"
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with asyncio.timeout(6):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 100_000:
                            raise ValueError("JWKS response too large")
                        data.extend(chunk)
        document = json.loads(data)
        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
            raise ValueError("invalid Auth0 JWKS")
        result = {}
        for entry in keys:
            if not isinstance(entry, dict):
                continue
            kid = entry.get("kid")
            if (
                isinstance(kid, str)
                and 1 <= len(kid) <= 128
                and entry.get("kty") == "RSA"
                and entry.get("alg", "RS256") == "RS256"
                and entry.get("use", "sig") == "sig"
            ):
                result[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(entry))
        if not result:
            raise ValueError("no usable signing keys in Auth0 JWKS")
        return result

    async def _get_key(self, kid: str):
        async with self._lock:
            if self.clock() >= self._expires_at:
                # Rotation is picked up at the next refresh. Unknown KIDs do not
                # trigger unbounded outbound requests on attacker-supplied tokens.
                self._jwks = await self._fetch_jwks()
                self._expires_at = self.clock() + 300
            return self._jwks.get(kid)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or len(token) > 8192 or token.count(".") != 2:
            return self._reject("token_format")
        try:
            header = jwt.get_unverified_header(token)
        except Exception:
            return self._reject("jwt_header")
        if header.get("alg") != "RS256":
            return self._reject("jwt_algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not 1 <= len(kid) <= 128:
            return self._reject("jwt_kid")
        try:
            key = await self._get_key(kid)
        except Exception:
            return self._reject("jwks_unavailable")
        if key is None:
            return self._reject("jwt_unknown_kid")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.config.issuer,
                audience=self.config.resource,
                leeway=30,
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
        except jwt.ExpiredSignatureError:
            return self._reject("jwt_expired")
        except jwt.InvalidIssuerError:
            return self._reject("jwt_issuer_mismatch")
        except jwt.InvalidAudienceError:
            return self._reject("jwt_audience_mismatch")
        except jwt.MissingRequiredClaimError:
            return self._reject("jwt_missing_required_claim")
        except jwt.ImmatureSignatureError:
            return self._reject("jwt_not_yet_valid")
        except jwt.InvalidSignatureError:
            return self._reject("jwt_bad_signature")
        except jwt.PyJWTError:
            return self._reject("jwt_invalid_claims")
        except Exception:
            return self._reject("jwt_decode_error")

        subject = claims.get("sub")
        if subject != self.config.allowed_sub:
            return self._reject("subject_mismatch")
        raw_scope = claims.get("scope", "")
        if not isinstance(raw_scope, str):
            return self._reject("scope_format")
        scopes = raw_scope.split()
        if self.config.scope not in scopes:
            return self._reject("scope_missing")
        client_id = claims.get("azp") or claims.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            return self._reject("client_claim_missing")
        if client_id != self.config.client_id:
            # Only a fixed diagnostic code. Never log the JWT, azp or client_id.
            if re.fullmatch(
                r"https://chatgpt[.]com/oauth/(?:[A-Za-z0-9_-]{1,128}/)?client[.]json",
                client_id,
            ):
                return self._reject("client_mismatch_cimd_url")
            return self._reject("client_mismatch")
        try:
            verified = AccessToken(
                token=token,
                client_id=client_id,
                scopes=scopes,
                expires_at=int(claims["exp"]),
                resource=self.config.resource,
                subject=subject,
            )
        except Exception:
            return self._reject("access_token_error")
        self._diagnose("verified")
        return verified
