"""Framework-independent invoice example; no real financial data."""
from __future__ import annotations

import os
import re
from typing import Callable

try:
    from .entra_security import AuthenticationError, bearer_value, token_client_app_id, has_app_role, has_scope
except ImportError:  # Azure Functions hosts backend/ as the application root
    from entra_security import AuthenticationError, bearer_value, token_client_app_id, has_app_role, has_scope

INVOICE_RE = re.compile(r"^INV-[0-9]{4}-[0-9]{4,8}$")
COMPANY_RE = re.compile(r"^[A-Z0-9]{2,8}$")


def handle_invoice(*, authorization: str | None, invoice_id: str, company: str,
                   agent_header: str | None, purpose_header: str | None,
                   verifier: Callable[[str], dict]) -> tuple[int, dict]:
    if not INVOICE_RE.fullmatch(invoice_id) or not COMPANY_RE.fullmatch(company):
        return 400, {"error": "Invalid invoice ID or company"}
    try:
        token = bearer_value(authorization)
        claims = verifier(token)
    except AuthenticationError:
        return 401, {"error": "Unauthorized"}

    gateway_client_id = os.environ.get("GATEWAY_CLIENT_ID", "").lower()
    if not gateway_client_id or token_client_app_id(claims) != gateway_client_id:
        return 403, {"error": "The backend accepts only the approved gateway application"}

    delegated = "scp" in claims
    if delegated:
        allowed = has_scope(claims, os.environ.get("BACKEND_DELEGATED_SCOPE", "invoice.read"))
    else:
        allowed = has_app_role(claims, os.environ.get("BACKEND_APP_ROLE", "Invoice.Read"))
    if not allowed:
        return 403, {"error": "Missing required backend permission"}

    return 200, {
        "invoice": {"id": invoice_id, "company": company, "amount": 123.45, "currency": "EUR", "demo": True},
        "auth": {
            "flow": "delegated-obo" if delegated else "app-only-client-credentials",
            "calling_app": token_client_app_id(claims),
            "user_oid": claims.get("oid") if delegated else None,
        },
        "audit_context": {
            "agent_claimed_by_gateway": agent_header,
            "purpose_claimed_by_gateway": purpose_header,
            "note": "Gateway headers are only audit hints; the Entra token is the authorization basis.",
        },
    }
