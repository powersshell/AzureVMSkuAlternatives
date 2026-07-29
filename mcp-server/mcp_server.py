# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastmcp>=2.0",
#   "httpx>=0.27",
# ]
# ///
"""
Azure VM SKU Alternatives — MCP Server

A Model Context Protocol server that lets AI agents (GitHub Copilot, Claude Desktop,
M365 Copilot, etc.) find and compare Azure VM SKUs using the Azure VM SKU Alternatives API.

Tools:
  - list_vm_skus            List available SKUs in a region
  - find_alternative_skus   Find similar SKUs ranked by similarity score
  - compare_sku_details     Detailed side-by-side comparison between two SKUs
  - compare_regions_for_sku Cross-region "where is this cheapest?" price comparison
  - list_region_vm_grid     Every SKU in a region with full specs + all pricing models
  - get_sku_price_history   Daily price-history series (Linux/Windows/Spot) per SKU
  - list_retiring_skus      VM sizes announced for retirement or already retired
  - health_check            Verify API connectivity

Transport modes:
  - stdio (default)       For VS Code / Claude Desktop — launched as a local subprocess
  - streamable-http       For M365 Copilot — set MCP_TRANSPORT=http, deployed to Azure Container Apps

Setup: see README.md
"""

import os
import httpx
from fastmcp import FastMCP

# The deployed Azure Functions API — no auth required
API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api"

# Azure Functions Flex Consumption can have a cold start of 3-5s on first call
HTTP_TIMEOUT = 60.0

mcp = FastMCP(
    name="Azure VM SKU Alternatives",
    instructions=(
        "Use this server to find and compare Azure Virtual Machine SKUs. "
        "Start with find_alternative_skus to get ranked alternatives, then use "
        "compare_sku_details to drill into specific pairs. Use list_vm_skus to "
        "explore what's available in a region, or list_region_vm_grid for every size "
        "in a region with full specs and all pricing models. Use compare_regions_for_sku "
        "to find the cheapest region for a size, get_sku_price_history to see price "
        "trends over time, and list_retiring_skus to flag sizes announced for "
        "retirement. Each SKU includes a CPU performance "
        "score (normalized to Ice Lake = 100) for cross-architecture comparison "
        "(Intel vs AMD vs ARM). Azure region slugs look like: "
        "eastus, westus2, westeurope, eastasia, australiaeast."
    ),
)


@mcp.tool
async def health_check() -> dict:
    """Check if the Azure VM SKU API is healthy and responding."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{API_BASE}/health")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            return {"status": "unhealthy", "error": str(exc)}


@mcp.tool
async def list_vm_skus(location: str) -> dict:
    """
    List all Azure VM SKUs available in a given region.

    Returns each SKU's name, vCPU count, memory (GB), CPU vendor (Intel/AMD/ARM),
    architecture (x64/Arm64), CPU generation (e.g. "Ice Lake", "Genoa (Zen 4)",
    "Cobalt 100 (Neoverse N2)"), and a normalized CPU performance score (Ice Lake = 100).
    Each SKU also carries pricing across models (Linux/Windows pay-as-you-go, Linux
    Spot, and 1-year reserved) and, when applicable, retirement info
    (retirementStatus "Announced"/"Retired", retirementDate, migrationGuideUrl).
    Use this to discover what SKUs exist before comparing, or to see which
    vendors/families are available.

    Args:
        location: Azure region slug, e.g. "eastus", "westeurope", "southeastasia"
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}/skus", params={"location": location})
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def find_alternative_skus(
    target_sku: str,
    location: str,
    min_similarity_score: int = 60,
    weight_cpu: float = 2.0,
    weight_memory: float = 2.0,
    weight_storage: float = 1.0,
    weight_network: float = 1.0,
    require_nvme_match: bool = False,
    require_gpu_match: bool = False,
) -> dict:
    """
    Find Azure VM SKUs similar to a target SKU, ranked by similarity score (0-100).

    Returns the target SKU details and a list of alternatives sorted by how closely
    they match — considering vCPUs, memory, storage, networking, and features.
    Each alternative includes its similarity score, vCPUs, memory, CPU vendor,
    CPU generation, CPU performance score (normalized to Ice Lake = 100, comparable
    across Intel/AMD/ARM), pricing (hourly/monthly USD), and availability zones.

    SKUs announced for retirement receive a ranking penalty (lower similarityScore).
    Check the retirementStatus field: 'Announced' means planned for retirement,
    'Retired' means no longer available. retirementDate shows the planned date,
    and migrationGuideUrl links to the official migration guide.

    Use the cpuPerfScore field to compare relative CPU performance across architectures.
    Higher scores mean faster per-vCPU performance. Examples: Ice Lake = 100,
    Sapphire Rapids = 115, Genoa (Zen 4) = 122, Cobalt 100 (ARM) = 120.

    Args:
        target_sku:           Target VM SKU name, e.g. "Standard_D4s_v5"
        location:             Azure region slug, e.g. "eastus"
        min_similarity_score: Minimum score to include (0-100). Default 60. Use 80+ for very close matches.
        weight_cpu:           Importance of CPU match (0-10). Default 2.0.
        weight_memory:        Importance of memory match (0-10). Default 2.0.
        weight_storage:       Importance of storage/IOPS match (0-10). Default 1.0.
        weight_network:       Importance of NIC count match (0-10). Default 1.0.
        require_nvme_match:   If True, only return SKUs with NVMe if the target has NVMe.
        require_gpu_match:    If True, only return GPU SKUs if the target has a GPU.
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            f"{API_BASE}/compare_vms",
            json={
                "skuName": target_sku,
                "location": location,
                "minSimilarityScore": min_similarity_score,
                "currencyCode": "USD",
                "weightCPU": weight_cpu,
                "weightMemory": weight_memory,
                "weightGPU": 2.0,
                "weightStorage": weight_storage,
                "weightNetwork": weight_network,
                "weightFeatures": 0.5,
                "requireNVMeMatch": require_nvme_match,
                "requireGPUMatch": require_gpu_match,
            },
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def compare_sku_details(
    target_sku: str,
    alternative_sku: str,
    location: str,
) -> dict:
    """
    Get a detailed side-by-side comparison between two specific Azure VM SKUs.

    Shows field-by-field differences across compute (vCPUs, memory, CPU generation,
    CPU performance score), storage (IOPS, throughput, disks, NVMe), networking
    (NICs, accelerated networking), features (Premium IO, encryption, ephemeral OS
    disk, Hyper-V Gen 2), and pricing — pay-as-you-go plus Linux Spot and 1-year
    reserved, with percentage change and cost-per-vCPU metrics. CPU performance scores
    are normalized to Ice Lake = 100, enabling cross-architecture comparison (Intel vs
    AMD vs ARM). The response also includes retirement status for each side
    (targetRetirement / alternativeRetirement) when a size is announced/retired.

    Use this after find_alternative_skus to drill into a specific candidate.

    Args:
        target_sku:      The reference SKU (what you're currently using), e.g. "Standard_D4s_v5"
        alternative_sku: The candidate to compare against, e.g. "Standard_D4as_v5"
        location:        Azure region slug, e.g. "eastus"
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{API_BASE}/compare_details",
            params={
                "target": target_sku,
                "alternative": alternative_sku,
                "location": location,
                "currency": "USD",
            },
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def check_region_availability(
    sku_names: list[str],
    region: str,
) -> dict:
    """
    Check whether a list of Azure VM SKUs are available in a specific region.

    Useful when evaluating migration or failover options: given a set of SKU
    candidates, determine which ones exist in an alternate region.

    Args:
        sku_names: List of VM SKU names to check, e.g. ["Standard_D4s_v5", "Standard_D4as_v5"]
        region:    Target Azure region slug to check availability in, e.g. "westus2"
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            f"{API_BASE}/check_region_availability",
            json={"skuNames": sku_names, "region": region},
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def compare_regions_for_sku(
    target_sku: str,
    os_type: str = "linux",
    currency: str = "USD",
) -> dict:
    """
    Compare a single Azure VM SKU's price across every region where it's offered
    ("where is this cheapest?").

    Returns each region's hourly and monthly pay-as-you-go price sorted cheapest-first,
    the cheapest region, and the maximum monthly savings versus the most expensive
    region. Useful for finding the lowest-cost region to run a given size.

    Args:
        target_sku: ARM SKU name, e.g. "Standard_D8s_v5"
        os_type:    "linux" (default) or "windows"
        currency:   ISO currency code, default "USD"
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{API_BASE}/compare_regions",
            params={"skuName": target_sku, "os": os_type, "currency": currency},
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def list_region_vm_grid(
    location: str,
    currency: str = "USD",
) -> dict:
    """
    List EVERY Azure VM SKU available in a region with full specs and pricing in one
    payload — the data behind the "Browse all VMs" grid.

    Each row includes vCPUs, memory, GPU, CPU vendor/generation/performance score,
    storage/network capabilities, retirement status (retirementStatus/retirementDate/
    migrationGuideUrl when applicable), and pricing across pricing models: Linux and
    Windows pay-as-you-go, Linux Spot (deeply discounted, interruptible), and 1-year
    reserved-instance prices (hourly and monthly). Use this to browse, filter, or rank
    an entire region; for a focused set of alternatives to a specific size use
    find_alternative_skus instead.

    Args:
        location: Azure region slug, e.g. "eastus", "westeurope"
        currency: ISO currency code, default "USD"
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{API_BASE}/grid",
            params={"location": location, "currency": currency},
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def get_sku_price_history(
    location: str,
    skus: list[str],
) -> dict:
    """
    Get daily price history for one or more Azure VM SKUs in a region.

    Returns a time series of price change-points (Linux, Windows, and Spot hourly USD)
    plus the current price, and summary stats (first, last, percent change, min, max)
    per SKU. Use this to see how a size's price has trended over time. Prices are USD
    only (history is stored in USD). History accrues going forward, so a series may be
    short or empty until at least two price change-points exist.

    Args:
        location: Azure region slug, e.g. "eastus"
        skus:     One or more ARM SKU names, e.g. ["Standard_D2s_v5", "Standard_D4s_v5"]
                  (up to 100 per call).
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{API_BASE}/history",
            params={"location": location, "skus": ",".join(skus)},
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def list_retiring_skus(sku: str = "") -> dict:
    """
    List Azure VM sizes that are announced for retirement or already retired.

    With no argument, returns the full retirement catalog: each entry has a sizePattern
    (a regex matching the affected size names — retirement is published per size-series,
    not per individual SKU), a retirementStatus ("Announced" or "Retired"), the
    retirementDate, and the official migrationGuideUrl, plus counts by status. Pass a
    specific sku to check just that size (retirementStatus is null when it is not
    retiring). Use this to proactively flag or migrate off retiring sizes.

    Args:
        sku: (optional) a single ARM SKU name to check, e.g. "Standard_D2_v2".
             Leave empty to list the entire retirement catalog.
    """
    params = {"sku": sku} if sku else None
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}/retirements", params=params)
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport == "http":
        # HTTP (streamable-http) mode — for M365 Copilot via Azure Container Apps.
        # Authentication is handled at the infrastructure level via Azure Container Apps
        # Easy Auth (Entra ID) — no auth logic needed here.
        mcp.run(
            transport="streamable-http",
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 8000)),
            stateless_http=True,
        )
    else:
        # stdio mode (default) — for VS Code / Claude Desktop
        mcp.run()
