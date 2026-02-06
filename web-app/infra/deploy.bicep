// Complete deployment template with proper role assignments
targetScope = 'subscription'

@description('Name of the resource group')
param resourceGroupName string = 'rg-vmsku-alternatives'

@description('Location for all resources')
param location string = 'eastus2'

@description('Name of the Static Web App')
param staticWebAppName string = 'vmsku-alternatives-webapp'

@description('SKU for the Static Web App')
@allowed([
  'Free'
  'Standard'
])
param sku string = 'Free'

@description('Repository URL for the Static Web App')
param repositoryUrl string = ''

@description('Branch name for deployment')
param branch string = 'main'

@description('Repository token for GitHub Actions deployment')
@secure()
param repositoryToken string = ''

@description('Azure Subscription ID for VM SKU queries')
param azureSubscriptionId string = subscription().subscriptionId

@description('Tags to apply to all resources')
param tags object = {
  Environment: 'Production'
  Application: 'Azure VM SKU Alternatives'
  ManagedBy: 'Bicep'
}

// Create resource group
resource resourceGroup 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// Deploy main infrastructure
module mainInfra 'main.bicep' = {
  name: 'main-infrastructure'
  scope: resourceGroup
  params: {
    staticWebAppName: staticWebAppName
    location: location
    sku: sku
    repositoryUrl: repositoryUrl
    branch: branch
    repositoryToken: repositoryToken
    tags: tags
  }
}

// Assign Reader role to the managed identity (Standard SKU only)
// This allows the Functions to query VM SKUs
module readerRoleAssignment 'modules/role-assignment.bicep' = if (sku == 'Standard') {
  name: 'reader-role-assignment'
  scope: resourceGroup
  params: {
    principalId: mainInfra.outputs.identityPrincipalId
    roleDefinitionId: 'acdd72a7-3385-48ef-bd42-f606fba81ae7' // Reader role
  }
}

// Outputs
@description('Resource Group name')
output resourceGroupName string = resourceGroup.name

@description('URL of the Static Web App')
output staticWebAppUrl string = mainInfra.outputs.staticWebAppUrl

@description('Static Web App resource ID')
output staticWebAppId string = mainInfra.outputs.staticWebAppId

@description('Application Insights Instrumentation Key')
output appInsightsInstrumentationKey string = mainInfra.outputs.appInsightsInstrumentationKey

@description('Application Insights Connection String')
output appInsightsConnectionString string = mainInfra.outputs.appInsightsConnectionString

@description('Managed Identity Principal ID')
output identityPrincipalId string = sku == 'Standard' ? mainInfra.outputs.identityPrincipalId : ''

@description('Subscription ID configured for VM SKU queries')
output azureSubscriptionId string = azureSubscriptionId
