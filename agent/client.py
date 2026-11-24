"""Pure MCP JSON-RPC client. Only gets a token for agentgateway, never backend.

No custom gateway; /mcp is served by real standalone agentgateway.
"""
from __future__ import annotations

import argparse
import json
import os
import requests
from dotenv import load_dotenv


def get_agent_token() -> str:
    tenant = os.environ["TENANT_ID"]
    response = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["AGENT_CLIENT_ID"],
            "client_secret": os.environ["AGENT_CLIENT_SECRET"],
            "scope": os.environ["GATEWAY_AUDIENCE"].rstrip("/") + "/.default",
        }, timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def parse_mcp_response(resp: requests.Response) -> dict:
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for block in resp.text.split("\n\n"):
            for line in block.splitlines():
                if line.startswith("data:"):
                    try:
                        value = json.loads(line[5:].strip())
                        if "result" in value or "error" in value:
                            return value
                    except ValueError:
                        continue
        raise RuntimeError("No JSON-RPC result in SSE response")
    if not resp.content:
        return {}
    return resp.json()


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--invoice", default="INV-2026-0001")
    p.add_argument("--company", default="PL01")
    args = p.parse_args()
    token = get_agent_token()
    url = os.environ["GATEWAY_URL"].rstrip("/") + "/mcp"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    sess = requests.Session()
    def rpc(method: str, params: dict | None, n: int) -> dict:
        result = sess.post(url, headers=headers, json={"jsonrpc":"2.0", "id":n, "method":method, "params":params or {}}, timeout=30)
        return parse_mcp_response(result)
    print("initialize:", rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mcp-demo-cli", "version":"1.0"}}, 1))
    tools = rpc("tools/list", {}, 2)
    print("tools/list:", json.dumps(tools, ensure_ascii=False))
    call = rpc("tools/call", {
        "name": "invoice.read",
        "arguments": {
            "path": {"invoice_id": args.invoice},
            "query": {"company": args.company},
        },
    }, 3)
    print("tools/call:", json.dumps(call, ensure_ascii=False))
    # Never print tokens.


if __name__ == "__main__":
    main()
