# Azure VM SKU Alternatives

A comprehensive toolkit for comparing Azure VM SKUs based on hardware specifications, capabilities, and pricing. Available in two formats:

- **🌐 Web Application**: Serverless web app built with Azure Static Web Apps ([web-app/](web-app/))
- **⚡ PowerShell Script**: Command-line tool for Azure VM comparison ([Compare-AzureVms.ps1](Compare-AzureVms.ps1))

## Quick Links

- **Web App Quick Start**: [web-app/QUICKSTART.md](web-app/QUICKSTART.md) - Deploy in 10 minutes
- **Web App Full Guide**: [web-app/README.md](web-app/README.md) - Complete documentation
- **PowerShell Usage**: [Below](#powershell-script) - Command-line instructions

---

# PowerShell Script

A powerful PowerShell script for comparing Azure VM SKUs based on comprehensive hardware specifications, capabilities, and pricing. This tool helps you find similar or alternative VM SKUs in any Azure region with intelligent weighted scoring across all VM capabilities.

## Features

- **Comprehensive Capability Comparison**: Compares ALL VM capabilities including CPU, Memory, GPU, Storage, Network, and Features
- **CPU Vendor & Generation Awareness**: Detects Intel / AMD / ARM vendor and reports CPU generation and a relative performance score
- **GPU Performance Awareness**: Identifies documented N-series accelerator variants and compares VM-level allocation, memory, dense compute, and memory bandwidth
- **CPU Vendor Filtering**: Restrict alternatives to specific vendors (Intel, AMD, ARM)
- **Retirement Awareness**: Flags retiring/retired SKUs, hides them by default (matching the website), and applies a ranking penalty when shown
- **Growth Restriction Awareness**: Flags capacity-limited (growth-restricted) SKUs that new subscriptions can't deploy and that won't be granted additional quota, applies a ranking penalty, and surfaces recommended migration targets
- **Customizable Weighting System**: Adjust importance of different capabilities (CPU, Memory, GPU, Storage, Network, Features)
- **Intelligent Scoring**: Weighted similarity scores (0-100) to rank alternatives
- **Pricing Integration**: Real-time pricing data from Azure Retail Prices API, including **Reserved Instance (1-year / 3-year)** and **Windows** pricing
- **Cost-Efficiency Metrics**: Cost per vCPU and cost per GB for the selected pricing model
- **Availability Zone Information**: Shows which availability zones each SKU supports
- **Cross-Region Availability Check**: Optionally check whether each alternative is available in a second region
- **Special Hardware Support**: Enhanced handling for NVMe and GPU-enabled VMs
- **Flexible Filtering**: Filter by similarity threshold, require specific features (NVMe/GPU matching)
- **Multiple Output Formats**: Condensed or detailed capability display, plus CSV export

> The CPU, generation, and retirement reference data is ported from the web app's API
> (`web-app/api/function_app.py`), which is the source of truth. Keep them in sync when that data changes.
>
> The PowerShell GPU model is self-contained. Azure accelerator mappings come from
> Microsoft Learn, while performance values come only from official NVIDIA or AMD
> specifications.

## Requirements

- **PowerShell Module**: `Az.Compute`
- **Azure Authentication**: Must be logged in to Azure (`Connect-AzAccount`)
- **PowerShell Version**: 5.1 or higher recommended

### Installation

```powershell
# Install Az.Compute module if not already installed
Install-Module -Name Az.Compute -Scope CurrentUser

# Connect to Azure
Connect-AzAccount
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `SkuName` | String | *Required* | The VM SKU to compare (e.g., "Standard_D4s_v3") |
| `Location` | String | *Required* | The Azure region to search (e.g., "eastus", "westus2") |
| `Tolerance` | Int | 20 | Percentage tolerance for matching capabilities (±%) |
| `CurrencyCode` | String | USD | Currency code for pricing (USD, EUR, GBP, etc.) |
| `WeightCPU` | Double | 2.0 | Weight for CPU comparison |
| `WeightMemory` | Double | 2.0 | Weight for Memory comparison |
| `WeightGPU` | Double | 2.0 | Weight for GPU comparison |
| `WeightStorage` | Double | 1.0 | Weight for Storage capabilities |
| `WeightNetwork` | Double | 1.0 | Weight for Network capabilities |
| `WeightFeatures` | Double | 0.5 | Weight for feature flags |
| `MinSimilarityScore` | Int | 60 | Minimum similarity score (0-100) to include in results |
| `ShowAllCapabilities` | Switch | Off | Display all capabilities in output (verbose) |
| `RequireNVMeMatch` | Switch | Off | Only show alternatives with NVMe if target has NVMe |
| `RequireGPUMatch` | Switch | Off | Only show alternatives with GPU if target has GPU |
| `CpuVendor` | String[] | *(all)* | Filter alternatives by CPU vendor: `Intel`, `AMD`, `ARM` (one or more) |
| `HideRetiring` | Bool | `$true` | Exclude retiring/retired SKUs (matches the website). Use `-HideRetiring:$false` to include them |
| `HideGrowthRestricted` | Switch | Off | Exclude growth-restricted (capacity-limited) SKUs. Shown by default with a warning + ranking penalty, matching the website |
| `PricingModel` | String | PAYG | Pricing to display/use: `PAYG`, `RI1Year`, `RI3Year` |
| `OS` | String | Linux | Operating system for pricing: `Linux` or `Windows` |
| `CheckRegion` | String | *(none)* | Second region to check each alternative's availability in |
| `ExportCsv` | String | *(none)* | Path to export the full result set as CSV |

## Usage Examples

### Basic Comparison
Find similar VMs to Standard_D4s_v3 in East US:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus"
```

### Custom Weighting
Prioritize CPU and Memory over other factors:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -WeightCPU 3.0 -WeightMemory 2.5
```

### Storage-Intensive Workloads
Find alternatives with similar storage performance:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_E8s_v5" -Location "westus2" -WeightStorage 2.5 -MinSimilarityScore 70
```

### NVMe-Enabled VMs
Compare NVMe VMs and require NVMe in alternatives:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_L8s_v3" -Location "eastus" -RequireNVMeMatch
```

### GPU VMs
Find similar GPU-enabled VMs. NC, NCC, and ND targets use the AI score; NV targets
use the graphics score:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_NC6s_v3" -Location "eastus" -RequireGPUMatch -WeightGPU 3.0
```

### Different Currency
Get pricing in Euros:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "westeurope" -CurrencyCode "EUR"
```

### High Similarity Threshold
Only show very similar alternatives:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -MinSimilarityScore 80 -Tolerance 10
```

### Verbose Output
Show all capabilities for detailed analysis:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -ShowAllCapabilities
```

### Filter by CPU Vendor
Only show AMD and ARM alternatives:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4as_v5" -Location "eastus" -CpuVendor AMD,ARM
```

### Reserved Instance / Windows Pricing
Compare using 3-year Reserved Instance pricing for Windows:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v5" -Location "eastus" -PricingModel RI3Year -OS Windows
```

### Include Retiring SKUs
Show retiring/retired SKUs (a ranking penalty is applied):
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_NC6s_v3" -Location "eastus" -HideRetiring:$false
```

### Exclude Growth-Restricted SKUs
Capacity-limited sizes are shown by default with a `GrowthRestricted` column and a warning. To drop them entirely:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -HideGrowthRestricted
```

### Cross-Region Availability + CSV Export
Check availability in a second region and export results:
```powershell
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v5" -Location "eastus" -CheckRegion "westeurope" -ExportCsv ".\results.csv"
```

## Output

The script provides detailed output including:

### Target SKU Information
- SKU name and availability zones
- CPU vendor, generation, and performance score
- GPU model/variant, physical allocation, allocated memory, theoretical dense FP32 and FP16/BF16 tensor throughput, memory bandwidth, and normalized scores
- Retirement status (if announced/retired) with a migration guide link
- Capacity limitation warning (if growth-restricted) with recommended targets and a documentation link
- Organized capability listing by category (Compute, Memory, GPU, Storage, Network, Features)
- Pricing information for the selected model/OS (hourly and monthly) plus cost efficiency

### Comparison Results
**Condensed View** (default):
- SKU Name
- Similarity Score (0-100)
- CPU Vendor and Generation
- vCPUs and Memory
- GPUs (if applicable)
- GPU type, effective allocation, allocated memory, and the target-profile GPU comparison score
- Availability Zones
- Retirement status
- Growth restriction (capacity limitation) status — shown as a `Limited` column
- Monthly pricing and cost per vCPU
- Availability in the comparison region (when `-CheckRegion` is used)

**Detailed View** (`-ShowAllCapabilities`):
- All capabilities from the target SKU
- Side-by-side comparison of every capability
- GPU result properties including `GpuReferenceId`, `GpuType`, `GpuAllocation`,
  `GpuMemoryGB`, `GpuFp32Tflops`, `GpuFp16Bf16TensorTflops`,
  `GpuMemoryBandwidthGBps`, `GpuAiScore`, `GpuGraphicsScore`,
  `GpuComparisonScore`, `GpuSimilarityScore`, `GpuScoringBasis`, and source URLs

### Summary Statistics
- Average and highest similarity scores
- Price range analysis
- Count of cheaper alternatives

## How It Works

1. **Capability Extraction**: Retrieves all capabilities from the target VM SKU
2. **Weighted Scoring**: Each capability is assigned a weight based on its category
3. **Similarity Calculation**: Compares each alternative SKU across all capabilities
4. **Filtering**: Applies tolerance ranges and optional filters (NVMe, GPU)
5. **Ranking**: Sorts results by similarity score
6. **Pricing**: Fetches real-time pricing from Azure Retail Prices API

### Similarity Scoring

The similarity score (0-100) is calculated by:
- Comparing each capability between target and alternative SKUs
- Applying weighted differences based on capability importance
- Normalizing to a 0-100 scale (100 = identical)

**Special Handling**:
- **NVMe**: Major penalty if target has NVMe but alternative doesn't
- **GPU**: Uses the target family's theoretical performance profile for known GPU targets and falls back to GPU allocation/count when performance is unknown. Non-GPU targets retain the previous capability-matching behavior.
- **Numeric Values**: Percentage difference calculation
- **Boolean/String Values**: Exact match or mismatch

### GPU performance model

GPU properties are calculated for the whole VM:

```text
VM metric = official per-GPU metric × Azure-documented physical GPU allocation
```

Fractional allocations are resolved from the SKU name: NVv4 MI25 sizes use
1/8, 1/4, 1/2, or 1 GPU; NVads A10 sizes use 1/6, 1/3, 1/2, 1, or 2 GPUs;
and NVads V710 sizes use 1/6, 1/3, 1/2, or 1 GPU. Other sizes use the GPU
count reported by Azure, including multi-GPU NC/ND VMs.

Both normalized scores use one NVIDIA A100 PCIe 80 GB as 100:

```text
AI score       = 100 × (65% × dense FP16/BF16 tensor ratio
                      + 20% × memory-bandwidth ratio
                      + 15% × memory-capacity ratio)

Graphics score = 100 × (70% × FP32 ratio
                      + 20% × memory-bandwidth ratio
                      + 10% × memory-capacity ratio)
```

NC, NCC, ND, and NP targets select the AI score; NV targets select the
graphics score. A candidate that meets or exceeds the target score has no GPU
shortfall penalty. If the required first-party metrics are unavailable (for
example, V710 performance), comparison retains the existing GPU-count
fallback using the known physical allocation where available. GPU performance is
excluded entirely when the target is not a GPU VM.

| Azure mapping | Accelerator reference |
|---|---|
| NCasT4_v3 | NVIDIA T4 16 GB |
| NCv3 | NVIDIA V100 PCIe 16 GB |
| NDv2 | NVIDIA V100 SXM2 32 GB |
| ND | NVIDIA P40 24 GB |
| NVadsA10_v5 | NVIDIA A10 24 GB |
| NCadsA100_v4 | NVIDIA A100 PCIe 80 GB |
| NDasrA100_v4 | NVIDIA A100 SXM 40 GB |
| NDmA100_v4 | NVIDIA A100 SXM 80 GB |
| ND-H100-v5 | NVIDIA H100 SXM 80 GB |
| NC/NCC-H100-v5 | NVIDIA H100 NVL 94 GB |
| NVv4 | AMD Instinct MI25 16 GB |
| ND-MI300X-v5 | AMD Instinct MI300X 192 GB |
| NVadsV710_v5 | AMD Radeon PRO V710 24 GB; allocation and memory only |

Azure model/allocation sources:
[NC family](https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nc-family),
[ND family](https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nd-family), and
[NV family](https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nv-family).
Hardware sources are embedded with each script reference entry and point to
official [NVIDIA](https://www.nvidia.com/en-us/data-center/) or
[AMD Instinct](https://www.amd.com/en/products/accelerators/instinct.html)
specifications.

> **Theoretical values only:** These are dense peak hardware specifications, not
> measured application benchmarks. Sparse-mode figures are excluded. Actual
> performance depends on drivers, clocks, virtualization, framework, precision,
> topology, and workload efficiency.

## Troubleshooting

### Common Issues

**Module Not Found**
```powershell
Install-Module -Name Az.Compute -Force -Scope CurrentUser
```

**Not Logged In**
```powershell
Connect-AzAccount
```

**SKU Not Found**
- Verify the SKU name is correct (case-sensitive)
- Ensure the SKU is available in the specified location
- Use `Get-AzComputeResourceSku -Location "eastus"` to list available SKUs

**No Pricing Data**
- Some SKUs may not have public pricing available
- Try a different currency code
- Pricing data is retrieved from Azure Retail Prices API

### Performance Tips

- Use `-Verbose` flag to see progress during SKU analysis
- The script analyzes all available SKUs in the region (can be 500+)
- First run may take 30-60 seconds depending on region
- Subsequent runs are faster due to API caching

## Advanced Scenarios

### Finding Cost-Optimized Alternatives
```powershell
# Find cheaper alternatives with at least 70% similarity
$results = .\Compare-AzureVms.ps1 -SkuName "Standard_D8s_v3" -Location "eastus" -MinSimilarityScore 70
$cheaper = $results | Where-Object { $_.'MonthlyPrice(USD)' -ne 'N/A' -and $_.'MonthlyPrice(USD)' -lt 200 }
$cheaper | Format-Table
```

### Comparing Across VM Families
```powershell
# Compare D-series with E-series using custom weights
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v5" -Location "eastus" -WeightMemory 3.0 -WeightCPU 1.0
```

### Zone-Specific Selection
```powershell
# Find alternatives available in specific zones
$results = .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus"
$results | Where-Object { $_.AvailabilityZones -match "1" } | Format-Table
```

## Output Variable

The script returns the collection of similar SKUs, which can be captured for further analysis:

```powershell
$alternatives = .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus"

# Export to CSV
$alternatives | Export-Csv -Path "vm-alternatives.csv" -NoTypeInformation

# Filter and analyze
$alternatives | Where-Object { $_.SimilarityScore -gt 80 } | Sort-Object 'MonthlyPrice(USD)'
```

## Capabilities Compared

The script compares the following capability categories:

**Compute**
- vCPUs, vCPUs Available, vCPUs Per Core, ACUs, Hyper-V Generations
- CPU vendor (Intel/AMD/ARM), CPU generation, and relative performance score

**Memory**
- Memory GB, Memory Preserving Maintenance Support

**GPU**
- GPU count and physical allocation
- Accelerator model, vendor, and architecture
- Allocated accelerator memory and memory bandwidth
- Theoretical dense FP32 and supported FP16/BF16 tensor TFLOPS
- AI and graphics scores normalized to one A100 PCIe 80 GB

**Storage**
- Max Data Disks, Cached/Uncached IOPS and Throughput
- Premium IO, NVMe Disk Size, Ephemeral OS Disk Support
- Write Accelerator Support

**Network**
- Max Network Interfaces, Network Bandwidth
- Accelerated Networking, RDMA Support

**Features**
- Low Priority Capable, Encryption at Host
- Capacity Reservation, Ultra SSD Available
- Confidential Computing, Trusted Launch
- Nested Virtualization

## Contributing

Feel free to enhance this script with additional features such as:
- Support for spot pricing
- Export to additional formats (JSON, HTML)
- Interactive selection mode

## License

This script is provided as-is for use with Azure infrastructure management.

## Version History

- **v3.0** - Feature parity with the web app: CPU vendor/generation/performance, CPU vendor filtering, retirement awareness, Reserved Instance & Windows pricing, cost-efficiency metrics, cross-region availability check, and CSV export
- **v2.0** - Added GPU support, availability zones, and enhanced filtering
- **v1.5** - Added NVMe support and custom weighting
- **v1.0** - Initial release with basic capability comparison

---

**Note**: This script uses the Azure Retail Prices API for pricing information. Prices are estimates and may vary. Always verify pricing through the Azure Portal or Azure Pricing Calculator for production deployments.
