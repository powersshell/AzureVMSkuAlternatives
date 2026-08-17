# Azure VM SKU Alternatives

> Find similar and alternative Azure Virtual Machine SKUs based on comprehensive hardware specifications, capabilities, and pricing.

[![Live Demo](https://img.shields.io/badge/🌐_Live_Demo-blue?style=for-the-badge)](https://blue-grass-0e1bb5e10.7.azurestaticapps.net)
[![PowerShell](https://img.shields.io/badge/PowerShell-Script-blue?style=for-the-badge&logo=powershell)](powershell-script/)

---

## 📋 Recent Changes

### 2026-08-17
- **improvement:** **The hosted MCP server had no automated coverage at all.** All seven existing CI guards call the Functions API; none ever connected to MCP. That gap has teeth, because MCP deploys *independently* (`deploy-mcp-container.yml` fires only on `mcp-server/**`), so its container can sit at an older commit than the API while every check stays green — and it's the one surface consumed by **agents rather than people**, so a renamed field or dropped tool would surface as an agent quietly reasoning over missing data, with nothing red anywhere. Added `scripts/check_mcp_server.py`, which speaks real MCP over the wire (exercising `tools/list`, input schemas and result envelopes, not just the HTTP handlers underneath) and runs **28 assertions across all 10 tools** — including that `recommendationScore`, `scoreBreakdown` and `migrationReadiness` actually reached MCP, that `similarityScore` is still there for back-compat, that #1 is never older / retired / capacity-limited, and that `priority_mode` and `architecture_filter` genuinely change the answer. Runs on merge and **daily** — the daily run being the substantive part, since an API-only change that alters the shared contract would otherwise never trigger it. Live server: **28 of 28 pass**.
- **fix:** **The results list was squeezed into a tiny scroll box on ordinary laptop screens.** The page was a fixed-viewport app shell (`html, body` locked to the window with scrolling disabled), so the document could never scroll and results had to be read through a nested pane. The escape hatch that restored normal scrolling was keyed on **width** (`max-width: 900px`) — but the problem is **height**, and a 1610 × 1003 laptop window is wide. Combined with the "Before you migrate" panel added earlier today (~250 px pinned *above* the results), fixed chrome took **~92% of the viewport and the results list got ~83 px**. Two competing scroll panes made it worse — scrolling over the left source panel did nothing to the results. The page now scrolls normally at every size, with the results header and source panel **sticky** so filters stay reachable; the Browse all VMs table scrolls with the page and its headers pin to the viewport. Layout only — no data or behaviour changes.
- **feature:** **Recommendations are now ranked by more than raw spec similarity.** Every alternative previously scored on one thing: how close its specs were to the target. When vCPU and memory matched exactly, every term saturated at 100 and the score collapsed — `Standard_D2_v3` in East US returned 189 candidates sharing only **18 distinct scores**, with **24 tied at exactly 100.00** and the tie broken *alphabetically*. That put a **burstable v2** at #1 for a general-purpose v3 source (`Standard_B2as_v2` — a CPU-credit performance model entirely), returned an **older v3 confidential-computing size** for `Standard_E8s_v4`, and buried the obviously-correct `Standard_F4alds_v7` at **#8** for `Standard_F4s_v2`. A new **recommendation score** blends technical fit (60%) with generation currency (25%) and workload-family match (15%), and the sort has a real tie-breaker chain instead of falling back to alphabetical. Across ten representative sources the correct same-family answer now ranks **#1 in all ten**. Applies to the site, API, MCP server and PowerShell script.
- **fix:** **Modernization is scored relative to each family, not against v7.** "v7 is best" is wrong, because not every family *has* a v7 — **L-series tops out at v4**, **B-series at v2** — so on an absolute scale a cross-family v7 always wins: storage-optimized `Standard_L8s_v2` was answered with **memory-optimized** `Standard_E8ads_v7`, and burstable `Standard_B2s` with **compute-optimized** `Standard_F2alds_v7`. Each size is now scored against the newest generation its own family actually ships **in the region you searched**. `Standard_L8s_v2` now returns `Standard_L8as_v4`; `Standard_B2s` returns `Standard_B2als_v2`.
- **feature:** **Migration considerations surfaced on every result.** Ranking a v7 first without saying what changes underneath would be bad advice — Microsoft's guidance is explicit that v6/v7 is a *planned upgrade*, not a blind resize. Each recommendation now carries badges for **Gen 2 (UEFI) required**, **MANA network adapter** (v6+, may need updated drivers), **local NVMe temp disk**, **architecture change** (Arm64 needs binaries rebuilt), and **older generation than your source**. A "Before you migrate" panel estimates effort from your *source* generation — Very low from v5, Low from v4, Moderate from v2/v3 — with the Assess → Plan → Migrate → Validate links from Microsoft Learn.
- **feature:** **A "Why this ranking?" breakdown on every result**, showing the four score components and any penalties — including the retirement and capacity-limitation penalties that were already in effect but previously invisible.
- **feature:** **Priority mode and architecture filter.** *Balanced* (default) uses the blend above; *Lowest cost* shifts weight toward cheaper alternatives. Architecture is a **filter** (Any / x64 / Arm64) rather than a ranking mode on purpose — measured across ten sources, ranking by architecture produced an **identical top 5 in all ten cases**. Both available on the site, in the MCP server (`priority_mode`, `architecture_filter`) and in PowerShell (`-PriorityMode`, `-ArchitectureFilter`).
- **improvement:** The existing `similarityScore` is **unchanged** and still means pure technical fit; the minimum-similarity threshold still filters on it. The recommendation score is a separate additive field, so anything reading the API today sees the same numbers.
- **fix:** The NVMe badge is now labelled **"Local NVMe"** — the underlying flag measures the local *temp disk* (only on `d`-suffixed sizes), not the remote-disk NVMe interface the v6/v7 guidance discusses.
- **improvement:** Added `scripts/check_recommendation_quality.py` and a CI check asserting the ranking properties above against the **live** API: a source's own family must lead, a burstable size must never lead for a non-burstable source, #1 must never be older, retired or capacity-limited, cost mode must actually reorder, and the top 5 must hold ≥3 distinct scores. It re-implements size-name parsing independently rather than importing it, so it can't pass just because the API is self-consistently wrong. Negative-tested against production first: **8 of 8 assertions failed**, reproducing every bug above.

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
