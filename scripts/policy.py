"""Offline OpenIDM approval simulator -> native agentgateway standalone config.

This is a CONFIG RENDERER, not an HTTP gateway or live OpenIDM integration.
Changes on Azure require building and deploying a NEW container revision.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parents[1]
APPROVALS = BASE / "config" / "approvals.json"
CONFIG = BASE / "config" / "config.yaml"
ALLOWED_TOOLS = {"invoice.read"}
COMPANY = re.compile(r"^[A-Z0-9]{2,8}$")
PURPOSE = re.compile(r"^[a-zA-Z0-9_.:-]{3,100}$")


def load_approvals(path: Path = APPROVALS) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"agents": {}}
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        raise ValueError("Invalid approvals JSON")
    return data


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".policy-", delete=False, encoding="utf-8") as fh:
        name = Path(fh.name)
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(name, 0o600)
    os.replace(name, path)


def render(approvals: dict) -> dict:
    # CEL is generated only from previously validated fields; never from free-text expressions.
    rules = []
    company_clauses = []
    for agent_id, entry in sorted(approvals["agents"].items()):
        agent_id = str(uuid.UUID(agent_id))
        if entry.get("status") != "approved":
            continue
        if not entry.get("agent_owner") or not entry.get("resource_owner") or not entry.get("purpose"):
            continue
        if not PURPOSE.fullmatch(entry["purpose"]):
            continue
        companies = entry.get("companies", [])
        if not companies or not all(COMPANY.fullmatch(x) for x in companies):
            continue
        for tool in entry.get("tools", []):
            if tool not in ALLOWED_TOOLS:
                raise ValueError(f"Unsupported tool: {tool}")
            checks = f'has(jwt.azp) && jwt.azp == "{agent_id}" && has(jwt.roles) && "Gateway.Invoke" in jwt.roles'
            rules.append(f'{checks} && mcp.tool.name == "{tool}"')
            for company in sorted(set(companies)):
                company_clauses.append(
                    f'(jwt.azp == "{agent_id}" && mcp.params.arguments.query.company == "{company}")'
                )
    if not rules:
        # No permissive catch-all, ever.
        rules = ['mcp.tool.name == "__NOT_APPROVED__"']
    base = {
        "frontendPolicies": {
            "tracing": {
                "host": "${AGENTGATEWAY_OTLP_HOST:-localhost:4317}",
                "randomSampling": "${AGENTGATEWAY_TRACE_SAMPLING:-false}",
            },
            "accessLog": {
                "otlp": {
                    "host": "${AGENTGATEWAY_SECURITY_OTLP_HOST:-http://localhost:4318}",
                    "protocol": "http",
                    "path": "/v1/logs",
                    "filter": "${AGENTGATEWAY_SECURITY_OTLP_FILTER:-false}",
                    "fields": {
                        "remove": ["url.query", "url.full", "mcp.session.id", "mcp.resource.uri",
                                   "mcp.error.message", "jwt.sub", "error"],
                        "add": {
                            "security.caller.app_id": "has(jwt.azp) ? jwt.azp : null",
                            "security.guardrail.rejected": (
                                'has(proxy.error) && proxy.error.message.startsWith("mcp: mcpGuardrails rejected:")'
                            ),
                        },
                    },
                    "policies": {
                        "backendAuth": {
                            "azure": {
                                "scopes": ["${BACKEND_AUDIENCE}/.default"],
                                "explicitConfig": {"managedIdentity": {"userAssignedIdentity": {"clientId": "${BACKEND_MI_CLIENT_ID}"}}},
                            },
                        },
                    },
                },
            },
        },
        "mcp": {
            "port": 3000,
            "statefulMode": "stateless",
            "failureMode": "failClosed",
            "policies": {
                "mcpAuthentication": {
                    "mode": "strict",
                    "issuer": "${ENTRA_ISSUER}",
                    "audiences": ["${GATEWAY_AUDIENCE}"],
                    "provider": {"entra": {}},
                    "clientId": "${GATEWAY_CLIENT_ID}",
                    "resourceMetadata": {
                        "resource": "${MCP_RESOURCE_URL}",
                        "scopesSupported": ["api://${GATEWAY_CLIENT_ID}/gateway.invoke"],
                        "bearerMethodsSupported": ["header"],
                    },
                },
                "mcpAuthorization": {"rules": rules},
                "mcpGuardrails": {
                    "processors": [{
                        "kind": "expression", "methods": {"tools/call": "request"},
                        "condition": (
                            'mcp.params.name == "invoice.read" && '
                            '(!has(mcp.params.arguments) || '
                            '!has(mcp.params.arguments.query) || '
                            '!has(mcp.params.arguments.query.company) || '
                            '!has(jwt.azp) || '
                            f'!({" || ".join(company_clauses) if company_clauses else "false"}))'
                        ),
                        "reject": "Company is not approved for this agent",
                    }],
                },
            },
            "targets": [{
                "name": "invoices",
                "openapi": {
                    "schema": {"file": "/etc/agentgateway/openapi.json"},
                    "host": "${BACKEND_HOST}",
                },
                "policies": {
                    "backendAuth": {
                        "azure": {
                            "scopes": ["${BACKEND_AUDIENCE}/.default"],
                            "explicitConfig": {"managedIdentity": {"userAssignedIdentity": {"clientId": "${BACKEND_MI_CLIENT_ID}"}}},
                        },
                    },
                },
            }],
        },
    }
    return base


def main() -> None:
    p = argparse.ArgumentParser(description="OpenIDM -> agentgateway CEL policy simulator")
    p.add_argument("action", choices=["approve", "revoke", "render", "show"])
    p.add_argument("--agent-client-id")
    p.add_argument("--purpose", default="review-overdue-invoices")
    p.add_argument("--company", default="PL01")
    p.add_argument("--agent-owner")
    p.add_argument("--resource-owner")
    args = p.parse_args()
    data = load_approvals()
    if args.action in ("approve", "revoke"):
        if not args.agent_client_id:
            p.error("--agent-client-id required")
        agent_id = str(uuid.UUID(args.agent_client_id))
        if args.action == "approve":
            if not args.agent_owner or not args.resource_owner:
                p.error("Both --agent-owner and --resource-owner are required")
            if not COMPANY.fullmatch(args.company) or not PURPOSE.fullmatch(args.purpose):
                p.error("Invalid company or purpose")
            data["agents"][agent_id] = {
                "status": "approved", "purpose": args.purpose,
                "agent_owner": args.agent_owner, "resource_owner": args.resource_owner,
                "companies": [args.company], "tools": ["invoice.read"],
            }
        else:
            data["agents"].pop(agent_id, None)
        atomic_write(APPROVALS, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    if args.action == "show":
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    doc = render(data)
    yaml_content = "# GENERATED; native agentgateway v1.7 standalone config\n# yaml-language-server: $schema=https://agentgateway.dev/schema/config\n" + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)
    atomic_write(CONFIG, yaml_content)
    print(f"rendered {CONFIG}: {len(doc['mcp']['policies']['mcpAuthorization']['rules'])} explicit authz rules")
    if args.action in ("approve", "revoke"):
        print("NOTE: Azure Container Apps still runs the OLD image until redeployment; run ./scripts/deploy.sh")


if __name__ == "__main__":
    main()
