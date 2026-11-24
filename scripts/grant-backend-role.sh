#!/usr/bin/env bash
# Grant backend API's Invoice.Read app role to the gateway UAMI service principal.
# Requires Microsoft Graph AppRoleAssignment.ReadWrite.All and suitable admin role.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo 'Missing .env' >&2; exit 1; }
set -a; source .env; set +a
: "${AZ_RESOURCE_GROUP:?}" "${AZ_IDENTITY_NAME:?}" "${BACKEND_CLIENT_ID:?}"
PRINCIPAL_ID=$(az identity show -g "$AZ_RESOURCE_GROUP" -n "$AZ_IDENTITY_NAME" --query principalId -o tsv)
CLIENT_ID=$(az identity show -g "$AZ_RESOURCE_GROUP" -n "$AZ_IDENTITY_NAME" --query clientId -o tsv)
BACKEND_SP_ID=$(az ad sp show --id "$BACKEND_CLIENT_ID" --query id -o tsv)
ROLE_ID=$(az ad sp show --id "$BACKEND_CLIENT_ID" --query "appRoles[?value=='Invoice.Read'].id | [0]" -o tsv)
[[ -n "$ROLE_ID" ]] || { echo 'Backend appRole Invoice.Read missing' >&2; exit 1; }
if az rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/${PRINCIPAL_ID}/appRoleAssignments" \
  --query "value[?resourceId=='${BACKEND_SP_ID}' && appRoleId=='${ROLE_ID}'] | length(@)" -o tsv | grep -qx 1; then
  echo 'Role already assigned'
else
  BODY=$(printf '{"principalId":"%s","resourceId":"%s","appRoleId":"%s"}' "$PRINCIPAL_ID" "$BACKEND_SP_ID" "$ROLE_ID")
  az rest --method POST --url "https://graph.microsoft.com/v1.0/servicePrincipals/${PRINCIPAL_ID}/appRoleAssignments" \
    --headers 'Content-Type=application/json' --body "$BODY" -o none
fi
printf 'Gateway UAMI clientId: %s\n' "$CLIENT_ID"
printf 'Configure Azure Function App setting GATEWAY_CLIENT_ID=%s\n' "$CLIENT_ID"
