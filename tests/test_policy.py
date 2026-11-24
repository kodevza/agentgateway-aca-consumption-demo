from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import policy

AGENT = "11111111-1111-1111-1111-111111111111"


def approved(company="PL01"):
    return {"agents": {AGENT: {
        "status": "approved", "purpose": "invoice-reconciliation",
        "agent_owner": "owner@example.invalid", "resource_owner": "sap-owner@example.invalid",
        "companies": [company], "tools": ["invoice.read"]
    }}}


def test_defaults_to_explicit_deny():
    config = policy.render({"agents": {}})
    p = config["mcp"]["policies"]
    assert p["mcpAuthorization"]["rules"] == ['mcp.tool.name == "__NOT_APPROVED__"']
    assert 'false' in p["mcpGuardrails"]["processors"][0]["condition"]


def test_never_uses_unauthenticated_policy():
    p = policy.render(approved())["mcp"]["policies"]
    assert p["mcpAuthentication"]["mode"] == "strict"
    assert p["mcpAuthentication"]["provider"] == {"entra": {}}
    assert p["mcpAuthentication"]["audiences"] == ["${GATEWAY_AUDIENCE}"]


def test_agent_and_role_explicitly_pinned():
    r = policy.render(approved())["mcp"]["policies"]["mcpAuthorization"]["rules"]
    assert len(r) == 1
    assert AGENT in r[0]
    assert "jwt.azp" in r[0]
    assert "Gateway.Invoke" in r[0]
    assert "invoice.read" in r[0]


def test_company_preinvoke_scope():
    guard = policy.render(approved("PL01"))["mcp"]["policies"]["mcpGuardrails"]["processors"][0]
    assert guard["methods"] == {"tools/call": "request"}
    assert "PL01" in guard["condition"]
    assert "has(mcp.params.arguments.query)" in guard["condition"]
    assert 'mcp.params.arguments.query.company == "PL01"' in guard["condition"]
    assert "DE01" not in guard["condition"]


def test_unapproved_agent_not_in_allowlist():
    config = policy.render(approved())
    rule = config["mcp"]["policies"]["mcpAuthorization"]["rules"]
    assert not any('"22222222-2222-2222-2222-222222222222"' in x for x in rule)


def test_incomplete_owner_approval_denied():
    broken = approved()
    broken["agents"][AGENT]["resource_owner"] = ""
    cfg = policy.render(broken)
    assert cfg["mcp"]["policies"]["mcpAuthorization"]["rules"] == ['mcp.tool.name == "__NOT_APPROVED__"']


def test_rejects_unsupported_tool():
    bad = approved()
    bad["agents"][AGENT]["tools"] = ["payments.execute"]
    with pytest.raises(ValueError):
        policy.render(bad)


def test_bad_company_not_interpolated():
    bad = approved('PL01" || true || "')
    assert policy.render(bad)["mcp"]["policies"]["mcpAuthorization"]["rules"] == ['mcp.tool.name == "__NOT_APPROVED__"']


def test_managed_identity_scoped_to_backend_resource():
    auth = policy.render(approved())["mcp"]["targets"][0]["policies"]["backendAuth"]["azure"]
    assert auth["scopes"] == ["${BACKEND_AUDIENCE}/.default"]
    assert auth["explicitConfig"] == {"managedIdentity": {"userAssignedIdentity": {"clientId": "${BACKEND_MI_CLIENT_ID}"}}}
    assert "clientSecret" not in str(auth)


def test_stand_alone_is_stateless_and_fail_closed():
    obj = policy.render(approved())["mcp"]
    assert obj["statefulMode"] == "stateless"
    assert obj["failureMode"] == "failClosed"


def test_generated_yaml_roundtrip():
    obj = policy.render(approved())
    assert yaml.safe_load(yaml.safe_dump(obj)) == obj


def test_invoice_openapi_has_only_one_operation():
    spec = json.loads((Path(__file__).resolve().parents[1]/"config"/"openapi.json").read_text())
    ops = [p["operationId"] for v in spec["paths"].values() for p in v.values() if "operationId" in p]
    assert ops == ["invoice.read"]


def test_backend_enforces_gateway_identity_and_app_role(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.api_logic import handle_invoice
    monkeypatch.setenv("GATEWAY_CLIENT_ID", "44444444-4444-4444-4444-444444444444")
    monkeypatch.setenv("BACKEND_APP_ROLE", "Invoice.Read")
    def check(agent, role):
        claims = {"azp": agent, "roles": role}
        return handle_invoice(
            authorization="Bearer TEST", invoice_id="INV-2026-1234", company="PL01",
            agent_header=None, purpose_header=None, verifier=lambda _: claims
        )[0]
    assert check("44444444-4444-4444-4444-444444444444", ["Invoice.Read"]) == 200
    assert check("55555555-5555-5555-5555-555555555555", ["Invoice.Read"]) == 403
    assert check("44444444-4444-4444-4444-444444444444", []) == 403
