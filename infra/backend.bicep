targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Name of the gateway user-assigned managed identity created by foundation.bicep.')
param identityName string

param tenantId string = tenant().tenantId

@description('Client ID of the existing backend Entra API registration, used as the v2 JWT audience.')
param backendClientId string

var suffix = uniqueString(resourceGroup().id, identityName)

resource gatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: 'agwbackend${suffix}'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storage
  name: 'default'
}

resource packageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobService
  name: 'function-package'
  properties: {
    publicAccess: 'None'
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'asp-agw-backend-${suffix}'
  location: location
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource backend 'Microsoft.Web/sites@2024-04-01' = {
  name: 'func-agw-backend-${suffix}'
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      runtime: {
        name: 'python'
        version: '3.11'
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 512
        maximumInstanceCount: 40
      }
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${packageContainer.name}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
    }
    siteConfig: {
      appSettings: [
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storage.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'TENANT_ID'
          value: tenantId
        }
        {
          name: 'BACKEND_AUDIENCE'
          value: backendClientId
        }
        {
          name: 'BACKEND_APP_ROLE'
          value: 'Invoice.Read'
        }
        {
          name: 'GATEWAY_CLIENT_ID'
          value: gatewayIdentity.properties.clientId
        }
      ]
    }
  }
}

resource auth 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: backend
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: backendClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [backendClientId]
          defaultAuthorizationPolicy: {
            allowedApplications: [gatewayIdentity.properties.clientId]
            allowedPrincipals: {
              identities: [gatewayIdentity.properties.principalId]
            }
          }
        }
      }
    }
  }
}

// The Functions host creates containers for its runtime state.
var blobOwnerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
resource blobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, backend.id, blobOwnerRole)
  properties: {
    principalId: backend.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobOwnerRole
  }
}

output backendHost string = 'https://${backend.properties.defaultHostName}'
output backendName string = backend.name
