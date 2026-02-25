# Azure VM SKU Alternatives — MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that lets 
AI agents find and compare Azure VM SKUs using the Azure VM SKU Alternatives API.

## Available Tools

| Tool | Description |
|------|-------------|
| `find_alternative_skus` | Find SKUs similar to a target, ranked by similarity score |
| `compare_sku_details` | Detailed side-by-side comparison between two SKUs |
| `list_vm_skus` | List all SKUs available in a region |
| `health_check` | Verify API connectivity |

## Prerequisites

```bash
pip install -r mcp-server/requirements.txt
```

Requires Python 3.10+ and the `fastmcp` and `httpx` packages.

## VS Code / GitHub Copilot Setup

A `.vscode/mcp.json` is included in this repo. VS Code with GitHub Copilot will
automatically discover the server. No extra configuration required.

If prompted, approve the server when VS Code asks to start it.

## Claude Desktop Setup

Add the following to your `claude_desktop_config.json`
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "azure-vm-skus": {
      "command": "python",
      "args": ["C:\\Azure\\AzureVMSkuAlternatives\\mcp-server\\mcp_server.py"]
    }
  }
}
```

Adjust the path to match where the repo is cloned on your machine. Restart Claude Desktop
after saving.

## Example Prompts

Once the server is active, try prompts like:

- **"Find alternatives to Standard_D8s_v5 in eastus that are cheaper"**
- **"What AMD-based alternatives exist for Standard_D4s_v3 in westeurope?"**
- **"Compare Standard_D8s_v5 and Standard_D8as_v5 in eastus side by side"**
- **"List all available VM SKUs in eastasia and summarize the options"**
- **"I need to migrate from Standard_E16s_v4, what are the closest matches in West US 2?"**

## Architecture

The MCP server is a thin HTTP client that calls the deployed Azure Functions API.
No credentials are required — all endpoints are public read-only APIs.

```
AI Agent (Copilot / Claude)
       │  MCP (stdio)
       ▼
mcp_server.py (FastMCP)
       │  HTTPS
       ▼
Azure Functions API
  vmsku-api-functions-flex.azurewebsites.net/api
       │
       ▼
Azure Table Storage (SKU cache)
Azure Retail Pricing API (live pricing)
Azure Management API (SKU capabilities)
```

## Troubleshooting

**Server doesn't start**: Ensure `fastmcp` is installed — `pip install fastmcp httpx`

**Slow first response**: The Azure Functions backend uses Flex Consumption, which may
take 3-5 seconds to cold start. Subsequent calls within a few minutes are fast.

**Tool returns error**: Run `health_check` first to verify API connectivity.
