"""Local stand-in for Azure Function using the SAME auth handler."""
from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse
from .api_logic import handle_invoice
from .entra_security import EntraVerifier

app = FastAPI(title="Entra-protected demo backend (Azure Function equivalent)")
_verifier = None


def verifier():
    global _verifier
    if _verifier is None:
        _verifier = EntraVerifier(os.environ["TENANT_ID"], os.environ["BACKEND_AUDIENCE"])
    return _verifier


@app.get("/api/invoices/{invoice_id}")
def invoice(invoice_id: str, company: str = Query(...),
            authorization: str | None = Header(default=None),
            x_agent_client_id: str | None = Header(default=None),
            x_purpose_id: str | None = Header(default=None)):
    status, body = handle_invoice(
        authorization=authorization,
        invoice_id=invoice_id, company=company,
        agent_header=x_agent_client_id, purpose_header=x_purpose_id,
        verifier=verifier().validate,
    )
    return JSONResponse(status_code=status, content=body)
