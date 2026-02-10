// Create a standalone Azure Functions App for the API
targetScope = 'resourceGroup'

@description('Name for the Functions App')
param functionsAppName string

@description('Location for all resources')
param location string = resourceGroup().location

@description('Storage account name for Functions')
param storageAccountName string

@description('App Service Plan name')
param appServicePlanName string

@description('Subscription ID for VM SKU access')
param subscriptionId string

@description('Tags to apply to all resources')
param tags object = {
  Environment: 'Production'
  Application: 'Azure VM SKU Alternatives API'
  ManagedBy: 'Bicep'
}

// Storage Account for Functions
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
}

// App Service Plan (Consumption/Free tier)
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {}
}

// Azure Functions App
resource functionsApp 'Microsoft.Web/sites@2023-01-01' = {
  name: functionsAppName
  location: location
  tags: tags
  kind: 'functionapp'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
        }
        {
          name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
        }
        {
          name: 'WEBSITE_CONTENTSHARE'
          value: toLower(functionsAppName)
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'node'
        }
        {
          name: 'WEBSITE_NODE_DEFAULT_VERSION'
          value: '~18'
        }
        {
          name: 'AZURE_SUBSCRIPTION_ID'
          value: subscriptionId
        }
      ]
      cors: {
        allowedOrigins: [
          'https://black-sea-0784c5d0f.1.azurestaticapps.net'
          'http://localhost:4280'
        ]
        supportCredentials: false
      }
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
    }
  }
}

// Output the Functions App details
output functionsAppName string = functionsApp.name
output functionsAppHostname string = functionsApp.properties.defaultHostName
output functionsAppPrincipalId string = functionsApp.identity.principalId
