// Role assignment module for granting access to Azure resources
// This module is deployed at resource group scope

@description('Principal ID of the identity to grant access')
param principalId string

@description('Role Definition ID to assign')
param roleDefinitionId string

// Assign Reader role at resource group scope
resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(principalId, roleDefinitionId, resourceGroup().id)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = roleAssignment.id
