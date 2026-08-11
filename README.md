# Azure VM SKU Alternatives

> Find similar and alternative Azure Virtual Machine SKUs based on comprehensive hardware specifications, capabilities, and pricing.

[![Live Demo](https://img.shields.io/badge/🌐_Live_Demo-blue?style=for-the-badge)](https://blue-grass-0e1bb5e10.7.azurestaticapps.net)
[![PowerShell](https://img.shields.io/badge/PowerShell-Script-blue?style=for-the-badge&logo=powershell)](powershell-script/)

---

## 📋 Recent Changes

### 2026-08-11
- **feature:** Added **growth-restricted (capacity-limited) VM size awareness**. Microsoft has published capacity limitations (effective July 2026) for many previous-generation series — new subscriptions can't deploy them and additional quota won't be approved, even though most aren't retired. Affected sizes now get a **🔒 Limited** badge, a warning banner with recommended migration targets, and a ranking penalty so scalable alternatives sort higher. They're still shown by default, with a new opt-in **"Hide growth-restricted"** filter.
- **feature:** The **MCP server** gained `list_growth_restricted_skus`, and every SKU-returning tool now includes `growthRestricted` / `recommendedTargets` so AI agents stop recommending sizes a customer can't scale into. Backed by a new `/api/growth-restrictions` endpoint.
- **feature:** The **PowerShell script** reached parity — a `GrowthRestricted` column, a target-size warning with recommended targets, the same ranking penalty, and a `-HideGrowthRestricted` switch.
- **data:** Added the capacity-limitation table covering 31 size patterns across compute optimized, general purpose (including Dv3/Dsv3/Dv4/Dsv4/Ddv4/Ddsv4/Dav4/Dasv4), memory optimized (including Ev3/Esv3/Ev4/Esv4/Edv4/Edsv4/Eav4/Easv4), and storage optimized series, each with recommended migration targets.

📄 [Full changelog →](CHANGELOG.md)

---

## 🎯 What is This?

Azure VM SKU Alternatives helps you discover similar or alternative VM SKUs when:
- 🚫 Your preferred SKU isn't available in a region
- 💰 You need a more cost-effective option
- ⚡ You want better performance for similar specs
- 🔄 You're migrating between VM families (e.g., Dv3 → Dv5)
- ⚠️ Your current SKU is announced for retirement
- 🔒 Your current SKU is growth-restricted (capacity limited) and can't get more quota
- 📊 You need to compare VM capabilities side-by-side

**Example:** Need an alternative to `Standard_D4s_v3`? Get instant recommendations with similarity scores, specs, and pricing!

---

## 🚀 Quick Start

### Web Application (Recommended)

**Live Demo:** https://blue-grass-0e1bb5e10.7.azurestaticapps.net

**Features:**
- ✨ **Searchable dropdown** - Type "D2s" to filter 1000+ SKUs
- ⚡ **Blazing fast** - Results in 10-50ms (cached data)
- 💰 **Live pricing** - See hourly/monthly costs
- 📱 **Mobile-friendly** - Works on all devices
- 🔍 **Smart filtering** - Shows only available SKUs per region

**Usage:**
1. Select your Azure region (e.g., East US)
2. Type to search for a VM SKU (e.g., "D2s_v3")
3. Adjust similarity threshold and weights
4. Click "Compare VM SKUs"
5. View ranked alternatives with specs and pricing!

### PowerShell Script

For automation and CI/CD pipelines, use the PowerShell script:

```powershell
cd powershell-script
.\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus"
```

📖 **Full documentation:** [powershell-script/README.md](powershell-script/README.md)

### 🤖 AI Agent Integration (MCP Server + Copilot CLI)

Use the included tools to let AI agents find and compare Azure VM SKUs in natural language.

**GitHub Copilot CLI** — zero-dependency extension, works out of the box when you clone this repo:

```
ghcs  # open Copilot CLI in this repo directory — tools load automatically
```

To use from *any* directory (global install), copy one file:

```powershell
# Windows
$dest = "$env:USERPROFILE\.copilot\extensions\azure-vm-skus"
New-Item -ItemType Directory -Force $dest | Out-Null
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/powersshell/AzureVMSkuAlternatives/main/.github/extensions/azure-vm-skus/extension.mjs" -OutFile "$dest\extension.mjs"
```

```bash
# macOS / Linux
mkdir -p ~/.copilot/extensions/azure-vm-skus
curl -sSL https://raw.githubusercontent.com/powersshell/AzureVMSkuAlternatives/main/.github/extensions/azure-vm-skus/extension.mjs \
  -o ~/.copilot/extensions/azure-vm-skus/extension.mjs
```

**VS Code / Claude Desktop** — install [`uv`](https://docs.astral.sh/uv/) (handles Python + packages automatically):

```powershell
# Windows
winget install astral-sh.uv
# macOS
brew install uv
```

Then open the repo in VS Code — the MCP server starts automatically. No `pip install` needed.

**Microsoft M365 Copilot** — deploy to Azure Container Apps and register in Copilot Studio:

📖 **Setup guide:** [mcp-server/COPILOT-STUDIO-SETUP.md](mcp-server/COPILOT-STUDIO-SETUP.md)

Then ask your AI agent:
> *"Find alternatives to Standard_D8s_v5 in eastus that are cheaper"*

📖 **Full documentation:** [mcp-server/README.md](mcp-server/README.md)

---

## 🧮 How the Comparison Works

### Comparison Algorithm

The tool calculates a **similarity score (0-100)** by comparing multiple aspects of each VM:

```
Similarity Score = Weighted Average of:
  - CPU Match Score
  - Memory Match Score
  - GPU Match Score
  - Storage Match Score
  - Network Match Score
  - Features Match Score
```

### Values Compared

#### 1. **CPU (Weight: 2.0 by default)**
- **vCPUs** - Number of virtual CPU cores
- **Score calculation:** Percentage difference from target
  - Example: 4 vCPUs vs 4 vCPUs = 100% match
  - Example: 4 vCPUs vs 6 vCPUs = 66.7% match

#### 2. **Memory (Weight: 2.0 by default)**
- **Memory (GB)** - RAM in gigabytes
- **Score calculation:** Percentage difference from target
  - Example: 16 GB vs 16 GB = 100% match
  - Example: 16 GB vs 32 GB = 50% match

#### 3. **GPU (Weight: 2.0 by default)**
- **GPU Count** - Number of GPUs (0, 1, 2, 4, etc.)
- **GPU Type** - GPU model/family (e.g., Tesla T4, V100)
- **Score calculation:** Exact match or 0%
  - Example: 1 GPU vs 1 GPU = 100% match
  - Example: 1 GPU vs 2 GPUs = 0% match

#### 4. **Storage (Weight: 1.0 by default)**
- **Max Data Disks** - Maximum attachable data disks
- **Uncached Disk IOPS** - Disk I/O operations per second (performance)
- **Uncached Disk Throughput** - Disk bandwidth (MB/s)
- **Cached Disk IOPS** - Cached disk performance
- **Cached Disk Throughput** - Cached disk bandwidth
- **NVMe Support** - Local NVMe SSD storage (ultra-fast)
- **Write Accelerator** - Specialized disk acceleration
- **Score calculation:** Based on IOPS difference
  - Example: 12,800 IOPS vs 12,800 IOPS = 100% match
  - Example: 12,800 IOPS vs 25,600 IOPS = 50% match

#### 5. **Network (Weight: 1.0 by default)**
- **Max NICs** - Maximum network interfaces
- **Accelerated Networking** - Enhanced network performance (SR-IOV)
- **Score calculation:** Percentage difference in NICs
  - Example: 2 NICs vs 2 NICs = 100% match
  - Example: 2 NICs vs 4 NICs = 50% match

#### 6. **Features (Weight: 0.5 by default)**
- **Premium IO** - Premium SSD support
- **Encryption at Host** - Data encryption at VM host level
- **Ephemeral OS Disk** - Temporary OS disk for stateless workloads
- **Availability Zones** - Zone redundancy support
- **Score calculation:** Percentage of matching features
  - Example: 3 out of 4 features match = 75%

#### 7. **Pricing (Display Only)**
- **Hourly Rate** - Cost per hour (Linux pricing)
- **Monthly Rate** - Cost per month (730 hours)
- **Currency** - USD, EUR, GBP, etc.
- *Note: Pricing is displayed but not used in similarity calculation*

### Customizable Weights

Adjust importance of each category based on your workload:

| Workload Type | Recommended Weights |
|---------------|---------------------|
| **General Purpose** | CPU: 2.0, Memory: 2.0, Storage: 1.0 |
| **Compute-Intensive** | CPU: 3.0, Memory: 1.0, GPU: 3.0 |
| **Memory-Intensive** | CPU: 1.0, Memory: 3.0, Storage: 1.0 |
| **Storage-Intensive** | CPU: 1.0, Memory: 1.0, Storage: 3.0 |
| **GPU Workloads** | GPU: 4.0, CPU: 2.0, Memory: 2.0 |

### Example Calculation

**Target:** `Standard_D4s_v3` (4 vCPUs, 16 GB RAM)  
**Candidate:** `Standard_D4as_v4` (4 vCPUs, 16 GB RAM)

```
CPU Score:     100% (4 = 4)          × Weight 2.0 = 200
Memory Score:  100% (16 = 16)        × Weight 2.0 = 200
Storage Score: 90% (similar IOPS)    × Weight 1.0 = 90
Network Score: 100% (2 NICs = 2 NICs) × Weight 1.0 = 100
Features:      75% (3/4 match)       × Weight 0.5 = 37.5

Total Score = (200 + 200 + 90 + 100 + 37.5) / (2 + 2 + 1 + 1 + 0.5)
            = 627.5 / 6.5
            = 96.5% Similarity ⭐
```

---

## 🏗️ Architecture

### Web Application

```
┌─────────────────────────────────┐
│  User Browser                    │
│  • Type to search SKUs          │
│  • See live results             │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Azure Static Web App           │
│  • HTML, CSS, JavaScript        │
│  • Native datalist search       │
└──────────────┬──────────────────┘
               │
               │ HTTPS (CORS)
               ▼
┌─────────────────────────────────┐
│  Azure Functions                 │
│  (Flex Consumption)             │
│                                  │
│  GET  /api/skus?location=X      │
│  POST /api/compare_vms          │
│  Timer: Daily cache refresh     │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Azure Storage Table (Cache)    │
│  • ~25,000 VM SKUs              │
│  • 33 Azure regions             │
│  • Refreshed daily at 2 AM UTC  │
│  • 10-50ms query time ⚡         │
└─────────────────────────────────┘
```

### Key Features

**🚀 Performance:**
- **20-50x faster** than live API queries
- **10-50ms response time** (cached data)
- **Daily automatic refresh** of SKU data
- **~25,000 SKUs** cached across 33 regions

**🎨 User Experience:**
- **Type-to-search** - Filter 1000+ SKUs instantly
- **Region-first** - Only shows available SKUs
- **Formatted display** - See specs and pricing
- **Mobile-friendly** - Responsive design
- **Validation** - Prevents invalid SKU names
- **Retirement awareness** - Retiring SKUs flagged with ⚠️, hidden by default
- **Growth restriction awareness** - Capacity-limited SKUs flagged with 🔒, shown by default with a ranking penalty and an opt-in hide filter
- **CPU performance scoring** - Cross-architecture comparison (Intel/AMD/ARM normalized to Ice Lake = 100)
- **CPU generation filter** - Collapsible dropdown to filter by microarchitecture

**🔒 Security:**
- **Private storage** - No public access
- **Managed identity** - No storage keys
- **OIDC authentication** - No secrets in GitHub
- **CORS configured** - Secure API access

---

## 📊 Use Cases

### 1. Migration Planning
**Scenario:** Moving from Dv3 to Dv5 generation  
**Solution:** Compare `Standard_D4s_v3` and see `Standard_D4s_v5` as top match (similar specs, better performance)

### 2. Cost Optimization
**Scenario:** Need to reduce VM costs  
**Solution:** Find alternatives with lower pricing but similar capabilities

### 3. Regional Availability
**Scenario:** Preferred SKU not available in target region  
**Solution:** Get ranked alternatives available in that region

### 4. Performance Upgrade
**Scenario:** Need more storage IOPS  
**Solution:** Adjust Storage weight higher, find alternatives with better disk performance

### 5. Feature Requirements
**Scenario:** Workload requires Accelerated Networking  
**Solution:** Filter results to only show SKUs with that feature

---

## 🛠️ Deployment

### Prerequisites
- Azure subscription
- GitHub account
- Azure CLI (for local development)

### Quick Deploy

1. **Fork this repository**

2. **Set up Azure resources:**
   ```powershell
   # Deploy infrastructure
   .\Deploy-Flex-Functions.ps1
   ```

3. **Configure GitHub Actions:**
   ```powershell
   # Set up OIDC authentication
   .\Setup-GitHub-OIDC.ps1
   ```

4. **Push to GitHub** - Auto-deploys via GitHub Actions!

📖 **Detailed guides:**
- [GITHUB-ACTIONS-SETUP.md](GITHUB-ACTIONS-SETUP.md) - Complete deployment guide
- [FLEX-CONSUMPTION-GUIDE.md](FLEX-CONSUMPTION-GUIDE.md) - Architecture details
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues

---

## 📂 Repository Structure

```
azure-vm-sku-alternatives/
├── README.md                    # This file
├── powershell-script/          # PowerShell script version
│   ├── README.md               # PowerShell documentation
│   └── Compare-AzureVms.ps1    # PowerShell script
├── web-app/                    # Web application
│   ├── src/                    # Frontend (HTML, JS, CSS)
│   ├── api/                    # Azure Functions (Python)
│   └── infra/                  # Bicep infrastructure
├── .github/                    # GitHub Actions workflows
├── Deploy-*.ps1                # Deployment scripts
├── FLEX-CONSUMPTION-GUIDE.md   # Architecture guide
├── GITHUB-ACTIONS-SETUP.md     # Deployment guide
└── TROUBLESHOOTING.md          # Common issues
```

---

## 🤝 Contributing

Contributions are welcome! Here are some ideas:

- 🌍 Add more Azure regions
- 📊 Add more comparison metrics
- 🎨 Improve UI/UX
- 📝 Improve documentation
- 🐛 Fix bugs
- ✨ Add new features

**To contribute:**
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **Azure Retail Prices API** - For live pricing data
- **Azure Management API** - For VM SKU information
- **Azure Functions Flex Consumption** - For serverless backend
- **Azure Static Web Apps** - For frontend hosting

---

## 📞 Support

- 📖 **Documentation:** Check the guides in this repository
- 🐛 **Issues:** [GitHub Issues](../../issues)
- 🧭 **In-app reporting:** Use the **Report an issue** link in the web app footer (opens issue templates)
- 💬 **Discussions:** [GitHub Discussions](../../discussions)

---

## 🎯 Quick Links

- 🌐 **Live Demo:** https://blue-grass-0e1bb5e10.7.azurestaticapps.net
- ⚡ **PowerShell Script:** [powershell-script/README.md](powershell-script/README.md)
- 🏗️ **Deployment Guide:** [GITHUB-ACTIONS-SETUP.md](GITHUB-ACTIONS-SETUP.md)
- 🔧 **Troubleshooting:** [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- 📊 **Architecture:** [FLEX-CONSUMPTION-GUIDE.md](FLEX-CONSUMPTION-GUIDE.md)

---

**Made with ❤️ for the Azure community**
