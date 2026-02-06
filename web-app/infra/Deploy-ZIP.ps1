#Requires -Version 7.0
<#
.SYNOPSIS
    Package and upload web app files to Azure Static Web Apps using ZIP deployment
.DESCRIPTION
    Creates a deployment package and uploads via Kudu/REST API
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Azure Static Web App - ZIP Deployment" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$webAppPath = Split-Path -Parent $scriptPath

# Create deployment package
$timestamp = Get-Date -Format 'yyyyMMddHHmmss'
$zipPath = Join-Path $env:TEMP "swa-deployment-$timestamp.zip"

Write-Host "Creating deployment package..." -ForegroundColor Cyan

try {
    # Create temp directory
    $tempDir = Join-Path $env:TEMP "swa-package-$timestamp"
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    
    # Copy src files
    $srcPath = Join-Path $webAppPath "src"
    $destSrc = Join-Path $tempDir "src"
    Copy-Item -Path $srcPath -Destination $destSrc -Recurse
    Write-Host "  ✓ Copied frontend files" -ForegroundColor Green
    
    # Copy api files (without local.settings.json)
    $apiPath = Join-Path $webAppPath "api"
    $destApi = Join-Path $tempDir "api"
    Copy-Item -Path $apiPath -Destination $destApi -Recurse
    
    # Remove local.settings.json
    $localSettings = Join-Path $destApi "local.settings.json"
    if (Test-Path $localSettings) {
        Remove-Item $localSettings -Force
    }
    Write-Host "  ✓ Copied API files" -ForegroundColor Green
    
    # Copy config
    $configPath = Join-Path $webAppPath "staticwebapp.config.json"
    if (Test-Path $configPath) {
        Copy-Item -Path $configPath -Destination $tempDir
        Write-Host "  ✓ Copied configuration" -ForegroundColor Green
    }
    
    # Create ZIP
    Write-Host ""
    Write-Host "Creating ZIP archive..." -ForegroundColor Cyan
    Compress-Archive -Path "$tempDir\*" -DestinationPath $zipPath -Force
    
    $zipSize = (Get-Item $zipPath).Length / 1MB
    Write-Host "  ✓ Package created: $([Math]::Round($zipSize, 2)) MB" -ForegroundColor Green
    Write-Host "  Location: $zipPath" -ForegroundColor Gray
    Write-Host ""
    
    Write-Host "================================" -ForegroundColor Cyan
    Write-Host "Deployment Options" -ForegroundColor Cyan
    Write-Host "================================" -ForegroundColor Cyan
    Write-Host ""
    
    Write-Host "Option 1: Azure Portal Upload (Easiest)" -ForegroundColor Yellow
    Write-Host "1. Open: https://portal.azure.com" -ForegroundColor White
    Write-Host "2. Search for 'vmsku-alternatives-webapp'" -ForegroundColor White
    Write-Host "3. Click 'Deployment' → 'Advanced Tools' → 'Go'" -ForegroundColor White
    Write-Host "4. In Kudu, click 'Tools' → 'Zip Push Deploy'" -ForegroundColor White
    Write-Host "5. Drag and drop: $zipPath" -ForegroundColor Gray
    Write-Host ""
    
    Write-Host "Option 2: Copy Files via Portal" -ForegroundColor Yellow
    Write-Host "1. Open: https://portal.azure.com" -ForegroundColor White
    Write-Host "2. Go to your Static Web App" -ForegroundColor White
    Write-Host "3. Click 'App Service Editor' → 'Go'" -ForegroundColor White
    Write-Host "4. Upload files from: $tempDir" -ForegroundColor Gray
    Write-Host ""
    
    Write-Host "Option 3: Manual REST API Upload" -ForegroundColor Yellow
    Write-Host "Use the deployment token to upload via REST:" -ForegroundColor White
    Write-Host ""
    
    $token = "bb18181120161d5f889a641c667d99d3d345cc83e2e55cb34885414b322ee7e001-349f170d-5f2f-4086-9abc-a622e14c1efa00f01030784c5d0f"
    $apiUrl = "https://black-sea-0784c5d0f.1.azurestaticapps.net"
    
    Write-Host "`$token = '$token'" -ForegroundColor Gray
    Write-Host "`$headers = @{ 'api-key' = `$token }" -ForegroundColor Gray
    Write-Host "Invoke-RestMethod -Uri '$apiUrl/api/deploy' -Method Post -Headers `$headers -InFile '$zipPath'" -ForegroundColor Gray
    Write-Host ""
    
    $choice = Read-Host "Would you like to open the Azure Portal now? (y/n)"
    if ($choice -eq 'y') {
        Start-Process "https://portal.azure.com/#@/resource/subscriptions/e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7/resourceGroups/rg-vmsku-alternatives/providers/Microsoft.Web/staticSites/vmsku-alternatives-webapp"
    }
    
    Write-Host ""
    Write-Host "Package will be kept at: $zipPath" -ForegroundColor Yellow
    Write-Host "Temp files at: $tempDir" -ForegroundColor Gray
    
} catch {
    Write-Error "Failed to create package: $_"
    exit 1
}

Write-Host ""
Write-Host "After deployment, configure the API:" -ForegroundColor Cyan
Write-Host "az staticwebapp appsettings set --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives --setting-names AZURE_SUBSCRIPTION_ID=`"e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7`"" -ForegroundColor Gray
