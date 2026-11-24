"""Deploy THIS directory to Azure Functions (Python v2 programming model)."""
from __future__ import annotations

import json
import os

import azure.functions as func

# Functions loads this file as the root of the Function App project.
from api_logic import handle_invoice  # type: ignore[import-not-found]
from entra_security import EntraVerifier  # type: ignore[import-not-found]

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
_verifier = None


def verifier():
    global _verifier
    if _verifier is None:
        _verifier = EntraVerifier(os.environ["TENANT_ID"], os.environ["BACKEND_AUDIENCE"])
    return _verifier


@app.route(route="invoices/{invoice_id}", methods=["GET"])
def invoice_read(req: func.HttpRequest) -> func.HttpResponse:
    status, data = handle_invoice(
        authorization=req.headers.get("Authorization"),
        invoice_id=req.route_params.get("invoice_id", ""),
        company=req.params.get("company", ""),
        agent_header=req.headers.get("X-Agent-Client-ID"),
        purpose_header=req.headers.get("X-Purpose-ID"),
        verifier=verifier().validate,
    )
    return func.HttpResponse(json.dumps(data), status_code=status, mimetype="application/json")
