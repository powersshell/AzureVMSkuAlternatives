# Setup GitHub Actions OIDC Federation for Azure
# No secrets needed - uses federated credentials

param(
    [Parameter(Mandatory=$false)]
    [string]$GitHubOrg = "powersshell",
    
    [Parameter(Mandatory=$false)]
    [string]$GitHubRepo = "AzureVMSkuAlternatives",
    
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroupName = "rg-vmsku-alternatives",
    
    [Parameter(Mandatory=$false)]
    [string]$AppName = "github-actions-vmsku-functions"
)

Write-Host "`n═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "GITHUB ACTIONS OIDC SETUP FOR AZURE" -ForegroundColor Yellow
Write-Host "═══════════════════════════════════════════════════════`n" -ForegroundColor Cyan

Write-Host "This script will:" -ForegroundColor White
Write-Host "  1. Create Azure AD App Registration (Service Principal)" -ForegroundColor Gray
Write-Host "  2. Configure federated credentials for GitHub" -ForegroundColor Gray
Write-Host "  3. Assign required Azure permissions" -ForegroundColor Gray
Write-Host "  4. Output GitHub secrets to configure`n" -ForegroundColor Gray

# Get current subscription details
Write-Host "1. Getting subscription information..." -ForegroundColor Cyan
$subscriptionInfo = az account show --output json | ConvertFrom-Json
$subscriptionId = $subscriptionInfo.id
$tenantId = $subscriptionInfo.tenantId

Write-Host "   Subscription: $($subscriptionInfo.name)" -ForegroundColor Gray
Write-Host "   Subscription ID: $subscriptionId" -ForegroundColor Gray
Write-Host "   Tenant ID: $tenantId`n" -ForegroundColor Gray

# Create App Registration
Write-Host "2. Creating App Registration..." -ForegroundColor Cyan
$appExists = az ad app list --display-name $AppName --query "[0].appId" -o tsv

if ($appExists) {
    Write-Host "   App already exists, using existing: $appExists" -ForegroundColor Yellow
    $appId = $appExists
} else {
    $app = az ad app create --display-name $AppName --output json | ConvertFrom-Json
    $appId = $app.appId
    Write-Host "   ✅ Created App Registration: $appId" -ForegroundColor Green
}

# Create Service Principal
Write-Host "`n3. Creating Service Principal..." -ForegroundColor Cyan
$spExists = az ad sp list --display-name $AppName --query "[0].appId" -o tsv

if ($spExists) {
    Write-Host "   Service Principal already exists" -ForegroundColor Yellow
    $sp = az ad sp list --display-name $AppName --query "[0]" --output json | ConvertFrom-Json
} else {
    $sp = az ad sp create --id $appId --output json | ConvertFrom-Json
    Write-Host "   ✅ Created Service Principal: $($sp.id)" -ForegroundColor Green
}

$servicePrincipalId = $sp.id

# Configure Federated Credential for GitHub Actions
Write-Host "`n4. Configuring federated credentials for GitHub..." -ForegroundColor Cyan
Write-Host "   GitHub: $GitHubOrg/$GitHubRepo" -ForegroundColor Gray

# Federated credential for main branch
$federatedCredentialName = "github-actions-main"
$subject = "repo:${GitHubOrg}/${GitHubRepo}:ref:refs/heads/main"

Write-Host "   Subject: $subject" -ForegroundColor Gray

# Check if credential already exists
$existingCred = az ad app federated-credential list --id $appId --query "[?name=='$federatedCredentialName'].name" -o tsv

if ($existingCred) {
    Write-Host "   Federated credential already exists, deleting and recreating..." -ForegroundColor Yellow
    az ad app federated-credential delete --id $appId --federated-credential-id $federatedCredentialName 2>$null
}

# Create federated credential
$federatedCredential = @"
{
  "name": "$federatedCredentialName",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "description": "GitHub Actions deployment for main branch",
  "audiences": [
    "api://AzureADTokenExchange"
  ]
}
"@

$credFile = [System.IO.Path]::GetTempFileName()
$federatedCredential | Out-File -FilePath $credFile -Encoding UTF8

az ad app federated-credential create --id $appId --parameters $credFile

Remove-Item $credFile

Write-Host "   ✅ Federated credential configured" -ForegroundColor Green

# Assign Azure permissions
Write-Host "`n5. Assigning Azure permissions..." -ForegroundColor Cyan

# Contributor role at SUBSCRIPTION scope
# NOTE: deploy.bicep is a subscription-scoped template (targetScope='subscription') that
# CREATES the resource group and assigns a subscription-scoped Reader role to the Function
# App managed identity. Creating an RG, submitting a subscription-scoped deployment, and
# creating subscription-scoped role assignments all require rights at the SUBSCRIPTION
# scope — RG-scoped Contributor/UAA is not sufficient and fails with ResourceGroupNotFound.
$subScope = "/subscriptions/$subscriptionId"

Write-Host "   Assigning Contributor role at subscription scope..." -ForegroundColor White

$roleAssignment = az role assignment create `
    --role "Contributor" `
    --assignee $servicePrincipalId `
    --scope $subScope `
    --output json 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✅ Contributor role assigned" -ForegroundColor Green
} else {
    if ($roleAssignment -like "*already exists*") {
        Write-Host "   ℹ️  Contributor role already assigned" -ForegroundColor Yellow
    } else {
        Write-Host "   ❌ Failed to assign Contributor role" -ForegroundColor Red
        Write-Host "   Error: $roleAssignment" -ForegroundColor Red
    }
}

# User Access Administrator role at SUBSCRIPTION scope (assign RBAC roles, incl. the
# subscription-scoped Reader assignment for the managed identity in deploy.bicep)
Write-Host "   Assigning User Access Administrator role at subscription scope..." -ForegroundColor White

$roleAssignment = az role assignment create `
    --role "User Access Administrator" `
    --assignee $servicePrincipalId `
    --scope $subScope `
    --output json 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✅ User Access Administrator role assigned" -ForegroundColor Green
} else {
    if ($roleAssignment -like "*already exists*") {
        Write-Host "   ℹ️  User Access Administrator role already assigned" -ForegroundColor Yellow
    } else {
        Write-Host "   ❌ Failed to assign User Access Administrator role" -ForegroundColor Red
        Write-Host "   Error: $roleAssignment" -ForegroundColor Red
    }
}

# Reader role at subscription scope (for reading VM SKUs)
Write-Host "   Assigning Reader role at subscription scope..." -ForegroundColor White

$roleAssignment = az role assignment create `
    --role "Reader" `
    --assignee $servicePrincipalId `
    --scope $subScope `
    --output json 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✅ Reader role assigned" -ForegroundColor Green
} else {
    if ($roleAssignment -like "*already exists*") {
        Write-Host "   ℹ️  Reader role already assigned" -ForegroundColor Yellow
    } else {
        Write-Host "   ⚠️  Could not assign Reader role (may already exist)" -ForegroundColor Yellow
    }
}

Write-Host "`n═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "SETUP COMPLETE!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════`n" -ForegroundColor Cyan

Write-Host "📋 GitHub Secrets to Configure:" -ForegroundColor Yellow
Write-Host "`nGo to: https://github.com/$GitHubOrg/$GitHubRepo/settings/secrets/actions" -ForegroundColor White
Write-Host "`nAdd these secrets:" -ForegroundColor White
Write-Host ""
Write-Host "  Name: AZURE_CLIENT_ID" -ForegroundColor Cyan
Write-Host "  Value: $appId" -ForegroundColor White
Write-Host ""
Write-Host "  Name: AZURE_TENANT_ID" -ForegroundColor Cyan
Write-Host "  Value: $tenantId" -ForegroundColor White
Write-Host ""
Write-Host "  Name: AZURE_SUBSCRIPTION_ID" -ForegroundColor Cyan
Write-Host "  Value: $subscriptionId" -ForegroundColor White
Write-Host ""

Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "CONFIGURATION SUMMARY" -ForegroundColor Yellow
Write-Host "═══════════════════════════════════════════════════════`n" -ForegroundColor Cyan

Write-Host "App Registration:" -ForegroundColor White
Write-Host "  Name: $AppName" -ForegroundColor Gray
Write-Host "  App ID: $appId" -ForegroundColor Gray
Write-Host "  Object ID: $servicePrincipalId`n" -ForegroundColor Gray

Write-Host "Federated Credentials:" -ForegroundColor White
Write-Host "  Repository: $GitHubOrg/$GitHubRepo" -ForegroundColor Gray
Write-Host "  Branch: main" -ForegroundColor Gray
Write-Host "  Issuer: https://token.actions.githubusercontent.com`n" -ForegroundColor Gray

Write-Host "Azure Permissions:" -ForegroundColor White
Write-Host "  ✅ Contributor on subscription" -ForegroundColor Green
Write-Host "  ✅ User Access Administrator on subscription" -ForegroundColor Green
Write-Host "  ✅ Reader on subscription`n" -ForegroundColor Green

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Add the three secrets to GitHub (see above)" -ForegroundColor White
Write-Host "  2. Remove old AZURE_FUNCTIONAPP_PUBLISH_PROFILE secret (no longer needed)" -ForegroundColor White
Write-Host "  3. Commit the updated workflow file" -ForegroundColor White
Write-Host "  4. Push to main branch to trigger deployment`n" -ForegroundColor White

Write-Host "Security Benefits:" -ForegroundColor Green
Write-Host "  ✅ No secrets in GitHub (OIDC tokens are temporary)" -ForegroundColor White
Write-Host "  ✅ No storage account keys" -ForegroundColor White
Write-Host "  ✅ Scoped to specific repository and branch" -ForegroundColor White
Write-Host "  ✅ Azure AD authentication" -ForegroundColor White
Write-Host "  ✅ Auditable in Azure AD logs`n" -ForegroundColor White

# Save configuration to file for reference
$configOutput = @"
# GitHub OIDC Configuration
# Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

## Azure AD App Registration
App Name: $AppName
Application ID (Client ID): $appId
Service Principal Object ID: $servicePrincipalId

## Azure Details
Subscription ID: $subscriptionId
Tenant ID: $tenantId
Resource Group: $ResourceGroupName

## GitHub Repository
Repository: $GitHubOrg/$GitHubRepo
Branch: main

## GitHub Secrets (to add manually)
AZURE_CLIENT_ID=$appId
AZURE_TENANT_ID=$tenantId
AZURE_SUBSCRIPTION_ID=$subscriptionId

## Federated Credential Configuration
Issuer: https://token.actions.githubusercontent.com
Subject: repo:${GitHubOrg}/${GitHubRepo}:ref:refs/heads/main
Audiences: api://AzureADTokenExchange

## Azure Role Assignments
# Subscription scope is required: deploy.bicep is subscription-scoped (creates the RG and
# assigns a subscription-scoped Reader role to the Function App managed identity).
- Contributor on /subscriptions/$subscriptionId
- User Access Administrator on /subscriptions/$subscriptionId
- Reader on /subscriptions/$subscriptionId
"@

$configFile = "github-oidc-config.txt"
$configOutput | Out-File -FilePath $configFile -Encoding UTF8

Write-Host "✅ Configuration saved to: $configFile" -ForegroundColor Green
Write-Host ""
