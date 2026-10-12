<#
.SYNOPSIS
    Run offline regression fixtures for the PowerShell VM comparison's savings plan pricing.
.DESCRIPTION
    Requires only PowerShell 5.1 or later. Azure module checks, SKU discovery, Retail Prices
    requests, and CSV writes are mocked; no network, authentication, or new dependencies.
.EXAMPLE
    pwsh -NoProfile -File .\scripts\Test-SavingsPlanPricing.ps1
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:AssertionCount = 0
$script:FixtureMode = 'pricing'
$script:Requests = @()
$script:Pages = @()
$script:CapturedCsv = @()
$comparisonPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'powershell-script\Compare-AzureVms.ps1'
$source = Get-Content -LiteralPath $comparisonPath -Raw
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:AssertionCount++
}

function Assert-Rate {
    param($Actual, [double]$Expected, [string]$Message)
    Assert-True ($null -ne $Actual -and [Math]::Abs([double]$Actual - $Expected) -le 1e-12) $Message
}

function New-Meter {
    param(
        [switch]$Windows,
        [string]$Sku = 'Standard_D4s_v5',
        $Rate = 0.192000987,
        [string]$Currency = 'USD'
    )
    [pscustomobject]@{
        type = 'Consumption'
        armSkuName = $Sku
        armRegionName = 'eastus'
        currencyCode = $Currency
        unitOfMeasure = '1 Hour'
        productName = 'Virtual Machines Dsv5 Series' + $(if ($Windows) { ' Windows' } else { '' })
        skuName = $Sku
        meterName = $Sku
        unitPrice = $Rate
        savingsPlan = @(
            [pscustomobject]@{ term = '1 Year'; unitPrice = 0.123456789 },
            [pscustomobject]@{ term = '3 Years'; unitPrice = 0.087654321 }
        )
    }
}

function Set-PricingFixture {
    param([object[]]$Items)
    $script:FixtureMode = 'pricing'
    $script:Requests = @()
    $script:Pages = @([pscustomobject]@{ Items = $Items; NextPageLink = $null })
}

function Invoke-RestMethod {
    param($Uri, $Method, $ErrorAction)
    $script:Requests += [string]$Uri
    if ($Method -ne 'Get') { throw "Unexpected HTTP method: $Method" }
    if ($script:FixtureMode -eq 'pricing') {
        if ($script:Pages.Count -eq 0) { throw 'Unexpected pricing request; network access is disabled.' }
        $page = $script:Pages[0]
        $script:Pages = @($script:Pages | Select-Object -Skip 1)
        return $page
    }

    $sku = [regex]::Match([string]$Uri, "armSkuName eq '([^']+)'").Groups[1].Value
    $payg = switch ($sku) {
        'Standard_D4s_v5' { 0.4 }
        'Standard_D4as_v5' { 0.2 }
        'Standard_D4ps_v5' { 0.3 }
        default { throw "Unexpected fixture SKU: $sku" }
    }
    $sp = switch ($sku) {
        'Standard_D4s_v5' { 0.123456789 }
        'Standard_D4as_v5' { 0.18 }
        'Standard_D4ps_v5' { 0.01 }
    }
    $linux = New-Meter -Sku $sku -Rate $payg
    $linux.savingsPlan = @([pscustomobject]@{ term = '1 Year'; unitPrice = $sp })
    if ($sku -ne 'Standard_D4as_v5') {
        $linux.savingsPlan += [pscustomobject]@{ term = '3 Years'; unitPrice = $sp / 2 }
    }
    $windows = New-Meter -Windows -Sku $sku -Rate ($payg + 0.184)
    $ri1 = [pscustomobject]@{
        type = 'Reservation'; productName = $linux.productName
        reservationTerm = '1 Year'; unitPrice = $payg * 730 * 12 * 0.7
    }
    $ri3 = [pscustomobject]@{
        type = 'Reservation'; productName = $linux.productName
        reservationTerm = '3 Years'; unitPrice = $payg * 730 * 36 * 0.5
    }
    [pscustomobject]@{ Items = @($windows, $linux, $ri1, $ri3); NextPageLink = $null }
}

function Start-Sleep {
    param($Seconds)
    throw 'Unexpected retry; fixtures must never sleep or fall through to real requests.'
}

function Get-Module {
    param([switch]$ListAvailable, $Name)
    if ($Name -ne 'Az.Compute') { throw "Unexpected module check: $Name" }
    [pscustomobject]@{ Name = 'Az.Compute' }
}

function Get-AzComputeResourceSku {
    param($Location)
    if ($Location -ne 'eastus') { throw "Unexpected fixture location: $Location" }
    foreach ($sku in @('Standard_D4s_v5', 'Standard_D4as_v5', 'Standard_D4ps_v5')) {
        [pscustomobject]@{
            Name = $sku
            ResourceType = 'virtualMachines'
            Restrictions = @()
            LocationInfo = @([pscustomobject]@{ Location = 'eastus'; Zones = @('1', '2', '3') })
            Capabilities = @(
                [pscustomobject]@{ Name = 'vCPUs'; Value = '4' },
                [pscustomobject]@{ Name = 'MemoryGB'; Value = '16' },
                [pscustomobject]@{ Name = 'CpuArchitectureType'; Value = 'x64' }
                if ($script:BootFixture) {
                    [pscustomobject]@{ Name = 'HyperVGenerations'; Value = $(switch ($sku) {
                        'Standard_D4as_v5' { 'V1' }; 'Standard_D4ps_v5' { 'V2' }; default { 'V1,V2' }
                    }) }
                }
            )
        }
    }
}

function Export-Csv {
    param(
        [Parameter(ValueFromPipeline = $true)]$InputObject,
        $Path,
        [switch]$NoTypeInformation,
        $Encoding
    )
    begin { $rows = @() }
    process { $rows += $InputObject }
    end { $script:CapturedCsv = @($rows | ConvertTo-Csv -NoTypeInformation) }
}

# Load the actual helper bodies without executing Azure discovery or #requires.
foreach ($name in @('Get-SelectedPrice', 'Get-ValidHourlyRate', 'Get-VmPricingInfo')) {
    $node = $ast.Find({
        param($n)
        $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
    }, $true)
    if (-not $node) { throw "Missing production function: $name" }
    . ([scriptblock]::Create($node.Extent.Text))
}

$linux = New-Meter
$windows = New-Meter -Windows -Rate 0.376000123
Set-PricingFixture @($windows, $linux)
$pricing = Get-VmPricingInfo -SkuName Standard_D4s_v5 -Location eastus
Assert-True ($script:Requests[0] -match 'api-version=2023-01-01-preview') 'Preview API version is requested.'
foreach ($term in @('1Year', '3Year')) {
    $rate = if ($term -eq '1Year') { 0.123456789 } else { 0.087654321 }
    Assert-Rate $pricing."sp${term}Hourly" $rate "$term preserves hourly precision."
    Assert-Rate $pricing."sp${term}Monthly" ($rate * 730) "$term monthly equals hourly * 730."
    $estimated = $rate + (0.376000123 - 0.192000987)
    Assert-Rate $pricing."sp${term}HourlyWindows" $estimated "$term uses unrounded PAYG license components."
    Assert-Rate $pricing."sp${term}MonthlyWindows" ($estimated * 730) "$term Windows monthly preserves precision."
    foreach ($os in @('Linux', 'Windows')) {
        $selected = Get-SelectedPrice -Pricing ([pscustomobject]$pricing) -Model "SP$term" -OS $os
        Assert-Rate $selected.Hourly $(if ($os -eq 'Windows') { $estimated } else { $rate }) "$term/$os selects the requested rate."
        $label = if ($os -eq 'Windows') { 'Windows estimated total' } else { 'compute only' }
        Assert-True ($selected.Source -match $label) "$term/$os labels compute rates and Windows estimates."
    }
}

foreach ($value in @($null, -1, [double]::NaN, [double]::PositiveInfinity, 'invalid', $true)) {
    foreach ($term in @('1Year', '3Year')) {
        $meter = New-Meter
        $meter.savingsPlan = @([pscustomobject]@{
            term = $(if ($term -eq '1Year') { '1 Year' } else { '3 Years' })
            unitPrice = $value
        })
        Set-PricingFixture @($meter)
        $pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
        Assert-True ($null -eq $pricing."sp${term}Hourly" -and $null -eq $pricing."sp${term}Monthly") "$term rejects invalid rate '$value'."
        Assert-True ($null -eq (Get-SelectedPrice $pricing "SP$term" Linux)) "$term never falls back to PAYG."
    }
}

foreach ($rate in @(0.0, 0.000000789)) {
    $meter = New-Meter
    $meter.savingsPlan = @([pscustomobject]@{ term = '1 Year'; unitPrice = $rate })
    Set-PricingFixture @($meter)
    $pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
    Assert-Rate (Get-SelectedPrice $pricing SP1Year Linux).Hourly $rate 'Zero/sub-four-decimal hourly rates remain available.'
    Assert-Rate $pricing.sp1YearMonthly ($rate * 730) 'Small monthly rates are not rounded away.'
    Assert-True ($null -eq (Get-SelectedPrice $pricing SP3Year Linux)) 'An absent term is unavailable, not the other term.'
}

$meter = New-Meter
$meter.savingsPlan = @([pscustomobject]@{ term = '1 Year'; retailPrice = 0.0123456789 })
Set-PricingFixture @($meter)
Assert-Rate (Get-VmPricingInfo Standard_D4s_v5 eastus).sp1YearHourly 0.0123456789 'Retail-only nested rates are supported.'
$meter.savingsPlan = @()
Set-PricingFixture @($meter)
$pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
Assert-True ($null -eq (Get-SelectedPrice $pricing SP1Year Linux)) 'An empty savingsPlan array has no PAYG fallback.'
Assert-True ($null -eq (Get-SelectedPrice $null SP1Year Windows)) 'Null pricing remains unavailable.'

foreach ($component in @('Linux', 'Windows')) {
    foreach ($value in @($null, -1, [double]::NaN, [double]::PositiveInfinity, 'invalid', $true)) {
        $linux = New-Meter
        $windows = New-Meter -Windows -Rate 0.376
        if ($component -eq 'Linux') { $linux.unitPrice = $value } else { $windows.unitPrice = $value }
        Set-PricingFixture @($windows, $linux)
        $pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
        Assert-Rate $pricing.sp1YearHourly 0.123456789 "Invalid $component PAYG does not suppress Linux SP."
        Assert-True ($null -eq $pricing.sp1YearHourlyWindows -and $null -eq $pricing.sp3YearHourlyWindows) "Invalid $component PAYG prevents both Windows estimates."
        Assert-True ($null -eq (Get-SelectedPrice $pricing SP1Year Windows)) 'Windows SP never falls back to Linux.'
    }
}

foreach ($windowsRate in @(0.192000987, 0.191)) {
    Set-PricingFixture @((New-Meter), (New-Meter -Windows -Rate $windowsRate))
    $pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
    if ($windowsRate -eq 0.192000987) {
        Assert-Rate $pricing.sp1YearHourlyWindows $pricing.sp1YearHourly 'A zero license surcharge is valid.'
    } else {
        Assert-True ($null -eq $pricing.sp1YearHourlyWindows) 'A negative license surcharge is unavailable.'
    }
}

$mismatches = @{
    armSkuName = 'Standard_D8s_v5'; armRegionName = 'westus'; currencyCode = 'EUR'
    unitOfMeasure = '1 Month'; skuName = 'other SKU'; meterName = 'other meter'
    productName = 'Virtual Machines Other Series Windows'
}
foreach ($field in $mismatches.Keys) {
    $windows = New-Meter -Windows -Rate 0.376
    $windows.$field = $mismatches[$field]
    Set-PricingFixture @($windows, (New-Meter))
    $pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
    Assert-True ($null -eq $pricing.sp1YearHourlyWindows) "Windows estimate requires matching $field."
}
Set-PricingFixture @((New-Meter))
Assert-True ($null -eq (Get-VmPricingInfo Standard_D4s_v5 eastus).sp1YearHourlyWindows) 'A missing Windows meter is unavailable.'

$exclusions = @(
    @{ skuName = 'D4s v5 Spot' },
    @{ skuName = 'D4s v5 Low Priority' },
    @{ meterName = 'D4s v5 Spot' },
    @{ productName = 'DedicatedHost Dsv5' },
    @{ productName = 'Dedicated Host Dsv5' },
    @{ productName = 'Virtual Machines Dsv5 Cloud' },
    @{ productName = 'Virtual Machines Dsv5 Series Windows' },
    @{ armSkuName = 'Standard_D8s_v5' },
    @{ armRegionName = 'westus' },
    @{ currencyCode = 'EUR' },
    @{ unitOfMeasure = '1 Month' },
    @{ type = 'Reservation' }
)
foreach ($excluded in $exclusions) {
    $meter = New-Meter
    foreach ($field in $excluded.Keys) { $meter.$field = $excluded[$field] }
    Set-PricingFixture @($meter)
    $pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
    Assert-True ($null -eq $pricing -or $null -eq $pricing.sp1YearHourly) 'Excluded/mismatched meters cannot supply SP, including through the legacy PAYG fallback.'
}

Set-PricingFixture @((New-Meter -Currency EUR))
$pricing = Get-VmPricingInfo Standard_D4s_v5 eastus EUR
Assert-True ($script:Requests[0] -match "currencyCode='EUR'") 'The requested currency reaches the API.'
Assert-Rate $pricing.sp1YearHourly 0.123456789 'Savings plan pricing uses the matched currency.'

$script:Requests = @()
$script:Pages = @(
    [pscustomobject]@{ Items = @(); NextPageLink = 'https://prices.azure.com/api/retail/prices?$skip=100' },
    [pscustomobject]@{ Items = @((New-Meter)); NextPageLink = $null }
)
$pricing = Get-VmPricingInfo Standard_D4s_v5 eastus
Assert-True ($script:Requests.Count -eq 2 -and $script:Requests[1] -match 'api-version=2023-01-01-preview') 'Pagination retains the preview API version.'
Assert-Rate $pricing.sp3YearHourly 0.087654321 'Nested savings plans can arrive on later pages.'

# Execute the full production script with only its Az.Compute #requires removed.
$comparison = [scriptblock]::Create(($source -replace '(?m)^#requires -Module Az\.Compute\s*$', ''))
$script:FixtureMode = 'comparison'
$balancedSignature = $null
foreach ($mode in @('balanced', 'cost')) {
    $baseline = @(& $comparison -SkuName Standard_D4s_v5 -Location eastus -PriorityMode $mode -MinSimilarityScore 0 6>$null |
        Where-Object { $_.SkuName })
    $signature = ($baseline | ForEach-Object { "$($_.SkuName):$($_.RecommendationScore)" }) -join '|'
    Assert-True ($baseline.Count -eq 2) "$mode includes both fixture candidates."
    if ($mode -eq 'balanced') { $balancedSignature = $signature } else {
        Assert-True ($signature -ne $balancedSignature) 'Cost mode actually exercises PAYG savings scoring.'
    }
    foreach ($model in @('PAYG', 'RI1Year', 'RI3Year', 'SP1Year', 'SP3Year')) {
        foreach ($os in @('Linux', 'Windows')) {
            $script:CapturedCsv = @()
            $results = @(& $comparison -SkuName Standard_D4s_v5 -Location eastus -PriorityMode $mode -MinSimilarityScore 0 `
                -PricingModel $model -OS $os -ExportCsv '.\fixture-not-written.csv' 6>$null |
                Where-Object { $_.SkuName })
            $actual = ($results | ForEach-Object { "$($_.SkuName):$($_.RecommendationScore)" }) -join '|'
            Assert-True ($actual -eq $signature) "$mode/$model/$os preserves PAYG-based scores and ordering."
            $csv = @($script:CapturedCsv | ConvertFrom-Csv)
            Assert-True ($csv.Count -eq 2 -and $csv[0].PricingModel -eq $model -and $csv[0].OS -eq $os) "$mode/$model/$os CSV preserves membership and metadata."
            Assert-True ($csv[0].SavingsPlanAssumptions -match 'full coverage' -and
                $csv[0].SavingsPlanAssumptions -match 'hourly spend commitment' -and
                $csv[0].SavingsPlanAssumptions -match 'exclude software' -and
                $csv[0].SavingsPlanAssumptions -match 'No extra discount') 'CSV labels all raw SP fields, even under PAYG/RI.'
            if ($model -like 'SP*') {
                $term = $model.Substring(2)
                $suffix = if ($os -eq 'Windows') { 'Windows' } else { '' }
                $priced = $results | Where-Object SkuName -eq Standard_D4ps_v5
                $expected = $(if ($model -eq 'SP1Year') { 0.01 } else { 0.005 }) + $(if ($os -eq 'Windows') { 0.184 } else { 0 })
                Assert-Rate $priced."sp${term}Hourly$suffix" $expected 'Raw SP rates are not discounted.'
                Assert-Rate $priced.'HourlyPrice(USD)' $expected 'Selected hourly cost uses the requested term/OS.'
                Assert-Rate $priced.'MonthlyPrice(USD)' ($expected * 730) 'Selected monthly cost uses full precision.'
                Assert-Rate $priced.'CostPerVCPU(USD)' ([Math]::Round($expected / 4, 4)) 'Cost-efficiency displays use selected pricing.'
                if ($os -eq 'Windows') {
                    Assert-True (($csv | Where-Object SkuName -eq Standard_D4ps_v5).PricingSource -match 'estimated total') 'CSV labels Windows total estimates.'
                }
            }
            if ($model -eq 'SP3Year') {
                $missing = $csv | Where-Object SkuName -eq Standard_D4as_v5
                Assert-True ($missing.'MonthlyPrice(USD)' -eq 'N/A' -and $missing.sp3YearHourly -eq '' -and
                    $missing.sp3YearHourlyWindows -eq '') 'An unavailable term remains visible with N/A selected costs and blank raw CSV fields.'
            }
        }
    }
}

$invalidRejected = $false
try {
    & $comparison -SkuName Standard_D4s_v5 -Location eastus -PricingModel Invalid 6>$null | Out-Null
} catch [System.Management.Automation.ParameterBindingException] {
    $invalidRejected = $true
}
Assert-True $invalidRejected 'PricingModel validation rejects unsupported models.'

$script:BootFixture = $true
foreach ($sourceGen in @('auto', 'gen1', 'gen2')) {
    foreach ($targetGen in @('same', 'any', 'gen1', 'gen2')) {
        $results = @(& $comparison -SkuName Standard_D4s_v5 -Location eastus -SourceGen $sourceGen -TargetGen $targetGen `
            -MinSimilarityScore 0 -ExportCsv '.\fixture-not-written.csv' 6>$null | Where-Object { $_.SkuName })
        $csv = @($script:CapturedCsv | ConvertFrom-Csv)
        Assert-True ($results.Count -eq 2 -and $csv.Count -eq 2) 'Generation preferences retain all candidates and CSV rows.'
        foreach ($candidate in $results) {
            $row = $csv | Where-Object SkuName -eq $candidate.SkuName
            Assert-True ($row.MigrationEffort -eq $candidate.MigrationEffort -and $row.MigrationPath -eq $candidate.MigrationPath) 'CSV preserves the candidate path and effort.'
            Assert-True ($row.MigrationPrerequisites -match 'Verify|Trusted Launch') 'CSV includes actionable planning prerequisites.'
            if ($sourceGen -eq 'auto') {
                Assert-True ($candidate.MigrationPathStatus -eq 'unassessed' -and $candidate.MigrationEffort -eq 'Cannot assess effort') 'Ambiguous source cannot claim readiness.'
            }
            if ($targetGen -eq 'any') {
                Assert-True ($candidate.GenerationMismatchPenalty -eq 0 -and $candidate.GenerationUnknownPenalty -eq 0) 'Any removes preference penalties.'
            }
            if ($sourceGen -eq 'gen1' -and $targetGen -eq 'gen2' -and $candidate.SkuName -eq 'Standard_D4as_v5') {
                Assert-True ($candidate.PreferredGenerationSupport -eq 'unsupported' -and $candidate.DestinationGeneration -eq 1) 'Incompatible target preference reports the alternate Gen1 path.'
            }
        }
    }
}
$script:BootFixture = $false

# Exercise the actual default sort expression with tied recommendation/CPU scores.
$sortNode = $ast.Find({
    param($n)
    $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.GetCommandName() -eq 'Sort-Object' -and $n.Extent.Text -match 'RecommendationPaygMonthly'
}, $true)
Assert-True ($null -ne $sortNode) 'The production recommendation sort exists.'
$sort = [scriptblock]::Create('param($Rows, $CurrencyCode) $Rows | ' + $sortNode.Extent.Text)
$tied = @(
    [pscustomobject]@{ SkuName = 'A'; RecommendationScore = 80; CpuPerfScore = 100; RecommendationPaygMonthly = 200; 'MonthlyPrice(USD)' = 10 },
    [pscustomobject]@{ SkuName = 'B'; RecommendationScore = 80; CpuPerfScore = 100; RecommendationPaygMonthly = 100; 'MonthlyPrice(USD)' = 90 },
    [pscustomobject]@{ SkuName = 'C'; RecommendationScore = 80; CpuPerfScore = 100; RecommendationPaygMonthly = $null; 'MonthlyPrice(USD)' = 1 }
)
$ordered = @(& $sort $tied USD)
Assert-True (($ordered.SkuName -join ',') -eq 'B,A,C') 'Default price tie-breaking uses PAYG, with unavailable PAYG last.'
$tied[1].'MonthlyPrice(USD)' = 'N/A'
$ordered = @(& $sort $tied USD)
Assert-True (($ordered.SkuName -join ',') -eq 'B,A,C') 'Missing selected SP pricing cannot change the default tie-break.'

Write-Host "PASS: $script:AssertionCount assertions; offline pricing fixtures and 20 model/OS/priority combinations."
