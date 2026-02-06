// Main infrastructure template for Azure VM SKU Alternatives Web App
targetScope = 'resourceGroup'

@description('Name of the Static Web App')
param staticWebAppName string

@description('Location for all resources')
param location string = resourceGroup().location

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

@description('Tags to apply to all resources')
param tags object = {
  Environment: 'Production'
  Application: 'Azure VM SKU Alternatives'
  ManagedBy: 'Bicep'
}

// Static Web App with integrated Azure Functions
// If no repository URL is provided, deploy without GitHub integration (can be configured later)
resource staticWebApp 'Microsoft.Web/staticSites@2023-01-01' = {
  name: staticWebAppName
  location: location
  tags: tags
  sku: {
    name: sku
    tier: sku
  }
  properties: empty(repositoryUrl) ? {
    // Deploy without GitHub integration - will use manual deployment or Azure CLI
    buildProperties: {
      appLocation: '/src'
      apiLocation: '/api'
      outputLocation: ''
    }
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
    enterpriseGradeCdnStatus: 'Disabled'
  } : {
    // Deploy with GitHub integration
    repositoryUrl: repositoryUrl
    branch: branch
    repositoryToken: repositoryToken
    buildProperties: {
      appLocation: '/src'
      apiLocation: '/api'
      outputLocation: ''
    }
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
    provider: 'GitHub'
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

// Managed Identity for the Static Web App (for accessing Azure resources)
resource staticWebAppIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (sku == 'Standard') {
  name: '${staticWebAppName}-identity'
  location: location
  tags: tags
}

// Link managed identity to Static Web App (Standard SKU only)
resource staticWebAppIdentityLink 'Microsoft.Web/staticSites/linkedBackends@2023-01-01' = if (sku == 'Standard') {
  parent: staticWebApp
  name: 'backend-identity'
  properties: {
    backendResourceId: staticWebAppIdentity.id
    region: location
  }
  dependsOn: [
    staticWebAppIdentity
  ]
}

// Application Insights for monitoring
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${staticWebAppName}-insights'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// Log Analytics Workspace for centralized logging
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${staticWebAppName}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    workspaceCapping: {
      dailyQuotaGb: 1
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// Outputs
@description('URL of the Static Web App')
output staticWebAppUrl string = 'https://${staticWebApp.properties.defaultHostname}'

@description('Static Web App resource ID')
output staticWebAppId string = staticWebApp.id

@description('Static Web App name')
output staticWebAppName string = staticWebApp.name

@description('Application Insights Instrumentation Key')
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey

@description('Application Insights Connection String')
output appInsightsConnectionString string = appInsights.properties.ConnectionString

@description('Managed Identity Principal ID (Standard SKU only)')
output identityPrincipalId string = sku == 'Standard' ? staticWebAppIdentity.properties.principalId : ''

@description('Managed Identity Client ID (Standard SKU only)')
output identityClientId string = sku == 'Standard' ? staticWebAppIdentity.properties.clientId : ''
