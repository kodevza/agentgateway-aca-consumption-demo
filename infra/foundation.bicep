targetScope = 'resourceGroup'

param location string = resourceGroup().location

@minLength(5)
@maxLength(50)
param registryName string

param environmentName string
param identityName string
param workspaceName string
param applicationInsightsName string = '${environmentName}-insights'

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    roleAssignmentMode: 'LegacyRegistryPermissions'
    policies: {
      azureADAuthenticationAsArmPolicy: {
        status: 'enabled'
      }
    }
  }
}

resource gatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, gatewayIdentity.id, acrPullRole)
  properties: {
    roleDefinitionId: acrPullRole
    principalId: gatewayIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableLocalAuth: false
  }
}

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: environmentName
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    appInsightsConfiguration: {
      connectionString: applicationInsights.properties.ConnectionString
    }
    openTelemetryConfiguration: {
      tracesConfiguration: {
        destinations: [
          'appInsights'
        ]
      }
    }
  }
}
