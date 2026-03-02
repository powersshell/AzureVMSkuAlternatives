// MCP Server — Azure Container Apps module
// Deploys a Container Apps Environment and Container App for the MCP server
// Authentication: Entra ID Easy Auth (configured here, no auth code in the application)

@description('Base name for MCP container app resources')
param mcpAppName string = 'vmsku-mcp-server'

@description('Azure region')
param location string = resourceGroup().location

@description('Container image to deploy (from GHCR)')
param containerImage string

@description('Entra ID Application (client) ID for Easy Auth')
param entraClientId string

@description('Entra ID Tenant ID for Easy Auth')
param entraTenantId string

@description('Tags to apply to all resources')
param tags object = {
  Environment: 'Production'
  Application: 'Azure VM SKU Alternatives'
  ManagedBy: 'Bicep'
}

// Container Apps Environment (serverless consumption plan)
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: '${mcpAppName}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'none'
    }
  }
}

// MCP Server Container App
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: mcpAppName
  location: location
  tags: tags
  properties: {
    environmentId: containerAppsEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'mcp-server'
          image: containerImage
          env: [
            {
              name: 'MCP_TRANSPORT'
              value: 'http'
            }
            {
              name: 'PORT'
              value: '8000'
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 10
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

// Easy Auth — Entra ID authentication enforced at infrastructure level
// No auth code needed in the application
resource containerAppAuth 'Microsoft.App/containerApps/authConfigs@2023-05-01' = {
  name: 'current'
  parent: containerApp
  properties: {
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          openIdIssuer: 'https://login.microsoftonline.com/${entraTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${entraClientId}'
          ]
        }
      }
    }
    platform: {
      enabled: true
    }
  }
}

@description('FQDN of the deployed MCP server')
output mcpServerFqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Full MCP endpoint URL for Copilot Studio registration')
output mcpEndpointUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}/mcp'
