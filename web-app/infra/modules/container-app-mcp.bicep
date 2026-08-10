// MCP Server — Azure Container Apps module
// Deploys a Container Apps Environment and Container App for the MCP server
// Authentication: Disabled — MCP server is a stateless proxy to the public API

@description('Base name for MCP container app resources')
param mcpAppName string = 'vmsku-mcp-server'

@description('Azure region')
param location string = resourceGroup().location

@description('Container image to deploy (from GHCR)')
param containerImage string

@description('Application Insights connection string for usage telemetry (optional).')
@secure()
param appInsightsConnectionString string = ''

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
  properties: {}

}

// Telemetry wiring — only present when a connection string is supplied. When set, the
// MCP server exports per-tool usage telemetry to Application Insights (the same workspace
// the usage workbook reads).
var telemetryEnabled = !empty(appInsightsConnectionString)
var baseEnv = [
  {
    name: 'MCP_TRANSPORT'
    value: 'http'
  }
  {
    name: 'PORT'
    value: '8000'
  }
]
var telemetryEnv = telemetryEnabled ? [
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    secretRef: 'appinsights-connection-string'
  }
  {
    name: 'OTEL_SERVICE_NAME'
    value: mcpAppName
  }
] : []

// MCP Server Container App
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: mcpAppName
  location: location
  tags: tags
  properties: {
    environmentId: containerAppsEnv.id
    configuration: {
      secrets: telemetryEnabled ? [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ] : []
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
          env: concat(baseEnv, telemetryEnv)
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

// Auth disabled — MCP server is a public proxy to the already-public Functions API.
// No sensitive data or operations require authentication.
resource containerAppAuth 'Microsoft.App/containerApps/authConfigs@2023-05-01' = {
  name: 'current'
  parent: containerApp
  properties: {
    platform: {
      enabled: false
    }
  }
}

@description('FQDN of the deployed MCP server')
output mcpServerFqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Full MCP endpoint URL for Copilot Studio registration')
output mcpEndpointUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}/mcp'
