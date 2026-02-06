#Requires -Version 7.0
<#
.SYNOPSIS
    Manually deploy web app to Azure Static Web Apps without GitHub Actions
.DESCRIPTION
    Uses Azure CLI to deploy directly, bypassing GitHub Actions and secrets
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Manual Azure Static Web App Deployment" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$webAppPath = Split-Path -Parent $scriptPath
$projectRoot = Split-Path -Parent $webAppPath

Write-Host "Project root: $projectRoot" -ForegroundColor Gray
Write-Host ""

# Check Azure login
Write-Host "Checking Azure login..." -ForegroundColor Cyan
$accountInfo = az account show 2>$null | ConvertFrom-Json

if (-not $accountInfo) {
    Write-Host "Not logged in. Please login..." -ForegroundColor Yellow
    az login
    $accountInfo = az account show | ConvertFrom-Json
}

Write-Host "✓ Logged in as: $($accountInfo.user.name)" -ForegroundColor Green
Write-Host ""

# Deploy using Azure CLI
Write-Host "Deploying to Azure Static Web App..." -ForegroundColor Cyan
Write-Host "This may take a few minutes..." -ForegroundColor Yellow
Write-Host ""

try {
    # Create a temporary deployment package
    $tempDir = Join-Path $env:TEMP "swa-deploy-$(Get-Date -Format 'yyyyMMddHHmmss')"
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    
    Write-Host "Preparing deployment files..." -ForegroundColor Gray
    
    # Copy frontend files
    $srcPath = Join-Path $webAppPath "src"
    $destSrcPath = Join-Path $tempDir "src"
    Copy-Item -Path $srcPath -Destination $destSrcPath -Recurse
    
    # Copy API files
    $apiPath = Join-Path $webAppPath "api"
    $destApiPath = Join-Path $tempDir "api"
    Copy-Item -Path $apiPath -Destination $destApiPath -Recurse
    
    # Remove local.settings.json if it exists (not needed for deployment)
    $localSettings = Join-Path $destApiPath "local.settings.json"
    if (Test-Path $localSettings) {
        Remove-Item $localSettings -Force
    }
    
    Write-Host "✓ Files prepared" -ForegroundColor Green
    Write-Host ""
    
    # Deploy using oryx build
    Write-Host "Deploying to Azure..." -ForegroundColor Cyan
    
    $deployResult = az staticwebapp deploy `
        --name vmsku-alternatives-webapp `
        --resource-group rg-vmsku-alternatives `
        --source $tempDir `
        --app-location "src" `
        --api-location "api" `
        --output-location "" `
        --verbose 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "✓ Deployment completed successfully!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Your app is live at:" -ForegroundColor Cyan
        Write-Host "https://black-sea-0784c5d0f.1.azurestaticapps.net" -ForegroundColor White
        Write-Host ""
        Write-Host "Testing with:" -ForegroundColor Yellow
        Write-Host "  SKU Name: Standard_D4s_v3" -ForegroundColor Gray
        Write-Host "  Location: eastus" -ForegroundColor Gray
    } else {
        Write-Host $deployResult
        throw "Deployment failed"
    }
    
} catch {
    Write-Error "Deployment failed: $_"
    Write-Host ""
    Write-Host "Note: Azure CLI staticwebapp deploy requires the staticwebapp extension." -ForegroundColor Yellow
    Write-Host "If not installed, run: az extension add --name staticwebapp" -ForegroundColor Yellow
    exit 1
} finally {
    # Cleanup
    if (Test-Path $tempDir) {
        Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Configure API subscription ID:" -ForegroundColor White
Write-Host "   az staticwebapp appsettings set --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives --setting-names AZURE_SUBSCRIPTION_ID=`"e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7`"" -ForegroundColor Gray
Write-Host ""
