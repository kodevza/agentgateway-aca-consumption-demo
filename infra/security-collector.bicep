targetScope = 'resourceGroup'

param location string = resourceGroup().location
param identityName string
param tenantId string = tenant().tenantId
@description('Client ID of the existing backend Entra API registration used by the gateway managed identity.')
param backendClientId string
param backendAudience string = 'api://${backendClientId}'

var suffix = uniqueString(resourceGroup().id, identityName)
var functionName = 'func-agw-security-${suffix}'
var archiveContainerName = 'security-events'

resource gatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: 'agwsecurity${suffix}'
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

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = [for name in ['function-package', archiveContainerName]: {
  parent: blobService
  name: name
  properties: {
    publicAccess: 'None'
  }
}]

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'asp-agw-security-${suffix}'
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

resource collector 'Microsoft.Web/sites@2024-04-01' = {
  name: functionName
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
        version: '3.12'
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 512
        maximumInstanceCount: 40
      }
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${containers[0].name}'
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
          name: 'SECURITY_BLOB_ENDPOINT'
          value: storage.properties.primaryEndpoints.blob
        }
        {
          name: 'SECURITY_BLOB_CONTAINER'
          value: containers[1].name
        }
      ]
    }
  }
}

resource auth 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: collector
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
          allowedAudiences: [backendClientId, backendAudience]
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

// The Functions host also needs to create its own containers for runtime state.
var blobOwnerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
resource blobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, collector.id, blobOwnerRole)
  properties: {
    principalId: collector.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobOwnerRole
  }
}

output securityCollectorHost string = 'https://${collector.properties.defaultHostName}'
output securityCollectorName string = collector.name
output storageAccountName string = storage.name
output archiveContainer string = archiveContainerName
