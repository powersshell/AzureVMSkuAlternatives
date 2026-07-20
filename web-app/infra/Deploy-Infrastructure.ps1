#Requires -Version 7.0
<#
.SYNOPSIS
    Deploy the Azure VM SKU Alternatives web application infrastructure.
.DESCRIPTION
    Runs the subscription-scoped Bicep deployment (deploy.bicep) which creates the
    resource group, Static Web App, Log Analytics workspace, Application Insights,
    and the subscription Reader role assignment for the Function App managed identity.

    This is the FIRST infrastructure step for a new tenant/subscription. The Functions
    Flex Consumption app (functions-app-flex.bicep) is deployed separately and expects
    the Log Analytics workspace created here to already exist.
.PARAMETER Location
    Azure region for the deployment. Defaults to centralus.
.PARAMETER DeploymentName
    Name of the subscription deployment. Defaults to vmsku-deployment.
.PARAMETER TemplateFile
    Path to the Bicep template. Defaults to deploy.bicep in this folder.
.PARAMETER ParametersFile
    Path to the parameters file. Defaults to deploy.parameters.json in this folder.
.PARAMETER WhatIf
    Run a what-if preview instead of an actual deployment.
.EXAMPLE
    .\Deploy-Infrastructure.ps1
.EXAMPLE
    .\Deploy-Infrastructure.ps1 -Location centralus -WhatIf
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Location = 'centralus',

    [Parameter(Mandatory = $false)]
    [string]$DeploymentName = 'vmsku-deployment',

    [Parameter(Mandatory = $false)]
    [string]$TemplateFile = (Join-Path $PSScriptRoot 'deploy.bicep'),

    [Parameter(Mandatory = $false)]
    [string]$ParametersFile = (Join-Path $PSScriptRoot 'deploy.parameters.json'),

    [Parameter(Mandatory = $false)]
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Deploy Azure VM SKU Alternatives Infrastructure" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check Azure login
$accountInfo = az account show 2>$null | ConvertFrom-Json

if (-not $accountInfo) {
    Write-Host "Not logged in to Azure. Please login first." -ForegroundColor Yellow
    az login
    $accountInfo = az account show | ConvertFrom-Json
}

Write-Host "Subscription: $($accountInfo.name) ($($accountInfo.id))" -ForegroundColor White
Write-Host "Location:     $Location" -ForegroundColor White
Write-Host "Template:     $TemplateFile" -ForegroundColor White
Write-Host "Parameters:   $ParametersFile" -ForegroundColor White
Write-Host ""

if (-not (Test-Path $TemplateFile)) {
    Write-Host "Template file not found: $TemplateFile" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $ParametersFile)) {
    Write-Host "Parameters file not found: $ParametersFile" -ForegroundColor Red
    exit 1
}

if ($WhatIf) {
    Write-Host "Running what-if preview..." -ForegroundColor Cyan
    az deployment sub what-if `
        --name $DeploymentName `
        --location $Location `
        --template-file $TemplateFile `
        --parameters $ParametersFile
    exit $LASTEXITCODE
}

Write-Host "Starting deployment..." -ForegroundColor Cyan
az deployment sub create `
    --name $DeploymentName `
    --location $Location `
    --template-file $TemplateFile `
    --parameters $ParametersFile

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n✗ Deployment failed (exit code $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`n✓ Deployment complete" -ForegroundColor Green
Write-Host "`nOutputs:" -ForegroundColor Cyan
az deployment sub show `
    --name $DeploymentName `
    --query properties.outputs `
    --output json
