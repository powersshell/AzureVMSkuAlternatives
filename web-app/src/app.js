// API Configuration
// Direct connection to Flex Consumption Function App (Static Web App rewrite doesn't support POST)
const API_BASE_URL = 'https://vmsku-api-functions-flex.azurewebsites.net/api';

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

let currentResults = null;
let currentPricingOS = 'linux';
let locationChoices = null; // Choices.js instance for location
let skuChoices = null; // Choices.js instance for SKU
const expandedDetailsCache = new Map(); // Cache for expanded row details (Phase 2)
let prefetchAbortController = null; // Cancels in-flight prefetch wave on new search (Phase 3)

// Event Listeners
compareBtn.addEventListener('click', handleCompare);
dismissErrorBtn.addEventListener('click', hideError);
exportBtn.addEventListener('click', exportToCSV);

// Initialize dropdown functionality on page load
document.addEventListener('DOMContentLoaded', () => {
    const locationSelect = document.getElementById('location');
    const skuSelect = document.getElementById('skuName');
    
    // Initialize Choices.js for location dropdown
    locationChoices = new Choices(locationSelect, {
        searchEnabled: true,
        searchPlaceholderValue: 'Search regions...',
        itemSelectText: '',
        shouldSort: false
    });
    
    // Initialize Choices.js for SKU dropdown
    skuChoices = new Choices(skuSelect, {
        searchEnabled: true,
        searchPlaceholderValue: 'Type to search SKUs...',
        itemSelectText: '',
        shouldSort: false,
        placeholder: true,
        placeholderValue: 'Select a region first...'
    });
    
    // Disable SKU dropdown initially
    skuChoices.disable();
    
    // Listen for region changes - using Choices.js passedElement
    locationChoices.passedElement.element.addEventListener('change', async (e) => {
        const location = e.target.value;
        
        if (!location) {
            // No region selected - disable SKU dropdown
            skuChoices.clearStore();
            skuChoices.setChoices([{ value: '', label: 'Select a region first...', disabled: true }], 'value', 'label', true);
            skuChoices.disable();
            document.getElementById('skuCount').textContent = '';
            return;
        }
        
        // Fetch SKUs for selected region
        await loadSkusForRegion(location);
    });
    
    // Listen for CPU vendor filter changes (dropdown filters)
    document.getElementById('filterIntel').addEventListener('change', updateSkuFilters);
    document.getElementById('filterAMD').addEventListener('change', updateSkuFilters);
    document.getElementById('filterARM').addEventListener('change', updateSkuFilters);
    
    // Listen for CPU vendor filter changes (result filters)
    document.getElementById('resultFilterIntel').addEventListener('change', updateResultsFilters);
    document.getElementById('resultFilterAMD').addEventListener('change', updateResultsFilters);
    document.getElementById('resultFilterARM').addEventListener('change', updateResultsFilters);
});

// Store all SKUs for current region (unfiltered)
let allSkusForRegion = [];

// Update SKU count display
function updateSkuCount(filteredCount, totalCount) {
    const skuCount = document.getElementById('skuCount');
    if (filteredCount === totalCount) {
        skuCount.textContent = `${totalCount} SKUs available`;
    } else {
        skuCount.textContent = `${filteredCount} of ${totalCount} SKUs (filtered by vendor)`;
    }
    skuCount.style.color = '#107c10'; // Success green
}

// Update SKU dropdown based on CPU vendor filters
function updateSkuFilters() {
    if (allSkusForRegion.length === 0) return;
    
    const filteredSkus = getFilteredSkus(allSkusForRegion);
    populateSkuChoices(filteredSkus);
    updateSkuCount(filteredSkus.length, allSkusForRegion.length);
}

// Update comparison results based on CPU vendor filters
function updateResultsFilters() {
    if (!currentResults || !currentResults.alternatives) return;
    
    // Re-display results with current filters
    displayResults(currentResults);
}

// Filter SKUs based on checked CPU vendor checkboxes
function getFilteredSkus(skus) {
    const showIntel = document.getElementById('filterIntel').checked;
    const showAMD = document.getElementById('filterAMD').checked;
    const showARM = document.getElementById('filterARM').checked;
    
    return skus.filter(sku => {
        if (sku.cpuVendor === 'Intel' && showIntel) return true;
        if (sku.cpuVendor === 'AMD' && showAMD) return true;
        if (sku.cpuVendor === 'ARM' && showARM) return true;
        return false;
    });
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
        
        // Store all SKUs for filtering
        allSkusForRegion = skus;
        
        // Apply current filters and populate dropdown
        const filteredSkus = getFilteredSkus(skus);
        populateSkuChoices(filteredSkus);
        
        // Update count - show filtered/total
        updateSkuCount(filteredSkus.length, skus.length);
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
    const choices = skus.map(sku => ({
        value: sku.name,
        label: `${sku.name} (${sku.vCPUs} vCPUs, ${sku.memoryGB} GB)`,
        customProperties: {
            vCPUs: sku.vCPUs,
            memoryGB: sku.memoryGB
        }
    }));
    
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

    if (!skuName || !location) {
        showError('Please provide both SKU name and location');
        return;
    }
    
    // Validate that entered SKU exists in the list (optional but recommended)
    if (window.validSkuNames && !window.validSkuNames.includes(skuName)) {
        showError(`Invalid SKU: "${skuName}". Please select a valid SKU from the dropdown.`);
        return;
    }

    const params = {
        skuName,
        location,
        minSimilarityScore: parseInt(document.getElementById('minSimilarityScore').value),
        currencyCode: document.getElementById('currencyCode').value,
        weightCPU: parseFloat(document.getElementById('weightCPU').value),
        weightMemory: parseFloat(document.getElementById('weightMemory').value),
        weightGPU: parseFloat(document.getElementById('weightGPU').value),
        weightStorage: parseFloat(document.getElementById('weightStorage').value),
        weightNetwork: parseFloat(document.getElementById('weightNetwork').value),
        weightFeatures: parseFloat(document.getElementById('weightFeatures').value),
        requireNVMeMatch: document.getElementById('requireNVMeMatch').checked,
        requireGPUMatch: document.getElementById('requireGPUMatch').checked
    };

    showLoading();
    hideError();
    hideResults();

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

        currentResults = data;
        displayResults(data);
    } catch (error) {
        console.error('Error comparing VMs:', error);
        showError(error.message || 'Failed to compare VMs. Please check your input and try again.');
    } finally {
        hideLoading();
    }
}

// OS Pricing toggle
function setPricingOS(os) {
    currentPricingOS = os;
    document.getElementById('btn-linux-pricing')?.classList.toggle('active', os === 'linux');
    document.getElementById('btn-windows-pricing')?.classList.toggle('active', os === 'windows');
    document.getElementById('th-hourly').textContent = os === 'windows' ? 'Hourly Cost (Win)' : 'Hourly Cost (Linux)';
    document.getElementById('th-monthly').textContent = os === 'windows' ? 'Monthly Cost (Win)' : 'Monthly Cost (Linux)';
    // Re-render results if available
    if (currentResults) {
        displayAlternatives(filterResultsByVendor(currentResults.alternatives));
        displayTargetSku(currentResults.targetSku);
    }
}

function getHourlyPrice(pricing) {
    if (!pricing) return null;
    return currentPricingOS === 'windows'
        ? (pricing.hourlyPriceWindows ?? pricing.hourlyPrice)
        : pricing.hourlyPrice;
}

function getMonthlyPrice(pricing) {
    if (!pricing) return null;
    return currentPricingOS === 'windows'
        ? (pricing.monthlyPriceWindows ?? pricing.monthlyPrice)
        : pricing.monthlyPrice;
}

// Display Results
function displayResults(data) {
    if (!data.alternatives || data.alternatives.length === 0) {
        noResults.classList.remove('hidden');
        resultsTableBody.innerHTML = '';
        targetSkuInfo.innerHTML = '';
    } else {
        noResults.classList.add('hidden');
        displayTargetSku(data.targetSku);
        
        // Apply CPU vendor filter to results
        const filteredAlternatives = filterResultsByVendor(data.alternatives);
        displayAlternatives(filteredAlternatives);
    }
    resultsSection.classList.remove('hidden');
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

// Display Target SKU Info
function displayTargetSku(targetSku) {
    const cpuDisplay = targetSku.cpuVendor ? `${targetSku.cpuVendor} (${targetSku.architecture || 'x64'})` : 'N/A';
    const html = `
        <h3>Target SKU: ${targetSku.name}</h3>
        <div class="target-sku-grid">
            <div class="target-sku-item">
                <strong>CPU Vendor</strong>
                <span>${cpuDisplay}</span>
            </div>
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
                <strong>Hourly Cost</strong>
                <span>${targetSku.pricing ? formatHourlyCurrency(getHourlyPrice(targetSku.pricing), targetSku.pricing.currency) : 'N/A'}</span>
            </div>
            <div class="target-sku-item">
                <strong>Monthly Cost</strong>
                <span>${targetSku.pricing ? formatCurrency(getMonthlyPrice(targetSku.pricing), targetSku.pricing.currency) : 'N/A'}</span>
            </div>
            <div class="target-sku-item">
                <strong>Availability Zones</strong>
                <span>${targetSku.zones || 'N/A'}</span>
            </div>
        </div>
    `;
    targetSkuInfo.innerHTML = html;
}

// Calculate difference indicators from existing data
function calculateIndicators(targetSku, alternativeSku) {
    return {
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

// Display Alternatives Table
function displayAlternatives(alternatives) {
    resultsTableBody.innerHTML = '';
    
    // Get target SKU for comparison
    const targetSku = currentResults?.targetSku;
    if (!targetSku) {
        console.error('No target SKU available for comparison');
        // Fall back to display without indicators
        alternatives.forEach((alt, index) => {
            const row = document.createElement('tr');
            const scoreClass = alt.similarityScore >= 80 ? 'score-high' :
                              alt.similarityScore >= 60 ? 'score-medium' : 'score-low';
            const cpuDisplay = `${alt.cpuVendor || 'Intel'} (${alt.architecture || 'x64'})`;

            row.innerHTML = `
                <td><span class="rank-badge">${index + 1}</span></td>
                <td><span class="sku-name">${alt.name}</span></td>
                <td><div class="similarity-score"><span class="score-badge ${scoreClass}">${alt.similarityScore.toFixed(1)}%</span></div></td>
                <td>${cpuDisplay}</td>
                <td>${alt.vCPUs || 'N/A'}</td>
                <td>${alt.memoryGB ? alt.memoryGB + ' GB' : 'N/A'}</td>
                <td>${alt.pricing ? formatHourlyCurrency(getHourlyPrice(alt.pricing), alt.pricing.currency) : 'N/A'}</td>
                <td>${alt.pricing ? formatCurrency(getMonthlyPrice(alt.pricing), alt.pricing.currency) : 'N/A'}</td>
                <td>${alt.zones || 'N/A'}</td>
            `;
            resultsTableBody.appendChild(row);
        });
        return;
    }

    alternatives.forEach((alt, index) => {
        // Main result row
        const row = document.createElement('tr');
        row.classList.add('result-row', 'clickable');
        row.dataset.index = index;
        row.dataset.skuName = alt.name;

        const scoreClass = alt.similarityScore >= 80 ? 'score-high' :
                          alt.similarityScore >= 60 ? 'score-medium' : 'score-low';

        // Format CPU vendor with architecture
        const cpuDisplay = `${alt.cpuVendor || 'Intel'} (${alt.architecture || 'x64'})`;
        
        // Calculate indicators from existing data
        const indicators = calculateIndicators(targetSku, alt);

        row.innerHTML = `
            <td><span class="rank-badge">${index + 1}</span></td>
            <td><span class="sku-name">${alt.name}</span></td>
            <td>
                <div class="similarity-score">
                    <span class="score-badge ${scoreClass}">${alt.similarityScore.toFixed(1)}%</span>
                </div>
            </td>
            <td>${cpuDisplay}</td>
            <td>${alt.vCPUs || 'N/A'} ${renderIndicator(indicators.vCPUs, 'vCPUs')}</td>
            <td>${alt.memoryGB ? alt.memoryGB + ' GB' : 'N/A'} ${renderIndicator(indicators.memory, 'Memory')}</td>
            <td>${alt.pricing ? formatHourlyCurrency(getHourlyPrice(alt.pricing), alt.pricing.currency) : 'N/A'} ${renderIndicator(indicators.hourlyPrice, 'Hourly Price')}</td>
            <td>${alt.pricing ? formatCurrency(getMonthlyPrice(alt.pricing), alt.pricing.currency) : 'N/A'} ${renderIndicator(indicators.monthlyPrice, 'Monthly Price')}</td>
            <td>${alt.zones || 'N/A'}</td>
        `;
        
        // Click handler for expand/collapse (Phase 2)
        row.addEventListener('click', () => toggleDetails(index, alt, targetSku));
        
        resultsTableBody.appendChild(row);
        
        // Create hidden details row (Phase 2)
        const detailsRow = document.createElement('tr');
        detailsRow.classList.add('details-row', 'hidden');
        detailsRow.dataset.index = index;
        detailsRow.innerHTML = `<td colspan="9"><div class="details-content"></div></td>`;
        
        resultsTableBody.appendChild(detailsRow);
    });

    // Kick off background prefetch for top 10 rows (Phase 3)
    const location = currentResults?.location || document.getElementById('location').value;
    prefetchTopDetails(alternatives, targetSku, location);
}

// Toggle details row expand/collapse (Phase 2)
async function toggleDetails(index, altSku, targetSku) {
    const detailsRow = document.querySelector(`.details-row[data-index="${index}"]`);
    const detailsContent = detailsRow.querySelector('.details-content');
    
    // Toggle visibility
    if (detailsRow.classList.contains('hidden')) {
        // Show details
        detailsRow.classList.remove('hidden');
        
        // Check cache first
        const cacheKey = `${targetSku.name}_${altSku.name}`;
        if (expandedDetailsCache.has(cacheKey)) {
            detailsContent.innerHTML = renderDetailedComparison(
                expandedDetailsCache.get(cacheKey),
                targetSku,
                altSku
            );
        } else {
            // Show loading state
            detailsContent.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading detailed comparison...</p></div>';
            
            // Fetch from API
            try {
                const details = await fetchComparisonDetails(
                    targetSku.name,
                    altSku.name,
                    currentResults.location || document.getElementById('location').value
                );
                
                // Cache it
                expandedDetailsCache.set(cacheKey, details);
                
                // Render it
                detailsContent.innerHTML = renderDetailedComparison(details, targetSku, altSku);
            } catch (error) {
                console.error('Failed to load details:', error);
                detailsContent.innerHTML = '<div class="error"><p>❌ Failed to load details. Click row again to retry.</p></div>';
            }
        }
    } else {
        // Hide details
        detailsRow.classList.add('hidden');
    }
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
    
    return `
        <div class="comparison-details">
            <h4>📊 Detailed Comparison: ${altSku.name} vs ${targetSku.name}</h4>
            
            <div class="details-grid">
                <!-- Compute Section -->
                <div class="details-section">
                    <h5>Compute</h5>
                    ${renderNumericDiff('vCPUs', diff.compute.vCPUs)}
                    ${renderNumericDiff('Memory', diff.compute.memory)}
                    ${renderBooleanDiff(diff.compute.hyperVGen2)}
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
                    ${renderNumericDiff('Max NICs', diff.network.maxNics)}
                    ${renderBooleanDiff(diff.network.acceleratedNetworking)}
                </div>
                
                <!-- Cost Section -->
                <div class="details-section">
                    <h5>Cost Analysis</h5>
                    ${renderPriceDiff('Hourly Price', diff.pricing.hourly)}
                    ${renderPriceDiff('Monthly Price', diff.pricing.monthly)}
                    ${renderEfficiency(diff.pricing.efficiency)}
                </div>
            </div>
            
            <!-- Features Section -->
            ${renderFeatures(diff.features)}
        </div>
    `;
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

function renderBooleanDiff(diff) {
    const icon = diff.changed ? (diff.direction === 'added' ? '✅' : '❌') : '●';
    const className = diff.changed ? 'diff-item' : 'diff-item same';
    const text = diff.changed ? 
        `${diff.target ? 'Yes' : 'No'} → ${diff.alternative ? 'Yes' : 'No'}` :
        `${diff.alternative ? 'Yes' : 'No'} (same)`;
    
    return `<div class="${className}">${icon} ${diff.feature}: ${text}</div>`;
}

function renderEfficiency(efficiency) {
    let html = '';
    
    if (efficiency.costPerVCPU) {
        const icon = efficiency.costPerVCPU.betterEfficiency ? '✅' : '⚠️';
        html += `
            <div class="diff-item">
                ${icon} Cost per vCPU: $${efficiency.costPerVCPU.alternative.toFixed(4)}
                (${efficiency.costPerVCPU.betterEfficiency ? 'better' : 'worse'} efficiency)
            </div>
        `;
    }
    
    if (efficiency.costPerGB) {
        const icon = efficiency.costPerGB.betterEfficiency ? '✅' : '⚠️';
        html += `
            <div class="diff-item">
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

// Export to CSV
function exportToCSV() {
    if (!currentResults || !currentResults.alternatives || currentResults.alternatives.length === 0) {
        showError('No data to export');
        return;
    }

    const headers = ['Rank', 'SKU Name', 'Similarity Score', 'vCPUs', 'Memory (GB)', 'Hourly Cost (Linux)', 'Monthly Cost (Linux)', 'Hourly Cost (Windows)', 'Monthly Cost (Windows)', 'Currency', 'Availability Zones'];
    const rows = currentResults.alternatives.map((alt, index) => [
        index + 1,
        alt.name,
        alt.similarityScore.toFixed(1),
        alt.vCPUs || 'N/A',
        alt.memoryGB || 'N/A',
        alt.pricing ? alt.pricing.hourlyPrice : 'N/A',
        alt.pricing ? alt.pricing.monthlyPrice : 'N/A',
        alt.pricing ? (alt.pricing.hourlyPriceWindows ?? 'N/A') : 'N/A',
        alt.pricing ? (alt.pricing.monthlyPriceWindows ?? 'N/A') : 'N/A',
        alt.pricing ? alt.pricing.currency : 'N/A',
        alt.zones || 'N/A'
    ]);

    const csvContent = [
        headers.join(','),
        ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    link.setAttribute('href', url);
    link.setAttribute('download', `azure-vm-comparison-${currentResults.targetSku.name}-${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// UI Helper Functions
function showLoading() {
    loadingOverlay.classList.remove('hidden');
    compareBtn.disabled = true;
}

function hideLoading() {
    loadingOverlay.classList.add('hidden');
    compareBtn.disabled = false;
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
