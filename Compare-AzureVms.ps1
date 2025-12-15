#requires -Module Az.Compute
<#
.SYNOPSIS
    Compare Azure VM SKUs based on comprehensive hardware specifications and capabilities
.DESCRIPTION
    Compares a given VM SKU with all available SKUs in a region and finds similar alternatives based on ALL capabilities.
    Supports custom weighting for CPU, Memory, and all other capabilities to fine-tune comparison results.
.PARAMETER SkuName
    The VM SKU to compare (e.g., "Standard_D4s_v3")
.PARAMETER Location
    The Azure region to search for SKUs (e.g., "eastus")
.PARAMETER Tolerance
    Percentage tolerance for matching capabilities (default: 20%)
.PARAMETER CurrencyCode
    Currency code for pricing information (default: USD)
.PARAMETER WeightCPU
    Weight for vCPU comparison (default: 2.0). Higher values prioritize CPU matching.
.PARAMETER WeightMemory
    Weight for Memory comparison (default: 2.0). Higher values prioritize memory matching.
.PARAMETER WeightStorage
    Weight for storage-related capabilities like IOPS, throughput, and disk counts (default: 1.0)
.PARAMETER WeightNetwork
    Weight for network capabilities like NICs and bandwidth (default: 1.0)
.PARAMETER WeightFeatures
    Weight for feature flags like PremiumIO, Ephemeral OS Disk, etc. (default: 0.5)
.PARAMETER WeightGPU
    Weight for GPU comparison (default: 2.0). Higher values prioritize GPU matching.
.PARAMETER MinSimilarityScore
    Minimum similarity score (0-100) to include in results (default: 60)
.PARAMETER ShowAllCapabilities
    Display all capabilities in the output table (can be verbose)
.PARAMETER RequireNVMeMatch
    If the target SKU has NVMe support, only show alternatives that also have NVMe support
.PARAMETER RequireGPUMatch
    If the target SKU has GPU support, only show alternatives that also have GPU support
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus"
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -WeightCPU 3.0 -WeightMemory 1.5
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -WeightStorage 2.0 -MinSimilarityScore 70
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_L8s_v3" -Location "eastus" -RequireNVMeMatch
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_NC6s_v3" -Location "eastus" -RequireGPUMatch -WeightGPU 3.0
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SkuName,

    [Parameter(Mandatory = $true)]
    [string]$Location,

    [Parameter(Mandatory = $false)]
    [int]$Tolerance = 20,

    [Parameter(Mandatory = $false)]
    [string]$CurrencyCode = 'USD',

    [Parameter(Mandatory = $false)]
    [double]$WeightCPU = 2.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightMemory = 2.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightStorage = 1.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightNetwork = 1.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightFeatures = 0.5,

    [Parameter(Mandatory = $false)]
    [double]$WeightGPU = 2.0,

    [Parameter(Mandatory = $false)]
    [int]$MinSimilarityScore = 60,

    [Parameter(Mandatory = $false)]
    [switch]$ShowAllCapabilities,

    [Parameter(Mandatory = $false)]
    [switch]$RequireNVMeMatch,

    [Parameter(Mandatory = $false)]
    [switch]$RequireGPUMatch
)

# Ensure Az.Compute module is available
if (-not (Get-Module -ListAvailable -Name Az.Compute)) {
    Write-Error "Az.Compute module is not installed. Install it with: Install-Module -Name Az.Compute"
    exit 1
}

# Function to get pricing information for VM SKUs
function Get-VmPricingInfo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SkuName,

        [Parameter(Mandatory = $true)]
        [string]$Location,

        [Parameter(Mandatory = $false)]
        [string]$CurrencyCode = 'USD'
    )

    try {
        # Construct the Azure Retail Prices API URL
        $apiUrl = "https://prices.azure.com/api/retail/prices"
        $filter = "serviceName eq 'Virtual Machines' and armSkuName eq '$SkuName' and armRegionName eq '$Location' and type eq 'Consumption'"

        if ($CurrencyCode -ne 'USD') {
            $requestUrl = "$($apiUrl)?currencyCode='$CurrencyCode'&`$filter=$filter"
        } else {
            $requestUrl = "$($apiUrl)?`$filter=$filter"
        }

        Write-Verbose "Fetching pricing data from: $requestUrl"

        # Make the API call with retry logic
        $maxRetries = 2
        $retryCount = 0
        $response = $null

        do {
            try {
                $response = Invoke-RestMethod -Uri $requestUrl -Method Get -ErrorAction Stop
                break
            }
            catch {
                $retryCount++
                if ($retryCount -ge $maxRetries) {
                    Write-Warning "Failed to fetch pricing data for $SkuName after $maxRetries attempts: $($_.Exception.Message)"
                    return $null
                }
                Write-Verbose "Retry $retryCount for pricing data..."
                Start-Sleep -Seconds (2 * $retryCount)  # Exponential backoff
            }
        } while ($retryCount -lt $maxRetries)

        if ($response -and $response.Items -and $response.Items.Count -gt 0) {
            # Find the best matching price (prefer Linux if available, otherwise use Windows)
            $linuxPrice = $response.Items | Where-Object { $_.productName -like '*Linux*' -or $_.productName -notlike '*Windows*' } | Select-Object -First 1
            $windowsPrice = $response.Items | Where-Object { $_.productName -like '*Windows*' } | Select-Object -First 1

            $priceItem = if ($linuxPrice) { $linuxPrice } else { $windowsPrice }

            if ($priceItem) {
                return @{
                    HourlyPrice = [Math]::Round($priceItem.unitPrice, 4)
                    MonthlyPrice = [Math]::Round($priceItem.unitPrice * 730, 2)  # 730 hours = ~1 month
                    Currency = $priceItem.currencyCode
                    PriceType = $priceItem.type
                    ProductName = $priceItem.productName
                }
            }
        }

        Write-Verbose "No pricing data found for $SkuName in $Location"
        return $null
    }
    catch {
        Write-Warning "Error fetching pricing data for $SkuName`: $($_.Exception.Message)"
        return $null
    }
}

# Get all VM sizes for the specified location
Write-Host "Retrieving VM SKUs for location: $Location..." -ForegroundColor Cyan
$allSkus = Get-AzComputeResourceSku -Location $Location | Where-Object { $_.ResourceType -eq 'virtualMachines' }

# Find the target SKU
$targetSku = $allSkus | Where-Object { $_.Name -eq $SkuName }

if (-not $targetSku) {
    Write-Error "SKU '$SkuName' not found in location '$Location'"
    exit 1
}

# Extract ALL target SKU capabilities into a hashtable for easy lookup
$targetCapabilities = @{}
foreach ($capability in $targetSku.Capabilities) {
    $targetCapabilities[$capability.Name] = $capability.Value
}

# Get availability zones for target SKU
$targetZones = @()
if ($targetSku.LocationInfo -and $targetSku.LocationInfo.Count -gt 0) {
    foreach ($locationInfo in $targetSku.LocationInfo) {
        if ($locationInfo.Location -eq $Location -and $locationInfo.Zones) {
            $targetZones = $locationInfo.Zones | Sort-Object
            break
        }
    }
}
$targetZonesDisplay = if ($targetZones.Count -gt 0) { ($targetZones -join ', ') } else { 'None' }

Write-Host "`nTarget SKU: $SkuName" -ForegroundColor Green
Write-Host "Availability Zones: $targetZonesDisplay" -ForegroundColor Cyan
Write-Host "Capabilities:" -ForegroundColor Cyan

# Display all capabilities organized by category
$capabilityDisplay = @{
    'Compute' = @('vCPUs', 'vCPUsAvailable', 'vCPUsPerCore', 'ACUs', 'HyperVGenerations')
    'Memory' = @('MemoryGB', 'MemoryPreservingMaintenanceSupported')
    'GPU' = @('GPUs', 'vGPUsPerCore')
    'Storage' = @('MaxDataDiskCount', 'UncachedDiskIOPS', 'UncachedDiskBytesPerSecond',
                  'CachedDiskBytes', 'MaxCachedDiskIOPS', 'MaxCachedDiskBytesPerSecond',
                  'EphemeralOSDiskSupported', 'PremiumIO', 'NvmeDiskSizeInMiB',
                  'MaxWriteAcceleratorDisksAllowed')
    'Network' = @('MaxNetworkInterfaces', 'ExpectedNetworkBandwidth',
                  'MaxNetworkBandwidthInMbps', 'AcceleratedNetworkingEnabled',
                  'RdmaEnabled', 'RdmaNetworkInterfaceCount')
    'Features' = @('LowPriorityCapable', 'EncryptionAtHostSupported', 'CapacityReservationSupported',
                   'UltraSSDAvailable', 'ConfidentialComputingType', 'TrustedLaunchDisabled',
                   'vCPUsConstraintEnabled', 'NestedVirtualizationEnabled')
}

foreach ($category in $capabilityDisplay.Keys | Sort-Object) {
    $hasCapabilities = $false
    $categoryOutput = ""

    foreach ($capName in $capabilityDisplay[$category]) {
        if ($targetCapabilities.ContainsKey($capName)) {
            $value = $targetCapabilities[$capName]

            # Format specific values for readability
            if ($capName -eq 'UncachedDiskBytesPerSecond' -or $capName -eq 'MaxCachedDiskBytesPerSecond' -or $capName -eq 'CachedDiskBytes') {
                $value = "$([Math]::Round([double]$value / 1MB, 2)) MB/s"
            }
            elseif ($capName -eq 'NvmeDiskSizeInMiB' -and [double]$value -gt 0) {
                $value = "$([Math]::Round([double]$value / 1024, 2)) GB"
            }
            elseif ($capName -eq 'MaxNetworkBandwidthInMbps' -or $capName -eq 'ExpectedNetworkBandwidth') {
                $value = "$value Mbps"
            }

            $categoryOutput += "`n    $capName`: $value"
            $hasCapabilities = $true
        }
    }

    if ($hasCapabilities) {
        Write-Host "  $category`:" -ForegroundColor Yellow
        Write-Host $categoryOutput
    }
}

# Get pricing for target SKU
Write-Host "`nFetching pricing information for target SKU..." -ForegroundColor Cyan
$targetPricing = Get-VmPricingInfo -SkuName $SkuName -Location $Location -CurrencyCode $CurrencyCode
if ($targetPricing) {
    Write-Host "  Hourly Price: `$$($targetPricing.HourlyPrice) $($targetPricing.Currency)"
    Write-Host "  Monthly Price: `$$($targetPricing.MonthlyPrice) $($targetPricing.Currency)"
    Write-Host "  Product: $($targetPricing.ProductName)"
} else {
    Write-Host "  Pricing information not available" -ForegroundColor Yellow
}

Write-Host "`nSearching for similar SKUs (±$Tolerance% tolerance)..." -ForegroundColor Cyan
Write-Host "Weighting Configuration:" -ForegroundColor Cyan
Write-Host "  CPU Weight: $WeightCPU"
Write-Host "  Memory Weight: $WeightMemory"
Write-Host "  GPU Weight: $WeightGPU"
Write-Host "  Storage Weight: $WeightStorage"
Write-Host "  Network Weight: $WeightNetwork"
Write-Host "  Features Weight: $WeightFeatures"
Write-Host "  Minimum Similarity Score: $MinSimilarityScore%"

# Define capability categories for weighted scoring
$capabilityWeights = @{
    # Compute capabilities
    'vCPUs' = $WeightCPU
    'vCPUsAvailable' = $WeightCPU * 0.5
    'vCPUsPerCore' = $WeightCPU * 0.3
    'ACUs' = $WeightCPU * 0.8

    # Memory capabilities
    'MemoryGB' = $WeightMemory
    'MemoryPreservingMaintenanceSupported' = $WeightFeatures * 0.3

    # GPU capabilities
    'GPUs' = $WeightGPU  # GPU count is critical for GPU workloads
    'vGPUsPerCore' = $WeightGPU * 0.5

    # Storage capabilities
    'MaxDataDiskCount' = $WeightStorage * 0.8
    'UncachedDiskIOPS' = $WeightStorage * 1.2
    'UncachedDiskBytesPerSecond' = $WeightStorage * 1.2
    'CachedDiskBytes' = $WeightStorage * 0.7
    'MaxCachedDiskIOPS' = $WeightStorage * 0.7
    'MaxCachedDiskBytesPerSecond' = $WeightStorage * 0.7
    'EphemeralOSDiskSupported' = $WeightFeatures * 0.5
    'PremiumIO' = $WeightStorage * 0.6
    'NvmeDiskSizeInMiB' = $WeightStorage * 1.5  # Higher weight for NVMe as it's a critical differentiator
    'MaxWriteAcceleratorDisksAllowed' = $WeightStorage * 0.4

    # Network capabilities
    'MaxNetworkInterfaces' = $WeightNetwork * 0.7
    'ExpectedNetworkBandwidth' = $WeightNetwork * 1.0
    'MaxNetworkBandwidthInMbps' = $WeightNetwork * 1.0
    'AcceleratedNetworkingEnabled' = $WeightNetwork * 0.8
    'RdmaEnabled' = $WeightNetwork * 0.6
    'RdmaNetworkInterfaceCount' = $WeightNetwork * 0.5

    # Feature flags
    'LowPriorityCapable' = $WeightFeatures * 0.3
    'EncryptionAtHostSupported' = $WeightFeatures * 0.6
    'CapacityReservationSupported' = $WeightFeatures * 0.3
    'UltraSSDAvailable' = $WeightFeatures * 0.7
    'ConfidentialComputingType' = $WeightFeatures * 0.5
    'TrustedLaunchDisabled' = $WeightFeatures * 0.4
    'HyperVGenerations' = $WeightFeatures * 0.3
    'vCPUsConstraintEnabled' = $WeightFeatures * 0.2
    'NestedVirtualizationEnabled' = $WeightFeatures * 0.3
}

# Calculate total weight for normalization
$totalWeight = ($capabilityWeights.Values | Measure-Object -Sum).Sum

# Get key capabilities for basic filtering
if (-not $targetCapabilities.ContainsKey('vCPUs') -or -not $targetCapabilities.ContainsKey('MemoryGB')) {
    Write-Error "Target SKU missing required capabilities (vCPUs or MemoryGB)"
    exit 1
}

$targetCores = [double]($targetCapabilities['vCPUs'])
$targetMemoryGB = [double]($targetCapabilities['MemoryGB'])

# Calculate tolerance ranges for basic filtering
$coreMin = $targetCores - ($targetCores * $Tolerance / 100)
$coreMax = $targetCores + ($targetCores * $Tolerance / 100)
$memoryMin = $targetMemoryGB - ($targetMemoryGB * $Tolerance / 100)
$memoryMax = $targetMemoryGB + ($targetMemoryGB * $Tolerance / 100)

# Check if target has NVMe support
$targetHasNVMe = $targetCapabilities.ContainsKey('NvmeDiskSizeInMiB') -and
                 $null -ne $targetCapabilities['NvmeDiskSizeInMiB'] -and
                 $targetCapabilities['NvmeDiskSizeInMiB'] -ne '' -and
                 $targetCapabilities['NvmeDiskSizeInMiB'] -ne '0' -and
                 [double]$targetCapabilities['NvmeDiskSizeInMiB'] -gt 0

if ($targetHasNVMe) {
    $targetNVMeSize = [Math]::Round([double]$targetCapabilities['NvmeDiskSizeInMiB'] / 1024, 2)
    Write-Host "  Target has NVMe: $targetNVMeSize GB" -ForegroundColor Green
    if ($RequireNVMeMatch) {
        Write-Host "  Filtering to only NVMe-enabled SKUs" -ForegroundColor Yellow
    }
}

# Check if target has GPU support
$targetHasGPU = $targetCapabilities.ContainsKey('GPUs') -and
                $null -ne $targetCapabilities['GPUs'] -and
                $targetCapabilities['GPUs'] -ne '' -and
                $targetCapabilities['GPUs'] -ne '0' -and
                [double]$targetCapabilities['GPUs'] -gt 0

if ($targetHasGPU) {
    $targetGPUCount = [double]$targetCapabilities['GPUs']
    Write-Host "  Target has GPUs: $targetGPUCount" -ForegroundColor Green
    if ($RequireGPUMatch) {
        Write-Host "  Filtering to only GPU-enabled SKUs" -ForegroundColor Yellow
    }
}

# Function to calculate capability difference
function Get-CapabilityDifference {
    param(
        [string]$CapabilityName,
        $TargetValue,
        $CompareValue
    )

    # Handle null or missing values
    if ($null -eq $TargetValue -or $TargetValue -eq '' -or $TargetValue -eq '0' -or $null -eq $CompareValue -or $CompareValue -eq '' -or $CompareValue -eq '0') {
        # Special handling for NVMe - if target has NVMe and compare doesn't, it's a major difference
        if ($CapabilityName -eq 'NvmeDiskSizeInMiB') {
            $targetHasNVMe = ($null -ne $TargetValue -and $TargetValue -ne '' -and $TargetValue -ne '0' -and [double]$TargetValue -gt 0)
            $compareHasNVMe = ($null -ne $CompareValue -and $CompareValue -ne '' -and $CompareValue -ne '0' -and [double]$CompareValue -gt 0)

            if ($targetHasNVMe -and -not $compareHasNVMe) {
                return 1.0  # Target has NVMe, compare doesn't - major difference
            }
            elseif (-not $targetHasNVMe -and $compareHasNVMe) {
                return 0.3  # Target doesn't have NVMe but compare does - minor difference (bonus)
            }
            elseif ($targetHasNVMe -and $compareHasNVMe) {
                return 0.0  # Both have NVMe - will be compared by size below
            }
            else {
                return 0.0  # Neither has NVMe - no difference
            }
        }

        # Special handling for GPUs - if target has GPUs and compare doesn't, it's a major difference
        if ($CapabilityName -eq 'GPUs') {
            $targetHasGPU = ($null -ne $TargetValue -and $TargetValue -ne '' -and $TargetValue -ne '0' -and [double]$TargetValue -gt 0)
            $compareHasGPU = ($null -ne $CompareValue -and $CompareValue -ne '' -and $CompareValue -ne '0' -and [double]$CompareValue -gt 0)

            if ($targetHasGPU -and -not $compareHasGPU) {
                return 1.0  # Target has GPU, compare doesn't - major difference
            }
            elseif (-not $targetHasGPU -and $compareHasGPU) {
                return 0.3  # Target doesn't have GPU but compare does - minor difference (bonus)
            }
            elseif ($targetHasGPU -and $compareHasGPU) {
                return 0.0  # Both have GPUs - will be compared by count below
            }
            else {
                return 0.0  # Neither has GPUs - no difference
            }
        }

        # If one has the capability and the other doesn't, it's a significant difference
        if (($null -eq $TargetValue -or $TargetValue -eq '' -or $TargetValue -eq '0') -and ($null -ne $CompareValue -and $CompareValue -ne '' -and $CompareValue -ne '0')) {
            return 1.0
        }
        elseif (($null -ne $TargetValue -and $TargetValue -ne '' -and $TargetValue -ne '0') -and ($null -eq $CompareValue -or $CompareValue -eq '' -or $CompareValue -eq '0')) {
            return 1.0
        }
        else {
            return 0.0  # Both are null/missing/zero - no difference
        }
    }

    # Handle boolean/True-False values
    if ($TargetValue -eq 'True' -or $TargetValue -eq 'False') {
        if ($TargetValue -eq $CompareValue) {
            return 0.0
        } else {
            return 1.0
        }
    }

    # Handle string values (like HyperVGenerations)
    if ($TargetValue -is [string] -and $TargetValue -notmatch '^\d+\.?\d*$') {
        if ($TargetValue -eq $CompareValue) {
            return 0.0
        } else {
            return 0.5
        }
    }

    # Handle numeric values
    try {
        $targetNum = [double]$TargetValue
        $compareNum = [double]$CompareValue

        if ($targetNum -eq 0) {
            if ($compareNum -eq 0) {
                return 0.0
            } else {
                return 1.0
            }
        }

        # Calculate percentage difference
        return [Math]::Abs($compareNum - $targetNum) / $targetNum
    }
    catch {
        # If conversion fails, treat as string comparison
        if ($TargetValue -eq $CompareValue) {
            return 0.0
        } else {
            return 0.5
        }
    }
}

# Find similar SKUs
Write-Host "`nAnalyzing SKUs..." -ForegroundColor Cyan
$skuCount = 0
$totalSkus = ($allSkus | Where-Object { $_.Name -ne $SkuName }).Count

$similarSkus = $allSkus | Where-Object {
    $_.Name -ne $SkuName
} | ForEach-Object {
    $sku = $_
    $skuCount++

    # Show progress every 50 SKUs
    if ($skuCount % 50 -eq 0) {
        Write-Verbose "Processed $skuCount of $totalSkus SKUs..."
    }

    # Build capabilities hashtable for this SKU
    $skuCapabilities = @{}
    foreach ($capability in $sku.Capabilities) {
        $skuCapabilities[$capability.Name] = $capability.Value
    }

    # Get availability zones for this SKU
    $skuZones = @()
    if ($sku.LocationInfo -and $sku.LocationInfo.Count -gt 0) {
        foreach ($locationInfo in $sku.LocationInfo) {
            if ($locationInfo.Location -eq $Location -and $locationInfo.Zones) {
                $skuZones = $locationInfo.Zones | Sort-Object
                break
            }
        }
    }
    $skuZonesDisplay = if ($skuZones.Count -gt 0) { ($skuZones -join ', ') } else { 'None' }

    # Get basic specs for filtering
    $cores = if ($skuCapabilities.ContainsKey('vCPUs')) { [double]$skuCapabilities['vCPUs'] } else { 0 }
    $memoryGB = if ($skuCapabilities.ContainsKey('MemoryGB')) { [double]$skuCapabilities['MemoryGB'] } else { 0 }

    # Check if this SKU has NVMe
    $skuHasNVMe = $skuCapabilities.ContainsKey('NvmeDiskSizeInMiB') -and
                  $null -ne $skuCapabilities['NvmeDiskSizeInMiB'] -and
                  $skuCapabilities['NvmeDiskSizeInMiB'] -ne '' -and
                  $skuCapabilities['NvmeDiskSizeInMiB'] -ne '0' -and
                  [double]$skuCapabilities['NvmeDiskSizeInMiB'] -gt 0

    # If RequireNVMeMatch is set and target has NVMe, only consider SKUs with NVMe
    $nvmeFilterPass = $true
    if ($RequireNVMeMatch -and $targetHasNVMe -and -not $skuHasNVMe) {
        $nvmeFilterPass = $false
    }

    # Check if this SKU has GPU
    $skuHasGPU = $skuCapabilities.ContainsKey('GPUs') -and
                 $null -ne $skuCapabilities['GPUs'] -and
                 $skuCapabilities['GPUs'] -ne '' -and
                 $skuCapabilities['GPUs'] -ne '0' -and
                 [double]$skuCapabilities['GPUs'] -gt 0

    # If RequireGPUMatch is set and target has GPU, only consider SKUs with GPU
    $gpuFilterPass = $true
    if ($RequireGPUMatch -and $targetHasGPU -and -not $skuHasGPU) {
        $gpuFilterPass = $false
    }

    # Apply basic tolerance filter on CPU, Memory, NVMe, and GPU (if required)
    if ($cores -ge $coreMin -and $cores -le $coreMax -and
        $memoryGB -ge $memoryMin -and $memoryGB -le $memoryMax -and
        $nvmeFilterPass -and $gpuFilterPass) {

        # Calculate weighted similarity score across ALL capabilities
        $weightedScore = 0
        $applicableWeight = 0
        $capabilityScores = @{}

        foreach ($capName in $targetCapabilities.Keys) {
            $targetValue = $targetCapabilities[$capName]
            $skuValue = if ($skuCapabilities.ContainsKey($capName)) { $skuCapabilities[$capName] } else { $null }

            # Get the weight for this capability (default to 0.5 if not specified)
            $weight = if ($capabilityWeights.ContainsKey($capName)) { $capabilityWeights[$capName] } else { $WeightFeatures * 0.5 }

            # Calculate difference (0 = identical, 1 = completely different)
            $difference = Get-CapabilityDifference -CapabilityName $capName -TargetValue $targetValue -CompareValue $skuValue

            # Calculate similarity (1 = identical, 0 = completely different)
            $similarity = 1 - [Math]::Min($difference, 1.0)

            # Add to weighted score
            $weightedScore += ($similarity * $weight)
            $applicableWeight += $weight

            # Store for detailed output if needed
            $capabilityScores[$capName] = [Math]::Round($similarity * 100, 1)
        }

        # Normalize to 0-100 scale
        $similarityScore = if ($applicableWeight -gt 0) {
            [Math]::Round(($weightedScore / $applicableWeight) * 100, 2)
        } else {
            0
        }

        # Only include SKUs above minimum similarity threshold
        if ($similarityScore -ge $MinSimilarityScore) {
            # Get pricing information for this SKU
            Write-Verbose "Fetching pricing for $($sku.Name)..."
            $pricingInfo = Get-VmPricingInfo -SkuName $sku.Name -Location $Location -CurrencyCode $CurrencyCode

            # Build result object with key capabilities
            $resultObject = [PSCustomObject]@{
                SkuName                           = $sku.Name
                SimilarityScore                   = $similarityScore
                vCPUs                             = $cores
                MemoryGB                          = $memoryGB
                AvailabilityZones                 = $skuZonesDisplay
                "HourlyPrice($CurrencyCode)"      = if ($pricingInfo) { $pricingInfo.HourlyPrice } else { 'N/A' }
                "MonthlyPrice($CurrencyCode)"     = if ($pricingInfo) { $pricingInfo.MonthlyPrice } else { 'N/A' }
            }

            # Add all other capabilities if ShowAllCapabilities is specified
            if ($ShowAllCapabilities) {
                foreach ($capName in ($targetCapabilities.Keys | Sort-Object)) {
                    if ($capName -notin @('vCPUs', 'MemoryGB')) {
                        $value = if ($skuCapabilities.ContainsKey($capName)) { $skuCapabilities[$capName] } else { 'N/A' }

                        # Format certain values
                        if ($capName -match 'BytesPerSecond' -and $value -ne 'N/A' -and $null -ne $value -and $value -ne '') {
                            try {
                                $numValue = [double]$value
                                $value = "$([Math]::Round($numValue / 1MB, 2)) MB/s"
                            } catch {
                                # Keep original value if conversion fails
                            }
                        }
                        elseif ($capName -eq 'NvmeDiskSizeInMiB' -and $value -ne 'N/A' -and $null -ne $value -and $value -ne '') {
                            try {
                                $numValue = [double]$value
                                if ($numValue -gt 0) {
                                    $value = "$([Math]::Round($numValue / 1024, 2)) GB"
                                }
                            } catch {
                                # Keep original value if conversion fails
                            }
                        }

                        $resultObject | Add-Member -NotePropertyName $capName -NotePropertyValue $value -Force
                    }
                }
            }
            else {
                # Add selected important capabilities
                $importantCaps = @(
                    'GPUs', 'MaxDataDiskCount', 'UncachedDiskIOPS', 'UncachedDiskBytesPerSecond',
                    'MaxNetworkInterfaces', 'PremiumIO', 'AcceleratedNetworkingEnabled',
                    'EphemeralOSDiskSupported', 'NvmeDiskSizeInMiB', 'HyperVGenerations'
                )

                foreach ($capName in $importantCaps) {
                    if ($skuCapabilities.ContainsKey($capName)) {
                        $value = $skuCapabilities[$capName]
                        $propName = $capName

                        # Format certain values
                        if ($capName -eq 'UncachedDiskBytesPerSecond') {
                            if ($null -ne $value -and $value -ne '' -and $value -ne '0') {
                                try {
                                    $value = "$([Math]::Round([double]$value / 1MB, 2)) MB/s"
                                } catch {
                                    # Keep original value if conversion fails
                                }
                            }
                            $propName = 'MaxDiskThroughput'
                        }
                        elseif ($capName -eq 'NvmeDiskSizeInMiB') {
                            if ($null -ne $value -and $value -ne '') {
                                try {
                                    if ([double]$value -gt 0) {
                                        $value = "$([Math]::Round([double]$value / 1024, 2)) GB"
                                    } else {
                                        $value = '0'
                                    }
                                } catch {
                                    $value = '0'
                                }
                            } else {
                                $value = '0'
                            }
                            $propName = 'NVMeSize'
                        }

                        $resultObject | Add-Member -NotePropertyName $propName -NotePropertyValue $value -Force
                    }
                }
            }

            $resultObject
        }
    }
} | Sort-Object -Property SimilarityScore -Descending

# Remove any SKUs with N/A pricing
$similarSkus = $similarSkus | Where-Object { $_."MonthlyPrice($($CurrencyCode))" -ne 'N/A' }

# Display results
if ($similarSkus.Count -gt 0) {
    Write-Host "`nFound $($similarSkus.Count) similar SKUs (similarity >= $MinSimilarityScore%):" -ForegroundColor Green

    # Display top 20 results
    $displaySkus = $similarSkus | Select-Object -First 20

    if ($ShowAllCapabilities) {
        $displaySkus | Format-Table -AutoSize
    }
    else {
        # Show condensed view with key metrics - include GPUs if target has them
        if ($targetHasGPU) {
            $displaySkus | Format-Table -Property SkuName, SimilarityScore, vCPUs, MemoryGB, GPUs,
                AvailabilityZones, MaxDataDiskCount, PremiumIO, "MonthlyPrice($CurrencyCode)" -AutoSize
        } else {
            $displaySkus | Format-Table -Property SkuName, SimilarityScore, vCPUs, MemoryGB,
                AvailabilityZones, MaxDataDiskCount, PremiumIO, AcceleratedNetworkingEnabled,
                "MonthlyPrice($CurrencyCode)" -AutoSize
        }
    }

    if ($similarSkus.Count -gt 20) {
        Write-Host "`nShowing top 20 of $($similarSkus.Count) results. Access `$similarSkus variable for all results." -ForegroundColor Yellow
    }

    # Show summary statistics
    Write-Host "`nSummary Statistics:" -ForegroundColor Cyan
    Write-Host "  Average Similarity Score: $([Math]::Round(($similarSkus | Measure-Object -Property SimilarityScore -Average).Average, 2))%"
    Write-Host "  Highest Similarity Score: $(($similarSkus | Measure-Object -Property SimilarityScore -Maximum).Maximum)%"

    # Price comparison if available
    $priceField = "MonthlyPrice($CurrencyCode)"
    $validPrices = $similarSkus | Where-Object { $_.$priceField -ne 'N/A' } | Select-Object -ExpandProperty $priceField
    if ($validPrices.Count -gt 0) {
        $avgPrice = [Math]::Round(($validPrices | Measure-Object -Average).Average, 2)
        $minPrice = ($validPrices | Measure-Object -Minimum).Minimum
        $maxPrice = ($validPrices | Measure-Object -Maximum).Maximum

        Write-Host "  Average Monthly Price: `$$avgPrice $CurrencyCode"
        Write-Host "  Price Range: `$$minPrice - `$$maxPrice $CurrencyCode"

        if ($targetPricing) {
            $targetMonthly = $targetPricing.MonthlyPrice
            $cheaperCount = ($similarSkus | Where-Object { $_.$priceField -ne 'N/A' -and $_.$priceField -lt $targetMonthly }).Count
            Write-Host "  SKUs cheaper than target: $cheaperCount of $($similarSkus.Count)"
        }
    }
} else {
    Write-Host "`nNo similar SKUs found within the specified criteria." -ForegroundColor Yellow
    Write-Host "Try adjusting the following parameters:" -ForegroundColor Yellow
    Write-Host "  - Increase -Tolerance (current: $Tolerance%)"
    Write-Host "  - Decrease -MinSimilarityScore (current: $MinSimilarityScore)"
    Write-Host "  - Adjust weights to prioritize different capabilities"
}

# Return results for further analysis
return $similarSkus