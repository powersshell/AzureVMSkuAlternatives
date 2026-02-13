// Azure Functions Flex Consumption with Private Storage
// Security: No public access, no storage keys, managed identity only

@description('Name of the Function App')
param functionsAppName string = 'vmsku-api-functions'

@description('Name of the App Service Plan')
param appServicePlanName string = '${functionsAppName}-flex-plan'

@description('Name of the storage account (must be globally unique, 3-24 chars, lowercase alphanumeric)')
param storageAccountName string = 'vmskunapi${uniqueString(resourceGroup().id)}'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Azure subscription ID')
param subscriptionId string = subscription().subscriptionId

@description('Tags to apply to all resources')
param tags object = {
  Application: 'Azure VM SKU Alternatives API'
  Environment: 'Production'
  ManagedBy: 'Bicep'
  Security: 'Private'
}

// Virtual Network Configuration
@description('Virtual Network address space')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Function App integration subnet address range')
param functionSubnetPrefix string = '10.0.1.0/24'

@description('Private endpoint subnet address range')
param privateEndpointSubnetPrefix string = '10.0.2.0/24'

// ============================================================================
// NETWORKING RESOURCES
// ============================================================================

// Virtual Network for private connectivity
resource virtualNetwork 'Microsoft.Network/virtualNetworks@2023-04-01' = {
  name: '${functionsAppName}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: 'function-integration-subnet'
        properties: {
          addressPrefix: functionSubnetPrefix
          delegations: [
            {
              name: 'delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
          serviceEndpoints: []
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
      {
        name: 'private-endpoint-subnet'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          serviceEndpoints: []
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

// Reference to subnets
var functionSubnetId = '${virtualNetwork.id}/subnets/function-integration-subnet'
var privateEndpointSubnetId = '${virtualNetwork.id}/subnets/private-endpoint-subnet'

// ============================================================================
// STORAGE ACCOUNT - PRIVATE & KEYLESS
// ============================================================================

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    // SECURITY: Disable public network access
    publicNetworkAccess: 'Disabled'
    
    // SECURITY: Disable shared key access (no storage account keys)
    allowSharedKeyAccess: false
    
    // Additional security settings
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    
    // Default to deny all network access
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

// Blob Service for storage account
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

// Deployment storage container for Flex Consumption (required for functionAppConfig)
resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'deployment-packages'
  properties: {
    publicAccess: 'None'
  }
}

// ============================================================================
// PRIVATE DNS ZONES
// ============================================================================

resource blobPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource filePrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.file.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource queuePrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.queue.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource tablePrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.table.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

// Link DNS zones to VNet
resource blobDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: blobPrivateDnsZone
  name: '${functionsAppName}-blob-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource fileDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: filePrivateDnsZone
  name: '${functionsAppName}-file-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource queueDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: queuePrivateDnsZone
  name: '${functionsAppName}-queue-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource tableDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: tablePrivateDnsZone
  name: '${functionsAppName}-table-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

// ============================================================================
// PRIVATE ENDPOINTS
// ============================================================================

// Private Endpoint for Blob
resource blobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-04-01' = {
  name: '${storageAccountName}-blob-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-blob-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource blobPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01' = {
  parent: blobPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: blobPrivateDnsZone.id
        }
      }
    ]
  }
}

// Private Endpoint for File
resource filePrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-04-01' = {
  name: '${storageAccountName}-file-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-file-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'file'
          ]
        }
      }
    ]
  }
}

resource filePrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01' = {
  parent: filePrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: filePrivateDnsZone.id
        }
      }
    ]
  }
}

// Private Endpoint for Queue
resource queuePrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-04-01' = {
  name: '${storageAccountName}-queue-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-queue-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'queue'
          ]
        }
      }
    ]
  }
}

resource queuePrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01' = {
  parent: queuePrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: queuePrivateDnsZone.id
        }
      }
    ]
  }
}

// Private Endpoint for Table
resource tablePrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-04-01' = {
  name: '${storageAccountName}-table-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-table-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'table'
          ]
        }
      }
    ]
  }
}

resource tablePrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01' = {
  parent: tablePrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: tablePrivateDnsZone.id
        }
      }
    ]
  }
}

// ============================================================================
// APPLICATION INSIGHTS
// ============================================================================

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${functionsAppName}-insights'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    RetentionInDays: 30
    WorkspaceResourceId: null
    IngestionMode: 'ApplicationInsights'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ============================================================================
// APP SERVICE PLAN - FLEX CONSUMPTION
// ============================================================================

resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  sku: {
    name: 'FC1'  // Flex Consumption SKU
    tier: 'FlexConsumption'
  }
  kind: 'functionapp,linux'
  properties: {
    reserved: true  // Required for Linux
    // Flex Consumption specific - maximum instances that can scale
    maximumElasticWorkerCount: 100
  }
}

// ============================================================================
// FUNCTION APP - FLEX CONSUMPTION WITH PRIVATE STORAGE
// ============================================================================

resource functionsApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionsAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    reserved: true
    
    // VNet Integration - REQUIRED for private storage access
    virtualNetworkSubnetId: functionSubnetId
    vnetRouteAllEnabled: true  // Route all traffic through VNet
    vnetContentShareEnabled: false  // Flex Consumption doesn't use file shares
    
    // REQUIRED for Flex Consumption: functionAppConfig
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: 'https://${storageAccount.name}.blob.${environment().suffixes.storage}/${deploymentContainer.name}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 100
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    
    siteConfig: {
      // NOTE: linuxFxVersion and pythonVersion are NOT valid for Flex Consumption
      // Runtime is specified in functionAppConfig.runtime instead
      
      // Flex Consumption Function App settings
      appSettings: [
        // MANAGED IDENTITY: Storage access without keys
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccount.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: 'https://${storageAccount.name}.blob.${environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: 'https://${storageAccount.name}.queue.${environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: 'https://${storageAccount.name}.table.${environment().suffixes.storage}'
        }
        // NOTE: FUNCTIONS_EXTENSION_VERSION and FUNCTIONS_WORKER_RUNTIME are INVALID for Flex Consumption
        // Runtime is specified in functionAppConfig.runtime instead
        {
          name: 'AzureWebJobsFeatureFlags'
          value: 'EnableWorkerIndexing'
        }
        {
          name: 'AZURE_SUBSCRIPTION_ID'
          value: subscriptionId
        }
        // Application Insights
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: appInsights.properties.InstrumentationKey
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        // Build settings
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
      ]
      
      // CORS settings (if needed for frontend)
      cors: {
        allowedOrigins: [
          'https://portal.azure.com'
        ]
        supportCredentials: false
      }
      
      // Security settings
      minTlsVersion: '1.2'
      // NOTE: ftpsState is not valid for Flex Consumption
    }
  }
  dependsOn: [
    // Ensure network is ready before creating Function App
    virtualNetwork
    blobPrivateEndpoint
    filePrivateEndpoint
    queuePrivateEndpoint
    tablePrivateEndpoint
  ]
}

// ============================================================================
// RBAC ROLE ASSIGNMENTS - MANAGED IDENTITY ACCESS
// ============================================================================

// Storage Blob Data Owner - Read/write blobs (deployment packages)
resource blobDataOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionsApp.id, 'StorageBlobDataOwner')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: functionsApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Queue Data Contributor - Access to queues
resource queueDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionsApp.id, 'StorageQueueDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalId: functionsApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Table Data Contributor - Access to tables
resource tableDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionsApp.id, 'StorageTableDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
    principalId: functionsApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage File Data Privileged Contributor - Access to file shares (if needed)
resource fileDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionsApp.id, 'StorageFileDataPrivilegedContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69566ab7-960f-475b-8e7c-b3118f30c6bd')
    principalId: functionsApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// NOTE: Reader role at subscription level cannot be assigned from resource group scope
// This must be assigned separately after deployment via Azure CLI or Portal
// Role needed: Reader (acdd72a7-3385-48ef-bd42-f606fba81ae7)
// Purpose: Allow Function App to read VM SKU information from Azure Compute API

// ============================================================================
// OUTPUTS
// ============================================================================

output functionsAppName string = functionsApp.name
output functionsAppHostname string = functionsApp.properties.defaultHostName
output functionsAppPrincipalId string = functionsApp.identity.principalId

output storageAccountName string = storageAccount.name
output storageAccountId string = storageAccount.id

output vnetId string = virtualNetwork.id
output functionSubnetId string = functionSubnetId
output privateEndpointSubnetId string = privateEndpointSubnetId

output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
output appInsightsConnectionString string = appInsights.properties.ConnectionString

// NOTE: Private endpoint IPs are not output as customDnsConfigs may not be populated during initial deployment
// Private endpoints are accessible via private DNS zones
