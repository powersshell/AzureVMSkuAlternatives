# Copilot Instructions for AzureVMSkuAlternatives

## Build, test, and run commands

### Web frontend and infra wrapper (`web-app/`)
- Install deps (if needed for local tooling):  
  `npm --prefix web-app install`
- Deploy infra via wrapper script:  
  `npm --prefix web-app run deploy`
- Clean up infra via wrapper script:  
  `npm --prefix web-app run clean`

### Azure Functions API (`web-app/api`, Python)
- Install API dependencies locally:  
  `python -m pip install -r web-app\api\requirements.txt`
- Start Functions host locally (single-service run):  
  `func start --python --script-root web-app\api`
- Quick endpoint check (single “test”):  
  `curl http://localhost:7071/api/health`

### CI/CD checks for shipped site changes
- Static site deploy is triggered by push to `main` via `.github/workflows/azure-static-web-apps.yml`.
- Check latest deploy status:  
  `gh run list --repo powersshell/AzureVMSkuAlternatives --workflow "Deploy to Azure Static Web Apps" --limit 3`

## High-level architecture

- This repo has two delivery surfaces:
  - `web-app/` serverless app (frontend + Azure Functions API + Bicep infra)
  - `powershell-script/` standalone CLI comparison workflow
- Frontend is static HTML/CSS/JS in `web-app/src` and calls a direct Functions host URL from `src/app.js` (`API_BASE_URL`) because SWA rewrite does not support the required POST flow.
- API is Python Azure Functions v2 programming model in a single file: `web-app/api/function_app.py` with key routes:
  - `/api/compare_vms` (GET/POST)
  - `/api/skus`
  - `/api/compare_details`
  - `/api/health`
- Infra is Bicep-first under `web-app/infra` with:
  - `deploy.bicep` (subscription-scoped orchestration)
  - `functions-app-flex.bicep` (Flex Consumption Functions + private storage/networking + monitoring module wiring)
- Deploy pipelines:
  - `.github/workflows/azure-static-web-apps.yml` for frontend publishing on `main`
  - `.github/workflows/deploy-functions.yml` for Functions/API and related infra paths

## Key conventions and workflow requirements

### 1) Ship status must be explicit in handoff
When reporting completion for code changes intended to go live, always include:
- commit SHA
- target branch pushed
- latest relevant workflow run status/id

Do not say work is “done” without these publish details.

### 2) Completion gate for user-visible site changes
Before final handoff for web UI/site changes, run and report:
1. `git --no-pager status --short`
2. push to remote branch (usually `main`)
3. `gh run list ... "Deploy to Azure Static Web Apps"` status
4. If a **PR preview** environment was created, allowlist its origin for CORS — see section 7.

If deployment is still running, explicitly say so.

### 3) Prefer code truth over stale docs
Some docs still reference a Node API layout; the active API implementation is Python in `web-app/api/function_app.py`.
When docs and code conflict, treat runtime code and workflows as source of truth and update docs as part of the change.

### 4) Generated artifact hygiene (Bicep)
- Treat `web-app/infra/modules/*.json` as generated local artifacts from `az bicep build`.
- Do not commit these generated module JSON files unless explicitly requested.
- If they appear as untracked noise, remove them or keep them ignored.

### 5) Changelog maintenance
- Every commit that changes user-facing behavior (features, fixes, improvements, data updates) **must** include a corresponding entry in `CHANGELOG.md`.
- Entries are grouped by date (`## YYYY-MM-DD`). If multiple changes land on the same day, add them to the existing date section — do not create a duplicate heading.
- Use the format: `- **category:** description` where category is one of `feature`, `fix`, `improvement`, or `data`.
- The `README.md` "Recent Changes" section should always reflect only the **most recent date group** from CHANGELOG.md, followed by a link to the full changelog (`📄 [Full changelog →](CHANGELOG.md)`).
- When adding entries for a new date, update the README's "Recent Changes" section to show the new date's entries and remove the previous ones.

### 6) Command accuracy
- Do not claim lint/test/build execution unless commands exist and were actually run.
- Current `web-app/package.json` scripts are deployment-focused (`deploy`, `clean`) and do not provide lint/test scripts.
- For verification, use available checks (e.g., Functions health endpoint, workflow status) rather than inventing absent test commands.

### 7) PR preview CORS allowlisting (required for every new PR)
The frontend (`web-app/src/app.js`) calls the Functions API **cross-origin** at
`https://vmsku-api-functions-flex.azurewebsites.net/api` (the SWA rewrite can't do the POST flow).
The Function App CORS allowlist is an **explicit list**, and each new PR gets a **unique** SWA
preview origin, so SKUs fail to load on a fresh PR preview until that origin is allowlisted.

Whenever you open a PR that produces a SWA preview environment, allowlist its origin:
1. Get the preview URL from the deploy run log (look for `Visit your site at:`):
   `gh run view <run-id> --log | Select-String 'Visit your site at'`
   The origin pattern is `https://black-sea-0784c5d0f-<PR#>.eastus2.1.azurestaticapps.net`.
2. Add it to the live Function App CORS (takes effect immediately, no redeploy):
   `az functionapp cors add --name vmsku-api-functions-flex --resource-group rg-vmsku-alternatives --allowed-origins "<preview-origin>"`
3. Verify the API returns the matching `Access-Control-Allow-Origin` and real data:
   `Invoke-WebRequest 'https://vmsku-api-functions-flex.azurewebsites.net/api/skus?location=eastus2' -Headers @{Origin='<preview-origin>'} -UseBasicParsing`

Notes:
- The Bicep source of truth is `web-app/infra/functions-app-flex.bicep` (`cors.allowedOrigins`,
  ~line 466). The `az` CLI add is the established quick fix and does **not** persist to Bicep;
  add a matching `// SWA preview (PR #<n>)` entry there only if asked to make it durable.
- Origins can be pruned when a PR is closed/merged: `az functionapp cors remove ... --allowed-origins "<origin>"`.

## Practical editing guidance for this repo

- For simple UI changes, edit only:
  - `web-app/src/index.html`
  - `web-app/src/styles.css`
  - `web-app/src/app.js` (if behavior changes)
- Keep issue reporting links pointed to:
  - `https://github.com/powersshell/AzureVMSkuAlternatives/issues/new/choose`
- Keep user messaging aligned with current product stance:
  - Results are guidance; users should validate compatibility, regional availability, and pricing before production decisions.
