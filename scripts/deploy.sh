#!/usr/bin/env bash
# Deploy real agentgateway v1.7.0 standalone to Azure Container Apps Consumption.
# Requires: az login, permissions to create ACR/ACA/MI and create role assignments.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then echo 'Missing .env (cp .env.example .env)' >&2; exit 1; fi
set -a; source .env; set +a
for key in TENANT_ID GATEWAY_CLIENT_ID GATEWAY_AUDIENCE BACKEND_AUDIENCE BACKEND_HOST AZ_RESOURCE_GROUP AZ_LOCATION AZ_ACA_ENV AZ_CONTAINER_APP AZ_IDENTITY_NAME AZ_ACR_NAME; do
  if [[ -z "${!key:-}" || "${!key}" == *REPLACE* || "${!key}" == *00000000* || "${!key}" == *22222222* || "${!key}" == *33333333* ]]; then
    echo "Set real $key in .env" >&2; exit 1
  fi
done
[[ "$BACKEND_HOST" == https://* ]] || { echo 'BACKEND_HOST must be https://' >&2; exit 1; }
command -v az >/dev/null || { echo 'Azure CLI not found' >&2; exit 1; }
python3 scripts/policy.py render
az extension add --name containerapp --upgrade --yes >/dev/null
az provider register --namespace Microsoft.App --wait >/dev/null
az provider register --namespace Microsoft.OperationalInsights --wait >/dev/null
az group create -n "$AZ_RESOURCE_GROUP" -l "$AZ_LOCATION" -o none
if ! az acr show -n "$AZ_ACR_NAME" -g "$AZ_RESOURCE_GROUP" >/dev/null 2>&1; then
  az acr create -n "$AZ_ACR_NAME" -g "$AZ_RESOURCE_GROUP" --sku Basic --admin-enabled false -o none
fi
if ! az identity show -n "$AZ_IDENTITY_NAME" -g "$AZ_RESOURCE_GROUP" >/dev/null 2>&1; then
  az identity create -g "$AZ_RESOURCE_GROUP" -n "$AZ_IDENTITY_NAME" -l "$AZ_LOCATION" -o none
fi
MI_ID=$(az identity show -g "$AZ_RESOURCE_GROUP" -n "$AZ_IDENTITY_NAME" --query id -o tsv)
MI_PRINCIPAL=$(az identity show -g "$AZ_RESOURCE_GROUP" -n "$AZ_IDENTITY_NAME" --query principalId -o tsv)
MI_CLIENT=$(az identity show -g "$AZ_RESOURCE_GROUP" -n "$AZ_IDENTITY_NAME" --query clientId -o tsv)
ACR_ID=$(az acr show -n "$AZ_ACR_NAME" -g "$AZ_RESOURCE_GROUP" --query id -o tsv)
# Registry pull is granted only to the Container App's user-assigned MI.
if [[ $(az role assignment list --assignee "$MI_PRINCIPAL" --scope "$ACR_ID" --query "[?roleDefinitionName=='AcrPull'] | length(@)" -o tsv) == 0 ]]; then
  az role assignment create --assignee-object-id "$MI_PRINCIPAL" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$ACR_ID" -o none
fi
if ! az containerapp env show -g "$AZ_RESOURCE_GROUP" -n "$AZ_ACA_ENV" >/dev/null 2>&1; then
  az containerapp env create -n "$AZ_ACA_ENV" -g "$AZ_RESOURCE_GROUP" -l "$AZ_LOCATION" -o none
fi
DEFAULT_DOMAIN=$(az containerapp env show -g "$AZ_RESOURCE_GROUP" -n "$AZ_ACA_ENV" --query properties.defaultDomain -o tsv)
if [[ -z "$DEFAULT_DOMAIN" ]]; then echo 'Could not determine Container Apps defaultDomain' >&2; exit 1; fi
URL="https://${AZ_CONTAINER_APP}.${DEFAULT_DOMAIN}"
export ENTRA_ISSUER="https://login.microsoftonline.com/${TENANT_ID}/v2.0"
MCP_RESOURCE_URL="${URL}/mcp"
IMAGE_TAG="$(sha256sum Dockerfile config/config.yaml config/openapi.json | sha256sum | cut -c1-20)"
IMAGE="${AZ_ACR_NAME}.azurecr.io/agentgateway:${IMAGE_TAG}"
security_env=()
if [[ -n "${SECURITY_COLLECTOR_HOST:-}" ]]; then
  security_env=("AGENTGATEWAY_SECURITY_OTLP_HOST=$SECURITY_COLLECTOR_HOST"
    "AGENTGATEWAY_SECURITY_OTLP_FILTER=true" "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf")
fi
az acr build -g "$AZ_RESOURCE_GROUP" -r "$AZ_ACR_NAME" -t "agentgateway:${IMAGE_TAG}" .
if az containerapp show -n "$AZ_CONTAINER_APP" -g "$AZ_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp revision set-mode -g "$AZ_RESOURCE_GROUP" -n "$AZ_CONTAINER_APP" --mode single -o none
  az containerapp update -g "$AZ_RESOURCE_GROUP" -n "$AZ_CONTAINER_APP" --image "$IMAGE" \
    --set-env-vars "ENTRA_ISSUER=$ENTRA_ISSUER" "GATEWAY_AUDIENCE=$GATEWAY_AUDIENCE" \
      "GATEWAY_CLIENT_ID=$GATEWAY_CLIENT_ID" "BACKEND_AUDIENCE=$BACKEND_AUDIENCE" "BACKEND_MI_CLIENT_ID=$MI_CLIENT" \
      "BACKEND_HOST=$BACKEND_HOST" "MCP_RESOURCE_URL=$MCP_RESOURCE_URL" "${security_env[@]}" -o none
else
  az containerapp create -n "$AZ_CONTAINER_APP" -g "$AZ_RESOURCE_GROUP" \
    --environment "$AZ_ACA_ENV" --image "$IMAGE" \
    --registry-server "${AZ_ACR_NAME}.azurecr.io" --registry-identity "$MI_ID" \
    --user-assigned "$MI_ID" --ingress external --target-port 3000 --transport http \
    --min-replicas 0 --max-replicas 1 --cpu 0.5 --memory 1Gi \
    --env-vars "ENTRA_ISSUER=$ENTRA_ISSUER" "GATEWAY_AUDIENCE=$GATEWAY_AUDIENCE" \
      "GATEWAY_CLIENT_ID=$GATEWAY_CLIENT_ID" "BACKEND_AUDIENCE=$BACKEND_AUDIENCE" "BACKEND_MI_CLIENT_ID=$MI_CLIENT" \
      "BACKEND_HOST=$BACKEND_HOST" "MCP_RESOURCE_URL=$MCP_RESOURCE_URL" "${security_env[@]}" -o none
  az containerapp revision set-mode -g "$AZ_RESOURCE_GROUP" -n "$AZ_CONTAINER_APP" --mode single -o none
fi
printf '\nGateway MCP: %s\nManaged Identity clientId (used by backend): %s\n' "$MCP_RESOURCE_URL" "$MI_CLIENT"
printf 'Next: ./scripts/grant-backend-role.sh (requires Graph appRoleAssignment permissions)\n'
printf 'Then set Function App setting GATEWAY_CLIENT_ID to the identity CLIENT ID above.\n'
printf 'NOTE: a new revision, not an immediate revocation, applies approval changes.\n'
