# Simple Azure CLI Deployment for Static Web Apps
# This uses the Oryx builder to properly deploy Functions

Write-Host "Deploying to Azure Static Web Apps via Azure CLI" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

$resourceGroup = "rg-vmsku-alternatives"
$staticWebAppName = "vmsku-alternatives-webapp"

# Step 1: Get deployment token
Write-Host "1. Getting deployment token..." -ForegroundColor Yellow
$token = az staticwebapp secrets list `
    --name $staticWebAppName `
    --resource-group $resourceGroup `
    --query "properties.apiKey" `
    --output tsv

if ([string]::IsNullOrEmpty($token)) {
    Write-Host "❌ Failed to get deployment token" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Token retrieved" -ForegroundColor Green
Write-Host ""

# Step 2: Install dependencies
Write-Host "2. Installing API dependencies..." -ForegroundColor Yellow
Push-Location web-app\api
try {
    if (Get-Command node -ErrorAction SilentlyContinue) {
        if (Get-Command npm -ErrorAction SilentlyContinue) {
            npm install
            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ Dependencies installed" -ForegroundColor Green
            } else {
                Write-Host "⚠️  npm install had warnings (may be ok)" -ForegroundColor Yellow
            }
        } else {
            Write-Host "⚠️  npm not found in PATH" -ForegroundColor Yellow
        }
    } else {
        Write-Host "⚠️  Node.js not found" -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}

Write-Host ""

# Step 3: Create deployment artifact
Write-Host "3. Creating deployment package..." -ForegroundColor Yellow

$tempDir = Join-Path $env:TEMP "swa-deploy-$(Get-Date -Format 'yyyyMMddHHmmss')"
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

# Copy app files
Copy-Item -Path "web-app\src\*" -Destination $tempDir -Recurse -Force

# Copy API with node_modules
$apiDest = Join-Path $tempDir "api"
New-Item -ItemType Directory -Path $apiDest -Force | Out-Null
Copy-Item -Path "web-app\api\*" -Destination $apiDest -Recurse -Force -Exclude "node_modules"

# Copy critical API files
Copy-Item -Path "web-app\api\compare-vms" -Destination $apiDest -Recurse -Force
Copy-Item -Path "web-app\api\health" -Destination $apiDest -Recurse -Force
Copy-Item -Path "web-app\api\host.json" -Destination $apiDest -Force
Copy-Item -Path "web-app\api\package.json" -Destination $apiDest -Force

Write-Host "✅ Package created at: $tempDir" -ForegroundColor Green
Write-Host ""

# Step 4: Deploy using GitHub Actions workflow trigger
Write-Host "4. The easiest way to deploy is via GitHub Actions" -ForegroundColor Yellow
Write-Host ""
Write-Host "The workflow file exists at: .github/workflows/azure-static-web-apps.yml" -ForegroundColor White
Write-Host "But it's not deploying the Functions properly." -ForegroundColor White
Write-Host ""

# Step 5: Alternative - use curl to upload directly
Write-Host "5. Attempting direct upload to deployment API..." -ForegroundColor Yellow
Write-Host ""

$appHostname = "black-sea-0784c5d0f.1.azurestaticapps.net"

Write-Host "⚠️  Direct API upload requires the SWA CLI or REST API" -ForegroundColor Yellow
Write-Host ""
Write-Host "RECOMMENDED: Install and use SWA CLI:" -ForegroundColor Cyan
Write-Host "  npm install -g @azure/static-web-apps-cli" -ForegroundColor White
Write-Host "  swa deploy ./web-app/src --api-location ./web-app/api --deployment-token `$token --env production" -ForegroundColor White
Write-Host ""

Write-Host "OR: Trigger GitHub Actions deployment by pushing a change:" -ForegroundColor Cyan
Write-Host "  git commit --allow-empty -m 'Trigger deployment'" -ForegroundColor White
Write-Host "  git push origin main" -ForegroundColor White
Write-Host ""

Write-Host "Deployment token (save this): $token" -ForegroundColor Yellow

# Cleanup
Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Install SWA CLI: npm install -g @azure/static-web-apps-cli" -ForegroundColor White
Write-Host "2. Run: swa deploy ./web-app/src --api-location ./web-app/api --deployment-token $token" -ForegroundColor White
