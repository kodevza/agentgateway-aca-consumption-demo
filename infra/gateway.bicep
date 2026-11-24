targetScope = 'resourceGroup'

param location string = resourceGroup().location

param registryName string
param environmentName string
param identityName string
param containerAppName string

@description('Digest of the published agentgateway image, including the sha256: prefix.')
@minLength(71)
@maxLength(71)
param imageDigest string

param tenantId string = tenant().tenantId
param gatewayClientId string
param gatewayAudience string
param backendAudience string

@description('Host and port of the managed Container Apps OTLP gRPC collector.')
param tracingOtlpHost string = 'k8se-otel.k8se-apps.svc.cluster.local:4317'

@description('HTTPS origin of the existing Azure Function, without a trailing slash.')
param backendHost string

@description('HTTPS origin of the optional security telemetry Function, without a trailing slash.')
param securityCollectorHost string = ''

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: registryName
}

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource gatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

// The default external ingress hostname is determined by the app name and
// environment domain, so it can be passed to the container on first deploy.
var gatewayUrl = 'https://${containerAppName}.${acaEnvironment.properties.defaultDomain}'
var mcpResourceUrl = '${gatewayUrl}/mcp'

resource gateway 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${gatewayIdentity.id}': {}
    }
  }
  properties: {
    environmentId: acaEnvironment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 3000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: gatewayIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'agentgateway'
          image: '${registry.properties.loginServer}/agentgateway@${imageDigest}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat([
            {
              name: 'ENTRA_ISSUER'
              value: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
            }
            {
              name: 'GATEWAY_CLIENT_ID'
              value: gatewayClientId
            }
            {
              name: 'GATEWAY_AUDIENCE'
              value: gatewayAudience
            }
            {
              name: 'BACKEND_AUDIENCE'
              value: backendAudience
            }
            {
              name: 'BACKEND_MI_CLIENT_ID'
              value: gatewayIdentity.properties.clientId
            }
            {
              name: 'BACKEND_HOST'
              value: backendHost
            }
            {
              name: 'MCP_RESOURCE_URL'
              value: mcpResourceUrl
            }
            {
              name: 'AGENTGATEWAY_OTLP_HOST'
              value: tracingOtlpHost
            }
            {
              name: 'AGENTGATEWAY_TRACE_SAMPLING'
              value: 'true'
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: containerAppName
            }
          ], empty(securityCollectorHost) ? [] : [
            {
              name: 'AGENTGATEWAY_SECURITY_OTLP_HOST'
              value: securityCollectorHost
            }
            {
              name: 'AGENTGATEWAY_SECURITY_OTLP_FILTER'
              value: 'true'
            }
            {
              name: 'OTEL_EXPORTER_OTLP_LOGS_PROTOCOL'
              value: 'http/protobuf'
            }
          ])
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

output gatewayUrl string = 'https://${gateway.properties.configuration.ingress.fqdn}'
output mcpResourceUrl string = '${gatewayUrl}/mcp'
output managedIdentityClientId string = gatewayIdentity.properties.clientId
