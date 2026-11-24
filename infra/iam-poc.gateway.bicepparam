using './gateway.bicep'

param location = 'westeurope'
param registryName = 'iampoc9c899ccb3b4e'
param environmentName = 'acae-iam-poc'
param identityName = 'id-iam-poc'
param containerAppName = 'agentgateway-iam-poc'
param imageDigest = readEnvironmentVariable('IMAGE_DIGEST')
param tenantId = '655ccf7b-6f5b-4110-86e3-45c4b8ffc39a'
param gatewayClientId = '45b26c99-3735-4d0d-89bc-150eef50e91f'
param gatewayAudience = gatewayClientId
param backendAudience = 'api://9a9b76c6-002a-4987-b07a-71e1b6d21dfa'
param backendHost = 'https://func-iam-poc-9c899ccb3b4e.azurewebsites.net'
param securityCollectorHost = readEnvironmentVariable('SECURITY_COLLECTOR_HOST', '')
