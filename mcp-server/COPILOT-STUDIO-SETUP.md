# Connecting the MCP Server to Microsoft M365 Copilot

This guide walks through connecting the Azure VM SKU Alternatives MCP server to Microsoft M365 Copilot via Copilot Studio.

---

## Prerequisites

- Microsoft 365 tenant with Copilot Studio access
- Azure subscription (resource group `rg-vmsku-alternatives` already exists)
- Owner/Contributor access to the Azure subscription
- GitHub repository admin access (for Actions secrets)

---

## Step 1: Register an Entra ID Application

The MCP server uses Azure Container Apps Easy Auth — authentication is enforced at the infrastructure level, with no credentials in application code.

1. Go to [Azure Portal](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations** → **New registration**
2. Name: `vmsku-mcp-server`
3. Supported account types: **Single tenant** (or your org's preferred setting)
4. Click **Register**
5. Copy the **Application (client) ID** and **Directory (tenant) ID** — you'll need these throughout this guide

**Expose an API:**
1. In the app registration → **Expose an API** → **Add a scope**
2. Accept the default Application ID URI (`api://<client-id>`)
3. Scope name: `access`
4. Who can consent: **Admins and users**
5. Click **Add scope**

**Create a Client Secret:**
1. In the app registration → **Certificates & secrets** → **New client secret**
2. Description: `Copilot Studio`
3. Choose an expiry (e.g., 24 months)
4. Click **Add**
5. **Copy the secret Value immediately** — it won't be shown again

**Add a Redirect URI** (you will update this after Step 4):
1. In the app registration → **Authentication** → **Add a platform** → **Web**
2. Leave the redirect URI blank for now — you'll fill it in after getting the URL from Copilot Studio in Step 4
3. Click **Configure**

---

## Step 2: Configure GitHub Actions Secrets

In your GitHub repository → **Settings** → **Secrets and variables** → **Actions**, add:

| Secret name | Value |
|-------------|-------|
| `MCP_ENTRA_CLIENT_ID` | Application (client) ID from Step 1 |

> The tenant ID is already stored as `AZURE_TENANT_ID` from the existing OIDC setup.

---

## Step 3: Deploy the Container App

The GitHub Actions workflow `deploy-mcp-container.yml` handles the build and deployment automatically:

1. Push any change to `mcp-server/` on the `main` branch, **or**
2. Manually trigger via **Actions** → **Deploy MCP Server Container** → **Run workflow** (check "Deploy/update infrastructure")

The workflow will:
- Build the Docker image and push to GitHub Container Registry (free, public)
- Deploy the Azure Container App into `rg-vmsku-alternatives` in eastus2
- Configure Entra ID Easy Auth on the Container App

At the end of the workflow run, the **MCP Endpoint URL** is printed in the logs:
```
MCP Endpoint: https://vmsku-mcp-server.<hash>.eastus2.azurecontainerapps.io/mcp
```
Copy this URL — you need it in Step 4.

**Also update `openapi-mcp.json`** with your actual values before Step 4:
- Replace `REPLACE_WITH_YOUR_CONTAINER_APP_FQDN` with the Container App hostname
- Replace `REPLACE_WITH_YOUR_TENANT_ID` with your Entra tenant ID
- Replace `REPLACE_WITH_YOUR_CLIENT_ID` with your Entra client ID from Step 1

---

## Step 4: Register the MCP Server in Copilot Studio

1. Go to [Copilot Studio](https://copilotstudio.microsoft.com)
2. Select your environment → open (or create) an agent
3. Navigate to **Actions** → **Add an action** → **New action** → **Model Context Protocol**
4. Paste your MCP endpoint URL: `https://<fqdn>/mcp`
5. Upload `mcp-server/openapi-mcp.json` as the connector schema
6. For authentication, select **OAuth 2.0** and fill in:

   | Field | Value |
   |-------|-------|
   | **Service Provider** | Generic OAuth 2 |
   | **Client ID** | Application (client) ID from Step 1 |
   | **Client Secret** | Secret value from Step 1 |
   | **Authorization URL** | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize` |
   | **Token URL** | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token` |
   | **Refresh URL** | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token` |
   | **Scope** | `api://<client-id>/access` |

7. After saving, Copilot Studio will display a **Redirect URL** — copy it
8. Go back to the Entra app registration → **Authentication** → add that redirect URL to the Web platform you created in Step 1
9. Click **Save**

Copilot Studio will discover the four available tools:
- `health_check` — verify API connectivity
- `list_vm_skus` — list all SKUs available in a region
- `find_alternative_skus` — find similar SKUs ranked by similarity score
- `compare_sku_details` — detailed side-by-side comparison between two SKUs

---

## Step 5: Surface the Agent in M365 Copilot

1. In Copilot Studio, open your agent → **Channels** → **Microsoft 365**
2. Click **Add to Microsoft 365**
3. The agent is now available in Microsoft 365 Copilot (Teams, Word, Outlook, etc.)

Users can then ask questions like:
- *"Find alternatives to Standard_D8s_v5 in eastus that are cheaper"*
- *"What AMD-based alternatives exist for Standard_D4s_v3 in westeurope?"*
- *"Compare Standard_D8s_v5 and Standard_D8as_v5 in eastus side by side"*

---

## Architecture

```
M365 Copilot (Teams / Word / Outlook)
        │
        ▼
Copilot Studio Agent
        │  Entra ID auth (token issued by Copilot Studio)
        │  MCP Streamable HTTP
        ▼
Azure Container App (vmsku-mcp-server)
  • Easy Auth validates Entra ID token — no auth code in the app
  • Scales to zero when idle ($0 baseline cost)
        │  HTTPS
        ▼
Azure Functions API
  vmsku-api-func-cus.azurewebsites.net/api
        │
        ▼
Azure Table Storage (SKU cache) + Azure Retail Pricing API
```

---

## Troubleshooting

**401 Unauthorized from the Container App**
- Verify the Entra app registration is correctly configured with the `api://<client-id>` Application ID URI
- Ensure Copilot Studio is using the correct client ID and scope

**Container App not starting**
- Check GitHub Actions logs for build/push errors
- Verify `MCP_ENTRA_CLIENT_ID` secret is set in GitHub Actions

**Tools not appearing in Copilot Studio**
- Confirm the MCP endpoint URL ends in `/mcp`
- Verify the openapi-mcp.json has the correct FQDN and `x-ms-agentic-protocol: mcp-streamable-1.0`

**Slow first response**
- The Container App scales to zero when idle; first call after a period of inactivity has a ~5-10 second cold start
- Subsequent calls within a few minutes are fast
