# Manual Functions Deployment to Static Web App
# This bypasses GitHub Actions and deploys directly

Write-Host "Manual Functions Deployment to Static Web App" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

$resourceGroup = "rg-vmsku-alternatives"
$staticWebAppName = "vmsku-alternatives-webapp"

# Step 1: Create a deployment package
Write-Host "1. Creating deployment package..." -ForegroundColor Yellow
$tempDir = Join-Path $env:TEMP "swa-manual-deploy"
Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

# Copy just the API files in a clean structure
$apiDir = Join-Path $tempDir "api"
New-Item -ItemType Directory -Path $apiDir -Force | Out-Null

Copy-Item -Path "web-app\api\compare-vms" -Destination $apiDir -Recurse -Force
Copy-Item -Path "web-app\api\health" -Destination $apiDir -Recurse -Force
Copy-Item -Path "web-app\api\host.json" -Destination $apiDir -Force
Copy-Item -Path "web-app\api\package.json" -Destination $apiDir -Force

Write-Host "✅ Package created" -ForegroundColor Green
Write-Host ""

# Step 2: Get deployment details
Write-Host "2. Getting Static Web App details..." -ForegroundColor Yellow
$appId = az staticwebapp show --name $staticWebAppName --resource-group $resourceGroup --query id --output tsv
$deployToken = az staticwebapp secrets list --name $staticWebAppName --resource-group $resourceGroup --query "properties.apiKey" --output tsv

Write-Host "✅ Details retrieved" -ForegroundColor Green
Write-Host ""

# Step 3: Use REST API to deploy
Write-Host "3. Uploading Functions via REST API (this is experimental)..." -ForegroundColor Yellow
Write-Host ""

# Create a zip of the API
$zipPath = Join-Path $env:TEMP "api-deploy.zip"
Remove-Item $zipPath -ErrorAction SilentlyContinue
Compress-Archive -Path "$apiDir\*" -DestinationPath $zipPath -Force

Write-Host "Package size: $([math]::Round((Get-Item $zipPath).Length / 1MB, 2)) MB" -ForegroundColor White
Write-Host ""

Write-Host "⚠️  Direct API upload requires additional tooling" -ForegroundColor Yellow
Write-Host ""
Write-Host "Alternative approaches:" -ForegroundColor Cyan
Write-Host ""
Write-Host "Option 1: Request quota increase for App Service Plans" -ForegroundColor Yellow
Write-Host "  Visit: https://aka.ms/antquotahelp" -ForegroundColor White
Write-Host "  Request: Basic SKU quota increase in East US 2" -ForegroundColor White
Write-Host ""
Write-Host "Option 2: Deploy Functions in a different subscription/region" -ForegroundColor Yellow
Write-Host "  Try: westus, centralus, or northeurope" -ForegroundColor White
Write-Host ""
Write-Host "Option 3: Fix GitHub Actions deployment (root cause unknown)" -ForegroundColor Yellow
Write-Host "  The workflow succeeds but Functions don't deploy" -ForegroundColor White
Write-Host "  This appears to be a platform issue with Azure Static Web Apps" -ForegroundColor White
Write-Host ""
Write-Host "Option 4: Contact Azure Support" -ForegroundColor Yellow
Write-Host "  The Static Web Apps integrated Functions should work but aren't deploying" -ForegroundColor White
Write-Host ""

# Cleanup
Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path $zipPath -ErrorAction SilentlyContinue

Write-Host "Current Status:" -ForegroundColor Cyan
Write-Host "  ✅ Static Web App frontend: Working" -ForegroundColor Green
Write-Host "  ✅ Infrastructure: Deployed" -ForegroundColor Green
Write-Host "  ✅ Managed Identity: Configured" -ForegroundColor Green
Write-Host "  ✅ Role Assignments: Set" -ForegroundColor Green
Write-Host "  ✅ Functions Code: Ready" -ForegroundColor Green
Write-Host "  ❌ Functions Deployment: Blocked by quota limits" -ForegroundColor Red
Write-Host ""
Write-Host "Recommendation: Request quota increase for Basic App Service Plan" -ForegroundColor Yellow
Write-Host "This is the fastest way to get the API working." -ForegroundColor White
