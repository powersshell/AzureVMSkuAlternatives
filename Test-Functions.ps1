# Test Azure Functions after deployment
param(
    [Parameter(Mandatory=$false)]
    [string]$BaseUrl = "https://vmsku-api-functions.azurewebsites.net"
)

Write-Host "Testing Azure Functions" -ForegroundColor Cyan
Write-Host "======================" -ForegroundColor Cyan
Write-Host ""

# Test 1: Health endpoint
Write-Host "1. Testing /api/health endpoint..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$BaseUrl/api/health" -UseBasicParsing -ErrorAction Stop
    Write-Host "   ✅ Status: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "   Response:" -ForegroundColor Gray
    $response.Content | ConvertFrom-Json | ConvertTo-Json | Write-Host -ForegroundColor Gray
} catch {
    Write-Host "   ❌ Failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "   Status Code: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Red
}

Write-Host ""

# Test 2: Compare-VMs endpoint (GET)
Write-Host "2. Testing /api/compare-vms endpoint (GET)..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$BaseUrl/api/compare-vms" -UseBasicParsing -ErrorAction Stop
    Write-Host "   ✅ Status: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "   Response:" -ForegroundColor Gray
    $response.Content | ConvertFrom-Json | ConvertTo-Json | Write-Host -ForegroundColor Gray
} catch {
    Write-Host "   ❌ Failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "   Status Code: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Red
}

Write-Host ""

# Test 3: Compare-VMs endpoint (POST with data)
Write-Host "3. Testing /api/compare-vms endpoint (POST with data)..." -ForegroundColor Yellow
$body = @{
    skuName = "Standard_D4s_v3"
    location = "eastus"
} | ConvertTo-Json

try {
    $response = Invoke-WebRequest `
        -Uri "$BaseUrl/api/compare-vms" `
        -Method POST `
        -Body $body `
        -ContentType 'application/json' `
        -UseBasicParsing `
        -ErrorAction Stop

    Write-Host "   ✅ Status: $($response.StatusCode)" -ForegroundColor Green
    $data = $response.Content | ConvertFrom-Json
    Write-Host "   Target SKU: $($data.targetSku.name)" -ForegroundColor Gray
    Write-Host "   Alternatives found: $($data.alternatives.Count)" -ForegroundColor Gray
    if ($data.alternatives.Count -gt 0) {
        Write-Host "   Top alternative: $($data.alternatives[0].name) (Score: $($data.alternatives[0].similarityScore))" -ForegroundColor Gray
    }
} catch {
    Write-Host "   ❌ Failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "   Status Code: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Red
    if ($_.ErrorDetails.Message) {
        Write-Host "   Error Details:" -ForegroundColor Red
        $_.ErrorDetails.Message | Write-Host -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Testing complete!" -ForegroundColor Cyan
