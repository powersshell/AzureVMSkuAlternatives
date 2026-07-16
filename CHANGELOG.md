# Changelog

## 2026-07-16
- **feature:** New **Browse all VMs** mode — a top-level tab switches between "Find alternatives" (the existing compare tool) and a browsable, sortable, searchable grid of **every** VM size in the selected region. Filter by family, vCPU/RAM range, GPU-only, and CPU vendor; sort any column (including live hourly/monthly price); and page through results. Each row has **Find alternatives** (jumps to the compare tool prefilled with that SKU) and **Where is this cheapest?** actions, plus CSV/Excel export of the filtered grid. Prices honor the currency, discount, Linux/Windows, and PAYG/reserved toggles. Backed by a new `/api/grid` endpoint that returns per-region specs and pricing in one payload for instant client-side filtering.
- **feature:** New **Excel (.xlsx) export** alongside the existing CSV export. The workbook has three sheets — **Comparison Info** (target SKU, location, currency, displayed pricing OS, discount, result count, generated timestamp), **Summary** (ranking, similarity, CPU details, and all Linux/Windows/reserved pricing), and **Specifications** (per-SKU capabilities keyed by rank and SKU name). Prices and numeric specs are written as real numbers so they sort and sum natively in Excel, and columns are sized for readability.
- **improvement:** The detailed comparison view now handles missing **ACU** values correctly. Azure only publishes ACU (Azure Compute Unit) figures for certain VM series — mostly older/mid generations — so most newer sizes (e.g. v6/v7, Bsv2, Dsv5/Esv5) have no ACU. Previously the comparison could show a misleading zero-based delta (e.g. "ACU: 0 → 230"); now, when either size lacks a published ACU, the Compute section shows "ACU: not published by Azure for at least one of these sizes" with an explanatory tooltip instead.
- **improvement:** Removed the redundant **CPU Vendor** filter from the source-SKU search area. Vendor filtering now lives only where it's most useful — on the **Alternatives** results, where you can narrow recommendations to Intel/AMD/ARM. Picking your source VM is a name search, so the extra vendor checkboxes there only added clutter.
- **feature:** New **"Where is this cheapest?"** cross-region price comparison. From the source SKU panel, open a dialog that lists the selected VM's pay-as-you-go price across every Azure region where it's offered, sorted cheapest-first with the cheapest region highlighted and your current region flagged. Shows hourly and monthly prices, the percentage and monthly-dollar premium versus the cheapest region, and the maximum monthly savings. Respects the selected currency and Linux/Windows pricing toggle.
- **improvement:** The **"Where is this cheapest?"** action is now also available on every recommended alternative — each result card has a 🌍 button to the left of its price that opens the same cross-region price comparison for that SKU.

## 2026-07-08
- **feature:** Richer VM spec fields, inspired by capability coverage seen in a related internal "Compute-Compare" project. Result cards now surface an **ACU** (Azure Compute Unit) chip and an **NVMe** badge, and the detailed comparison view adds **ACU**, **vCPUs per core**, **Trusted Launch**, **Confidential Computing**, and **RDMA / InfiniBand** rows. All new fields are also included in the CSV export. New capabilities are populated for a region the next time its data is refreshed.
- **fix:** Static Web App **preview** environments (one per pull request) now load data automatically, including the "Compare" and region-availability actions. Cross-origin CORS is allowed at the App Service platform level for all origins, so every SWA preview slot, the production site, the Azure portal, and localhost work with no manual per-PR allowlisting. (An earlier same-day attempt to handle CORS in the Functions app code could not answer the browser preflight for POST requests on Flex Consumption, which broke the cross-origin Compare call; this platform-level fix resolves that.)

## 2026-06-08
- **improvement:** Removed the confusing "Min Score" dropdown. The tool now always shows the 50 closest alternatives, sorted by match score, instead of asking you to guess a similarity threshold (which could return an empty list). Each card still shows its match score, a caption notes how many matches were found, and specialty SKUs (large GPU, high-memory, NVMe-required) now reliably surface their nearest options. When no strong match exists, an inline note explains the results are the closest available; when the "Require NVMe/GPU match" options exclude everything, the empty state suggests relaxing them.

## 2026-06-05
- **fix:** Low-baseline and burstable SKUs (e.g. `Standard_B2s`) no longer return zero alternatives at the default minimum match score. Storage (IOPS/throughput) and network (bandwidth/NICs) dimensions are now scored asymmetrically — a candidate that meets or exceeds the target is treated as a full match instead of being penalized for "overshooting" — so an exact 2 vCPU / 4 GB twin now scores ~98 instead of ~79. Feature flags are likewise only penalized when the target has a capability the candidate lacks; extra capabilities are no longer counted against a candidate. The same fix was mirrored in the PowerShell script (`Compare-AzureVms.ps1`).

## 2026-06-04
- **improvement:** Mobile usability — on phones and small tablets (≤900px) the page now scrolls naturally so the results are always reachable; previously the tall stacked configuration bar could fill the screen and hide the results below it. The source-details panel now flows above the results and header spacing is tightened on small screens.
- **improvement:** Clearer "getting started" flow — numbered step labels (1 Region → 2 Source SKU → 3 Compare), a "Start here" hint and highlight on the Region selector until a region is chosen, downstream controls dimmed (and Compare disabled) until you pick a region, the Region dropdown opens automatically on load, and a 3-step "Get started" guide now fills the results area before your first comparison.
- **feature:** Expand all / Collapse all control for results — a button in the results header (next to CSV) opens or closes every result's detailed comparison at once, with a rotating chevron on each card to signal expandability. Uncached rows load through a concurrency-limited queue to avoid overloading the API.

## 2026-06-01
- **feature:** PowerShell script (`Compare-AzureVms.ps1`) brought to feature parity with the web app — added CPU vendor/generation/performance reporting, `-CpuVendor` filtering, retirement awareness (`-HideRetiring`, on by default), Reserved Instance and Windows pricing (`-PricingModel`, `-OS`), cost-efficiency metrics, cross-region availability (`-CheckRegion`), and CSV export (`-ExportCsv`)
- **data:** Ported CPU performance, series-to-CPU, and retirement reference tables into the PowerShell script from the API (`web-app/api/function_app.py`)

## 2026-05-29
- **feature:** Complete frontend redesign — new two-panel card layout inspired by SaaS comparison pages (Stytch-style)
- **feature:** Color-coded availability zones — green when alternative matches/exceeds target zones, red when zones are missing
- **feature:** Region availability shown as ✅/❌ chips on each result card
- **improvement:** Detailed comparison colors — green for upgrades/price decreases, red for downgrades/price increases
- **improvement:** Increased font sizes across the UI for better readability at 100% zoom
- **improvement:** Tighter spacing and alignment to reduce wasted vertical space
- **fix:** Region availability check crash caused by missing HTML element
- **fix:** Floating "West US?" text artifact removed from card layout
- **fix:** CPU performance data now always uses code mapping (overrides stale cache)

## 2026-05-28
- **fix:** Corrected CPU generation mapping for AMD B-series burstable SKUs (Balsv2, Batsv2) — previously showed "Ice Lake" (Intel) instead of "Milan/Zen 3" (AMD)

## 2026-05-19
- **feature:** Cross-region availability check — after running a comparison, select a second region to see which alternatives are available there (✅/❌ per SKU)
- **feature:** New API endpoint `/api/check_region_availability` for bulk SKU region lookups
- **feature:** MCP server tool `check_region_availability` for programmatic cross-region queries
- **improvement:** CSV export includes region availability column when a cross-region check is active
- **fix:** Region check dropdown now correctly populated (capture options before Choices.js initialization)

## 2026-05-15
- **feature:** VM SKU retirement awareness — SKUs announced for retirement are flagged with ⚠️ badges throughout the UI
- **feature:** Retirement ranking penalty — retiring SKUs are de-prioritized in recommendations (scaled by time-to-retirement)
- **feature:** "Hide retiring/retired SKUs" filter (enabled by default) to focus on current-gen alternatives
- **feature:** Retirement warning banner when the target/source SKU itself is retiring, with migration guide links
- **feature:** CPU generation filter collapsed into a clean dropdown (matches Advanced Options pattern)
- **data:** Official retirement data for 22 series sourced from [Microsoft docs](https://github.com/MicrosoftDocs/azure-compute-docs/blob/main/articles/virtual-machines/sizes/retirement/retired-sizes-list.md)
- **improvement:** Region and SKU dropdowns auto-focus the search input on open (MutationObserver on dropdown class)
- **fix:** Region search now matches anywhere in the name (e.g., typing "US" finds all US regions) via Fuse.js ignoreLocation + findAllMatches

## 2026-05-05
- **fix:** Coverage telemetry — `custom_dimensions` not supported on Flex Consumption; embed JSON in Message field instead
- **fix:** Workbook KQL queries updated to `parse_json(Message)` to match new telemetry format

## 2026-05-01
- **feature:** Comprehensive data validation test suite — 48 unit tests, 16 cache validation tests, 11 API contract tests
- **feature:** Cache last-refreshed timestamp displayed in results header
- **feature:** SKU data coverage section in Azure Monitor Workbook — per-region pricing/capability coverage, missing data tracking, coverage trend
- **fix:** Workbook KQL fixes — `Properties` column reference, `format_datetime` literal text, `mv-apply` empty array guards

## 2026-04-30
- **feature:** Reserved Instance (RI) pricing support — compare 1-Year and 3-Year RI costs alongside Pay-As-You-Go
- **feature:** Pricing model toggle (PAYG / 1-Year RI / 3-Year RI) with savings percentage shown in green
- **feature:** Expanded detail view reflects selected pricing model (PAYG/RI)
- **feature:** CSV export includes 4 new RI pricing columns (1yr/3yr hourly and monthly)
- **improvement:** Bulk RI pricing supplement ensures RI data is available even with stale cache
- **improvement:** OS toggle disabled when RI selected (RI covers compute only)
- **feature:** Windows RI pricing — OS toggle now active for RI modes, showing RI compute + Windows license surcharge
- **feature:** In-app GitHub issue reporting — "Report an issue" link in footer
- **feature:** Anonymous usage telemetry via Azure App Insights (no PII collected)
- **feature:** Azure Monitor Workbook for site analytics (usage, errors, performance)
- **feature:** Copilot CLI extension for Azure VM SKU tools
- **improvement:** CSV export with full capability columns

## 2026-03-05
- **fix:** Hourly prices now display with 4 decimal places to match the Azure Pricing Calculator (e.g. `$0.0510` instead of `$0.05`)
- **fix:** Removed backend rounding on hourly prices — full float precision stored from Azure Retail Prices API
- **fix:** Target SKU pricing labels now show Linux/Windows based on the OS toggle (e.g. "Hourly Cost (Linux)")
- **fix:** VM pricing now correctly selects Pay-As-You-Go Linux/Windows rates only (excludes Spot, Low Priority)
- **fix:** Daily cache refresh timer trigger now fires reliably on Flex Consumption (`use_monitor=True`)
- **fix:** Deployment workflow no longer fails on SyncTrigger timeout with private VNet — triggers synced via Azure management plane
- **improvement:** Advanced Options weight inputs replaced with Low / Normal / High priority dropdowns
