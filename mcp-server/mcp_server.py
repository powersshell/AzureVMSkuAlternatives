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
  - list_vm_skus          List available SKUs in a region
  - find_alternative_skus Find similar SKUs ranked by similarity score
  - compare_sku_details   Detailed side-by-side comparison between two SKUs
  - health_check          Verify API connectivity

Transport modes:
  - stdio (default)       For VS Code / Claude Desktop — launched as a local subprocess
  - streamable-http       For M365 Copilot — set MCP_TRANSPORT=http, deployed to Azure Container Apps

Setup: see README.md
"""

import os
import httpx
from fastmcp import FastMCP

# The deployed Azure Functions API — no auth required
API_BASE = "https://vmsku-api-functions-flex.azurewebsites.net/api"

# Azure Functions Flex Consumption can have a cold start of 3-5s on first call
HTTP_TIMEOUT = 60.0

mcp = FastMCP(
    name="Azure VM SKU Alternatives",
    instructions=(
        "Use this server to find and compare Azure Virtual Machine SKUs. "
        "Start with find_alternative_skus to get ranked alternatives, then use "
        "compare_sku_details to drill into specific pairs. Use list_vm_skus to "
        "explore what's available in a region. Each SKU includes a CPU performance "
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
    disk, Hyper-V Gen 2), and pricing (hourly/monthly with percentage change and
    cost-per-vCPU metrics). CPU performance scores are normalized to Ice Lake = 100,
    enabling cross-architecture comparison (Intel vs AMD vs ARM).

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
