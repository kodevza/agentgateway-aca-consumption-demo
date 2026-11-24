# Operations and failure modes

**No external database** is used. `agentgateway` runs standalone using file configuration baked into the OCI image. The UI and mutable in-memory configuration are not exposed on the public port (`ui` is not configured).

## Security boundaries

- Agentgateway `mcpAuthentication` validates incoming bearer JWT with Entra issuer/audience.
- `mcpAuthorization` filters tools/list and denies calls unless both an exact agent `azp` and the `Gateway.Invoke` role are approved.
- CEL `mcpGuardrails` enforces company scope before tools/call reaches the Azure Function.
- Outbound `backendAuth.azure` uses user-assigned Managed Identity selected by client ID (Azure host only) and requests one audience-specific backend token; it never sends the caller's token upstream.
- The backend independently checks token, client ID and `Invoice.Read`.

## Policy delivery

`python scripts/policy.py approve ...` / `revoke ...` updates `config/approvals.json` and generated `config/config.yaml` **locally**. `deploy.sh` hashes the config into an OCI tag, builds it in ACR and deploys an ACA revision in single-revision mode. For immediate revocation, this architecture is **insufficient** during rollout/cold start: integrate a central, highly available, fail-closed PDP or block access at the Entra/ACA edge. Do not promise instant revocation from file updates on the developer machine.

## Cost/cold starts

ACA is explicitly `--min-replicas 0 --max-replicas 1 --cpu 0.5 --memory 1Gi`. Scale-to-zero and cold starts can affect long-lived MCP sessions. ACR Basic has a fixed monthly price; Azure Function, logging, outbound data and Container Apps may add billing. You may instead host the image in a public OCI registry if you don't want ACR.

## Verification checklist (tenant required)

1. `agentgateway --validate-only` on v1.7.0 with the rendered config and environment values.
2. `GET /mcp` or `POST /mcp initialize` without Authorization: expect 401.
3. Agent with correct issuer/audience but no owner authorization: `tools/list` excludes `invoice.read`; `tools/call` is denied.
4. Approved Agent A with `Gateway.Invoke`: `tools/list` contains `invoice.read`; `tools/call(company=PL01)` reaches Function.
5. Approved Agent A with `company=DE01`: rejected before Function; Function logs zero invocation for that request.
6. Agent B without approval: denied even if authenticated in same tenant.
7. Function refuses direct Agent token (`aud=Gateway`) and refuses any backend token missing `Invoice.Read` or from a different client ID.
8. After `revoke`+image redeployment, old revision stops serving, new `tools/call` denied. Measure rollout delay.
9. Inspect logs and compare `agent azp -> tool -> decision -> backend response` without capturing JWTs.
10. Attempt an agent connection to Azure Function directly; ensure network/application identity restrictions block it.

**Limit:** offline tests in the ZIP do not prove that the pinned agentgateway image boots or that Entra grants are correctly provisioned in your tenant. No Azure credentials or live network access were available during package construction.
