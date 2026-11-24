# Microsoft Entra & Azure Function setup — exact separation of identities

The deployment script creates the gateway Container App and **user-assigned Managed Identity**; you create the Entra app registrations and Azure Function app.

## Three identities (do not confuse them)

1. **Agent app registration** (`AGENT_CLIENT_ID`). Issues a client-credentials token whose audience is the **gateway API**. The local sample uses a secret to simplify agent login. Replace it with certificate/federation for a serious environment.
2. **Gateway MCP resource app registration** (`GATEWAY_CLIENT_ID`, `GATEWAY_AUDIENCE`). Agentgateway uses this to validate **incoming** Entra JWT. Create an application role `Gateway.Invoke` allowed for **Applications**. Grant this role to the Agent app's service principal (admin consent). Set access token version 2 (see below).
3. **Gateway user-assigned Managed Identity**. This is NOT `GATEWAY_CLIENT_ID`. It is the gateway's **outbound** identity to Azure Function. The deployment script prints its `clientId`. Give this service principal the **backend** `Invoice.Read` app role using `scripts/grant-backend-role.sh`.

The **backend API app registration** (`BACKEND_CLIENT_ID`) exposes the backend's `api://...` audience and an application role `Invoice.Read` allowed to Applications. There are therefore **3 app registrations + 1 managed identity**.

## Required Entra setup

1. Register Agent, Gateway API, Backend API in one tenant; set their Application ID URIs (`api://<guid>`) as configured.
2. Under Gateway API > App roles, create `Gateway.Invoke` (Allowed member types: Applications). Under Backend API > App roles, create `Invoice.Read` (Allowed member types: Applications).
3. Set the Gateway API app manifest `api.requestedAccessTokenVersion: 2` (on some portals `requestedAccessTokenVersion`). Set the same for Backend API to match the sample Function's strict v2 JWT verifier.
4. Under Agent > API permissions, add the **Gateway API's application permission** `Gateway.Invoke`, then grant admin consent. **Do not** grant `Invoice.Read` directly to the Agent.
5. On Backend API, assign `Invoice.Read` to the Gateway MI service principal via `grant-backend-role.sh`. This needs a privileged administrator with Microsoft Graph appRole assignment privileges, and can be done only **after** MI exists.
6. Disable implicit grant and avoid permissive redirect URIs. The demo is **app-only**. No OBO of users is claimed.

## Azure Function

Publish from the `backend/` directory (functions v2 Python). Configure Function App settings:

```text
TENANT_ID = tenant UUID
BACKEND_AUDIENCE = <BACKEND_CLIENT_ID, the GUID in the Entra v2 aud claim>
BACKEND_APP_ROLE = Invoice.Read
GATEWAY_CLIENT_ID = <USER ASSIGNED MANAGED IDENTITY CLIENT ID, from deployment output>
BACKEND_DELEGATED_SCOPE = invoice.read  # unused in app-only flow
```

Important: The last setting `GATEWAY_CLIENT_ID` is the managed identity *client ID*, not the gateway's incoming resource app registration client ID. Backend verifies the Entra token's `azp`/`appid` claim and `roles=[Invoice.Read]`.

- The function `GET /api/invoices/{invoice_id}` deliberately uses `authLevel: anonymous` (no Function key), but **the code requires a valid Entra bearer JWT**. Enable App Service Authentication (Easy Auth) with `Require authentication`, set allowed token audience to the Backend API and restrict callers when possible; keep in-code validation as defense in depth.
- Backend's JWT issuer is `https://login.microsoftonline.com/<tenant>/v2.0`. Change the code if your Entra resource emits v1 app tokens; do **not** disable signature/audience/issuer validation.
- A function app hosted on the open internet with Easy Auth is not network-isolated. Restrict connectivity to the gateway if your architecture requires bypass-proof access, such as VNet/private access where supported by your hosting plans.

## Access token flow

```text
Agent (app credentials) -> Entra -> JWT aud=Gateway API roles=[Gateway.Invoke]
Agent -> agentgateway: POST /mcp tools/call + agent JWT
agentgateway -> checks validated jwt.azp + role + tool + company
agentgateway ACA User Assigned MI -> Entra -> JWT aud=Backend API roles=[Invoice.Read]
agentgateway -> Azure Function via HTTP Authorization: Bearer <backend JWT>
Function verifies tenant/issuer/signature/audience + MI app client ID + app role
```

This is **not** OAuth 2.0 RFC 8693 token exchange and **not** delegated OBO. It is token acquisition under an Azure managed workload identity, with separate `aud` and explicit approvals.

### A note on actual per-agent backend identity

If SAP or another backend must recognize agent-specific accounts, use a separate identity/token architecture: delegated token exchange if supported, per-agent managed identity, or per-agent application service principal. The single UAMI mode in this ZIP does not demonstrate this. The architecture still enforces per-agent MCP authorization before backend use.
