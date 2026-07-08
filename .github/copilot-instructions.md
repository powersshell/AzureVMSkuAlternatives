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
4. PR preview environments accept CORS automatically (handled in app code) — see section 7.

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

### 7) PR preview CORS — now automatic (no manual allowlisting)
The frontend (`web-app/src/app.js`) calls the Functions API **cross-origin** at
`https://vmsku-api-functions-flex.azurewebsites.net/api` (the SWA rewrite can't do the POST flow).

**CORS is handled in application code**, not the App Service platform allowlist. See
`with_cors` / `_match_cors_origin` in `web-app/api/function_app.py`: an origin regex accepts the
Azure portal, the production SWA, **every** SWA preview slot
(`https://black-sea-0784c5d0f-<PR#>.<region>.1.azurestaticapps.net`), and localhost. Each
browser-facing route is decorated with `@with_cors` and includes `OPTIONS` in its `methods` so the
runtime answers the preflight. This means **new PR previews work with no manual step** — the old
per-PR `az functionapp cors add` is no longer required.

Notes:
- The Bicep `cors.allowedOrigins` in `web-app/infra/functions-app-flex.bicep` is intentionally **empty**.
  It MUST stay empty: a non-empty platform allowlist makes App Service strip the app-emitted
  `Access-Control-Allow-Origin` header, which would break every preview origin.
- If the Static Web App hostname ever changes, update the regex in `function_app.py` (or set the
  `CORS_ALLOWED_ORIGIN_REGEX` app setting to a full override) — do NOT re-add origins to the Bicep list.
- Verify a preview origin is accepted:
  `Invoke-WebRequest 'https://vmsku-api-functions-flex.azurewebsites.net/api/skus?location=eastus2' -Headers @{Origin='<preview-origin>'} -UseBasicParsing`
  (this change must be deployed to prod — i.e. merged to `main` — before it affects live previews).

## Practical editing guidance for this repo

- For simple UI changes, edit only:
  - `web-app/src/index.html`
  - `web-app/src/styles.css`
  - `web-app/src/app.js` (if behavior changes)
- Keep issue reporting links pointed to:
  - `https://github.com/powersshell/AzureVMSkuAlternatives/issues/new/choose`
- Keep user messaging aligned with current product stance:
  - Results are guidance; users should validate compatibility, regional availability, and pricing before production decisions.
