#Requires -Version 7.0
<#
.SYNOPSIS
    Deploy the Azure VM SKU Alternatives web application infrastructure
.DESCRIPTION
    This script deploys the complete infrastructure for the Azure VM SKU Alternatives
    web application using Bicep templates. It creates an Azure Static Web App with
    integrated Azure Functions API.
.PARAMETER Location
    Azure region for deployment (default: eastus2)
.PARAMETER ResourceGroupName
    Name of the resource group (default: rg-vmsku-alternatives)
.PARAMETER StaticWebAppName
    Name of the Static Web App (default: vmsku-alternatives-webapp)
.PARAMETER Sku
    SKU for the Static Web App (Free or Standard, default: Free)
.PARAMETER RepositoryUrl
    GitHub repository URL for CI/CD integration
.PARAMETER Branch
    Branch name for deployment (default: main)
.PARAMETER SubscriptionId
    Azure subscription ID (uses current subscription if not specified)
.EXAMPLE
    .\Deploy-Infrastructure.ps1
.EXAMPLE
    .\Deploy-Infrastructure.ps1 -Location "westus2" -Sku "Standard"
.EXAMPLE
    .\Deploy-Infrastructure.ps1 -ResourceGroupName "my-rg" -StaticWebAppName "my-webapp"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Location = 'eastus2',

    [Parameter(Mandatory = $false)]
    [string]$ResourceGroupName = 'rg-vmsku-alternatives',

    [Parameter(Mandatory = $false)]
    [string]$StaticWebAppName = 'vmsku-alternatives-webapp',

    [Parameter(Mandatory = $false)]
    [ValidateSet('Free', 'Standard')]
    [string]$Sku = 'Standard',

    [Parameter(Mandatory = $false)]
    [string]$RepositoryUrl = '',

    [Parameter(Mandatory = $false)]
    [string]$Branch = 'main',

    [Parameter(Mandatory = $false)]
    [string]$SubscriptionId = ''
)

$ErrorActionPreference = 'Stop'

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Azure VM SKU Alternatives Deployment" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check if Azure CLI is installed
try {
    $azVersion = az version --output json 2>$null | ConvertFrom-Json
    Write-Host "✓ Azure CLI version: $($azVersion.'azure-cli')" -ForegroundColor Green
} catch {
    Write-Error "Azure CLI is not installed. Please install it from https://aka.ms/installazurecli"
    exit 1
}

# Check if logged in to Azure
Write-Host "`nChecking Azure login status..." -ForegroundColor Cyan
$accountInfo = az account show 2>$null | ConvertFrom-Json

if (-not $accountInfo) {
    Write-Host "Not logged in to Azure. Initiating login..." -ForegroundColor Yellow
    az login
    $accountInfo = az account show | ConvertFrom-Json
}

Write-Host "✓ Logged in as: $($accountInfo.user.name)" -ForegroundColor Green
Write-Host "✓ Current subscription: $($accountInfo.name) ($($accountInfo.id))" -ForegroundColor Green

# Set subscription if specified
if ($SubscriptionId) {
    Write-Host "`nSetting subscription to: $SubscriptionId" -ForegroundColor Cyan
    az account set --subscription $SubscriptionId
    $accountInfo = az account show | ConvertFrom-Json
    Write-Host "✓ Subscription set" -ForegroundColor Green
}

$currentSubscriptionId = $accountInfo.id

# Prepare deployment parameters
$deploymentName = "vmsku-alternatives-$(Get-Date -Format 'yyyyMMddHHmmss')"
$parametersFile = Join-Path $PSScriptRoot 'deploy.parameters.json'
$bicepFile = Join-Path $PSScriptRoot 'deploy.bicep'

# Update parameters file if needed
if (Test-Path $parametersFile) {
    $parameters = Get-Content $parametersFile -Raw | ConvertFrom-Json
    $parameters.parameters.resourceGroupName.value = $ResourceGroupName
    $parameters.parameters.location.value = $Location
    $parameters.parameters.staticWebAppName.value = $StaticWebAppName
    $parameters.parameters.sku.value = $Sku
    $parameters.parameters.azureSubscriptionId.value = $currentSubscriptionId

    if ($RepositoryUrl) {
        $parameters.parameters.repositoryUrl.value = $RepositoryUrl
    }

    if ($Branch) {
        $parameters.parameters.branch.value = $Branch
    }

    $parametersFile = Join-Path $PSScriptRoot 'deploy.parameters.runtime.json'
    $parameters | ConvertTo-Json -Depth 10 | Set-Content $parametersFile
}

Write-Host "`n================================" -ForegroundColor Cyan
Write-Host "Deployment Configuration" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host "Subscription:        $($accountInfo.name)" -ForegroundColor White
Write-Host "Subscription ID:     $currentSubscriptionId" -ForegroundColor White
Write-Host "Resource Group:      $ResourceGroupName" -ForegroundColor White
Write-Host "Location:            $Location" -ForegroundColor White
Write-Host "Static Web App:      $StaticWebAppName" -ForegroundColor White
Write-Host "SKU:                 $Sku" -ForegroundColor White
Write-Host "Deployment Name:     $deploymentName" -ForegroundColor White
Write-Host ""

$confirmation = Read-Host "Proceed with deployment? (y/n)"
if ($confirmation -ne 'y') {
    Write-Host "Deployment cancelled" -ForegroundColor Yellow
    exit 0
}

# Deploy infrastructure
Write-Host "`nDeploying infrastructure..." -ForegroundColor Cyan
Write-Host "This may take several minutes..." -ForegroundColor Yellow

try {
    $deployment = az deployment sub create `
        --name $deploymentName `
        --location $Location `
        --template-file $bicepFile `
        --parameters $parametersFile `
        --output json | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) {
        throw "Deployment failed"
    }

    Write-Host "`n✓ Deployment completed successfully!" -ForegroundColor Green

    # Display outputs
    Write-Host "`n================================" -ForegroundColor Cyan
    Write-Host "Deployment Outputs" -ForegroundColor Cyan
    Write-Host "================================" -ForegroundColor Cyan

    if ($deployment.properties.outputs) {
        foreach ($output in $deployment.properties.outputs.PSObject.Properties) {
            Write-Host "$($output.Name): $($output.Value.value)" -ForegroundColor White
        }
    }

    # Get deployment token for Static Web App
    Write-Host "`n================================" -ForegroundColor Cyan
    Write-Host "Static Web App Deployment Token" -ForegroundColor Cyan
    Write-Host "================================" -ForegroundColor Cyan

    $token = az staticwebapp secrets list `
        --name $StaticWebAppName `
        --resource-group $ResourceGroupName `
        --query "properties.apiKey" `
        --output tsv

    Write-Host "Save this deployment token for GitHub Actions:" -ForegroundColor Yellow
    Write-Host $token -ForegroundColor White
    Write-Host ""
    Write-Host "Add this as a secret named 'AZURE_STATIC_WEB_APPS_API_TOKEN' in your GitHub repository" -ForegroundColor Yellow

    Write-Host "`n✓ Infrastructure deployment complete!" -ForegroundColor Green
    Write-Host "`nNext steps:" -ForegroundColor Cyan
    Write-Host "1. Set up GitHub Actions with the deployment token above" -ForegroundColor White
    Write-Host "2. Configure AZURE_SUBSCRIPTION_ID in your function app settings" -ForegroundColor White
    Write-Host "3. Push your code to trigger automatic deployment" -ForegroundColor White

} catch {
    Write-Error "Deployment failed: $_"
    exit 1
} finally {
    # Clean up temporary parameters file
    if (Test-Path (Join-Path $PSScriptRoot 'deploy.parameters.runtime.json')) {
        Remove-Item (Join-Path $PSScriptRoot 'deploy.parameters.runtime.json') -Force
    }
}
