# OwnerLens / OpenIDM demo — real agentgateway standalone on Azure Container Apps Consumption

**Gateway is the real open-source agentgateway v1.7.0 standalone image, not the earlier FastAPI proxy.** The only Python service in this package is the separate sample **Azure Function** (business backend). There is **no Redis, PostgreSQL, Kubernetes controller, or custom authorization web service**.

## Architecture

```text
OwnerLens (discovery, outside this demo)
    |
OpenIDM (agent-owner + resource-owner approvals, simulated by policy.py)
    |  produces version-controlled CEL policies
    v
Azure Container Apps Consumption (0-1 replicas)
    | real agentgateway standalone: Entra JWT -> mcpAuthorization (default deny)
    | -> mcpGuardrails (company) -> OpenAPI-to-MCP invoice.read
    | backendAuth.azure.explicitConfig.managedIdentity: {}
    |            |
    |            +-- requests API token for api://BACKEND_CLIENT_ID/.default
    v
Azure Function GET /api/invoices/{invoice_id}?company=PL01
    | validates Entra JWT, gateway UAMI client ID and Invoice.Read app role
    v
Demo JSON invoice
```

The agent holds only its **own Entra credential to get a gateway token**. The agent never receives a backend credential. The gateway has a user-assigned **Managed Identity**, not a secret. Backend sees the **gateway's managed identity**, not the agent; the gateway keeps the agent identity for authorization and logs. **This is not Entra OBO/token exchange on behalf of an autonomous agent.**

## Quick start

For the deployed IAM PoC, open the `iam-poc-invoice-agent` prompt agent in
the `ownerlens-agent-devpl` Foundry project. The MCP connection uses
`AgenticIdentityToken` to obtain a gateway token as the agent's own Entra
identity. Assign `Gateway.Invoke` to that identity's Entra principal and
use its client ID in the gateway approval. In the Foundry playground, ask
the agent to fetch invoice `INV-2026-0001` for `PL01`. Use `DE01` to check
the company restriction. This flow requires no agent secret.

The steps below use the original local Python client.

1. `cp .env.example .env`, fill the real GUIDs, backend host, Azure resource names and the local agent's client secret. **Never commit `.env`.**
2. Configure Entra app registrations. See [docs/ENTRA_AND_BACKEND.md](docs/ENTRA_AND_BACKEND.md).
3. Prepare a security policy. Initially **no tools are approved**, so the gateway denies the only tool.
4. Grant approval with both owners (demo workflow):

```bash
python3 scripts/policy.py approve \
  --agent-client-id "<your-agent-app-client-id>" \
  --agent-owner finance@example.invalid \
  --resource-owner erp@example.invalid \
  --purpose invoice-reconciliation --company PL01
```

5. Deploy with `./scripts/deploy.sh`. Requires Azure CLI with `containerapp` extension and permissions to create app environments, managed identities, ACR and RBAC grants. It creates **Azure Container Apps Consumption**, a **single standalone container** with `--min-replicas 0 --max-replicas 1`, a user-assigned MI and ACR Basic. The script prints the real HTTPS MCP URL and gateway MI client ID.
6. Grant the backend app role to the managed identity (Graph admin permission required):

```bash
./scripts/grant-backend-role.sh
```

7. Set the Azure Function app setting `GATEWAY_CLIENT_ID` to the printed **Managed Identity client ID** (not the app registration client ID protecting the gateway). Enable Entra authentication for the Function and set settings per [docs/ENTRA_AND_BACKEND.md](docs/ENTRA_AND_BACKEND.md).
8. Replace `GATEWAY_URL` in your local `.env` with the printed HTTPS gateway URL. Call:

```bash
pip install -r requirements-dev.txt
python -m agent.client --company PL01
python -m agent.client --company DE01  # policy should reject
```

9. Revoke access and deploy the NEW image revision (no live policy database):

```bash
python3 scripts/policy.py revoke --agent-client-id "<your-agent-app-client-id>"
./scripts/deploy.sh
python -m agent.client --company PL01  # now denied by new revision
```

To provision Azure resources declaratively, follow the [Bicep deployment guide](infra/README.md). It supports an existing invoice Function or a new Flex Consumption backend. To publish the Python code to an existing Function, run `cd backend && func azure functionapp publish <YOUR_FUNCTION_APP>` with Azure Functions Core Tools.

## What this demo implements

| Function | Component | Status |
|---|---|---|
| Entra JWT validation (issuer / aud / signature) | native agentgateway `mcpAuthentication` | configured |
| No approved tools by default | native `mcpAuthorization` CEL | configured |
| Visible + callable only after approval | native `mcpAuthorization` CEL | configured |
| Restrict `invoice.read` to `PL01` | native request-phase `mcpGuardrails` CEL | configured |
| REST → real MCP `tools/list` and `tools/call` | native OpenAPI target | configured |
| Backend token without client secret | ACA Managed Identity + native `backendAuth.azure` | configured |
| Backend caller isolation and Invoice.Read | Function-level Entra JWT check | implemented |
| Dynamic OpenIDM provisioning | scripted JSON → CEL render → Container App revision | **simulated** |
| Genuine RFC8693 exchange, per-agent backend identities | not implemented | out of scope |
| Behavioral anomaly detector, external PDP | not implemented | out of scope |

### Revoke and fail-closed semantics

- `tools/list` hides unauthorized tools; unauthorized `tools/call` is denied before the backend call.
- Company restriction is an **MCP request-phase guardrail**, *not* only OpenAPI regex validation or an audit rule.
- The generated config is baked into an immutable container image; revocation requires a new Azure Container App **revision**. Older revisions must stop receiving traffic. The deployment script selects **single revision** mode, but there can be a rollout delay. **Not a suitable instant kill switch.**
- Scale-to-zero saves idle compute but causes cold starts. For continuously connected agents or faster revocations, use `minReplicas=1` and central fail-closed authorization, plus a deployment/revocation strategy.
- Never let the agent reach the backend directly. Protect the Function with Entra app-only role, and preferably network restrictions when feasible.
- Because the backend token represents the **gateway**, backend audit cannot distinguish agents without an additional cryptographically trustworthy agent context or logs correlated at the gateway.

### Operational cost

ACA Consumption can scale to zero, but **Azure Container Registry Basic is a fixed recurring cost**; Azure Container Apps environment, log ingestion, Function and outbound data also have possible charges. This is **not literally zero-cost infrastructure**. If you already have a registry (ACR or accessible OCI registry), adapt `scripts/deploy.sh` to reuse it.

### Secret scanning

With [pre-commit](https://pre-commit.com/#install) installed, enable the Gitleaks hook once per checkout:

```bash
pre-commit install --install-hooks
```

The hook scans staged changes on each commit and redacts detected secrets in its output. Run `pre-commit run gitleaks` to check staged changes manually.

### Validation status

**Offline Python unit tests** check the policy renderer, reject-by-default, approver requirements, company scope, backend app-role enforcement and OpenAPI shape. Run `pytest -q`.

**Not executed here:** Docker image build, real agentgateway CLI `--validate-only`, Azure CLI deployment, Entra login, or end-to-end MCP execution against your tenant. Those require external downloads/credentials. Before production deployment, validate with the same pinned binary:

```bash
agentgateway --validate-only -f config/config.yaml
```

Set environment variables (`ENTRA_ISSUER`, `GATEWAY_CLIENT_ID`, `GATEWAY_AUDIENCE`, `MCP_RESOURCE_URL`, `BACKEND_AUDIENCE`, `BACKEND_HOST`, `BACKEND_MI_CLIENT_ID`) and place the schema at the absolute container path or use the image itself; the validation command may need JWKS network access.

## Files

- `Dockerfile`: FROM the published **real** agentgateway OCI image.
- `config/config.yaml`: generated native gateway YAML; `config/openapi.json`: sole MCP tool.
- `scripts/policy.py`: offline approver simulator + generator of CEL allow rules (not an HTTP service).
- `scripts/deploy.sh`: builds image in ACR and deploys Consumption.
- `infra/` contains Bicep templates for the gateway, ACR, managed identity, ACA Consumption, Log Analytics, Application Insights, and the optional invoice backend and security collector. See the [Bicep deployment guide](infra/README.md).
- `scripts/grant-backend-role.sh`: assign backend application permission to gateway UAMI.
- `agent/client.py`: actual MCP JSON-RPC client, not the old REST shim.
- `backend/`: original Azure Function secured by Entra app-only JWT.

## Official references

- [Agentgateway standalone](https://agentgateway.dev/docs/standalone/latest/)
- [Entra auth for MCP](https://agentgateway.dev/docs/standalone/latest/integrations/auth/entra/)
- [OpenAPI-to-MCP](https://agentgateway.dev/docs/standalone/latest/integrations/mcp/servers/openapi/)
- [MCP authz and CEL](https://agentgateway.dev/docs/standalone/latest/documentation/configuration/security/mcp-authz/)
- [Guardrails](https://agentgateway.dev/docs/standalone/latest/documentation/mcp/guardrails/cel/)
- [Managed Identity backend auth](https://agentgateway.dev/docs/standalone/latest/documentation/configuration/security/backend-authn/providers/azure/)
- [Azure Container Apps Consumption](https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-overview)
