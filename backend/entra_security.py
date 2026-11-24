"""Strict Microsoft Entra v2 JWT validation, shared by gateway and demo backend.

HTTPS JWK fetch uses the issuer derived from a tenant UUID configured by the operator,
not from any untrusted JWT claim. This avoids attacker-controlled key sources.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import jwt
from jwt import PyJWKClient


class AuthenticationError(Exception):
    """A token is missing, malformed, or not valid for this particular API."""


def _tenant(tenant_id: str) -> str:
    try:
        return str(uuid.UUID(tenant_id))
    except (ValueError, TypeError) as exc:
        raise ValueError("TENANT_ID must be a Microsoft Entra tenant UUID") from exc


def bearer_value(header: str | None) -> str:
    if not header or not header.startswith("Bearer "):
        raise AuthenticationError("Missing Bearer access token")
    token = header[7:].strip()
    if not token or " " in token or len(token) > 16384:
        raise AuthenticationError("Malformed Bearer access token")
    return token


class EntraVerifier:
    def __init__(self, tenant_id: str, audience: str):
        self.tenant_id = _tenant(tenant_id)
        if not audience or audience.strip() != audience:
            raise ValueError("An exact API audience is required")
        self.audience = audience
        self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        jwks_uri = f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
        self.keys = PyJWKClient(jwks_uri, cache_jwk_set=True, lifespan=300)

    def validate(self, token: str) -> dict[str, Any]:
        try:
            signing_key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "tid"]},
                leeway=30,
            )
            if claims.get("tid", "").lower() != self.tenant_id:
                raise AuthenticationError("Unexpected tenant")
            if claims.get("ver") != "2.0":
                raise AuthenticationError("Expected a v2.0 access token; set requestedAccessTokenVersion=2")
            return claims
        except AuthenticationError:
            raise
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise AuthenticationError("JWT validation failed") from exc


def token_client_app_id(claims: dict[str, Any]) -> str:
    """azp (v2) or appid (legacy); not the oid of the signed-in person."""
    return str(claims.get("azp") or claims.get("appid") or "").lower()


def has_scope(claims: dict[str, Any], name: str) -> bool:
    return name in str(claims.get("scp") or "").split()


def has_app_role(claims: dict[str, Any], name: str) -> bool:
    return name in (claims.get("roles") or [])
