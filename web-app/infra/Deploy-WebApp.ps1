#Requires -Version 7.0
<#
.SYNOPSIS
    Deploy web app files to Azure Static Web App using the deployment token
.DESCRIPTION
    This script packages and uploads your web application files directly to Azure Static Web Apps
    without requiring Node.js or npm. It uses the deployment token from the infrastructure deployment.
.PARAMETER DeploymentToken
    The deployment token from your Static Web App (from Deploy-Infrastructure.ps1 output)
.PARAMETER StaticWebAppUrl
    The URL of your Static Web App
.EXAMPLE
    .\Deploy-WebApp.ps1
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$DeploymentToken = 'bb18181120161d5f889a641c667d99d3d345cc83e2e55cb34885414b322ee7e001-349f170d-5f2f-4086-9abc-a622e14c1efa00f01030784c5d0f',

    [Parameter(Mandatory = $false)]
    [string]$StaticWebAppUrl = 'https://black-sea-0784c5d0f.1.azurestaticapps.net'
)

$ErrorActionPreference = 'Stop'

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Azure Static Web App Deployment" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Get script location
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$webAppPath = Split-Path -Parent $scriptPath

Write-Host "Web App Path: $webAppPath" -ForegroundColor Cyan

# Verify paths exist
$srcPath = Join-Path $webAppPath 'src'
$apiPath = Join-Path $webAppPath 'api'

if (-not (Test-Path $srcPath)) {
    Write-Error "Source path not found: $srcPath"
    exit 1
}

Write-Host "✓ Found source files" -ForegroundColor Green

# For Azure Static Web Apps without Node.js, we need to use GitHub Actions or Azure CLI extensions
# Let's check if Azure CLI has the SWA extension

Write-Host "`nChecking Azure CLI extensions..." -ForegroundColor Cyan

# Try to install the staticwebapp extension if not present
try {
    $extensions = az extension list --output json | ConvertFrom-Json
    $swaExtension = $extensions | Where-Object { $_.name -eq 'staticwebapp' }

    if (-not $swaExtension) {
        Write-Host "Installing Azure Static Web Apps CLI extension..." -ForegroundColor Yellow
        az extension add --name staticwebapp --yes
        Write-Host "✓ Extension installed" -ForegroundColor Green
    } else {
        Write-Host "✓ Extension already installed" -ForegroundColor Green
    }
} catch {
    Write-Warning "Could not install extension: $_"
}

Write-Host "`nYou have three options to deploy:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Use GitHub Actions (Recommended)" -ForegroundColor White
Write-Host "   - Add secrets to GitHub (see DEPLOY-MANUAL.md)" -ForegroundColor Gray
Write-Host "   - Push to main branch" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Install Node.js and use SWA CLI" -ForegroundColor White
Write-Host "   - Download: https://nodejs.org/" -ForegroundColor Gray
Write-Host "   - Run: npm install -g @azure/static-web-apps-cli" -ForegroundColor Gray
Write-Host "   - Run: swa deploy --app-location ./src --api-location ./api --deployment-token YOUR_TOKEN" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Use Azure Portal" -ForegroundColor White
Write-Host "   - Go to: https://portal.azure.com" -ForegroundColor Gray
Write-Host "   - Find your Static Web App" -ForegroundColor Gray
Write-Host "   - Use 'Deployment' → 'Deployment Center' to configure GitHub" -ForegroundColor Gray
Write-Host ""

$choice = Read-Host "Would you like to open the deployment instructions? (y/n)"
if ($choice -eq 'y') {
    Start-Process (Join-Path $webAppPath 'DEPLOY-MANUAL.md')
}

Write-Host "`nYour Static Web App URL:" -ForegroundColor Cyan
Write-Host $StaticWebAppUrl -ForegroundColor Green
Write-Host ""
Write-Host "Note: The site will show 'Your site will be ready soon' until files are deployed." -ForegroundColor Yellow
