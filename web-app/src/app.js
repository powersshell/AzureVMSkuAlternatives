// API Configuration
// Direct connection to Flex Consumption Function App (Static Web App rewrite doesn't support POST)
const API_BASE_URL = 'https://vmsku-api-func-cus.azurewebsites.net/api';
const TELEMETRY_CONFIG_ENDPOINT = `${API_BASE_URL}/telemetry_config`;
const ANALYTICS_USER_KEY = 'vmsku_anonymous_user_id';
const ANALYTICS_USER_KEY_CREATED_AT = 'vmsku_anonymous_user_id_created_at';
const ANALYTICS_USER_TTL_MS = 90 * 24 * 60 * 60 * 1000; // 90 days

// Maps priority dropdown values to numeric weights used by the comparison algorithm
const PRIORITY_VALUES = { low: 0.5, normal: 1.5, high: 3.0 };

// Results presentation: show the closest N matches (a cap, not a quota) with no
// score threshold, so specialty/GPU/NVMe SKUs still surface their nearest options.
// 50 keeps the list scannable while leaving the client-side vendor/generation
// filters a healthy pool to refine within.
const MAX_RESULTS = 50;
// UI-only cutoff: if the best match scores below this, show a "no strong matches"
// note. It does NOT filter any results.
const STRONG_MATCH = 70;

let appInsights = null;
let analyticsUserId = null;
let telemetryReady = false;
const pendingTelemetryEvents = [];

function generateAnonymousId(prefix = 'anon') {
    if (window.crypto && window.crypto.randomUUID) {
        return `${prefix}-${window.crypto.randomUUID()}`;
    }
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function getOrCreateAnonymousUserId() {
    const now = Date.now();
    const existing = localStorage.getItem(ANALYTICS_USER_KEY);
    const createdAtRaw = localStorage.getItem(ANALYTICS_USER_KEY_CREATED_AT);
    const createdAt = createdAtRaw ? Number(createdAtRaw) : null;
    const isExpired = !createdAt || Number.isNaN(createdAt) || (now - createdAt) > ANALYTICS_USER_TTL_MS;

    if (existing && !isExpired) {
        return existing;
    }

    const nextId = generateAnonymousId('vmsku');
    localStorage.setItem(ANALYTICS_USER_KEY, nextId);
    localStorage.setItem(ANALYTICS_USER_KEY_CREATED_AT, String(now));
    return nextId;
}

function sanitizeTelemetryProperties(properties = {}) {
    const safe = {};
    for (const [key, value] of Object.entries(properties)) {
        if (value === null || value === undefined) continue;
        if (typeof value === 'string') {
            safe[key] = value.slice(0, 120);
            continue;
        }
        if (typeof value === 'number' || typeof value === 'boolean') {
            safe[key] = value;
        }
    }
    return safe;
}

function sanitizeTelemetryMeasurements(measurements = {}) {
    const safe = {};
    for (const [key, value] of Object.entries(measurements)) {
        if (typeof value === 'number' && Number.isFinite(value)) {
            safe[key] = value;
        }
    }
    return safe;
}

function sendTelemetryEvent(name, properties = {}, measurements = {}) {
    if (!appInsights || !telemetryReady) return;
    appInsights.trackEvent(
        { name },
        sanitizeTelemetryProperties({
            anonymousUserId: analyticsUserId || 'unknown',
            ...properties
        }),
        sanitizeTelemetryMeasurements(measurements)
    );
}

function flushPendingTelemetryEvents() {
    while (pendingTelemetryEvents.length > 0) {
        const event = pendingTelemetryEvents.shift();
        sendTelemetryEvent(event.name, event.properties, event.measurements);
    }
}

async function initializeTelemetry() {
    const aiGlobal = window.Microsoft && window.Microsoft.ApplicationInsights;
    if (!aiGlobal || !aiGlobal.ApplicationInsights) {
        console.warn('Application Insights SDK unavailable; telemetry disabled.');
        return;
    }

    let telemetryConfig = null;
    try {
        const response = await fetch(TELEMETRY_CONFIG_ENDPOINT, { cache: 'no-store' });
        if (response.ok) {
            telemetryConfig = await response.json();
        }
    } catch (error) {
        console.warn('Unable to load telemetry config; telemetry disabled.', error);
        return;
    }

    const connectionString = telemetryConfig?.connectionString;
    if (!telemetryConfig?.enabled || !connectionString || !connectionString.includes('InstrumentationKey=')) {
        console.warn('Telemetry config not enabled; telemetry disabled.');
        return;
    }

    analyticsUserId = getOrCreateAnonymousUserId();

    appInsights = new aiGlobal.ApplicationInsights({
        config: {
            connectionString,
            disableAjaxTracking: true,
            disableFetchTracking: true,
            autoTrackPageVisitTime: true,
            enableAutoRouteTracking: false
        }
    });

    appInsights.loadAppInsights();
    appInsights.context.user.id = analyticsUserId;
    appInsights.context.user.accountId = 'public-site';
    telemetryReady = true;
    appInsights.trackPageView({ name: 'home' });
    flushPendingTelemetryEvents();
}

function trackEvent(name, properties = {}, measurements = {}) {
    if (!telemetryReady || !appInsights) {
        if (pendingTelemetryEvents.length < 100) {
            pendingTelemetryEvents.push({ name, properties, measurements });
        }
        return;
    }
    sendTelemetryEvent(name, properties, measurements);
}

function getPriorityWeight(id) {
    return PRIORITY_VALUES[document.getElementById(id).value] ?? 1.5;
}

function getDiscountMultiplier() {
    const val = parseFloat(document.getElementById('discountPct').value);
    if (isNaN(val) || val <= 0) return 1.0;
    if (val >= 100) return 0.0;
    return 1.0 - (val / 100);
}

// DOM Elements
const compareBtn = document.getElementById('compareBtn');
const loadingOverlay = document.getElementById('loadingOverlay');
const resultsSection = document.getElementById('resultsSection');
const errorSection = document.getElementById('errorSection');
const errorMessage = document.getElementById('errorMessage');
const dismissErrorBtn = document.getElementById('dismissErrorBtn');
const resultsTableBody = document.getElementById('resultsTableBody');
const targetSkuInfo = document.getElementById('targetSkuInfo');
const noResults = document.getElementById('noResults');
const exportBtn = document.getElementById('exportBtn');
const exportXlsxBtn = document.getElementById('exportXlsxBtn');

// Getting-started / onboarding elements
const regionField = document.getElementById('regionField');
const configRow = document.getElementById('configRow');
const gettingStarted = document.getElementById('gettingStarted');

// Reflects the region-first onboarding cues: emphasizes Region while empty, dims downstream
// controls until a region is chosen, and keeps Compare disabled until both Region + SKU are set.
function updateOnboardingState() {
    const hasRegion = !!document.getElementById('location').value;
    const hasSku = !!document.getElementById('skuName').value;
    if (regionField) regionField.classList.toggle('needs-region', !hasRegion);
    if (configRow) configRow.classList.toggle('region-pending', !hasRegion);
    compareBtn.disabled = !(hasRegion && hasSku);
}

function hideGettingStarted() {
    if (gettingStarted) gettingStarted.classList.add('hidden');
}

let currentResults = null;
let currentPricingOS = 'linux';
let currentPricingModel = 'payg'; // 'payg', 'ri1year', 'ri3year'
let locationChoices = null; // Choices.js instance for location
let skuChoices = null; // Choices.js instance for SKU
const expandedDetailsCache = new Map(); // Cache for expanded row details (Phase 2)
let prefetchAbortController = null; // Cancels in-flight prefetch wave on new search (Phase 3)
let regionCheckAbortController = null; // Cancels in-flight region availability check
let regionAvailabilityData = null; // { region, availability: { skuName: bool } }

// Expand/Collapse-all state
let resultsRenderVersion = 0; // Bumped on every displayAlternatives render; guards stale async writes
let renderedAlternatives = []; // The currently-rendered (filtered) list — source of truth for expand-all
let expandAllAbortController = null; // Stops scheduling new expand-all fetches
const detailsInFlight = new Map(); // cacheKey -> Promise (de-dups fetches across expand-all/prefetch)
const expandAllBtn = document.getElementById('expandAllBtn');

// Capture region options before Choices.js takes over the <select>
const ALL_REGIONS = Array.from(document.getElementById('location').options)
    .filter(opt => opt.value)
    .map(opt => ({ value: opt.value, label: opt.textContent }));

// Event Listeners
compareBtn.addEventListener('click', handleCompare);
dismissErrorBtn.addEventListener('click', hideError);
exportBtn.addEventListener('click', exportToCSV);
if (exportXlsxBtn) exportXlsxBtn.addEventListener('click', exportToXLSX);

// Initialize dropdown functionality on page load
document.addEventListener('DOMContentLoaded', () => {
    initializeTelemetry();
    trackEvent('page_loaded', {
        page: 'home'
    });

    const reportIssueLink = document.getElementById('reportIssueLink');
    if (reportIssueLink) {
        reportIssueLink.addEventListener('click', () => {
            trackEvent('report_issue_clicked', {
                source: 'footer'
            });
        });
    }

    const locationSelect = document.getElementById('location');
    const skuSelect = document.getElementById('skuName');
    
    // Initialize Choices.js for location dropdown
    locationChoices = new Choices(locationSelect, {
        searchEnabled: true,
        searchPlaceholderValue: 'Search regions...',
        itemSelectText: '',
        shouldSort: false,
        searchResultLimit: 50,
        fuseOptions: {
            includeScore: true,
            threshold: 0.4,
            ignoreLocation: true,
            minMatchCharLength: 1,
            findAllMatches: true
        }
    });
    
    // Initialize Choices.js for SKU dropdown
    // fuseOptions with ignoreLocation and findAllMatches ensures substring-style matching
    // (e.g., typing "D4" matches "Standard_D4s_v5" even though it starts at position 9)
    skuChoices = new Choices(skuSelect, {
        searchEnabled: true,
        searchPlaceholderValue: 'Type to search SKUs...',
        itemSelectText: '',
        shouldSort: false,
        placeholder: true,
        placeholderValue: 'Select a region first...',
        searchResultLimit: 200,
        fuseOptions: {
            includeScore: true,
            threshold: 0.4,
            ignoreLocation: true,
            minMatchCharLength: 1,
            findAllMatches: true
        }
    });
    
    // Auto-focus search input when dropdowns open.
    // Choices.js internally focuses the input via requestAnimationFrame, but
    // containerOuter.focus() can steal focus back on some browsers.
    // We observe the 'is-active' class on the dropdown to reliably detect open state.
    function autoFocusSearch(choicesInstance) {
        const dropdown = choicesInstance.dropdown.element;
        const observer = new MutationObserver(() => {
            if (dropdown.classList.contains('is-active')) {
                setTimeout(() => {
                    if (choicesInstance.input && choicesInstance.input.element) {
                        choicesInstance.input.element.focus();
                    }
                }, 16);
            }
        });
        observer.observe(dropdown, { attributes: true, attributeFilter: ['class'] });
    }
    autoFocusSearch(locationChoices);
    autoFocusSearch(skuChoices);

    // Disable SKU dropdown initially
    skuChoices.disable();

    // Initialize region-first onboarding cues (emphasis, dimming, Compare disabled)
    updateOnboardingState();

    // E. Draw attention to the starting point: open the Region dropdown on first load
    // (only when nothing is selected yet).
    if (!locationSelect.value) {
        setTimeout(() => {
            try { locationChoices.showDropdown(); } catch (e) { /* non-fatal */ }
        }, 250);
    }
    
    // Listen for region changes - using Choices.js passedElement
    locationChoices.passedElement.element.addEventListener('change', async (e) => {
        const location = e.target.value;

        if (!location) {
            // No region selected - disable SKU dropdown
            skuChoices.clearStore();
            skuChoices.setChoices([{ value: '', label: 'Select a region first...', disabled: true }], 'value', 'label', true);
            skuChoices.disable();
            document.getElementById('skuCount').textContent = '';
            updateOnboardingState();
            return;
        }

        // Region chosen - clear the "start here" emphasis/dimming
        updateOnboardingState();

        // Fetch SKUs for selected region
        await loadSkusForRegion(location);

        // If browsing, (re)load the grid for the new region
        if (currentMode === 'browse') {
            loadGrid();
        }
    });

    // Listen for source SKU changes - enables Compare once both region + SKU are set
    skuChoices.passedElement.element.addEventListener('change', updateOnboardingState);
    
    // Listen for CPU vendor filter changes (result filters)
    document.getElementById('resultFilterIntel').addEventListener('change', updateResultsFilters);
    document.getElementById('resultFilterAMD').addEventListener('change', updateResultsFilters);
    document.getElementById('resultFilterARM').addEventListener('change', updateResultsFilters);

    // CPU generation filter buttons
    document.getElementById('genSelectAll').addEventListener('click', genSelectAll);
    document.getElementById('genDeselectAll').addEventListener('click', genDeselectAll);

    // Retirement filter checkbox
    document.getElementById('hideRetiringFilter').addEventListener('change', updateResultsFilters);

    // Update discount hint text as user types
    document.getElementById('discountPct').addEventListener('input', updateDiscountHint);

    // Expand/Collapse all results
    expandAllBtn?.addEventListener('click', () => {
        if (expandAllBtn.getAttribute('aria-pressed') === 'true') {
            collapseAll();
        } else {
            expandAll();
        }
    });
});

// Store all SKUs for current region
let allSkusForRegion = [];

// Update SKU count display
function updateSkuCount(count) {
    const skuCount = document.getElementById('skuCount');
    skuCount.textContent = `${count} SKUs available`;
    skuCount.style.color = '#107c10'; // Success green
}

function updateDiscountHint() {
    const hint = document.getElementById('discountHint');
    const val = parseFloat(document.getElementById('discountPct').value);
    if (isNaN(val) || val <= 0) {
        hint.textContent = 'No discount applied. Enter 0–100 to reduce displayed prices.';
        hint.style.color = '';
    } else if (val > 100) {
        hint.textContent = 'Discount must be between 0 and 100.';
        hint.style.color = '#a80000';
    } else {
        hint.textContent = `Prices shown at ${(100 - val).toFixed(val % 1 === 0 ? 0 : 1)}% of list price (${val}% discount applied).`;
        hint.style.color = '#107c10';
    }
}

// Update comparison results based on CPU vendor filters
function updateResultsFilters() {
    if (!currentResults || !currentResults.alternatives) return;
    trackEvent('result_vendor_filter_changed', {
        intel: document.getElementById('resultFilterIntel').checked,
        amd: document.getElementById('resultFilterAMD').checked,
        arm: document.getElementById('resultFilterARM').checked
    });
    
    // Repopulate generation filter (vendor change affects available generations)
    populateGenFilter(currentResults.alternatives);
    // Re-display with both filters
    displayAlternatives(filterResults(currentResults.alternatives));
}

// Load SKUs from cache for selected region
async function loadSkusForRegion(location) {
    const skuCount = document.getElementById('skuCount');
    
    try {
        // Show loading state
        skuChoices.clearStore();
        skuChoices.setChoices([{ value: '', label: 'Loading SKUs...', disabled: true }], 'value', 'label', true);
        skuChoices.disable();
        skuCount.textContent = 'Loading...';
        skuCount.style.color = '#666';
        
        // Fetch from cached API
        const response = await fetch(`${API_BASE_URL}/skus?location=${location}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        const skus = data.skus || []; // Extract skus array from response
        
        if (!skus || skus.length === 0) {
            allSkusForRegion = [];
            skuChoices.setChoices([{ value: '', label: 'No SKUs available for this region', disabled: true }], 'value', 'label', true);
            skuCount.textContent = 'No SKUs found';
            skuCount.style.color = '#d13438';
            return;
        }
        
        // Store all SKUs for the region
        allSkusForRegion = skus;
        
        // Populate dropdown with all SKUs
        populateSkuChoices(skus);
        
        // Update count
        updateSkuCount(skus.length);
        skuChoices.enable();
        
    } catch (error) {
        console.error('Failed to load SKUs:', error);
        skuChoices.setChoices([{ value: '', label: 'Error loading SKUs - try again', disabled: true }], 'value', 'label', true);
        skuCount.textContent = 'Failed to load SKUs';
        skuCount.style.color = '#d13438'; // Error red
    }
}

// Populate SKU dropdown with Choices.js
function populateSkuChoices(skus) {
    // Backend already sorts by vCPUs then memory, so we trust that order
    
    // Build choices array with displayName constructed on frontend
    const choices = skus.map(sku => {
        const retiring = sku.retirementStatus ? ' ⚠️ Retiring' : '';
        return {
            value: sku.name,
            label: `${sku.name} (${sku.vCPUs} vCPUs, ${sku.memoryGB} GB)${retiring}`,
            customProperties: {
                vCPUs: sku.vCPUs,
                memoryGB: sku.memoryGB
            }
        };
    });
    
    // Clear existing choices and add new ones
    skuChoices.clearStore();
    skuChoices.setChoices(choices, 'value', 'label', true);
    
    // Store valid SKU names for validation
    window.validSkuNames = skus.map(s => s.name);
}

// Handle Compare Button Click
async function handleCompare() {
    const skuName = document.getElementById('skuName').value.trim();
    const location = document.getElementById('location').value;
    const currencyCode = document.getElementById('currencyCode').value;

    if (!skuName || !location) {
        trackEvent('compare_validation_failed', {
            reason: 'missing_required_fields'
        });
        showError('Please provide both SKU name and location');
        return;
    }
    
    // Validate that entered SKU exists in the list (optional but recommended)
    if (window.validSkuNames && !window.validSkuNames.includes(skuName)) {
        trackEvent('compare_validation_failed', {
            reason: 'invalid_sku_selected'
        });
        showError(`Invalid SKU: "${skuName}". Please select a valid SKU from the dropdown.`);
        return;
    }

    const params = {
        skuName,
        location,
        minSimilarityScore: 0,
        maxResults: MAX_RESULTS,
        currencyCode,
        weightCPU: getPriorityWeight('weightCPU'),
        weightMemory: getPriorityWeight('weightMemory'),
        weightGPU: getPriorityWeight('weightGPU'),
        weightStorage: getPriorityWeight('weightStorage'),
        weightNetwork: getPriorityWeight('weightNetwork'),
        weightFeatures: getPriorityWeight('weightFeatures'),
        requireNVMeMatch: document.getElementById('requireNVMeMatch').checked,
        requireGPUMatch: document.getElementById('requireGPUMatch').checked
    };

    showLoading();
    hideError();
    hideResults();
    clearRegionCheck();

    trackEvent('compare_submitted', {
        location,
        currencyCode
    }, {
        maxResults: MAX_RESULTS
    });

    try {
        const response = await fetch(`${API_BASE_URL}/compare_vms`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(params)
        });

        if (!response.ok) {
            // Try to parse as JSON, but handle cases where it's not JSON
            const responseText = await response.text();
            let errorMessage;
            try {
                const errorData = JSON.parse(responseText);
                errorMessage = errorData.error || JSON.stringify(errorData);
            } catch {
                errorMessage = responseText || response.statusText;
            }
            throw new Error(`HTTP ${response.status}: ${errorMessage}`);
        }

        // Parse the response as JSON
        const data = await response.json();

        // Cap to the closest MAX_RESULTS client-side too, so the UI is consistent
        // whether or not the API applied maxResults. Preserve the true total count
        // (the API sends totalMatches; fall back to the pre-cap length otherwise).
        data.totalMatches = (typeof data.totalMatches === 'number')
            ? data.totalMatches
            : (Array.isArray(data.alternatives) ? data.alternatives.length : 0);
        if (Array.isArray(data.alternatives)) {
            data.alternatives = data.alternatives.slice(0, MAX_RESULTS);
        }

        currentResults = data;
        displayResults(data);

        trackEvent('compare_completed', {
            location,
            currencyCode,
            resultStatus: data.alternatives && data.alternatives.length > 0 ? 'results' : 'no_results'
        }, {
            alternativesCount: data.alternatives ? data.alternatives.length : 0
        });
    } catch (error) {
        console.error('Error comparing VMs:', error);
        trackEvent('compare_failed', {
            location,
            currencyCode,
            errorType: 'request_or_server_error'
        });
        showError(error.message || 'Failed to compare VMs. Please check your input and try again.');
    } finally {
        hideLoading();
    }
}

// OS Pricing toggle
function setPricingOS(os) {
    currentPricingOS = os;
    trackEvent('pricing_os_toggled', { os });
    document.getElementById('btn-linux-pricing')?.classList.toggle('active', os === 'linux');
    document.getElementById('btn-windows-pricing')?.classList.toggle('active', os === 'windows');
    updatePricingHeaders();
    // Re-render results if available
    if (currentResults) {
        displayAlternatives(filterResults(currentResults.alternatives));
        displayTargetSku(currentResults.targetSku);
    }
    syncGridPricingButtons();
    if (currentMode === 'browse' && gridRows.length) renderGrid();
}

// Pricing model toggle (PAYG / 1-Year RI / 3-Year RI)
function setPricingModel(model) {
    currentPricingModel = model;
    trackEvent('pricing_model_toggled', { model });
    document.getElementById('btn-payg')?.classList.toggle('active', model === 'payg');
    document.getElementById('btn-spot')?.classList.toggle('active', model === 'spot');
    document.getElementById('btn-ri1year')?.classList.toggle('active', model === 'ri1year');
    document.getElementById('btn-ri3year')?.classList.toggle('active', model === 'ri3year');

    // Spot pricing is Linux-only — force Linux OS and disable the Windows toggle
    applySpotOsLock(model === 'spot');

    updatePricingHeaders();
    if (currentResults) {
        displayAlternatives(filterResults(currentResults.alternatives));
        displayTargetSku(currentResults.targetSku);
    }
    syncGridPricingButtons();
    if (currentMode === 'browse' && gridRows.length) renderGrid();
}

// Spot pricing exists for Linux only; lock the OS toggle to Linux while Spot is selected.
function applySpotOsLock(isSpot) {
    if (isSpot && currentPricingOS !== 'linux') {
        currentPricingOS = 'linux';
        document.getElementById('btn-linux-pricing')?.classList.add('active');
        document.getElementById('btn-windows-pricing')?.classList.remove('active');
        document.getElementById('btn-grid-linux')?.classList.add('active');
        document.getElementById('btn-grid-windows')?.classList.remove('active');
    }
    ['btn-windows-pricing', 'btn-grid-windows'].forEach(id => {
        const btn = document.getElementById(id);
        if (!btn) return;
        btn.disabled = isSpot;
        btn.classList.toggle('disabled', isSpot);
        btn.title = isSpot ? 'Spot pricing is available for Linux only' : '';
    });
}

function updatePricingHeaders() {
    const thHourly = document.getElementById('th-hourly');
    const thMonthly = document.getElementById('th-monthly');
    const osLabel = currentPricingOS === 'windows' ? 'Win' : 'Linux';
    if (currentPricingModel === 'spot') {
        thHourly.textContent = 'Hourly Cost (Spot, Linux)';
        thMonthly.textContent = 'Monthly Cost (Spot, Linux)';
    } else if (currentPricingModel === 'ri1year') {
        thHourly.textContent = `Hourly Cost (1yr RI, ${osLabel})`;
        thMonthly.textContent = `Monthly Cost (1yr RI, ${osLabel})`;
    } else if (currentPricingModel === 'ri3year') {
        thHourly.textContent = `Hourly Cost (3yr RI, ${osLabel})`;
        thMonthly.textContent = `Monthly Cost (3yr RI, ${osLabel})`;
    } else {
        thHourly.textContent = `Hourly Cost (${osLabel})`;
        thMonthly.textContent = `Monthly Cost (${osLabel})`;
    }
}

function getHourlyPrice(pricing) {
    if (!pricing) return null;
    if (currentPricingModel === 'spot') {
        return pricing.spotHourly ?? null;
    }
    if (currentPricingModel === 'ri1year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri1YearHourlyWindows ?? pricing.ri1YearHourly ?? null)
            : (pricing.ri1YearHourly ?? null);
    }
    if (currentPricingModel === 'ri3year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri3YearHourlyWindows ?? pricing.ri3YearHourly ?? null)
            : (pricing.ri3YearHourly ?? null);
    }
    return currentPricingOS === 'windows'
        ? (pricing.hourlyPriceWindows ?? pricing.hourlyPrice)
        : pricing.hourlyPrice;
}

function getMonthlyPrice(pricing) {
    if (!pricing) return null;
    if (currentPricingModel === 'spot') {
        return pricing.spotMonthly ?? null;
    }
    if (currentPricingModel === 'ri1year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri1YearMonthlyWindows ?? pricing.ri1YearMonthly ?? null)
            : (pricing.ri1YearMonthly ?? null);
    }
    if (currentPricingModel === 'ri3year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri3YearMonthlyWindows ?? pricing.ri3YearMonthly ?? null)
            : (pricing.ri3YearMonthly ?? null);
    }
    return currentPricingOS === 'windows'
        ? (pricing.monthlyPriceWindows ?? pricing.monthlyPrice)
        : pricing.monthlyPrice;
}

// Get PAYG monthly price for savings calculation (OS-aware)
function getPaygMonthlyPrice(pricing) {
    if (!pricing) return null;
    return currentPricingOS === 'windows'
        ? (pricing.monthlyPriceWindows ?? pricing.monthlyPrice)
        : pricing.monthlyPrice;
}

// Render RI savings badge for monthly column
function renderRiSavings(pricing) {
    if (currentPricingModel === 'payg' || !pricing) return '';
    const payg = getPaygMonthlyPrice(pricing);
    const ri = getMonthlyPrice(pricing);
    if (!payg || payg <= 0 || ri == null) return '';
    const pct = ((payg - ri) / payg * 100).toFixed(0);
    if (pct <= 0) return '';
    return ` <span class="ri-savings">(-${pct}%)</span>`;
}

// Null-safe price formatters — return 'N/A' when price is unavailable
function formatHourlyPriceSafe(pricing) {
    if (!pricing) return 'N/A';
    const price = getHourlyPrice(pricing);
    if (price == null) return 'N/A';
    return formatHourlyCurrency(price * getDiscountMultiplier(), pricing.currency);
}

function formatMonthlyPriceSafe(pricing) {
    if (!pricing) return 'N/A';
    const price = getMonthlyPrice(pricing);
    if (price == null) return 'N/A';
    return formatCurrency(price * getDiscountMultiplier(), pricing.currency) + renderRiSavings(pricing);
}

// Display Results
function displayResults(data) {
    if (!data.alternatives || data.alternatives.length === 0) {
        const hintEl = document.getElementById('noResultsHint');
        if (hintEl) {
            const nvme = document.getElementById('requireNVMeMatch').checked;
            const gpu = document.getElementById('requireGPUMatch').checked;
            if (nvme || gpu) {
                const reqs = [nvme ? '"Require NVMe match"' : null, gpu ? '"Require GPU match"' : null]
                    .filter(Boolean).join(' and ');
                hintEl.textContent = `No alternatives matched the ${reqs} requirement in Advanced Options. Try turning that off to see the closest available options.`;
            } else {
                hintEl.textContent = 'Try adjusting your filters or choosing a different region.';
            }
        }
        noResults.classList.remove('hidden');
        resetExpansionState();
        renderedAlternatives = [];
        resultsTableBody.innerHTML = '';
        targetSkuInfo.innerHTML = '';
        updateExpandAllButton();
    } else {
        noResults.classList.add('hidden');
        displayTargetSku(data.targetSku);

        // Populate generation filter from results (already capped to MAX_RESULTS
        // when the response was stored), then apply all filters
        populateGenFilter(data.alternatives);
        const filteredAlternatives = filterResults(data.alternatives);
        displayAlternatives(filteredAlternatives);
    }
    // Show cache timestamp if available
    const timestampEl = document.getElementById('cacheTimestamp');
    if (data.dataLastUpdated) {
        const d = new Date(data.dataLastUpdated);
        const formatted = d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
            + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
        timestampEl.textContent = `Data last refreshed: ${formatted}`;
    } else {
        timestampEl.textContent = '';
    }
    resultsSection.classList.remove('hidden');
    hideGettingStarted();

    // Show cross-region check bar and populate with regions (excluding current)
    initRegionCheckBar();
}

// Filter results by CPU vendor checkboxes
function filterResultsByVendor(alternatives) {
    const showIntel = document.getElementById('resultFilterIntel').checked;
    const showAMD = document.getElementById('resultFilterAMD').checked;
    const showARM = document.getElementById('resultFilterARM').checked;
    
    return alternatives.filter(alt => {
        if (alt.cpuVendor === 'Intel' && showIntel) return true;
        if (alt.cpuVendor === 'AMD' && showAMD) return true;
        if (alt.cpuVendor === 'ARM' && showARM) return true;
        return false;
    });
}

// Filter results by CPU generation checkboxes
function filterResultsByGeneration(alternatives) {
    const container = document.getElementById('genFilterOptions');
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    if (checkboxes.length === 0) return alternatives;
    
    const selectedGens = new Set();
    checkboxes.forEach(cb => {
        if (cb.checked) selectedGens.add(cb.value);
    });
    
    // If all are selected, no filtering needed
    if (selectedGens.size === checkboxes.length) return alternatives;
    
    return alternatives.filter(alt => {
        const gen = alt.cpuGeneration || 'Unknown';
        return selectedGens.has(gen);
    });
}

// Apply vendor, generation, and retirement filters
function filterResults(alternatives) {
    let filtered = filterResultsByGeneration(filterResultsByVendor(alternatives));
    // Apply retirement filter if checkbox exists and is checked
    const hideRetiring = document.getElementById('hideRetiringFilter');
    if (hideRetiring && hideRetiring.checked) {
        filtered = filtered.filter(alt => !alt.retirementStatus);
    }
    return filtered;
}

// Populate generation filter checkboxes from current results
function populateGenFilter(alternatives) {
    const section = document.getElementById('genFilterSection');
    const container = document.getElementById('genFilterOptions');
    
    // Get unique generations from vendor-filtered results
    const vendorFiltered = filterResultsByVendor(alternatives);
    const generations = new Map();
    vendorFiltered.forEach(alt => {
        const gen = alt.cpuGeneration || 'Unknown';
        generations.set(gen, (generations.get(gen) || 0) + 1);
    });
    
    if (generations.size === 0) {
        section.style.display = 'none';
        return;
    }
    section.style.display = '';
    
    // Sort: by vendor grouping (Intel gens, AMD gens, ARM gens, Unknown last)
    const sorted = [...generations.entries()].sort((a, b) => {
        if (a[0] === 'Unknown') return 1;
        if (b[0] === 'Unknown') return -1;
        return a[0].localeCompare(b[0]);
    });
    
    container.innerHTML = sorted.map(([gen, count]) => `
        <label class="checkbox-label gen-checkbox">
            <input type="checkbox" value="${gen}" checked>
            ${gen} <span class="gen-count">(${count})</span>
        </label>
    `).join('');
    
    // Add change listeners to each checkbox
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', updateGenFilter);
    });
}

// Handle generation filter change
function updateGenFilter() {
    if (!currentResults || !currentResults.alternatives) return;
    trackEvent('result_generation_filter_changed', {});
    displayAlternatives(filterResults(currentResults.alternatives));
}

// Handle Select All / Deselect All buttons
function genSelectAll() {
    document.getElementById('genFilterOptions').querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
    updateGenFilter();
}

function genDeselectAll() {
    document.getElementById('genFilterOptions').querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    updateGenFilter();
}

// Display Target SKU Info
function displayTargetSku(targetSku) {
    const cpuDisplay = targetSku.cpuVendor ? `${targetSku.cpuVendor} (${targetSku.architecture || 'x64'})` : 'N/A';
    const cpuPerfDisplay = targetSku.cpuGeneration && targetSku.cpuPerfScore
        ? `${targetSku.cpuGeneration} — score ${targetSku.cpuPerfScore}`
        : null;
    const pricingLabel = currentPricingModel === 'ri1year' ? '1yr RI'
        : currentPricingModel === 'ri3year' ? '3yr RI'
        : (currentPricingOS === 'windows' ? 'Windows' : 'Linux');
    const html = `
        <h3>Target SKU: ${targetSku.name}</h3>
        <div class="target-sku-grid">
            <div class="target-sku-item">
                <strong>CPU Vendor</strong>
                <span>${cpuDisplay}</span>
            </div>
            ${cpuPerfDisplay ? `
            <div class="target-sku-item">
                <strong>CPU Performance</strong>
                <span>${cpuPerfDisplay}</span>
            </div>
            ` : ''}
            <div class="target-sku-item">
                <strong>vCPUs</strong>
                <span>${targetSku.vCPUs || 'N/A'}</span>
            </div>
            <div class="target-sku-item">
                <strong>Memory</strong>
                <span>${targetSku.memoryGB ? targetSku.memoryGB + ' GB' : 'N/A'}</span>
            </div>
            ${targetSku.gpuCount ? `
            <div class="target-sku-item">
                <strong>GPUs</strong>
                <span>${targetSku.gpuCount} ${targetSku.gpuType || ''}</span>
            </div>
            ` : ''}
            <div class="target-sku-item">
                <strong>Hourly Cost (${pricingLabel})</strong>
                <span>${formatHourlyPriceSafe(targetSku.pricing)}</span>
            </div>
            <div class="target-sku-item">
                <strong>Monthly Cost (${pricingLabel})</strong>
                <span>${formatMonthlyPriceSafe(targetSku.pricing)}</span>
            </div>
            <div class="target-sku-item">
                <strong>Availability Zones</strong>
                <span>${targetSku.zones || 'N/A'}</span>
            </div>
            ${targetSku.networkBandwidthMbps ? `
            <div class="target-sku-item">
                <strong>Network Bandwidth</strong>
                <span>${formatBandwidth(targetSku.networkBandwidthMbps)}</span>
            </div>
            ` : ''}
        </div>
        <button type="button" id="whereCheapestBtn" class="where-cheapest-btn" onclick="showRegionPriceComparison('${targetSku.name}')">
            🌍 Where is this cheapest?
        </button>
    `;
    targetSkuInfo.innerHTML = html;

    // Show retirement warning banner if target SKU is retiring
    const retirementBanner = document.getElementById('retirementBanner');
    if (retirementBanner) {
        if (targetSku.retirementStatus) {
            const dateStr = targetSku.retirementDate ? new Date(targetSku.retirementDate + 'T00:00:00Z').toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }) : 'TBD';
            const statusClass = targetSku.retirementStatus === 'Retired' ? 'retirement-retired' : 'retirement-announced';
            const statusLabel = targetSku.retirementStatus === 'Retired' ? '❌ This VM SKU has been retired' : '⚠️ This VM SKU is announced for retirement';
            retirementBanner.className = `retirement-banner ${statusClass}`;
            retirementBanner.innerHTML = `
                <strong>${statusLabel}</strong> on ${dateStr}.
                ${targetSku.migrationGuideUrl ? `<a href="${targetSku.migrationGuideUrl}" target="_blank" rel="noopener">View Migration Guide →</a>` : ''}
            `;
            retirementBanner.classList.remove('hidden');
        } else {
            retirementBanner.classList.add('hidden');
        }
    }
}

// ---------------------------------------------------------------------------
// Cross-region price comparison (I-E: "Where is this cheapest?")
// ---------------------------------------------------------------------------
async function showRegionPriceComparison(skuName) {
    const modal = document.getElementById('regionPriceModal');
    const body = document.getElementById('regionPriceBody');
    const title = document.getElementById('regionPriceModalTitle');
    if (!modal || !body) return;

    const currency = document.getElementById('currencyCode')?.value || 'USD';
    const os = currentPricingOS === 'windows' ? 'windows' : 'linux';
    const osLabel = os === 'windows' ? 'Windows' : 'Linux';
    const currentRegion = document.getElementById('location')?.value || '';

    title.textContent = `Where is ${skuName} cheapest?`;
    body.innerHTML = '<div class="region-price-loading"><div class="spinner"></div><p>Fetching regional prices…</p></div>';
    modal.classList.remove('hidden');
    document.body.classList.add('modal-open');

    try {
        const url = `${API_BASE_URL}/compare_regions?skuName=${encodeURIComponent(skuName)}&currency=${encodeURIComponent(currency)}&os=${encodeURIComponent(os)}`;
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`Request failed (${response.status})`);
        }
        const data = await response.json();
        renderRegionPriceComparison(data, osLabel, currentRegion);
    } catch (err) {
        body.innerHTML = `<div class="region-price-error"><p>⚠️ Couldn't load regional pricing.</p><p class="region-price-error-detail">${escapeHtml(err.message)}</p></div>`;
    }
}

function renderRegionPriceComparison(data, osLabel, currentRegion) {
    const body = document.getElementById('regionPriceBody');
    if (!body) return;

    if (!data.regions || data.regions.length === 0) {
        body.innerHTML = `<div class="region-price-empty"><p>${escapeHtml(data.message || 'No pay-as-you-go pricing found for this SKU across regions.')}</p></div>`;
        return;
    }

    const currency = data.currency || 'USD';
    const cheapest = data.cheapest;
    const savings = data.maxMonthlySavings;

    const summary = `
        <div class="region-price-summary">
            <div class="region-price-summary-item">
                <span class="rp-label">Cheapest region</span>
                <span class="rp-value rp-cheapest">${escapeHtml(cheapest.location)} <small>(${escapeHtml(cheapest.region)})</small></span>
            </div>
            <div class="region-price-summary-item">
                <span class="rp-label">From</span>
                <span class="rp-value">${formatHourlyCurrency(cheapest.hourlyPrice, currency)}/hr · ${formatCurrency(cheapest.monthlyPrice, currency)}/mo</span>
            </div>
            <div class="region-price-summary-item">
                <span class="rp-label">Max monthly savings</span>
                <span class="rp-value rp-savings">${formatCurrency(savings, currency)}/mo</span>
            </div>
        </div>
        <p class="region-price-note">${osLabel} pay-as-you-go pricing across ${data.regionCount} region${data.regionCount === 1 ? '' : 's'}. Prices are guidance — validate before production decisions.</p>
    `;

    const rows = data.regions.map(r => {
        const isCheapest = r.region === cheapest.region;
        const isCurrent = currentRegion && r.region === currentRegion;
        const rowClass = [
            isCheapest ? 'rp-row-cheapest' : '',
            isCurrent ? 'rp-row-current' : ''
        ].filter(Boolean).join(' ');
        const badges = [
            isCheapest ? '<span class="rp-badge rp-badge-cheapest">Cheapest</span>' : '',
            isCurrent ? '<span class="rp-badge rp-badge-current">Current</span>' : ''
        ].filter(Boolean).join(' ');
        const deltaText = isCheapest
            ? '—'
            : `+${r.pctAboveCheapest}% · +${formatCurrency(r.monthlyVsCheapest, currency)}/mo`;
        return `
            <tr class="${rowClass}">
                <td class="rp-region">${escapeHtml(r.location)} <small>${escapeHtml(r.region)}</small> ${badges}</td>
                <td class="rp-hourly">${formatHourlyCurrency(r.hourlyPrice, currency)}</td>
                <td class="rp-monthly">${formatCurrency(r.monthlyPrice, currency)}</td>
                <td class="rp-delta">${deltaText}</td>
            </tr>
        `;
    }).join('');

    body.innerHTML = `
        ${summary}
        <div class="region-price-table-wrap">
            <table class="region-price-table">
                <thead>
                    <tr>
                        <th>Region</th>
                        <th>Hourly</th>
                        <th>Monthly</th>
                        <th>vs. cheapest</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

function closeRegionPriceModal() {
    const modal = document.getElementById('regionPriceModal');
    if (modal) modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
}

// ============================================================================
// Price history (I-G) — sparklines + modal chart. History is USD-only.
// ============================================================================
const historyCache = new Map(); // key `${region}|${sku}` -> { points, summary }

function fmtUsd(v, dp = 4) {
    if (v == null) return 'N/A';
    return '$' + Number(v).toFixed(dp);
}

// Map a numeric series to SVG polyline points within a viewBox.
function seriesToPoints(values, width, height, min, max, pad = 2) {
    const n = values.length;
    if (n === 0) return '';
    const span = (max - min) || 1;
    const stepX = n > 1 ? (width - pad * 2) / (n - 1) : 0;
    return values.map((v, i) => {
        const x = pad + i * stepX;
        const y = pad + (height - pad * 2) * (1 - (v - min) / span);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
}

// Build a compact sparkline SVG + %-change badge from a history series.
function renderSparklineInto(el, series) {
    const linux = (series?.points || []).map(p => p.hourlyLinux).filter(v => v != null);
    if (linux.length < 2) {
        el.innerHTML = '<span class="sparkline-empty" title="Price history is still building">— history</span>';
        el.classList.add('is-empty');
        return;
    }
    const min = Math.min(...linux), max = Math.max(...linux);
    const w = 68, h = 22;
    const pts = seriesToPoints(linux, w, h, min, max);
    const first = linux[0], last = linux[linux.length - 1];
    const pct = first ? ((last - first) / first * 100) : 0;
    const dir = pct < -0.5 ? 'down' : pct > 0.5 ? 'up' : 'flat';
    const stroke = dir === 'down' ? '#107c10' : dir === 'up' ? '#d13438' : '#8a8886';
    const arrow = dir === 'down' ? '↓' : dir === 'up' ? '↑' : '→';
    const badge = dir === 'flat'
        ? '<span class="sparkline-badge flat">±0%</span>'
        : `<span class="sparkline-badge ${dir}">${arrow}${Math.abs(pct).toFixed(0)}%</span>`;
    el.classList.remove('is-empty');
    el.innerHTML =
        `<svg class="sparkline-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">` +
        `<polyline fill="none" stroke="${stroke}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" points="${pts}"/></svg>` +
        badge;
}

// Batch-fetch history for the given SKUs and render sparklines into
// `.card-sparkline-wrap[data-sku]` elements found within `container`.
async function loadSparklines(skuNames, region, container) {
    const wraps = Array.from(container.querySelectorAll('.card-sparkline-wrap[data-sku]'));
    if (!wraps.length) return;

    const need = [];
    const seen = new Set();
    skuNames.forEach(name => {
        const key = `${region}|${name}`;
        if (!historyCache.has(key) && !seen.has(name)) { need.push(name); seen.add(name); }
    });

    if (need.length) {
        try {
            const url = `${API_BASE_URL}/history?location=${encodeURIComponent(region)}&skus=${encodeURIComponent(need.join(','))}`;
            const resp = await fetch(url);
            if (resp.ok) {
                const data = await resp.json();
                const series = data.series || {};
                need.forEach(name => historyCache.set(`${region}|${name}`, series[name] || { points: [], summary: null }));
            }
        } catch (e) {
            console.warn('Failed to load price-history sparklines:', e);
        }
    }

    wraps.forEach(el => {
        const name = el.dataset.sku;
        const series = historyCache.get(`${region}|${name}`);
        if (series) renderSparklineInto(el, series);
        else el.innerHTML = '';
    });
}

async function showPriceHistory(skuName) {
    const modal = document.getElementById('priceHistoryModal');
    const body = document.getElementById('priceHistoryBody');
    const title = document.getElementById('priceHistoryModalTitle');
    if (!modal || !body) return;

    const region = (currentMode === 'browse' ? gridRegionLoaded : currentResults?.location)
        || document.getElementById('location')?.value || '';
    title.textContent = `Price history — ${skuName}`;
    body.innerHTML = '<div class="region-price-loading"><div class="spinner"></div><p>Loading price history…</p></div>';
    modal.classList.remove('hidden');
    document.body.classList.add('modal-open');
    trackEvent('price_history_opened', { sku: skuName, region });

    try {
        let series = historyCache.get(`${region}|${skuName}`);
        // Always fetch the single-SKU series for the full (all-OS) point set
        const url = `${API_BASE_URL}/history?location=${encodeURIComponent(region)}&sku=${encodeURIComponent(skuName)}`;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
        const data = await resp.json();
        series = { points: data.points || [], summary: data.summary || null };
        historyCache.set(`${region}|${skuName}`, series);
        renderPriceHistory(series, skuName, region);
    } catch (err) {
        body.innerHTML = `<div class="region-price-error"><p>⚠️ Couldn't load price history.</p><p class="region-price-error-detail">${escapeHtml(err.message)}</p></div>`;
    }
}

function renderPriceHistory(series, skuName, region) {
    const body = document.getElementById('priceHistoryBody');
    if (!body) return;

    const points = series.points || [];
    const linux = points.map(p => p.hourlyLinux).filter(v => v != null);
    if (linux.length < 2) {
        body.innerHTML =
            `<div class="region-price-empty"><p>Price history is still building for this size.</p>` +
            `<p class="region-price-note">Daily snapshots are recorded only when a price changes, so a trend line will appear once at least two data points exist.</p></div>`;
        return;
    }

    const summary = series.summary || {};
    const pct = summary.pctChange ?? 0;
    const dir = pct < -0.5 ? 'down' : pct > 0.5 ? 'up' : 'flat';
    const trendClass = dir === 'down' ? 'rp-savings' : dir === 'up' ? 'delta-more' : '';

    const summaryHtml = `
        <div class="region-price-summary">
            <div class="region-price-summary-item">
                <span class="rp-label">Current (Linux)</span>
                <span class="rp-value">${fmtUsd(summary.last)}/hr</span>
            </div>
            <div class="region-price-summary-item">
                <span class="rp-label">Change over range</span>
                <span class="rp-value ${trendClass}">${pct > 0 ? '+' : ''}${pct.toFixed(1)}%</span>
            </div>
            <div class="region-price-summary-item">
                <span class="rp-label">Min / Max</span>
                <span class="rp-value">${fmtUsd(summary.min)} / ${fmtUsd(summary.max)}</span>
            </div>
        </div>`;

    const chart = buildHistoryChart(points);

    body.innerHTML =
        summaryHtml + chart +
        `<p class="region-price-note">Linux (blue), Windows (purple), Spot (orange) hourly pricing in <strong>USD</strong> for ${escapeHtml(region)}. ` +
        `Points are recorded when a price changes; lines carry the last value forward. Guidance only — validate before production decisions.</p>`;
}

// Build a multi-line SVG chart (Linux/Windows/Spot) over the date range.
function buildHistoryChart(points) {
    const W = 640, H = 220, padL = 54, padR = 16, padT = 16, padB = 34;
    const dates = points.map(p => p.date);
    const seriesDefs = [
        { key: 'hourlyLinux', color: '#0078d4', label: 'Linux' },
        { key: 'hourlyWindows', color: '#8661c5', label: 'Windows' },
        { key: 'hourlySpot', color: '#d97706', label: 'Spot' },
    ];

    const allVals = [];
    seriesDefs.forEach(s => points.forEach(p => { if (p[s.key] != null) allVals.push(p[s.key]); }));
    const min = Math.min(...allVals), max = Math.max(...allVals);
    const span = (max - min) || 1;
    const n = points.length;

    const xAt = i => padL + (n > 1 ? (W - padL - padR) * (i / (n - 1)) : 0);
    const yAt = v => padT + (H - padT - padB) * (1 - (v - min) / span);

    // Gridlines + y labels (min, mid, max)
    let grid = '';
    [max, (max + min) / 2, min].forEach(val => {
        const y = yAt(val);
        grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="#eee" stroke-width="1"/>`;
        grid += `<text x="${padL - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" class="hist-axis">${fmtUsd(val)}</text>`;
    });

    // Lines (carry last value forward across null points)
    let lines = '';
    let legend = '';
    seriesDefs.forEach(s => {
        let last = null;
        const pts = [];
        points.forEach((p, i) => {
            const v = p[s.key] != null ? p[s.key] : last;
            if (v == null) return;
            last = v;
            pts.push(`${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`);
        });
        if (pts.length >= 2) {
            lines += `<polyline fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" points="${pts.join(' ')}"/>`;
            legend += `<span class="hist-legend-item"><span class="hist-swatch" style="background:${s.color}"></span>${s.label}</span>`;
        }
    });

    const xFirst = `<text x="${padL}" y="${H - 12}" text-anchor="start" class="hist-axis">${escapeHtml(dates[0] || '')}</text>`;
    const xLast = `<text x="${W - padR}" y="${H - 12}" text-anchor="end" class="hist-axis">${escapeHtml(dates[dates.length - 1] || '')}</text>`;

    return `<div class="hist-chart-wrap">` +
        `<svg class="hist-chart" viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Price history chart">` +
        grid + lines + xFirst + xLast +
        `</svg><div class="hist-legend">${legend}</div></div>`;
}

function closePriceHistoryModal() {
    const modal = document.getElementById('priceHistoryModal');
    if (modal) modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
}

function escapeHtml(value) {
    const str = value === null || value === undefined ? '' : String(value);
    return str.replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

// Wire up modal close handlers once the DOM is ready.
(function initRegionPriceModal() {
    function bind() {
        const closeBtn = document.getElementById('regionPriceClose');
        const backdrop = document.getElementById('regionPriceBackdrop');
        if (closeBtn) closeBtn.addEventListener('click', closeRegionPriceModal);
        if (backdrop) backdrop.addEventListener('click', closeRegionPriceModal);

        const histClose = document.getElementById('priceHistoryClose');
        const histBackdrop = document.getElementById('priceHistoryBackdrop');
        if (histClose) histClose.addEventListener('click', closePriceHistoryModal);
        if (histBackdrop) histBackdrop.addEventListener('click', closePriceHistoryModal);

        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') { closeRegionPriceModal(); closePriceHistoryModal(); }
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();

// Wire up the "Use with AI" (MCP) modal: open/close, tabs, copy-to-clipboard.
(function initMcpModal() {
    function bind() {
        const modal = document.getElementById('mcpModal');
        if (!modal) return;
        const openBtn = document.getElementById('useWithAiBtn');
        const closeBtn = document.getElementById('mcpClose');
        const backdrop = document.getElementById('mcpBackdrop');

        const open = () => modal.classList.remove('hidden');
        const close = () => modal.classList.add('hidden');

        if (openBtn) openBtn.addEventListener('click', open);
        if (closeBtn) closeBtn.addEventListener('click', close);
        if (backdrop) backdrop.addEventListener('click', close);
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') close();
        });

        // Tab switching
        modal.querySelectorAll('.mcp-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.getAttribute('data-mcp-tab');
                modal.querySelectorAll('.mcp-tab').forEach(t => {
                    const active = t === tab;
                    t.classList.toggle('active', active);
                    t.setAttribute('aria-selected', active ? 'true' : 'false');
                });
                modal.querySelectorAll('.mcp-panel').forEach(p => {
                    p.classList.toggle('active', p.getAttribute('data-mcp-panel') === target);
                });
            });
        });

        // Copy-to-clipboard buttons
        modal.querySelectorAll('.mcp-copy-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const el = document.getElementById(btn.getAttribute('data-copy-target'));
                if (!el) return;
                const text = el.textContent;
                try {
                    await navigator.clipboard.writeText(text);
                } catch {
                    const ta = document.createElement('textarea');
                    ta.value = text;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    try { document.execCommand('copy'); } catch { /* ignore */ }
                    document.body.removeChild(ta);
                }
                const original = btn.textContent;
                btn.textContent = 'Copied!';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.textContent = original;
                    btn.classList.remove('copied');
                }, 1600);
            });
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();

// Format CPU performance display for table cells
function formatCpuPerf(sku) {
    const gen = sku.cpuGeneration;
    const score = sku.cpuPerfScore;
    const vendor = sku.cpuVendor || 'Intel';
    const arch = sku.architecture || 'x64';
    
    if (gen && score) {
        return `<span class="cpu-gen" title="${vendor} ${arch}">${gen}</span><span class="cpu-score">${score}</span>`;
    }
    // Fallback for GPU/specialty SKUs without CPU perf data
    return `<span class="cpu-gen">${vendor} (${arch})</span>`;
}

// Render retirement badge for a SKU
function renderRetirementBadge(alt) {
    if (!alt.retirementStatus) return '';
    if (alt.retirementStatus === 'Retired') {
        return '<span class="retirement-badge retired" title="This SKU has been retired">❌ Retired</span>';
    }
    const dateStr = alt.retirementDate ? new Date(alt.retirementDate + 'T00:00:00Z').toLocaleDateString('en-US', { year: 'numeric', month: 'short' }) : '';
    return `<span class="retirement-badge retiring" title="Retirement announced for ${dateStr}">⚠️ Retiring ${dateStr}</span>`;
}

// Get CPU performance direction indicator
function getCpuPerfIndicator(targetScore, altScore) {
    if (targetScore == null || altScore == null) {
        return { direction: 'unknown', icon: '', changed: false };
    }
    if (altScore > targetScore) {
        return { direction: 'faster', icon: '▲', changed: true };
    } else if (altScore < targetScore) {
        return { direction: 'slower', icon: '▼', changed: true };
    }
    return { direction: 'same', icon: '●', changed: false };
}

function renderCpuPerfIndicator(indicator) {
    if (!indicator.changed) {
        if (indicator.direction === 'unknown') return '';
        return `<span class="diff-indicator diff-same" title="CPU Perf: Same as target">●</span>`;
    }
    if (indicator.direction === 'faster') {
        return `<span class="diff-indicator diff-upgrade" title="CPU Perf: Faster than target">▲</span>`;
    }
    return `<span class="diff-indicator diff-downgrade" title="CPU Perf: Slower than target">▼</span>`;
}

// Calculate difference indicators from existing data
function calculateIndicators(targetSku, alternativeSku) {
    return {
        cpuPerf: getCpuPerfIndicator(targetSku.cpuPerfScore, alternativeSku.cpuPerfScore),
        vCPUs: getDirectionIndicator(targetSku.vCPUs, alternativeSku.vCPUs),
        memory: getDirectionIndicator(targetSku.memoryGB, alternativeSku.memoryGB),
        hourlyPrice: getPriceIndicator(
            getHourlyPrice(targetSku.pricing), 
            getHourlyPrice(alternativeSku.pricing)
        ),
        monthlyPrice: getPriceIndicator(
            getMonthlyPrice(targetSku.pricing), 
            getMonthlyPrice(alternativeSku.pricing)
        )
    };
}

function getDirectionIndicator(targetValue, altValue) {
    if (targetValue == null || altValue == null) {
        return { direction: 'unknown', icon: '', changed: false };
    }
    
    if (altValue > targetValue) {
        return { direction: 'upgrade', icon: '▲', changed: true };
    } else if (altValue < targetValue) {
        return { direction: 'downgrade', icon: '▼', changed: true };
    } else {
        return { direction: 'same', icon: '●', changed: false };
    }
}

function getPriceIndicator(targetPrice, altPrice) {
    if (targetPrice == null || altPrice == null) {
        return { direction: 'unknown', icon: '', changed: false };
    }
    
    if (altPrice > targetPrice) {
        // Higher price = bad (RED)
        return { direction: 'higher', icon: '▲', changed: true, negative: true };
    } else if (altPrice < targetPrice) {
        // Lower price = good (GREEN)
        return { direction: 'lower', icon: '▼', changed: true, positive: true };
    } else {
        return { direction: 'same', icon: '●', changed: false };
    }
}

function renderIndicator(indicator, fieldName) {
    if (!indicator.changed) {
        return `<span class="diff-indicator diff-same" title="${fieldName}: Same as target">●</span>`;
    }
    
    // Price has inverted logic (higher = bad, lower = good)
    const isPriceField = fieldName.toLowerCase().includes('price');
    
    if (isPriceField) {
        if (indicator.negative) {
            return `<span class="diff-indicator diff-negative" title="${fieldName}: Higher than target (worse)">▲</span>`;
        } else if (indicator.positive) {
            return `<span class="diff-indicator diff-positive" title="${fieldName}: Lower than target (better)">▼</span>`;
        }
    }
    
    // Normal fields (more = upgrade, less = downgrade)
    if (indicator.direction === 'upgrade') {
        return `<span class="diff-indicator diff-upgrade" title="${fieldName}: More than target">▲</span>`;
    } else if (indicator.direction === 'downgrade') {
        return `<span class="diff-indicator diff-downgrade" title="${fieldName}: Less than target">▼</span>`;
    }
    
    return '';
}

// Update the results caption ("closest N of M") and the weak-match note.
function updateResultsCaption(alternatives) {
    const caption = document.getElementById('resultsCaption');
    const note = document.getElementById('weakMatchNote');
    const shown = alternatives.length;
    // Total candidates found: prefer the API's pre-cap count, else fall back to the
    // length of the returned list (older API builds don't send totalMatches).
    const total = currentResults?.totalMatches ?? currentResults?.alternatives?.length ?? shown;

    if (caption) {
        if (shown === 0) {
            caption.textContent = '';
        } else {
            let text = `Showing the ${shown} closest match${shown === 1 ? '' : 'es'}`;
            if (typeof total === 'number' && total > shown) {
                text += ` of ${total} found`;
            }
            caption.textContent = text + '.';
        }
    }

    if (note) {
        const topScore = shown > 0 ? alternatives[0].similarityScore : null;
        if (topScore != null && topScore < STRONG_MATCH) {
            note.textContent = 'No strong matches for this SKU — these are the closest available alternatives. Check the match score on each card.';
            note.classList.remove('hidden');
        } else {
            note.textContent = '';
            note.classList.add('hidden');
        }
    }
}

// Display Alternatives as Cards
function displayAlternatives(alternatives) {
    // Bump render version + abort any in-flight expand-all so late fetches can't write into the rebuilt DOM
    resetExpansionState();
    renderedAlternatives = alternatives;
    resultsTableBody.innerHTML = '';

    // Update results count badge
    const countBadge = document.getElementById('resultsCount');
    if (countBadge) countBadge.textContent = alternatives.length;

    // Caption + weak-match note: communicate the "closest N" framing without a threshold control
    updateResultsCaption(alternatives);

    // Get target SKU for comparison
    const targetSku = currentResults?.targetSku;

    alternatives.forEach((alt, index) => {
        const scoreClass = alt.similarityScore >= 90 ? 'high' :
                          alt.similarityScore >= 85 ? 'mid' : 'low';
        const scorePercent = alt.similarityScore.toFixed(1);

        // Calculate price delta
        let deltaHtml = '';
        if (targetSku) {
            const targetPrice = getMonthlyPrice(targetSku.pricing);
            const altPrice = getMonthlyPrice(alt.pricing);
            if (targetPrice && altPrice && targetPrice > 0) {
                const pctDiff = ((altPrice - targetPrice) / targetPrice * 100).toFixed(0);
                if (pctDiff < -1) {
                    deltaHtml = `<span class="card-price-delta delta-save">${pctDiff}% ↓</span>`;
                } else if (pctDiff > 1) {
                    deltaHtml = `<span class="card-price-delta delta-more">+${pctDiff}% ↑</span>`;
                } else {
                    deltaHtml = `<span class="card-price-delta delta-same">same price</span>`;
                }
            }
        }

        // CPU info line
        const cpuInfo = alt.cpuVendor 
            ? `${alt.cpuVendor}${alt.cpuGeneration ? ' ' + alt.cpuGeneration : ''} (${alt.architecture || 'x64'})`
            : '';

        // Richer spec highlights (I-D): ACU chip + NVMe badge, surfaced on the card
        const caps = alt.capabilities || {};
        const acuChip = caps.acu
            ? `<div class="mini-spec" title="Azure Compute Unit — relative CPU performance"><div class="mini-spec-val">${caps.acu}</div><div class="mini-spec-lbl">ACU</div></div>`
            : '';
        const nvmeBadge = caps.nvme
            ? `<span class="feature-badge nvme-badge" title="Supports the NVMe disk interface">NVMe</span>`
            : '';

        // Region availability
        const regionAvail = renderRegionAvailCell(alt.name);

        // Build card
        const card = document.createElement('div');
        card.classList.add('result-card');
        card.dataset.index = index;
        card.dataset.skuName = alt.name;
        card.setAttribute('aria-expanded', 'false');

        card.innerHTML = `
            <div class="card-sku-info">
                <div class="card-sku-name">${alt.name} ${renderRetirementBadge(alt)}${nvmeBadge}</div>
                <div class="card-sku-cpu">${cpuInfo}</div>
                <div class="card-score-bar">
                    <div class="score-track"><div class="score-fill ${scoreClass}" style="width:${scorePercent}%"></div></div>
                    <span class="card-score-pct ${scoreClass}">${scorePercent}%</span>
                </div>
            </div>
            <div class="card-specs">
                <div class="mini-spec"><div class="mini-spec-val">${alt.vCPUs || '—'}</div><div class="mini-spec-lbl">vCPUs</div></div>
                <div class="mini-spec"><div class="mini-spec-val">${alt.memoryGB ? alt.memoryGB + 'GB' : '—'}</div><div class="mini-spec-lbl">Memory</div></div>
                ${acuChip}
                ${renderZonesChip(alt.zones, targetSku?.zones)}
                ${regionAvail}
            </div>
            <button type="button" class="card-cheapest-btn" title="Where is this cheapest?" aria-label="Where is ${alt.name} cheapest?" onclick="event.stopPropagation(); showRegionPriceComparison('${alt.name}')"><span class="card-cheapest-icon">🌍</span><span class="card-cheapest-label">Where is this Cheapest?</span></button>
            <div class="card-price">
                <div class="card-price-hourly">${formatHourlyPriceSafe(alt.pricing)}</div>
                <div class="card-price-monthly">${formatMonthlyPriceSafe(alt.pricing)}/mo</div>
                ${deltaHtml}
                <div class="card-sparkline-wrap" data-sku="${escapeHtml(alt.name)}" role="button" tabindex="0" title="View price history (USD)" aria-label="View price history for ${escapeHtml(alt.name)}" onclick="event.stopPropagation(); showPriceHistory('${alt.name}')"></div>
            </div>
            <svg class="card-chevron" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
        `;
        
        card.addEventListener('click', () => toggleDetails(index));
        resultsTableBody.appendChild(card);
        
        // Details expansion div
        const detailsDiv = document.createElement('div');
        detailsDiv.classList.add('details-row', 'hidden');
        detailsDiv.dataset.index = index;
        detailsDiv.dataset.skuName = alt.name;
        detailsDiv.innerHTML = `<div class="details-content"></div>`;
        resultsTableBody.appendChild(detailsDiv);
    });

    // Reset the expand/collapse-all button to reflect the freshly-rendered (all-collapsed) state
    updateExpandAllButton();

    // Kick off background prefetch for top 10 rows (Phase 3)
    if (targetSku) {
        const location = currentResults?.location || document.getElementById('location').value;
        prefetchTopDetails(alternatives, targetSku, location);
    }

    // Populate price-history sparklines for the rendered cards (I-G)
    const histLocation = currentResults?.location || document.getElementById('location')?.value;
    if (histLocation) {
        loadSparklines(alternatives.map(a => a.name), histLocation, resultsTableBody);
    }
}

// Resolve the alternative + target SKU for a rendered card index (render-scoped source of truth)
function getCardData(index) {
    const altSku = renderedAlternatives[index];
    if (!altSku) return null;
    const targetSku = currentResults?.targetSku || altSku;
    return { altSku, targetSku };
}

// De-duplicated details fetch shared across single-expand and expand-all
function getDetails(cacheKey, targetName, altName, location) {
    if (expandedDetailsCache.has(cacheKey)) return Promise.resolve(expandedDetailsCache.get(cacheKey));
    if (detailsInFlight.has(cacheKey)) return detailsInFlight.get(cacheKey);
    const p = fetchComparisonDetails(targetName, altName, location)
        .then(d => { expandedDetailsCache.set(cacheKey, d); detailsInFlight.delete(cacheKey); return d; })
        .catch(err => { detailsInFlight.delete(cacheKey); throw err; });
    detailsInFlight.set(cacheKey, p);
    return p;
}

// Write fetched content into a row only if it's still the same render and still expanded
function renderDetailsIfLive(index, altSku, targetSku, details, myVersion) {
    if (myVersion !== resultsRenderVersion) return;
    const row = document.querySelector(`.details-row[data-index="${index}"]`);
    if (!row || row.dataset.skuName !== altSku.name || row.classList.contains('hidden')) return;
    row.querySelector('.details-content').innerHTML = renderDetailedComparison(details, targetSku, altSku);
}

function renderErrorIfLive(index, altSku, myVersion) {
    if (myVersion !== resultsRenderVersion) return;
    const row = document.querySelector(`.details-row[data-index="${index}"]`);
    if (!row || row.dataset.skuName !== altSku.name || row.classList.contains('hidden')) return;
    row.querySelector('.details-content').innerHTML = '<div class="error"><p>❌ Failed to load details. Click row again to retry.</p></div>';
}

// Toggle a single details row (card click entry point)
function toggleDetails(index) {
    const detailsRow = document.querySelector(`.details-row[data-index="${index}"]`);
    if (!detailsRow) return;
    if (detailsRow.classList.contains('hidden')) {
        expandCard(index);
    } else {
        collapseCard(index);
    }
}

// Expand a single card: reveal the row, render from cache, or fetch with guards
async function expandCard(index) {
    const detailsRow = document.querySelector(`.details-row[data-index="${index}"]`);
    const card = document.querySelector(`.result-card[data-index="${index}"]`);
    const data = getCardData(index);
    if (!detailsRow || !card || !data) return;
    const { altSku, targetSku } = data;
    const myVersion = resultsRenderVersion;

    detailsRow.classList.remove('hidden');
    card.classList.add('expanded');
    card.setAttribute('aria-expanded', 'true');
    updateExpandAllButton();

    const detailsContent = detailsRow.querySelector('.details-content');
    const cacheKey = `${targetSku.name}_${altSku.name}`;
    if (expandedDetailsCache.has(cacheKey)) {
        detailsContent.innerHTML = renderDetailedComparison(expandedDetailsCache.get(cacheKey), targetSku, altSku);
        return;
    }
    detailsContent.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading detailed comparison...</p></div>';
    const location = currentResults?.location || document.getElementById('location').value;
    try {
        const details = await getDetails(cacheKey, targetSku.name, altSku.name, location);
        renderDetailsIfLive(index, altSku, targetSku, details, myVersion);
    } catch (error) {
        console.error('Failed to load details:', error);
        renderErrorIfLive(index, altSku, myVersion);
    }
}

// Collapse a single card
function collapseCard(index) {
    const detailsRow = document.querySelector(`.details-row[data-index="${index}"]`);
    const card = document.querySelector(`.result-card[data-index="${index}"]`);
    if (detailsRow) detailsRow.classList.add('hidden');
    if (card) {
        card.classList.remove('expanded');
        card.setAttribute('aria-expanded', 'false');
    }
    updateExpandAllButton();
}

// Expand every visible card: batch the visual reveal (rAF) + cap fetch concurrency to avoid a stampede
function expandAll() {
    const allCards = Array.from(resultsTableBody.querySelectorAll('.result-card'));
    if (!allCards.length) return;
    trackEvent('results_expand_all', { count: allCards.length });

    if (expandAllAbortController) expandAllAbortController.abort();
    expandAllAbortController = new AbortController();
    const signal = expandAllAbortController.signal;
    const myVersion = resultsRenderVersion;
    const location = currentResults?.location || document.getElementById('location').value;

    const collapsed = allCards.filter(card => {
        const r = document.querySelector(`.details-row[data-index="${card.dataset.index}"]`);
        return r && r.classList.contains('hidden');
    });

    const needFetch = [];
    const VISUAL_BATCH = 6; // reveal in small rAF batches to avoid a layout spike with many rows
    let vi = 0;

    function expandVisualBatch() {
        if (signal.aborted || myVersion !== resultsRenderVersion) return;
        collapsed.slice(vi, vi + VISUAL_BATCH).forEach(card => {
            const index = parseInt(card.dataset.index, 10);
            const data = getCardData(index);
            const detailsRow = document.querySelector(`.details-row[data-index="${index}"]`);
            if (!data || !detailsRow) return;
            const { altSku, targetSku } = data;
            detailsRow.classList.remove('hidden');
            card.classList.add('expanded');
            card.setAttribute('aria-expanded', 'true');
            const detailsContent = detailsRow.querySelector('.details-content');
            const cacheKey = `${targetSku.name}_${altSku.name}`;
            if (expandedDetailsCache.has(cacheKey)) {
                detailsContent.innerHTML = renderDetailedComparison(expandedDetailsCache.get(cacheKey), targetSku, altSku);
            } else {
                detailsContent.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading detailed comparison...</p></div>';
                needFetch.push({ index, altSku, targetSku, cacheKey });
            }
        });
        vi += VISUAL_BATCH;
        updateExpandAllButton();
        if (vi < collapsed.length) {
            requestAnimationFrame(expandVisualBatch);
        } else {
            drainFetchQueue();
        }
    }

    function drainFetchQueue() {
        const CONCURRENCY = 4;
        let qi = 0;
        async function worker() {
            while (!signal.aborted && myVersion === resultsRenderVersion && qi < needFetch.length) {
                const job = needFetch[qi++];
                try {
                    const details = await getDetails(job.cacheKey, job.targetSku.name, job.altSku.name, location);
                    if (signal.aborted || myVersion !== resultsRenderVersion) return;
                    renderDetailsIfLive(job.index, job.altSku, job.targetSku, details, myVersion);
                } catch (error) {
                    if (myVersion !== resultsRenderVersion) return;
                    renderErrorIfLive(job.index, job.altSku, myVersion);
                }
            }
        }
        for (let w = 0; w < Math.min(CONCURRENCY, needFetch.length); w++) worker();
    }

    requestAnimationFrame(expandVisualBatch);
}

// Collapse every expanded card and stop scheduling any pending expand-all fetches
function collapseAll() {
    if (expandAllAbortController) { expandAllAbortController.abort(); expandAllAbortController = null; }
    trackEvent('results_collapse_all', {});
    resultsTableBody.querySelectorAll('.result-card.expanded').forEach(card => {
        card.classList.remove('expanded');
        card.setAttribute('aria-expanded', 'false');
    });
    resultsTableBody.querySelectorAll('.details-row:not(.hidden)').forEach(row => row.classList.add('hidden'));
    updateExpandAllButton();
}

// Sync the global button label/state from the DOM (loading + error rows still count as expanded)
function updateExpandAllButton() {
    if (!expandAllBtn) return;
    const total = resultsTableBody.querySelectorAll('.result-card').length;
    const expanded = resultsTableBody.querySelectorAll('.result-card.expanded').length;
    const label = expandAllBtn.querySelector('.expand-all-label');
    expandAllBtn.disabled = total === 0;
    const allExpanded = total > 0 && expanded === total;
    expandAllBtn.setAttribute('aria-pressed', allExpanded ? 'true' : 'false');
    if (label) label.textContent = allExpanded ? 'Collapse all' : 'Expand all';
    expandAllBtn.title = allExpanded ? 'Collapse all result details' : 'Expand all result details';
}

// Reset expansion bookkeeping before a re-render so stale async writes are discarded
function resetExpansionState() {
    resultsRenderVersion++;
    if (expandAllAbortController) { expandAllAbortController.abort(); expandAllAbortController = null; }
}

// Fetch comparison details from API (Phase 2)
async function fetchComparisonDetails(targetName, altName, location) {
    const params = new URLSearchParams({
        target: targetName,
        alternative: altName,
        location: location,
        currency: document.getElementById('currencyCode')?.value || 'USD'
    });
    
    const response = await fetch(`${API_BASE_URL}/compare_details?${params}`);
    
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
}

// Fire a single prefetch for one alternative, storing in cache on success (Phase 3)
function prefetchOne(alt, targetSku, location, signal) {
    const cacheKey = `${targetSku.name}_${alt.name}`;
    if (expandedDetailsCache.has(cacheKey)) return;
    const params = new URLSearchParams({
        target: targetSku.name,
        alternative: alt.name,
        location: location,
        currency: document.getElementById('currencyCode')?.value || 'USD'
    });
    fetch(`${API_BASE_URL}/compare_details?${params}`, { signal })
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data && !signal.aborted) expandedDetailsCache.set(cacheKey, data); })
        .catch(() => {}); // fire-and-forget — errors silently swallowed
}

// Speculatively prefetch top 10 alternatives after results render (Phase 3)
// Top 3 fire immediately; rows 4-10 stagger in pairs after 200ms to avoid cold-start stampede.
function prefetchTopDetails(alternatives, targetSku, location) {
    if (prefetchAbortController) prefetchAbortController.abort();
    prefetchAbortController = new AbortController();
    const signal = prefetchAbortController.signal;

    const toPrefetch = alternatives.slice(0, 10);
    toPrefetch.slice(0, 3).forEach(alt => prefetchOne(alt, targetSku, location, signal));

    const deferred = toPrefetch.slice(3);
    setTimeout(() => {
        let i = 0;
        function next() {
            if (signal.aborted || i >= deferred.length) return;
            prefetchOne(deferred[i++], targetSku, location, signal);
            if (i < deferred.length) prefetchOne(deferred[i++], targetSku, location, signal);
            setTimeout(next, 50);
        }
        next();
    }, 200);
}

// Render detailed comparison (Phase 2)
function renderDetailedComparison(data, targetSku, altSku) {
    const diff = data.differences;
    
    // Derive CPU performance comparison from the SKU objects (not from backend diff)
    const cpuPerfHtml = renderCpuPerfComparison(targetSku, altSku);

    return `
        <div class="comparison-details">
            <h4>📊 Detailed Comparison: ${altSku.name} vs ${targetSku.name}</h4>
            
            <div class="details-grid">
                <!-- Compute Section -->
                <div class="details-section">
                    <h5>Compute</h5>
                    ${cpuPerfHtml}
                    ${renderNumericDiff('vCPUs', diff.compute.vCPUs)}
                    ${renderNumericDiff('Memory', diff.compute.memory)}
                    ${(diff.compute.acu && diff.compute.acu.target > 0 && diff.compute.acu.alternative > 0) ? renderNumericDiff('ACU', diff.compute.acu) : renderAcuUnavailable()}
                    ${diff.compute.vCPUsPerCore && diff.compute.vCPUsPerCore.target != null ? renderNumericDiff('vCPUs / Core', diff.compute.vCPUsPerCore) : ''}
                    ${renderBooleanDiff(diff.compute.hyperVGen2)}
                    ${diff.compute.trustedLaunch ? renderBooleanDiff(diff.compute.trustedLaunch) : ''}
                    ${diff.compute.confidentialComputing ? renderBooleanDiff(diff.compute.confidentialComputing) : ''}
                </div>
                
                <!-- Storage Section -->
                <div class="details-section">
                    <h5>Storage</h5>
                    ${renderNumericDiff('Max Data Disks', diff.storage.maxDataDisks)}
                    ${renderNumericDiff('Uncached IOPS', diff.storage.uncachedIOPS)}
                    ${renderNumericDiff('Uncached Throughput', diff.storage.uncachedThroughput)}
                    ${renderNumericDiff('OS VHD Size', diff.storage.osVhdSizeMB)}
                    ${renderBooleanDiff(diff.storage.premiumIO)}
                    ${renderBooleanDiff(diff.storage.ephemeralOSDisk)}
                    ${renderBooleanDiff(diff.storage.nvmeSupport)}
                </div>
                
                <!-- Network Section -->
                <div class="details-section">
                    <h5>Network</h5>
                    ${diff.network.networkBandwidthMbps && diff.network.networkBandwidthMbps.target != null ? renderNumericDiff('Max Bandwidth', diff.network.networkBandwidthMbps) : ''}
                    ${renderNumericDiff('Max NICs', diff.network.maxNics)}
                    ${renderBooleanDiff(diff.network.acceleratedNetworking)}
                    ${diff.network.rdma ? renderBooleanDiff(diff.network.rdma) : ''}
                </div>
                
                <!-- Cost Section -->
                <div class="details-section">
                    <h5>Cost Analysis</h5>
                    ${renderPricingSection(diff.pricing)}
                </div>
            </div>
            
            <!-- Features Section -->
            ${renderFeatures(diff.features)}
        </div>
    `;
}

// Render CPU performance comparison for the detailed expand view
function renderCpuPerfComparison(targetSku, altSku) {
    const tScore = targetSku.cpuPerfScore;
    const aScore = altSku.cpuPerfScore;
    const tGen = targetSku.cpuGeneration;
    const aGen = altSku.cpuGeneration;

    if (!tScore && !aScore) return '';

    if (tScore && aScore) {
        const delta = aScore - tScore;
        const pct = tScore > 0 ? ((delta / tScore) * 100).toFixed(1) : '0.0';
        const sign = delta > 0 ? '+' : '';
        let className, icon, label;
        if (delta > 0) {
            className = 'diff-item upgrade';
            icon = '▲';
            label = 'faster';
        } else if (delta < 0) {
            className = 'diff-item downgrade';
            icon = '▼';
            label = 'slower';
        } else {
            className = 'diff-item same';
            icon = '●';
            label = 'same';
        }
        return `<div class="${className}">${icon} CPU Perf: ${tGen || 'Unknown'} (${tScore}) → ${aGen || 'Unknown'} (${aScore}) <span class="diff-pct">${sign}${pct}% ${label}</span></div>`;
    }

    // Only one side has data
    if (aScore) {
        return `<div class="diff-item same">● CPU Perf: ${aGen || 'Unknown'} (score: ${aScore})</div>`;
    }
    return `<div class="diff-item same">● CPU Perf: Target ${tGen || 'Unknown'} (score: ${tScore}) — alternative: N/A</div>`;
}

function renderAcuUnavailable() {
    return `<div class="diff-item same acu-unavailable" title="Azure only publishes ACU (Azure Compute Unit) values for certain VM series — mostly older/mid generations. Many newer sizes (e.g. v6/v7, Bsv2, Dsv5/Esv5) do not publish an ACU, so a comparison isn't shown when either size is missing it.">● ACU: not published by Azure for at least one of these sizes</div>`;
}

function renderNumericDiff(label, diff) {
    if (!diff.changed) {
        return `<div class="diff-item same">● ${label}: ${diff.alternative} ${diff.unit} (same)</div>`;
    }
    
    const icon = diff.direction === 'upgrade' ? '▲' : '▼';
    const className = diff.direction === 'upgrade' ? 'diff-item upgrade' : 'diff-item downgrade';
    const sign = diff.delta > 0 ? '+' : '';
    const percent = diff.percentChange ? ` (${sign}${diff.percentChange}%)` : '';
    
    return `
        <div class="${className}">
            ${icon} ${label}: ${diff.target} → ${diff.alternative} ${diff.unit}
            <span class="delta">${sign}${diff.delta} ${diff.unit}${percent}</span>
        </div>
    `;
}

function renderPriceDiff(label, diff) {
    if (!diff) return `<div class="diff-item same">● ${label}: N/A</div>`;
    if (!diff.changed) {
        return `<div class="diff-item same">● ${label}: ${diff.currency} ${diff.alternative} (same)</div>`;
    }
    
    const icon = diff.isNegative ? '⚠️' : '✅';
    const className = diff.isNegative ? 'diff-item negative' : 'diff-item positive';
    const sign = diff.delta > 0 ? '+' : '';
    const percent = diff.percentChange ? ` (${sign}${diff.percentChange}%)` : '';
    
    return `
        <div class="${className}">
            ${icon} ${label}: ${diff.currency} ${diff.target.toFixed(2)} → ${diff.currency} ${diff.alternative.toFixed(2)}
            <span class="delta">${sign}${diff.currency} ${Math.abs(diff.delta).toFixed(2)}${percent}</span>
        </div>
    `;
}

function renderPricingSection(pricing) {
    if (!pricing) return '<div class="diff-item same">● Pricing data unavailable</div>';

    let hourly, monthly, modelLabel;
    const isWindows = currentPricingOS === 'windows';
    const osLabel = isWindows ? 'Windows' : 'Linux';

    if (currentPricingModel === 'spot' && pricing.spot) {
        hourly = pricing.spot.hourly;
        monthly = pricing.spot.monthly;
        modelLabel = 'Spot (Linux)';
    } else if (currentPricingModel === 'ri1year' && pricing.ri1Year) {
        hourly = isWindows ? (pricing.ri1YearWindows?.hourly ?? pricing.ri1Year.hourly) : pricing.ri1Year.hourly;
        monthly = isWindows ? (pricing.ri1YearWindows?.monthly ?? pricing.ri1Year.monthly) : pricing.ri1Year.monthly;
        modelLabel = `1-Year RI (${osLabel})`;
    } else if (currentPricingModel === 'ri3year' && pricing.ri3Year) {
        hourly = isWindows ? (pricing.ri3YearWindows?.hourly ?? pricing.ri3Year.hourly) : pricing.ri3Year.hourly;
        monthly = isWindows ? (pricing.ri3YearWindows?.monthly ?? pricing.ri3Year.monthly) : pricing.ri3Year.monthly;
        modelLabel = `3-Year RI (${osLabel})`;
    } else if (isWindows && pricing.hourlyWindows) {
        hourly = pricing.hourlyWindows;
        monthly = pricing.monthlyWindows;
        modelLabel = `Pay as you go (${osLabel})`;
    } else {
        hourly = pricing.hourly;
        monthly = pricing.monthly;
        modelLabel = `Pay as you go (${osLabel})`;
    }

    let html = `<div class="diff-item same" style="font-size:0.85em;opacity:0.7">Pricing model: ${modelLabel}</div>`;
    html += renderPriceDiff('Hourly Price', hourly);
    html += renderPriceDiff('Monthly Price', monthly);
    html += renderEfficiency(pricing.efficiency);
    return html;
}

function renderBooleanDiff(diff) {
    const icon = diff.changed ? (diff.direction === 'added' ? '✅' : '❌') : '●';
    const className = diff.changed ? (diff.direction === 'added' ? 'diff-item upgrade' : 'diff-item downgrade') : 'diff-item same';
    const text = diff.changed ? 
        `${diff.target ? 'Yes' : 'No'} → ${diff.alternative ? 'Yes' : 'No'}` :
        `${diff.alternative ? 'Yes' : 'No'} (same)`;
    
    return `<div class="${className}">${icon} ${diff.feature}: ${text}</div>`;
}

function renderEfficiency(efficiency) {
    let html = '';
    
    if (efficiency.costPerVCPU) {
        const icon = efficiency.costPerVCPU.betterEfficiency ? '✅' : '⚠️';
        const cls = efficiency.costPerVCPU.betterEfficiency ? 'diff-item positive' : 'diff-item negative';
        html += `
            <div class="${cls}">
                ${icon} Cost per vCPU: $${efficiency.costPerVCPU.alternative.toFixed(4)}
                (${efficiency.costPerVCPU.betterEfficiency ? 'better' : 'worse'} efficiency)
            </div>
        `;
    }
    
    if (efficiency.costPerGB) {
        const icon = efficiency.costPerGB.betterEfficiency ? '✅' : '⚠️';
        const cls = efficiency.costPerGB.betterEfficiency ? 'diff-item positive' : 'diff-item negative';
        html += `
            <div class="${cls}">
                ${icon} Cost per GB: $${efficiency.costPerGB.alternative.toFixed(4)}
                (${efficiency.costPerGB.betterEfficiency ? 'better' : 'worse'} efficiency)
            </div>
        `;
    }
    
    return html;
}

function renderFeatures(features) {
    if (features.added.length === 0 && features.removed.length === 0) {
        return '<div class="features-diff"><div class="diff-item same">● All features unchanged</div></div>';
    }
    
    let html = '<div class="features-diff">';
    
    if (features.added.length > 0) {
        html += `<div class="features-added">✅ <strong>Added:</strong> ${features.added.join(', ')}</div>`;
    }
    
    if (features.removed.length > 0) {
        html += `<div class="features-removed">❌ <strong>Removed:</strong> ${features.removed.join(', ')}</div>`;
    }
    
    html += '</div>';
    return html;
}

// Format Currency
function formatCurrency(amount, currency = 'USD') {
    if (amount === null || amount === undefined) return 'N/A';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(amount);
}

// Use for hourly prices — 4 decimal places to match Azure Pricing Calculator precision
function formatHourlyCurrency(amount, currency = 'USD') {
    if (amount === null || amount === undefined) return 'N/A';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 4,
        maximumFractionDigits: 4
    }).format(amount);
}

function formatBandwidth(mbps) {
    if (mbps === null || mbps === undefined || mbps === 0) return 'N/A';
    if (mbps >= 1000) {
        return `${(mbps / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })} Gbps`;
    }
    return `${mbps.toLocaleString('en-US')} Mbps`;
}

function escapeCsvCell(value) {
    const stringValue = value === null || value === undefined ? '' : String(value);
    return `"${stringValue.replace(/"/g, '""')}"`;
}

function toNumericOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function formatCapabilityValue(value, type = 'string', precision = 0) {
    if (value === null || value === undefined || value === '') return 'N/A';

    if (type === 'boolean') {
        return value ? 'Yes' : 'No';
    }

    if (type === 'number') {
        const numeric = toNumericOrNull(value);
        if (numeric === null) return 'N/A';
        return precision > 0 ? numeric.toFixed(precision) : String(Math.round(numeric));
    }

    return String(value);
}

function formatDeltaValue(targetValue, altValue, precision = 0) {
    const target = toNumericOrNull(targetValue);
    const alternative = toNumericOrNull(altValue);
    if (target === null || alternative === null) return 'N/A';
    const delta = alternative - target;
    return precision > 0 ? delta.toFixed(precision) : String(Math.round(delta));
}

// Build a shared comparison export model used by both CSV and XLSX export so the
// two formats never drift. Returns the metadata, split summary/spec tables, and a
// combined flat table (headers + rows) that matches the historical CSV layout.
function buildComparisonExportModel() {
    const discount = getDiscountMultiplier();
    const discountNote = discount < 1.0 ? ` (${((1 - discount) * 100).toFixed(1)}% discount applied)` : '';

    const targetSku = currentResults.targetSku || {};
    const exportLocation = currentResults.searchParameters?.location || currentResults.location || document.getElementById('location')?.value || 'N/A';

    const capabilityColumns = [
        { key: 'vCPUs', label: 'vCPUs', type: 'number', precision: 0 },
        { key: 'memoryGB', label: 'Memory (GB)', type: 'number', precision: 2 },
        { key: 'gpuCount', label: 'GPU Count', type: 'number', precision: 0 },
        { key: 'gpuType', label: 'GPU Type', type: 'string' },
        { key: 'maxDataDiskCount', label: 'Max Data Disks', type: 'number', precision: 0 },
        { key: 'maxNics', label: 'Max NICs', type: 'number', precision: 0 },
        { key: 'uncachedDiskIOPS', label: 'Uncached Disk IOPS', type: 'number', precision: 0 },
        { key: 'uncachedDiskBytesPerSecond', label: 'Uncached Disk Throughput (Bytes/s)', type: 'number', precision: 0 },
        { key: 'maxWriteAcceleratorDisks', label: 'Max Write Accelerator Disks', type: 'number', precision: 0 },
        { key: 'osVhdSizeMB', label: 'OS VHD Size (MB)', type: 'number', precision: 0 },
        { key: 'hyperVGenerations', label: 'Hyper-V Generations', type: 'string' },
        { key: 'premiumIO', label: 'Premium IO', type: 'boolean' },
        { key: 'ephemeralOSDisk', label: 'Ephemeral OS Disk', type: 'boolean' },
        { key: 'acceleratedNetworking', label: 'Accelerated Networking', type: 'boolean' },
        { key: 'encryptionAtHost', label: 'Encryption at Host', type: 'boolean' },
        { key: 'nvme', label: 'NVMe', type: 'boolean' },
        { key: 'acu', label: 'ACU', type: 'number', precision: 0 },
        { key: 'vCPUsPerCore', label: 'vCPUs per Core', type: 'number', precision: 0 },
        { key: 'diskControllerTypes', label: 'Disk Controller Types', type: 'string' },
        { key: 'rdmaEnabled', label: 'RDMA Enabled', type: 'boolean' },
        { key: 'confidentialComputingType', label: 'Confidential Computing Type', type: 'string' },
        { key: 'trustedLaunch', label: 'Trusted Launch', type: 'boolean' }
    ];

    const summaryHeaders = [
        'Rank',
        'Location',
        'Target SKU',
        'SKU Name',
        'Similarity Score (%)',
        'CPU Vendor',
        'Architecture',
        'CPU Generation',
        'CPU Perf Score',
        'Target CPU Perf Score',
        'CPU Perf Delta (%)',
        `Hourly Cost Linux${discountNote}`,
        `Monthly Cost Linux${discountNote}`,
        `Hourly Cost Windows${discountNote}`,
        `Monthly Cost Windows${discountNote}`,
        `Spot Hourly (Linux)${discountNote}`,
        `Spot Monthly (Linux)${discountNote}`,
        `1yr RI Hourly (Linux)${discountNote}`,
        `1yr RI Monthly (Linux)${discountNote}`,
        `1yr RI Hourly (Windows)${discountNote}`,
        `1yr RI Monthly (Windows)${discountNote}`,
        `3yr RI Hourly (Linux)${discountNote}`,
        `3yr RI Monthly (Linux)${discountNote}`,
        `3yr RI Hourly (Windows)${discountNote}`,
        `3yr RI Monthly (Windows)${discountNote}`,
        'Currency',
        'Availability Zones'
    ];

    // Add region availability column if active
    if (regionAvailabilityData) {
        summaryHeaders.push(`Available in ${regionAvailabilityData.region}`);
    }

    const capabilityHeaders = capabilityColumns.map(col => col.label);

    const headers = [...summaryHeaders, ...capabilityHeaders];

    const summaryRows = [];
    const capabilityRows = [];
    const combinedRows = currentResults.alternatives.map((alt, index) => {
        const altCaps = alt.capabilities || {};
        const summaryRow = [
            index + 1,
            exportLocation,
            targetSku.name || 'N/A',
            alt.name || 'N/A',
            alt.similarityScore != null ? alt.similarityScore.toFixed(1) : 'N/A',
            alt.cpuVendor || 'N/A',
            alt.architecture || 'N/A',
            alt.cpuGeneration || 'N/A',
            alt.cpuPerfScore != null ? alt.cpuPerfScore : 'N/A',
            targetSku.cpuPerfScore != null ? targetSku.cpuPerfScore : 'N/A',
            (alt.cpuPerfScore != null && targetSku.cpuPerfScore > 0)
                ? (((alt.cpuPerfScore - targetSku.cpuPerfScore) / targetSku.cpuPerfScore) * 100).toFixed(1)
                : 'N/A',
            alt.pricing ? (alt.pricing.hourlyPrice * discount).toFixed(4) : 'N/A',
            alt.pricing ? (alt.pricing.monthlyPrice * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.hourlyPriceWindows != null ? (alt.pricing.hourlyPriceWindows * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.monthlyPriceWindows != null ? (alt.pricing.monthlyPriceWindows * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.spotHourly != null ? (alt.pricing.spotHourly * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.spotMonthly != null ? (alt.pricing.spotMonthly * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri1YearHourly != null ? (alt.pricing.ri1YearHourly * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri1YearMonthly != null ? (alt.pricing.ri1YearMonthly * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri1YearHourlyWindows != null ? (alt.pricing.ri1YearHourlyWindows * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri1YearMonthlyWindows != null ? (alt.pricing.ri1YearMonthlyWindows * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri3YearHourly != null ? (alt.pricing.ri3YearHourly * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri3YearMonthly != null ? (alt.pricing.ri3YearMonthly * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri3YearHourlyWindows != null ? (alt.pricing.ri3YearHourlyWindows * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri3YearMonthlyWindows != null ? (alt.pricing.ri3YearMonthlyWindows * discount).toFixed(2) : 'N/A',
            alt.pricing?.currency || 'N/A',
            alt.zones || 'N/A'
        ];

        // Add region availability if active
        if (regionAvailabilityData) {
            const avail = regionAvailabilityData.availability[alt.name];
            summaryRow.push(avail === true ? 'Yes' : avail === false ? 'No' : 'N/A');
        }

        const capabilityRow = capabilityColumns.flatMap(col => {
            const altValue = altCaps[col.key];
            return formatCapabilityValue(altValue, col.type, col.precision ?? 0);
        });

        summaryRows.push(summaryRow);
        capabilityRows.push(capabilityRow);
        return [...summaryRow, ...capabilityRow];
    });

    const currency = currentResults.alternatives[0]?.pricing?.currency || document.getElementById('currencyCode')?.value || 'N/A';
    const pricingOs = (typeof currentPricingOS !== 'undefined' && currentPricingOS) ? currentPricingOS : 'linux';
    const meta = [
        ['Azure VM SKU Comparison', ''],
        ['Generated', new Date().toISOString()],
        ['Target SKU', targetSku.name || 'N/A'],
        ['Location', exportLocation],
        ['Currency', currency],
        ['Displayed pricing OS', pricingOs.charAt(0).toUpperCase() + pricingOs.slice(1)],
        ['Discount applied', discount < 1.0 ? `${((1 - discount) * 100).toFixed(1)}%` : 'None'],
        ['Alternatives', String(currentResults.alternatives.length)],
        ['Region availability column', regionAvailabilityData ? regionAvailabilityData.region : 'N/A']
    ];

    return {
        targetSku,
        exportLocation,
        summaryHeaders,
        capabilityHeaders,
        headers,
        summaryRows,
        capabilityRows,
        combinedRows,
        meta
    };
}

// Coerce a formatted string cell into a real number for Excel where it makes sense,
// so prices/specs are numeric (sortable/summable). Non-numeric text is left as-is.
function coerceExportCell(value) {
    if (typeof value === 'number') return value;
    if (value === null || value === undefined || value === '' || value === 'N/A') return value;
    const trimmed = String(value).trim();
    if (trimmed === '' || !/^-?\d+(\.\d+)?$/.test(trimmed)) return value;
    const numeric = Number(trimmed);
    return Number.isFinite(numeric) ? numeric : value;
}

// Export to CSV
function exportToCSV() {
    if (!currentResults || !currentResults.alternatives || currentResults.alternatives.length === 0) {
        trackEvent('export_csv_failed', {
            reason: 'no_data'
        });
        showError('No data to export');
        return;
    }

    const model = buildComparisonExportModel();

    const csvContent = [
        model.headers.map(escapeCsvCell).join(','),
        ...model.combinedRows.map(row => row.map(escapeCsvCell).join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    const targetName = model.targetSku.name || 'target-sku';
    link.setAttribute('href', url);
    link.setAttribute('download', `azure-vm-comparison-${targetName}-${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    trackEvent('export_csv_clicked', {
        location: model.exportLocation,
        targetSku: targetName
    }, {
        exportedRows: model.combinedRows.length
    });
}

// Export to a formatted multi-sheet Excel workbook (Comparison Info + Summary + Specifications)
function exportToXLSX() {
    if (!currentResults || !currentResults.alternatives || currentResults.alternatives.length === 0) {
        trackEvent('export_xlsx_failed', { reason: 'no_data' });
        showError('No data to export');
        return;
    }

    if (typeof XLSX === 'undefined') {
        trackEvent('export_xlsx_failed', { reason: 'library_unavailable' });
        showError('Excel export is unavailable right now. Please try CSV, or reload the page and retry.');
        return;
    }

    const model = buildComparisonExportModel();
    const colWidth = header => ({ wch: Math.min(Math.max(String(header).length + 2, 10), 44) });

    const wb = XLSX.utils.book_new();

    // Sheet 1: Comparison Info (metadata)
    const infoWs = XLSX.utils.aoa_to_sheet(model.meta);
    infoWs['!cols'] = [{ wch: 26 }, { wch: 44 }];
    XLSX.utils.book_append_sheet(wb, infoWs, 'Comparison Info');

    // Sheet 2: Summary (ranking + pricing)
    const summaryAoa = [model.summaryHeaders, ...model.summaryRows.map(r => r.map(coerceExportCell))];
    const summaryWs = XLSX.utils.aoa_to_sheet(summaryAoa);
    summaryWs['!cols'] = model.summaryHeaders.map(colWidth);
    summaryWs['!freeze'] = { xSplit: 0, ySplit: 1 };
    XLSX.utils.book_append_sheet(wb, summaryWs, 'Summary');

    // Sheet 3: Specifications (per-SKU capabilities, keyed by Rank + SKU Name)
    const specHeaders = ['Rank', 'SKU Name', ...model.capabilityHeaders];
    const specAoa = [
        specHeaders,
        ...model.capabilityRows.map((capRow, i) => [i + 1, model.summaryRows[i][3], ...capRow.map(coerceExportCell)])
    ];
    const specWs = XLSX.utils.aoa_to_sheet(specAoa);
    specWs['!cols'] = specHeaders.map(colWidth);
    specWs['!freeze'] = { xSplit: 0, ySplit: 1 };
    XLSX.utils.book_append_sheet(wb, specWs, 'Specifications');

    const targetName = model.targetSku.name || 'target-sku';
    const filename = `azure-vm-comparison-${targetName}-${new Date().toISOString().split('T')[0]}.xlsx`;
    XLSX.writeFile(wb, filename);

    trackEvent('export_xlsx_clicked', {
        location: model.exportLocation,
        targetSku: targetName
    }, {
        exportedRows: model.combinedRows.length
    });
}

// UI Helper Functions
function showLoading() {
    loadingOverlay.classList.remove('hidden');
    hideGettingStarted();
    compareBtn.disabled = true;
}

function hideLoading() {
    loadingOverlay.classList.add('hidden');
    updateOnboardingState();
}

function showError(message) {
    errorMessage.textContent = message;
    errorSection.classList.remove('hidden');
}

function hideError() {
    errorSection.classList.add('hidden');
}

function hideResults() {
    resultsSection.classList.add('hidden');
}

// ============================================================================
// Cross-Region Availability Check
// ============================================================================

function initRegionCheckBar() {
    const bar = document.getElementById('regionCheckBar');
    const select = document.getElementById('checkRegionSelect');
    const currentRegion = document.getElementById('location').value;

    // Use pre-captured region list (Choices.js modifies original <select> options)
    const options = ALL_REGIONS.filter(r => r.value !== currentRegion);

    select.innerHTML = '<option value="">— Select a region —</option>' +
        options.map(opt => `<option value="${opt.value}">${opt.label}</option>`).join('');

    // Show the bar
    bar.classList.remove('hidden');

    // Wire up change handler (remove old listener by replacing element)
    const newSelect = select.cloneNode(true);
    select.parentNode.replaceChild(newSelect, select);
    newSelect.addEventListener('change', handleRegionCheck);

    // Wire up clear button
    const clearBtn = document.getElementById('regionCheckClear');
    const newClear = clearBtn.cloneNode(true);
    clearBtn.parentNode.replaceChild(newClear, clearBtn);
    newClear.addEventListener('click', clearRegionCheck);
}

function clearRegionCheck() {
    // Abort in-flight request
    if (regionCheckAbortController) {
        regionCheckAbortController.abort();
        regionCheckAbortController = null;
    }
    regionAvailabilityData = null;

    // Reset UI
    const select = document.getElementById('checkRegionSelect');
    if (select) select.value = '';
    const summary = document.getElementById('regionCheckSummary');
    if (summary) summary.textContent = '';
    const clearBtn = document.getElementById('regionCheckClear');
    if (clearBtn) clearBtn.classList.add('hidden');

    // Hide availability column
    const thCol = document.getElementById('thRegionAvail');
    if (thCol) thCol.classList.add('hidden');

    // Re-render results without the column
    if (currentResults && currentResults.alternatives) {
        const filteredAlternatives = filterResults(currentResults.alternatives);
        displayAlternatives(filteredAlternatives);
    }
}

async function handleRegionCheck(e) {
    const region = e.target.value;
    if (!region) {
        clearRegionCheck();
        return;
    }

    // Abort previous in-flight request
    if (regionCheckAbortController) {
        regionCheckAbortController.abort();
    }
    regionCheckAbortController = new AbortController();
    const signal = regionCheckAbortController.signal;

    // Gather SKU names from current results (including target)
    if (!currentResults || !currentResults.alternatives) return;

    const skuNames = currentResults.alternatives.map(a => a.name);
    if (currentResults.targetSku && currentResults.targetSku.name) {
        skuNames.unshift(currentResults.targetSku.name);
    }

    // Show loading state
    const summary = document.getElementById('regionCheckSummary');
    summary.textContent = 'Checking availability...';
    const clearBtn = document.getElementById('regionCheckClear');
    clearBtn.classList.remove('hidden');

    // Store region label for chip display (no visible header in card layout)
    const thCol = document.getElementById('thRegionAvail');
    const regionLabel = e.target.options[e.target.selectedIndex]?.textContent || region;
    if (thCol) {
        thCol.textContent = regionLabel;
    }

    try {
        // Set a 30-second timeout for the region check
        const timeoutId = setTimeout(() => regionCheckAbortController.abort(), 30000);

        const response = await fetch(`${API_BASE_URL}/check_region_availability`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ skuNames, region }),
            signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.error || `HTTP ${response.status}`);
        }

        const data = await response.json();
        regionAvailabilityData = data;

        // Update summary
        summary.textContent = `${data.availableCount} of ${data.totalChecked} available in ${regionLabel}`;

        // Re-render alternatives to include availability column
        const filteredAlternatives = filterResults(currentResults.alternatives);
        displayAlternatives(filteredAlternatives);

        trackEvent('region_check_completed', {
            sourceRegion: document.getElementById('location').value,
            checkRegion: region
        }, {
            availableCount: data.availableCount,
            totalChecked: data.totalChecked
        });

    } catch (err) {
        if (err.name === 'AbortError') {
            // Could be user-initiated or timeout
            if (summary.textContent === 'Checking availability...') {
                summary.textContent = 'Timed out — try again';
            }
            return;
        }
        console.error('Region availability check failed:', err);
        summary.textContent = 'Check failed — try again';
        regionAvailabilityData = null;
    }
}

function renderZonesChip(altZones, targetZones) {
    const altStr = altZones || '—';
    if (!targetZones || !altZones) {
        return `<div class="mini-spec"><div class="mini-spec-val">${altStr}</div><div class="mini-spec-lbl">AZs</div></div>`;
    }
    const targetSet = new Set(targetZones.split(',').map(z => z.trim()));
    const altSet = new Set(altZones.split(',').map(z => z.trim()));
    // Check if alt has all the same zones as target
    const hasAll = [...targetSet].every(z => altSet.has(z));
    const extraZones = [...altSet].some(z => !targetSet.has(z));
    let cls = '';
    if (hasAll && altSet.size >= targetSet.size) {
        cls = ' mini-spec-zones-match'; // same or more zones = green
    } else {
        cls = ' mini-spec-zones-mismatch'; // missing zones = red
    }
    return `<div class="mini-spec${cls}"><div class="mini-spec-val">${altStr}</div><div class="mini-spec-lbl">AZs</div></div>`;
}

function renderRegionAvailCell(skuName) {
    if (!regionAvailabilityData) return '';
    const region = regionAvailabilityData.region || 'Region';
    const avail = regionAvailabilityData.availability[skuName];
    if (avail === true) {
        return `<div class="mini-spec mini-spec-avail avail-yes"><div class="mini-spec-val">✅</div><div class="mini-spec-lbl">${region}</div></div>`;
    } else if (avail === false) {
        return `<div class="mini-spec mini-spec-avail avail-no"><div class="mini-spec-val">❌</div><div class="mini-spec-lbl">${region}</div></div>`;
    }
    return `<div class="mini-spec mini-spec-avail"><div class="mini-spec-val">—</div><div class="mini-spec-lbl">${region}</div></div>`;
}

/* ============================================================
   Browse / Grid View (I-F)
   ============================================================ */
let currentMode = 'compare';
let gridRows = [];
let gridRegionLoaded = null;
let gridCurrencyLoaded = null;
let gridSort = { col: 'vCPUs', dir: 'asc' };
let gridPage = 1;
const GRID_PAGE_SIZE = 50;
const gridSelectedFamilies = new Set(); // empty = all families

// Switch between "Find alternatives" (compare) and "Browse all VMs" (grid)
function switchMode(mode) {
    if (mode !== 'compare' && mode !== 'browse') return;
    currentMode = mode;
    document.body.dataset.mode = mode;

    const compareTab = document.getElementById('tabCompare');
    const browseTab = document.getElementById('tabBrowse');
    const contentGrid = document.getElementById('contentGrid');
    const gridView = document.getElementById('gridView');

    compareTab.classList.toggle('active', mode === 'compare');
    compareTab.setAttribute('aria-selected', mode === 'compare');
    browseTab.classList.toggle('active', mode === 'browse');
    browseTab.setAttribute('aria-selected', mode === 'browse');

    if (mode === 'browse') {
        contentGrid.classList.add('hidden');
        gridView.classList.remove('hidden');
    } else {
        gridView.classList.add('hidden');
        contentGrid.classList.remove('hidden');
    }

    trackEvent('mode_switched', { mode });

    if (mode === 'browse') {
        const region = document.getElementById('location').value;
        const currency = document.getElementById('currencyCode').value || 'USD';
        if (!region) {
            showGridEmpty('👆 Pick a region above to browse its VM sizes.');
        } else if (gridRegionLoaded !== region || gridCurrencyLoaded !== currency) {
            loadGrid();
        }
    }
}

async function loadGrid() {
    const region = document.getElementById('location').value;
    const currency = document.getElementById('currencyCode').value || 'USD';
    if (!region) { showGridEmpty('👆 Pick a region above to browse its VM sizes.'); updateBrowseCount(null); return; }

    const loading = document.getElementById('gridLoading');
    const empty = document.getElementById('gridEmpty');
    const wrap = document.getElementById('gridTableWrap');
    const pagination = document.getElementById('gridPagination');

    empty.classList.add('hidden');
    wrap.classList.add('hidden');
    pagination.classList.add('hidden');
    loading.classList.remove('hidden');
    document.getElementById('gridStatus').textContent = '';

    try {
        const resp = await fetch(`${API_BASE_URL}/grid?location=${encodeURIComponent(region)}&currency=${encodeURIComponent(currency)}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        gridRows = Array.isArray(data.skus) ? data.skus : [];
        gridRegionLoaded = region;
        gridCurrencyLoaded = data.currency || currency;
        gridPage = 1;
        gridSelectedFamilies.clear();
        renderGridFacets();
        updateBrowseCount(gridRows.length);
        trackEvent('grid_loaded', { region, currency: gridCurrencyLoaded }, { skuCount: gridRows.length });
        loading.classList.add('hidden');
        if (gridRows.length === 0) {
            showGridEmpty('No VM sizes found for this region.');
            return;
        }
        wrap.classList.remove('hidden');
        pagination.classList.remove('hidden');
        renderGrid();
    } catch (err) {
        gridRows = [];
        gridRegionLoaded = null;
        updateBrowseCount(null);
        loading.classList.add('hidden');
        showGridEmpty(`⚠️ Couldn't load VM sizes: ${escapeHtml(err.message)}`);
    }
}

// Show the region's total VM-size count as a badge on the Browse tab (null hides it)
function updateBrowseCount(count) {
    const badge = document.getElementById('browseCount');
    if (!badge) return;
    if (count == null || count <= 0) {
        badge.hidden = true;
        badge.textContent = '';
    } else {
        badge.textContent = count.toLocaleString();
        badge.hidden = false;
    }
}

function showGridEmpty(msg) {
    const empty = document.getElementById('gridEmpty');
    document.getElementById('gridTableWrap').classList.add('hidden');
    document.getElementById('gridPagination').classList.add('hidden');
    empty.innerHTML = `<p>${msg}</p>`;
    empty.classList.remove('hidden');
    document.getElementById('gridStatus').textContent = '';
}

// Map a grid row's price fields to the shape used by getHourlyPrice/getMonthlyPrice
function gridRowPricing(row) {
    return {
        hourlyPrice: row.hourlyLinux,
        monthlyPrice: row.monthlyLinux,
        hourlyPriceWindows: row.hourlyWindows,
        monthlyPriceWindows: row.monthlyWindows,
        spotHourly: row.spotHourlyLinux,
        spotMonthly: row.spotMonthlyLinux,
        ri1YearHourly: row.ri1YearHourlyLinux,
        ri1YearMonthly: row.ri1YearMonthlyLinux,
        ri3YearHourly: row.ri3YearHourlyLinux,
        ri3YearMonthly: row.ri3YearMonthlyLinux,
        ri1YearHourlyWindows: row.ri1YearHourlyWindows,
        ri1YearMonthlyWindows: row.ri1YearMonthlyWindows,
        ri3YearHourlyWindows: row.ri3YearHourlyWindows,
        ri3YearMonthlyWindows: row.ri3YearMonthlyWindows,
        currency: gridCurrencyLoaded || 'USD'
    };
}

function gridPriceValue(row, kind) {
    const p = gridRowPricing(row);
    return kind === 'hourly' ? getHourlyPrice(p) : getMonthlyPrice(p);
}

// Build the family multi-select from the loaded dataset
function renderGridFacets() {
    const list = document.getElementById('gridFamilyList');
    if (!list) return;
    const families = [...new Set(gridRows.map(r => r.family).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    list.innerHTML = families.length
        ? families.map(f => `<label><input type="checkbox" class="grid-family-cb" value="${escapeHtml(f)}"> ${escapeHtml(f)}</label>`).join('')
        : '<span class="grid-muted">No families</span>';
    list.querySelectorAll('.grid-family-cb').forEach(cb => cb.addEventListener('change', onGridFamilyChange));
    updateGridFamilySummary();
}

function onGridFamilyChange(e) {
    if (e.target.checked) gridSelectedFamilies.add(e.target.value);
    else gridSelectedFamilies.delete(e.target.value);
    updateGridFamilySummary();
    gridPage = 1;
    renderGrid();
}

function updateGridFamilySummary() {
    const summary = document.getElementById('gridFamilySummary');
    if (!summary) return;
    const n = gridSelectedFamilies.size;
    summary.textContent = n === 0 ? 'All families' : `${n} famil${n === 1 ? 'y' : 'ies'} selected`;
}

function getGridFilters() {
    const num = id => { const v = parseFloat(document.getElementById(id).value); return Number.isFinite(v) ? v : null; };
    return {
        search: (document.getElementById('gridSearch').value || '').trim().toLowerCase(),
        vcpuMin: num('gridVcpuMin'), vcpuMax: num('gridVcpuMax'),
        ramMin: num('gridRamMin'), ramMax: num('gridRamMax'),
        gpuOnly: document.getElementById('gridGpuOnly').checked,
        intel: document.getElementById('gridVendorIntel').checked,
        amd: document.getElementById('gridVendorAMD').checked,
        arm: document.getElementById('gridVendorARM').checked
    };
}

function applyGridFilters(rows) {
    const f = getGridFilters();
    return rows.filter(r => {
        if (f.search && !`${r.name} ${r.family}`.toLowerCase().includes(f.search)) return false;
        if (gridSelectedFamilies.size && !gridSelectedFamilies.has(r.family)) return false;
        if (f.vcpuMin != null && (r.vCPUs || 0) < f.vcpuMin) return false;
        if (f.vcpuMax != null && (r.vCPUs || 0) > f.vcpuMax) return false;
        if (f.ramMin != null && (r.memoryGB || 0) < f.ramMin) return false;
        if (f.ramMax != null && (r.memoryGB || 0) > f.ramMax) return false;
        if (f.gpuOnly && !(r.gpuCount > 0)) return false;
        // Vendor (cpuVendor is exactly 'Intel' | 'AMD' | 'ARM')
        if (r.cpuVendor === 'Intel') return f.intel;
        if (r.cpuVendor === 'AMD') return f.amd;
        if (r.cpuVendor === 'ARM') return f.arm;
        return true; // unknown vendor always shown
    });
}

function sortGridRows(rows) {
    const { col, dir } = gridSort;
    const mult = dir === 'asc' ? 1 : -1;
    const val = r => {
        switch (col) {
            case 'name': return (r.name || '').toLowerCase();
            case 'family': return (r.family || '').toLowerCase();
            case 'vCPUs': return r.vCPUs || 0;
            case 'memoryGB': return r.memoryGB || 0;
            case 'gpuCount': return r.gpuCount || 0;
            case 'acu': return r.acu || 0;
            case 'hourly': return gridPriceValue(r, 'hourly');
            case 'monthly': return gridPriceValue(r, 'monthly');
            default: return 0;
        }
    };
    return [...rows].sort((a, b) => {
        const va = val(a), vb = val(b);
        const na = va == null, nb = vb == null;
        if (na && nb) return 0;
        if (na) return 1;  // nulls always last
        if (nb) return -1;
        if (typeof va === 'string' || typeof vb === 'string') return String(va).localeCompare(String(vb)) * mult;
        return (va - vb) * mult;
    });
}

function renderGrid() {
    if (!gridRows.length) return;
    const filtered = sortGridRows(applyGridFilters(gridRows));
    const total = gridRows.length;
    const matched = filtered.length;

    const pageCount = Math.max(1, Math.ceil(matched / GRID_PAGE_SIZE));
    if (gridPage > pageCount) gridPage = pageCount;
    const start = (gridPage - 1) * GRID_PAGE_SIZE;
    const pageRows = filtered.slice(start, start + GRID_PAGE_SIZE);

    const discount = getDiscountMultiplier();
    const tbody = document.getElementById('gridTableBody');

    if (matched === 0) {
        tbody.innerHTML = `<tr class="grid-no-match"><td colspan="9">No VM sizes match your filters. <button type="button" class="grid-action-btn" onclick="resetGridFilters()">Reset filters</button></td></tr>`;
    } else {
        tbody.innerHTML = pageRows.map(r => renderGridRow(r, discount)).join('');
    }

    const status = document.getElementById('gridStatus');
    if (matched === 0) {
        status.textContent = `0 of ${total} VM sizes`;
    } else {
        status.textContent = `Showing ${start + 1}–${start + pageRows.length} of ${matched}${matched !== total ? ` (filtered from ${total})` : ''}`;
    }

    renderGridPagination(pageCount, matched);
    updateGridSortIndicators();
}

function renderGridRow(r, discount) {
    const p = gridRowPricing(r);
    const hourly = formatHourlyPriceSafe(p);
    const monthly = formatMonthlyPriceSafe(p);
    const nvme = r.nvme ? '<span class="grid-badge nvme" title="Supports the NVMe disk interface">NVMe</span>' : '';
    const retiring = r.retirementStatus ? '<span class="grid-badge retiring" title="This size is being retired">⚠ Retiring</span>' : '';
    const gpu = r.gpuCount > 0 ? `${r.gpuCount}${r.gpuType ? ' ' + escapeHtml(r.gpuType) : ''}` : '<span class="grid-muted">—</span>';
    const acu = r.acu > 0 ? r.acu : '<span class="grid-muted">—</span>';
    const nameEsc = escapeHtml(r.name);
    const safeName = String(r.name).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    return `<tr>
        <td class="grid-name-cell">${nameEsc}${nvme}${retiring}</td>
        <td class="grid-family-cell">${escapeHtml(r.family || '—')}</td>
        <td class="num">${r.vCPUs || '—'}</td>
        <td class="num">${r.memoryGB != null ? r.memoryGB : '—'}</td>
        <td class="num">${gpu}</td>
        <td class="num">${acu}</td>
        <td class="num">${hourly}</td>
        <td class="num">${monthly}</td>
        <td class="grid-actions-col"><div class="grid-row-actions">
            <button type="button" class="grid-action-btn" onclick="gridFindAlternatives('${safeName}')">Find alternatives</button>
            <button type="button" class="grid-action-btn icon-btn" title="Where is this cheapest?" aria-label="Where is ${nameEsc} cheapest?" onclick="showRegionPriceComparison('${safeName}')">🌍</button>
            <button type="button" class="grid-action-btn icon-btn" title="Price history" aria-label="Price history for ${nameEsc}" onclick="showPriceHistory('${safeName}')">📈</button>
        </div></td>
    </tr>`;
}

function renderGridPagination(pageCount, matched) {
    const el = document.getElementById('gridPagination');
    if (matched === 0 || pageCount <= 1) { el.innerHTML = ''; return; }
    el.innerHTML = `
        <button type="button" id="gridPrev" ${gridPage <= 1 ? 'disabled' : ''}>← Prev</button>
        <span>Page ${gridPage} of ${pageCount}</span>
        <button type="button" id="gridNext" ${gridPage >= pageCount ? 'disabled' : ''}>Next →</button>`;
    document.getElementById('gridPrev')?.addEventListener('click', () => { if (gridPage > 1) { gridPage--; renderGrid(); scrollGridTop(); } });
    document.getElementById('gridNext')?.addEventListener('click', () => { gridPage++; renderGrid(); scrollGridTop(); });
}

function scrollGridTop() {
    document.getElementById('gridTableWrap')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function updateGridSortIndicators() {
    document.querySelectorAll('#gridTable thead th.sortable').forEach(th => {
        th.querySelector('.sort-arrow')?.remove();
        if (th.dataset.sort === gridSort.col) {
            const span = document.createElement('span');
            span.className = 'sort-arrow';
            span.textContent = gridSort.dir === 'asc' ? '▲' : '▼';
            th.appendChild(span);
        }
    });
}

function onGridSort(col) {
    if (gridSort.col === col) {
        gridSort.dir = gridSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
        gridSort.col = col;
        gridSort.dir = 'asc';
    }
    gridPage = 1;
    renderGrid();
}

function syncGridPricingButtons() {
    document.getElementById('btn-grid-payg')?.classList.toggle('active', currentPricingModel === 'payg');
    document.getElementById('btn-grid-spot')?.classList.toggle('active', currentPricingModel === 'spot');
    document.getElementById('btn-grid-ri1year')?.classList.toggle('active', currentPricingModel === 'ri1year');
    document.getElementById('btn-grid-ri3year')?.classList.toggle('active', currentPricingModel === 'ri3year');
    document.getElementById('btn-grid-linux')?.classList.toggle('active', currentPricingOS === 'linux');
    document.getElementById('btn-grid-windows')?.classList.toggle('active', currentPricingOS === 'windows');
}

// Row action: jump to the compare tab prefilled with this SKU and run the comparison
function gridFindAlternatives(skuName) {
    trackEvent('grid_find_alternatives', { sku: skuName });
    switchMode('compare');
    try { skuChoices.setChoiceByValue(skuName); } catch (e) { /* value may not be in the dropdown */ }
    const skuSelect = document.getElementById('skuName');
    if (skuSelect) skuSelect.value = skuName;
    updateOnboardingState();
    handleCompare();
}

function resetGridFilters() {
    document.getElementById('gridSearch').value = '';
    ['gridVcpuMin', 'gridVcpuMax', 'gridRamMin', 'gridRamMax'].forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('gridGpuOnly').checked = false;
    document.getElementById('gridVendorIntel').checked = true;
    document.getElementById('gridVendorAMD').checked = true;
    document.getElementById('gridVendorARM').checked = true;
    gridSelectedFamilies.clear();
    document.querySelectorAll('.grid-family-cb').forEach(cb => { cb.checked = false; });
    updateGridFamilySummary();
    gridPage = 1;
    renderGrid();
}

// Build an export model from the current filtered+sorted grid (all matches, not just the page)
function buildGridExportModel() {
    const discount = getDiscountMultiplier();
    const rows = sortGridRows(applyGridFilters(gridRows));
    const discountNote = discount < 1.0 ? ` (${((1 - discount) * 100).toFixed(1)}% discount applied)` : '';
    const fx4 = v => (v == null ? 'N/A' : (v * discount).toFixed(4));
    const fx2 = v => (v == null ? 'N/A' : (v * discount).toFixed(2));
    const currency = gridCurrencyLoaded || 'USD';

    const headers = [
        'Name', 'Family', 'vCPUs', 'Memory (GB)', 'GPU Count', 'GPU Type', 'ACU',
        'vCPUs per Core', 'Architecture', 'CPU Vendor', 'NVMe', 'RDMA', 'Availability Zones',
        `Hourly Linux${discountNote}`, `Monthly Linux${discountNote}`,
        `Hourly Windows${discountNote}`, `Monthly Windows${discountNote}`,
        `Spot Hourly (Linux)${discountNote}`, `Spot Monthly (Linux)${discountNote}`,
        `1yr RI Hourly (Linux)${discountNote}`, `1yr RI Monthly (Linux)${discountNote}`,
        `3yr RI Hourly (Linux)${discountNote}`, `3yr RI Monthly (Linux)${discountNote}`,
        `1yr RI Hourly (Windows)${discountNote}`, `1yr RI Monthly (Windows)${discountNote}`,
        `3yr RI Hourly (Windows)${discountNote}`, `3yr RI Monthly (Windows)${discountNote}`,
        'Currency'
    ];

    const dataRows = rows.map(r => [
        r.name, r.family || 'N/A', r.vCPUs ?? 'N/A', r.memoryGB ?? 'N/A',
        r.gpuCount || 0, r.gpuType || 'N/A', r.acu || 'N/A',
        r.vCPUsPerCore || 'N/A', r.architecture || 'N/A', r.cpuVendor || 'N/A',
        r.nvme ? 'Yes' : 'No', r.rdmaEnabled ? 'Yes' : 'No',
        (r.availabilityZones && r.availabilityZones.length) ? r.availabilityZones.join(' ') : 'N/A',
        fx4(r.hourlyLinux), fx2(r.monthlyLinux), fx4(r.hourlyWindows), fx2(r.monthlyWindows),
        fx4(r.spotHourlyLinux), fx2(r.spotMonthlyLinux),
        fx4(r.ri1YearHourlyLinux), fx2(r.ri1YearMonthlyLinux), fx4(r.ri3YearHourlyLinux), fx2(r.ri3YearMonthlyLinux),
        fx4(r.ri1YearHourlyWindows), fx2(r.ri1YearMonthlyWindows), fx4(r.ri3YearHourlyWindows), fx2(r.ri3YearMonthlyWindows),
        currency
    ]);

    const meta = [
        ['Azure VM Browse Export', ''],
        ['Generated', new Date().toISOString()],
        ['Region', gridRegionLoaded || 'N/A'],
        ['Currency', currency],
        ['Displayed pricing OS', currentPricingOS.charAt(0).toUpperCase() + currentPricingOS.slice(1)],
        ['Displayed pricing model', currentPricingModel],
        ['Discount applied', discount < 1.0 ? `${((1 - discount) * 100).toFixed(1)}%` : 'None'],
        ['Rows exported', String(rows.length)],
        ['Total in region', String(gridRows.length)]
    ];

    return { headers, dataRows, meta, rowCount: rows.length };
}

function exportGridCsv() {
    if (!gridRows.length) { showError('No VM sizes to export'); return; }
    const model = buildGridExportModel();
    const csv = [
        model.headers.map(escapeCsvCell).join(','),
        ...model.dataRows.map(row => row.map(escapeCsvCell).join(','))
    ].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `azure-vm-browse-${gridRegionLoaded || 'region'}-${new Date().toISOString().split('T')[0]}.csv`;
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    trackEvent('grid_export_csv', { region: gridRegionLoaded }, { exportedRows: model.rowCount });
}

function exportGridXlsx() {
    if (!gridRows.length) { showError('No VM sizes to export'); return; }
    if (typeof XLSX === 'undefined') {
        showError('Excel export is unavailable right now. Please try CSV, or reload the page and retry.');
        return;
    }
    const model = buildGridExportModel();
    const colWidth = h => ({ wch: Math.min(Math.max(String(h).length + 2, 10), 44) });
    const wb = XLSX.utils.book_new();

    const infoWs = XLSX.utils.aoa_to_sheet(model.meta);
    infoWs['!cols'] = [{ wch: 24 }, { wch: 42 }];
    XLSX.utils.book_append_sheet(wb, infoWs, 'Browse Info');

    const aoa = [model.headers, ...model.dataRows.map(r => r.map(coerceExportCell))];
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    ws['!cols'] = model.headers.map(colWidth);
    ws['!freeze'] = { xSplit: 0, ySplit: 1 };
    XLSX.utils.book_append_sheet(wb, ws, 'VM Sizes');

    XLSX.writeFile(wb, `azure-vm-browse-${gridRegionLoaded || 'region'}-${new Date().toISOString().split('T')[0]}.xlsx`);
    trackEvent('grid_export_xlsx', { region: gridRegionLoaded }, { exportedRows: model.rowCount });
}

// Wire up grid controls once the DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    document.body.dataset.mode = 'compare';

    document.getElementById('gridSearch')?.addEventListener('input', () => { gridPage = 1; renderGrid(); });
    ['gridVcpuMin', 'gridVcpuMax', 'gridRamMin', 'gridRamMax'].forEach(id => {
        document.getElementById(id)?.addEventListener('input', () => { gridPage = 1; renderGrid(); });
    });
    ['gridGpuOnly', 'gridVendorIntel', 'gridVendorAMD', 'gridVendorARM'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => { gridPage = 1; renderGrid(); });
    });
    document.getElementById('gridResetFilters')?.addEventListener('click', resetGridFilters);

    document.querySelectorAll('#gridTable thead th.sortable').forEach(th => {
        th.addEventListener('click', () => onGridSort(th.dataset.sort));
    });

    document.getElementById('gridExportCsvBtn')?.addEventListener('click', exportGridCsv);
    document.getElementById('gridExportXlsxBtn')?.addEventListener('click', exportGridXlsx);

    // Currency change refetches the grid (server-side conversion); only acts in browse mode
    document.getElementById('currencyCode')?.addEventListener('change', () => {
        if (currentMode === 'browse' && document.getElementById('location').value) loadGrid();
    });
    // Discount is a client-side multiplier — re-render the grid live
    document.getElementById('discountPct')?.addEventListener('input', () => {
        if (currentMode === 'browse' && gridRows.length) renderGrid();
    });
});
