# Changelog

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
