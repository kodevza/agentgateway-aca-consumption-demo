# Deploy Agent Gateway with Bicep

| Template | Resources |
| --- | --- |
| [`foundation.bicep`](foundation.bicep) | ACR Basic, gateway managed identity and `AcrPull`, Container Apps Consumption environment, Log Analytics and Application Insights |
| [`gateway.bicep`](gateway.bicep) | Gateway Container App with HTTPS ingress and an ACR image referenced by digest |
| [`backend.bicep`](backend.bicep) | Optional invoice API on Azure Functions Flex Consumption with Entra authentication and identity-based deployment and host storage |
| [`security-collector.bicep`](security-collector.bicep) | Optional OTLP security collector on Azure Functions Flex Consumption with Blob Storage archive |

Run the commands from the repository root in Bash. Use a new resource group
with a globally unique lowercase alphanumeric `AZ_ACR_NAME`. An existing
deployment from `scripts/deploy.sh` may already have an `AcrPull` assignment
under a different ID or an environment with a different profile or logging
configuration. The commands below target a fresh deployment.

Configure the Entra API registrations as described in
[Entra and backend setup](../docs/ENTRA_AND_BACKEND.md). Fill in `.env`
using `.env.example`. Set `BACKEND_HOST` to the HTTPS origin of your existing
Function or use the [optional backend deployment](#deploy-the-optional-invoice-backend)
below. The local agent secret stays in `.env`. For Entra v2 tokens the gateway
validates the API client ID (GUID) in the `aud` claim. The agent still
requests the token using the API URI (`api://<client-id>`), and the
gateway uses the backend API URI to request its outbound token. Configure
the Function's `BACKEND_AUDIENCE` with the backend API client ID.

## Create the foundation

Sign in to Azure CLI and select the intended subscription. The deploying
principal needs permission to create the resources, assign `AcrPull` on the
registry and run an ACR build. Register the resource providers in advance if
needed.

```bash
source .env

for provider in Microsoft.App Microsoft.ContainerRegistry Microsoft.ManagedIdentity Microsoft.OperationalInsights Microsoft.Insights; do
  az provider register --namespace "$provider" --wait
done

az group create --name "$AZ_RESOURCE_GROUP" --location "$AZ_LOCATION"

az deployment group create --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-foundation --template-file infra/foundation.bicep \
  --parameters location="$AZ_LOCATION" registryName="$AZ_ACR_NAME" \
    environmentName="$AZ_ACA_ENV" identityName="$AZ_IDENTITY_NAME" \
    workspaceName="${AZ_ACA_ENV}-logs"
```

## Deploy the optional invoice backend

For a new demo backend, run this after the foundation with `.env` loaded.
Register `Microsoft.Web` and `Microsoft.Storage` if needed. The template sets
the backend's expected token audience to `BACKEND_CLIENT_ID` and configures
App Service Authentication to allow the gateway managed identity. Keep
`BACKEND_AUDIENCE` set to the API URI in `.env` for the gateway's token request.
The existing Entra API registration must issue v2 access tokens. Grant the
gateway identity `Invoice.Read` with `./scripts/grant-backend-role.sh` after
the foundation creates the identity.

```bash
az deployment group create --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-backend --template-file infra/backend.bicep \
  --parameters location="$AZ_LOCATION" identityName="$AZ_IDENTITY_NAME" \
    tenantId="$TENANT_ID" backendClientId="$BACKEND_CLIENT_ID"

BACKEND_NAME="$(az deployment group show --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-backend --query properties.outputs.backendName.value -o tsv)"
BACKEND_HOST="$(az deployment group show --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-backend --query properties.outputs.backendHost.value -o tsv)"

package_dir="$(mktemp -d)"
(cd backend && zip -q "$package_dir/backend.zip" function_app.py api_logic.py entra_security.py host.json requirements.txt)
az functionapp deployment source config-zip --resource-group "$AZ_RESOURCE_GROUP" \
  --name "$BACKEND_NAME" --src "$package_dir/backend.zip" --build-remote true
```

Use the returned `BACKEND_HOST` when deploying the gateway. Save it
in `.env` for later gateway deployments. If the first code publish fails while
the Function identity's Blob Storage role propagates, retry the publish after
propagation.

## Build the image and deploy the gateway

Render the policy before building so the image contains the current approvals.
The app references the manifest digest so that later changes to the build tag
cannot change the image served by an existing revision.

```bash
python3 scripts/policy.py render

IMAGE_TAG="$(sha256sum Dockerfile config/config.yaml config/openapi.json | sha256sum | cut -c1-20)"
az acr build --resource-group "$AZ_RESOURCE_GROUP" --registry "$AZ_ACR_NAME" \
  --image "agentgateway:$IMAGE_TAG" .
IMAGE_DIGEST="$(az acr repository show --name "$AZ_ACR_NAME" \
  --image "agentgateway:$IMAGE_TAG" --query digest --output tsv)"

az deployment group create --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-app --template-file infra/gateway.bicep \
  --parameters location="$AZ_LOCATION" registryName="$AZ_ACR_NAME" \
    environmentName="$AZ_ACA_ENV" identityName="$AZ_IDENTITY_NAME" \
    containerAppName="$AZ_CONTAINER_APP" imageDigest="$IMAGE_DIGEST" \
    tenantId="$TENANT_ID" gatewayClientId="$GATEWAY_CLIENT_ID" \
    gatewayAudience="$GATEWAY_CLIENT_ID" backendAudience="$BACKEND_AUDIENCE" \
    backendHost="$BACKEND_HOST" securityCollectorHost="${SECURITY_COLLECTOR_HOST:-}" \
  --query properties.outputs
```

If the first revision fails to pull the image while the new `AcrPull`
assignment propagates, retry the gateway deployment after propagation.
Use the `gatewayUrl` output for the client's `GATEWAY_URL`. For an existing
Function, set its `GATEWAY_CLIENT_ID` to the `managedIdentityClientId` output
and configure its remaining settings using the Entra and backend guide above.
Run `scripts/grant-backend-role.sh` if the identity still needs `Invoice.Read`.
Repeat the build and gateway steps when approvals change.
For end-to-end checks, see [Operations](../docs/OPERATIONS.md).

## Gateway telemetry

Container console and system logs go to Log Analytics. The gateway sends
request traces over gRPC to the Container Apps managed OpenTelemetry agent,
which forwards them to the workspace-based Application Insights component.
The Bicep deployment samples all requests. The generated gateway configuration
defaults to zero random sampling outside that deployment. If Azure injects a
different collector address in `OTEL_EXPORTER_OTLP_ENDPOINT`, pass its host and
port as `tracingOtlpHost`.
For an existing Bicep deployment, use the same foundation parameters as
before, including its `workspaceName`, then rebuild the gateway image and
deploy it so the new revision picks up the tracing configuration.

After sending an MCP request, check the workspace for recent records:

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "<AZ_CONTAINER_APP>"
| summarize Entries = count(), LastSeen = max(TimeGenerated)
```

```kusto
union AppRequests, AppDependencies
| where AppRoleName in ("<AZ_CONTAINER_APP>", "<AZ_ACA_ENV>.<AZ_CONTAINER_APP>")
| project TimeGenerated, Name, OperationId, Success, ResultCode, DurationMs
| order by TimeGenerated desc
```

The Foundry project `ownerlens-agent-devpl` uses the project connection
`iam-poc-application-insights` to send agent traces to
`acae-iam-poc-insights`, backed by `log-iam-poc`. To inspect the agent's
model and tool steps, open **Build → Agents → Traces** in Foundry.
In Log Analytics, find the agent's runs by name and use `OperationId` to
look up their related spans in `AppDependencies`.

```kusto
AppDependencies
| where AppRoleName == "responsesapi"
| where Name startswith "invoke_agent iam-poc-invoice-agent"
| project TimeGenerated, Name, OperationId, Success, DurationMs
| order by TimeGenerated desc
```

## Archive Gateway security events to Blob Storage

The optional Flex Consumption Function receives Agent Gateway access logs at
`/v1/logs` over OTLP/HTTP with protobuf. It archives each batch as NDJSON in the
new `security-events` blob container. Records include HTTP status, the gateway
decision reason, MCP method and tool name, the guardrail rejection flag, and
caller application ID and trace ID when available. The archive projects these
fields from the records and excludes token values, headers, query strings and
MCP payloads. An MCP denial can return HTTP 200, so check `reason` and
`security.guardrail.rejected` as well as HTTP status.

App Service Authentication accepts tokens for the existing backend Entra API
from the gateway's managed identity. The Function uses its own managed identity
to write blobs. The deployment uses the existing trace and console log paths
for Application Insights and Log Analytics.

After creating the foundation, deploy the collector and publish its code before
building and deploying the gateway image. Run from the repository root with
`.env` loaded. Register `Microsoft.Web` and `Microsoft.Storage` if needed.

```bash
az deployment group create --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-security --template-file infra/security-collector.bicep \
  --parameters location="$AZ_LOCATION" identityName="$AZ_IDENTITY_NAME" \
    tenantId="$TENANT_ID" backendClientId="$BACKEND_CLIENT_ID" \
    backendAudience="$BACKEND_AUDIENCE" \
  --query properties.outputs

SECURITY_COLLECTOR_NAME="$(az deployment group show --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-security --query properties.outputs.securityCollectorName.value -o tsv)"
export SECURITY_COLLECTOR_HOST="$(az deployment group show --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-security --query properties.outputs.securityCollectorHost.value -o tsv)"
SECURITY_STORAGE_ACCOUNT="$(az deployment group show --resource-group "$AZ_RESOURCE_GROUP" \
  --name agentgateway-security --query properties.outputs.storageAccountName.value -o tsv)"
package_dir="$(mktemp -d)"
(cd security_collector && zip -q "$package_dir/function.zip" function_app.py host.json requirements.txt)
az functionapp deployment source config-zip --resource-group "$AZ_RESOURCE_GROUP" \
  --name "$SECURITY_COLLECTOR_NAME" --src "$package_dir/function.zip" --build-remote true
```

Use `SECURITY_COLLECTOR_HOST` for the gateway deployment command above or set it
in `.env` for `scripts/deploy.sh`. After deployment, send an MCP request without
a token and try an invoice request for a company outside the approval. Check
the new storage account output for blobs in `security-events`. With Blob Data
Reader access, `az storage blob list --account-name "$SECURITY_STORAGE_ACCOUNT"
--container-name security-events --auth-mode login` lists the archived batches.
The gateway batches OTLP logs and retries delivery. An outage that exhausts
those retries can leave gaps in the archive.

## Existing `iam-poc` deployment

The parameter files [iam-poc.foundation.bicepparam](iam-poc.foundation.bicepparam)
and [iam-poc.gateway.bicepparam](iam-poc.gateway.bicepparam) identify the
gateway resources in subscription `Test` (`7e7963c6-cddc-4d64-bcdd-1bfb727a05c2`),
resource group `iam-poc`, West Europe. Supply `IMAGE_DIGEST` from the new ACR
build when deploying the gateway. Use the foundation parameters first, then
rebuild the image as described above and pass its digest to the gateway
deployment.

```bash
az deployment group create --subscription Test --resource-group iam-poc \
  --name agentgateway-foundation --parameters infra/iam-poc.foundation.bicepparam

# Optional. Publish the collector code and export SECURITY_COLLECTOR_HOST
# as shown above if you want to connect it to the gateway.
az deployment group create --subscription Test --resource-group iam-poc \
  --name agentgateway-security --parameters infra/iam-poc.security-collector.bicepparam

# Run after publishing the new image and setting IMAGE_DIGEST.
IMAGE_DIGEST="$IMAGE_DIGEST" az deployment group create \
  --subscription Test --resource-group iam-poc \
  --name agentgateway-app --parameters infra/iam-poc.gateway.bicepparam
```

| Component | Where it is defined |
| --- | --- |
| ACR Basic, gateway managed identity and `AcrPull`, ACA Consumption with 0–1 replicas, Log Analytics | Foundation and gateway Bicep templates, deployed in `iam-poc` |
| Application Insights and gateway OpenTelemetry export | Foundation and gateway Bicep templates plus the gateway image configuration in `iam-poc` |
| Function `func-iam-poc-9c899ccb3b4e`, plan `ASP-iampoc-8d03` and storage `stiampoc9c899ccb3b4e` | Existing Azure resources in `iam-poc`. The gateway template references the Function URL. |
| Security collector Function and Blob Storage archive | `security-collector.bicep` with `iam-poc.security-collector.bicepparam`. Export `SECURITY_COLLECTOR_HOST` to enable log export from the gateway. |
| Gateway API and backend API app registrations and their app-role assignments | Entra. The Foundry agent's individual identity has `Gateway.Invoke`. The gateway managed identity has `Invoice.Read`. See [Entra and backend setup](../docs/ENTRA_AND_BACKEND.md). |
| Foundry project `ownerlens-agent-devpl`, agent `iam-poc-invoice-agent` and MCP connection `iam-poc-invoices-mcp` | Existing Microsoft Foundry resources. The agent uses its individual Entra identity for MCP authentication without an application secret. |
| Approval for `invoice.read` for `PL01` | Local `config/approvals.json` and the generated `config/config.yaml` baked into the image |

The earlier Entra registration `iam-poc-agent` was used for the Python
smoke test. The Foundry flow uses the agent identity listed below.

On a fresh checkout, recreate the approval from the Foundry agent's current
Entra client ID before building the image. The current ID is shown below.
Resolve the new ID in Entra and grant it `Gateway.Invoke` if the Foundry
agent is recreated.

```bash
python3 scripts/policy.py approve \
  --agent-client-id 65bf4b1b-b9a7-43a3-97c7-ea7b351f8873 \
  --agent-owner poc-agent-owner \
  --resource-owner poc-resource-owner \
  --purpose iam-poc-smoke \
  --company PL01
```

Validate the PoC from Foundry Playground by requesting demo invoice
`INV-2026-0001` for `PL01`, then check that the same tool call for `DE01`
is denied. After deploying the telemetry change, check the workspace queries
above.

## Validate templates locally

```bash
(
  set -e
  for template in infra/*.bicep; do
    az bicep lint --file "$template"
    az bicep build --file "$template" --stdout > /dev/null
  done
)
```

With Azure access, run `az deployment group validate` with the same resource
group and parameters as the corresponding `create` command. The gateway check
requires `IMAGE_DIGEST` when using `iam-poc.gateway.bicepparam`. The backend,
collector and gateway checks use resources already created by the foundation.
