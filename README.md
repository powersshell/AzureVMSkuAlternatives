# Compare-AzureVms.ps1

A powerful PowerShell script for comparing Azure VM SKUs based on comprehensive hardware specifications, capabilities, and pricing. This tool helps you find similar or alternative VM SKUs in any Azure region with intelligent weighted scoring across all VM capabilities.

## Features

- **Comprehensive Capability Comparison**: Compares ALL VM capabilities including CPU, Memory, GPU, Storage, Network, and Features
- **Customizable Weighting System**: Adjust importance of different capabilities (CPU, Memory, GPU, Storage, Network, Features)
- **Intelligent Scoring**: Weighted similarity scores (0-100) to rank alternatives
- **Pricing Integration**: Real-time pricing data from Azure Retail Prices API
- **Availability Zone Information**: Shows which availability zones each SKU supports
- **Special Hardware Support**: Enhanced handling for NVMe and GPU-enabled VMs
- **Flexible Filtering**: Filter by similarity threshold, require specific features (NVMe/GPU matching)
- **Multiple Output Formats**: Condensed or detailed capability display

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
Find similar GPU-enabled VMs:
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

## Output

The script provides detailed output including:

### Target SKU Information
- SKU name and availability zones
- Organized capability listing by category (Compute, Memory, GPU, Storage, Network, Features)
- Pricing information (hourly and monthly)

### Comparison Results
**Condensed View** (default):
- SKU Name
- Similarity Score (0-100)
- vCPUs and Memory
- GPUs (if applicable)
- Availability Zones
- Key storage/network capabilities
- Monthly pricing

**Detailed View** (`-ShowAllCapabilities`):
- All capabilities from the target SKU
- Side-by-side comparison of every capability

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
- **GPU**: Major penalty if target has GPU but alternative doesn't
- **Numeric Values**: Percentage difference calculation
- **Boolean/String Values**: Exact match or mismatch

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

**Memory**
- Memory GB, Memory Preserving Maintenance Support

**GPU**
- GPU Count, Virtual GPUs Per Core

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
- Reserved instance pricing
- Cross-region comparisons
- Export to different formats (JSON, HTML)
- Interactive selection mode

## License

This script is provided as-is for use with Azure infrastructure management.

## Version History

- **v2.0** - Added GPU support, availability zones, and enhanced filtering
- **v1.5** - Added NVMe support and custom weighting
- **v1.0** - Initial release with basic capability comparison

---

**Note**: This script uses the Azure Retail Prices API for pricing information. Prices are estimates and may vary. Always verify pricing through the Azure Portal or Azure Pricing Calculator for production deployments.
