# Manual Deployment Script for Azure Static Web Apps
# This script manually deploys the Static Web App content and Functions

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroup = "rg-vmsku-alternatives",

    [Parameter(Mandatory=$false)]
    [string]$StaticWebAppName = "vmsku-alternatives-webapp"
)

Write-Host "Manual Deployment to Azure Static Web Apps" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Get the deployment token
Write-Host "1. Getting deployment token..." -ForegroundColor Yellow
$deploymentToken = az staticwebapp secrets list `
    --name $StaticWebAppName `
    --resource-group $ResourceGroup `
    --query "properties.apiKey" `
    --output tsv

if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrEmpty($deploymentToken)) {
    Write-Host "❌ Failed to get deployment token" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Deployment token retrieved" -ForegroundColor Green
Write-Host ""

# Check if SWA CLI is installed
Write-Host "2. Checking for Azure Static Web Apps CLI..." -ForegroundColor Yellow
$swaInstalled = Get-Command swa -ErrorAction SilentlyContinue

if (-not $swaInstalled) {
    Write-Host "⚠️  SWA CLI not found. Installing..." -ForegroundColor Yellow
    npm install -g @azure/static-web-apps-cli

    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Failed to install SWA CLI" -ForegroundColor Red
        Write-Host "Install manually: npm install -g @azure/static-web-apps-cli" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "✅ SWA CLI available" -ForegroundColor Green
Write-Host ""

# Deploy using SWA CLI
Write-Host "3. Deploying application and Functions..." -ForegroundColor Yellow
Write-Host ""

$env:SWA_CLI_DEPLOYMENT_TOKEN = $deploymentToken

# Deploy with explicit paths and Node.js 18
swa deploy `
    --app-location "web-app/src" `
    --api-location "web-app/api" `
    --api-language "node" `
    --api-version "18" `
    --deployment-token $deploymentToken `
    --env "production"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ Deployment successful!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Waiting 30 seconds for deployment to propagate..." -ForegroundColor Yellow
    Start-Sleep -Seconds 30

    # Verify Functions deployed
    Write-Host ""
    Write-Host "4. Verifying Functions deployment..." -ForegroundColor Yellow
    $functions = az staticwebapp functions show `
        --name $StaticWebAppName `
        --resource-group $ResourceGroup `
        --output json | ConvertFrom-Json

    if ($functions -and $functions.Count -gt 0) {
        Write-Host "✅ Functions deployed successfully!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Deployed Functions:" -ForegroundColor Cyan
        $functions | ForEach-Object { Write-Host "  - $($_.functionName)" -ForegroundColor White }
    } else {
        Write-Host "⚠️  Functions not showing yet (may need more time)" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "5. Testing health endpoint..." -ForegroundColor Yellow
    try {
        $response = Invoke-WebRequest -Uri "https://black-sea-0784c5d0f.1.azurestaticapps.net/api/health" -UseBasicParsing
        Write-Host "✅ Health check passed! Status: $($response.StatusCode)" -ForegroundColor Green
        Write-Host "Response: $($response.Content.Substring(0, [Math]::Min(200, $response.Content.Length)))" -ForegroundColor White
    } catch {
        Write-Host "⚠️  Health check failed (may need more time): $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "Website: https://black-sea-0784c5d0f.1.azurestaticapps.net" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan

} else {
    Write-Host ""
    Write-Host "❌ Deployment failed" -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "1. Ensure you're in the repository root directory" -ForegroundColor White
    Write-Host "2. Check that web-app/src and web-app/api folders exist" -ForegroundColor White
    Write-Host "3. Verify the deployment token is valid" -ForegroundColor White
    Write-Host ""
    Write-Host "Try using alternative deployment:" -ForegroundColor Yellow
    Write-Host "  .\web-app\infra\Deploy-ZIP.ps1" -ForegroundColor White

    exit 1
}
