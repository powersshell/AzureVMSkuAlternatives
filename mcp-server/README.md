# Azure VM SKU Alternatives — MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that lets 
AI agents find and compare Azure VM SKUs using the Azure VM SKU Alternatives API.

## Available Tools

| Tool | Description |
|------|-------------|
| `find_alternative_skus` | Find SKUs similar to a target, ranked by similarity score |
| `compare_sku_details` | Detailed side-by-side comparison between two SKUs |
| `check_region_availability` | Check if SKUs are available in a different region |
| `list_vm_skus` | List all SKUs available in a region |
| `health_check` | Verify API connectivity |

---

## Quick Start — Remote HTTP (Easiest)

The MCP server is hosted publicly at:

```
https://vmsku-mcp-server.thankfulforest-045e8683.eastus2.azurecontainerapps.io/mcp
```

**No local setup required** — no Python, no dependencies, no cloning. Just point your MCP client at the URL.

### VS Code / GitHub Copilot (remote MCP)

Add to `.vscode/settings.json` or your user settings:

```json
{
  "mcp": {
    "servers": {
      "azure-vm-skus": {
        "url": "https://vmsku-mcp-server.thankfulforest-045e8683.eastus2.azurecontainerapps.io/mcp"
      }
    }
  }
}
```

### Claude Desktop

Add to `claude_desktop_config.json` (`%APPDATA%\Claude\claude_desktop_config.json` on Windows, `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "azure-vm-skus": {
      "url": "https://vmsku-mcp-server.thankfulforest-045e8683.eastus2.azurecontainerapps.io/mcp"
    }
  }
}
```

### Any MCP Client (generic)

Any client that supports [Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http) can connect using the URL above. No authentication is required.

---

## GitHub Copilot CLI Setup

The MCP server integrates directly with GitHub Copilot CLI via the `/mcp` command.

### Option 1: Interactive setup (recommended)

In any Copilot CLI session, run:

```
/mcp add
```

Then fill in:
- **Name:** `azure-vm-skus`
- **Type:** `http`
- **URL:** `https://vmsku-mcp-server.thankfulforest-045e8683.eastus2.azurecontainerapps.io/mcp`

Press `Ctrl+S` to save.

### Option 2: Manual config (one-liner)

Add to `~/.copilot/mcp-config.json` (create it if it doesn't exist):

**Windows (PowerShell):**
```powershell
$file = "$env:USERPROFILE\.copilot\mcp-config.json"
$config = if (Test-Path $file) { Get-Content $file | ConvertFrom-Json } else { @{ mcpServers = @{} } }
$config.mcpServers | Add-Member -Force -Name 'azure-vm-skus' -Value @{ type = 'http'; url = 'https://vmsku-mcp-server.thankfulforest-045e8683.eastus2.azurecontainerapps.io/mcp' } -MemberType NoteProperty
$config | ConvertTo-Json -Depth 10 | Set-Content $file
```

**macOS / Linux:**
```bash
cat ~/.copilot/mcp-config.json 2>/dev/null | \
  jq '.mcpServers["azure-vm-skus"] = {"type":"http","url":"https://vmsku-mcp-server.thankfulforest-045e8683.eastus2.azurecontainerapps.io/mcp"}' \
  > ~/.copilot/mcp-config.json.tmp && mv ~/.copilot/mcp-config.json.tmp ~/.copilot/mcp-config.json
```

### Option 3: Local stdio (for development)

If you prefer to run the server locally (requires `uv`):

```json
{
  "mcpServers": {
    "azure-vm-skus": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "C:\\Azure\\AzureVMSkuAlternatives\\mcp-server\\mcp_server.py"],
      "cwd": "C:\\Azure\\AzureVMSkuAlternatives"
    }
  }
}
```

After adding, run `/mcp` in Copilot CLI to verify the server appears, or start a new session.

---

## Local Setup (stdio mode)

For offline use or development, you can run the MCP server locally via stdio.

**Prerequisite:** Install [`uv`](https://docs.astral.sh/uv/) — a single binary that manages Python and packages automatically. No separate Python or `pip install` needed.

```powershell
# Windows
winget install astral-sh.uv

# macOS
brew install uv

# Linux / WSL
curl -LsSf https://astral.uv.sh/install.sh | sh
```

### VS Code (local stdio)

A `.vscode/mcp.json` is included in this repo. VS Code with GitHub Copilot will automatically discover and launch the server using `uv run` — which downloads the correct Python version and installs dependencies on first use (subsequent runs are near-instant from cache).

If prompted, approve the server when VS Code asks to start it.

### Claude Desktop (local stdio)

```json
{
  "mcpServers": {
    "azure-vm-skus": {
      "command": "uv",
      "args": ["run", "C:\\Azure\\AzureVMSkuAlternatives\\mcp-server\\mcp_server.py"]
    }
  }
}
```

Adjust the path to match where the repo is cloned on your machine. Restart Claude Desktop after saving.

---

## Microsoft M365 Copilot Setup

The MCP server can be connected to M365 Copilot via Copilot Studio using the hosted endpoint.

See **[COPILOT-STUDIO-SETUP.md](COPILOT-STUDIO-SETUP.md)** for the full step-by-step guide.

**High-level overview:**
1. Push to `main` — the `deploy-mcp-container.yml` workflow builds the image (pushed to GHCR) and deploys the Container App via Bicep into `rg-vmsku-alternatives`
2. Register the `/mcp` endpoint as a custom connector in Copilot Studio
3. Surface the agent in M365 Copilot

**Cost:** $0/month — Container App scales to zero when idle; GHCR is free for public repos.

---

## Example Prompts

Once the server is active, try prompts like:

- **"Find alternatives to Standard_D8s_v5 in eastus that are cheaper"**
- **"What AMD-based alternatives exist for Standard_D4s_v3 in westeurope?"**
- **"Compare Standard_D8s_v5 and Standard_D8as_v5 in eastus side by side"**
- **"List all available VM SKUs in eastasia and summarize the options"**
- **"I need to migrate from Standard_E16s_v4, what are the closest matches in West US 2?"**

---

## Architecture

```
AI Agent (GitHub Copilot CLI)
        │  Copilot CLI extension (JS, stdio)
        ▼
.github/extensions/azure-vm-skus/extension.mjs

AI Agent (VS Code / Claude Desktop / any MCP client)
        │  MCP (streamable-HTTP or stdio)
        ▼
mcp_server.py (FastMCP)
        │  HTTPS
        ▼
Azure Functions API
  vmsku-api-functions-flex.azurewebsites.net/api
        │
        ▼
Azure Table Storage (SKU cache) + Azure Retail Pricing API
```

## Troubleshooting

**Remote connection fails:** Ensure your client isn't trying OAuth discovery. Set authentication to `"none"` in your client config. The hosted server requires no authentication.

**Server doesn't start (local stdio):** Ensure `uv` is installed — `winget install astral-sh.uv`

**Slow first response:** The Azure Functions backend uses Flex Consumption, which may take 3-5 seconds to cold start. Subsequent calls are fast. The Container App also cold-starts after idle periods.

**Tool returns error:** Run `health_check` first to verify API connectivity.
