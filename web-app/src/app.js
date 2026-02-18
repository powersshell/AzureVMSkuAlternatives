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

// Event Listeners
compareBtn.addEventListener('click', handleCompare);
dismissErrorBtn.addEventListener('click', hideError);
exportBtn.addEventListener('click', exportToCSV);

// Initialize dropdown functionality on page load
document.addEventListener('DOMContentLoaded', () => {
    const locationSelect = document.getElementById('location');
    const skuSelect = document.getElementById('skuName');
    
    // Listen for region changes
    locationSelect.addEventListener('change', async (e) => {
        const location = e.target.value;
        const skuInput = document.getElementById('skuName');
        
        if (!location) {
            // No region selected - disable SKU input
            skuInput.disabled = true;
            skuInput.value = '';
            skuInput.placeholder = 'Select a region first...';
            document.getElementById('skuList').innerHTML = '';
            document.getElementById('skuCount').textContent = '';
            return;
        }
        
        // Fetch SKUs for selected region
        await loadSkusForRegion(location);
    });
});

// Load SKUs from cache for selected region
async function loadSkusForRegion(location) {
    const skuInput = document.getElementById('skuName');
    const skuCount = document.getElementById('skuCount');
    
    try {
        // Show loading state
        skuInput.disabled = true;
        skuInput.value = '';
        skuInput.placeholder = 'Loading SKUs...';
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
            skuInput.placeholder = 'No SKUs available for this region';
            skuCount.textContent = 'No SKUs found';
            skuCount.style.color = '#d13438';
            return;
        }
        
        // Populate datalist
        populateSkuDatalist(skus);
        
        // Update count and enable input
        skuCount.textContent = `${skus.length} SKUs available`;
        skuCount.style.color = '#107c10'; // Success green
        skuInput.disabled = false;
        skuInput.placeholder = 'Start typing to search (e.g., D2s, B1ls)...';
        
    } catch (error) {
        console.error('Failed to load SKUs:', error);
        skuInput.placeholder = 'Error loading SKUs - try again';
        skuCount.textContent = 'Failed to load SKUs';
        skuCount.style.color = '#d13438'; // Error red
    }
}

// Populate SKU datalist with formatted options
function populateSkuDatalist(skus) {
    const skuDatalist = document.getElementById('skuList');
    
    // Sort by vCPUs, then memory
    skus.sort((a, b) => {
        if (a.vCPUs !== b.vCPUs) return a.vCPUs - b.vCPUs;
        return a.memoryGB - b.memoryGB;
    });
    
    // Build datalist options
    let html = '';
    skus.forEach(sku => {
        // Use displayName which is already formatted: "Standard_D2s_v3 (2 vCPUs, 8.0 GB) - $0.096/hr"
        const label = sku.displayName || `${sku.name} (${sku.vCPUs} vCPUs, ${sku.memoryGB} GB)`;
        // Value is the SKU name that will be submitted to API
        html += `<option value="${sku.name}" label="${label}"></option>`;
    });
    
    skuDatalist.innerHTML = html;
    
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

        console.log('Response status:', response.status);
        console.log('Response headers:', response.headers);

        // Get the response text first to see what we're actually receiving
        const responseText = await response.text();
        console.log('Response text:', responseText);

        if (!response.ok) {
            // Try to parse as JSON, but handle cases where it's not JSON
            let errorMessage;
            try {
                const errorData = JSON.parse(responseText);
                errorMessage = errorData.error || JSON.stringify(errorData);
            } catch {
                errorMessage = responseText || response.statusText;
            }
            throw new Error(`HTTP ${response.status}: ${errorMessage}`);
        }

        // Parse the response text as JSON
        let data;
        try {
            data = JSON.parse(responseText);
        } catch (parseError) {
            console.error('Failed to parse response as JSON:', parseError);
            throw new Error(`Invalid JSON response: ${responseText.substring(0, 100)}`);
        }

        currentResults = data;
        displayResults(data);
    } catch (error) {
        console.error('Error comparing VMs:', error);
        showError(error.message || 'Failed to compare VMs. Please check your input and try again.');
    } finally {
        hideLoading();
    }
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
        displayAlternatives(data.alternatives);
    }
    resultsSection.classList.remove('hidden');
}

// Display Target SKU Info
function displayTargetSku(targetSku) {
    const html = `
        <h3>Target SKU: ${targetSku.name}</h3>
        <div class="target-sku-grid">
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
                <span>${targetSku.pricing ? formatCurrency(targetSku.pricing.hourlyPrice, targetSku.pricing.currency) : 'N/A'}</span>
            </div>
            <div class="target-sku-item">
                <strong>Monthly Cost</strong>
                <span>${targetSku.pricing ? formatCurrency(targetSku.pricing.monthlyPrice, targetSku.pricing.currency) : 'N/A'}</span>
            </div>
            <div class="target-sku-item">
                <strong>Availability Zones</strong>
                <span>${targetSku.zones || 'N/A'}</span>
            </div>
        </div>
    `;
    targetSkuInfo.innerHTML = html;
}

// Display Alternatives Table
function displayAlternatives(alternatives) {
    resultsTableBody.innerHTML = '';

    alternatives.forEach((alt, index) => {
        const row = document.createElement('tr');

        const scoreClass = alt.similarityScore >= 80 ? 'score-high' :
                          alt.similarityScore >= 60 ? 'score-medium' : 'score-low';

        const rankClass = index < 3 ? `rank-${index + 1}` : '';

        row.innerHTML = `
            <td><span class="rank-badge ${rankClass}">${index + 1}</span></td>
            <td><span class="sku-name">${alt.name}</span></td>
            <td>
                <div class="similarity-score">
                    <span class="score-badge ${scoreClass}">${alt.similarityScore.toFixed(1)}%</span>
                </div>
            </td>
            <td>${alt.vCPUs || 'N/A'}</td>
            <td>${alt.memoryGB ? alt.memoryGB + ' GB' : 'N/A'}</td>
            <td>${alt.pricing ? formatCurrency(alt.pricing.hourlyPrice, alt.pricing.currency) : 'N/A'}</td>
            <td>${alt.pricing ? formatCurrency(alt.pricing.monthlyPrice, alt.pricing.currency) : 'N/A'}</td>
            <td>${alt.zones || 'N/A'}</td>
        `;

        resultsTableBody.appendChild(row);
    });
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

// Export to CSV
function exportToCSV() {
    if (!currentResults || !currentResults.alternatives || currentResults.alternatives.length === 0) {
        showError('No data to export');
        return;
    }

    const headers = ['Rank', 'SKU Name', 'Similarity Score', 'vCPUs', 'Memory (GB)', 'Hourly Cost', 'Monthly Cost', 'Currency', 'Availability Zones'];
    const rows = currentResults.alternatives.map((alt, index) => [
        index + 1,
        alt.name,
        alt.similarityScore.toFixed(1),
        alt.vCPUs || 'N/A',
        alt.memoryGB || 'N/A',
        alt.pricing ? alt.pricing.hourlyPrice : 'N/A',
        alt.pricing ? alt.pricing.monthlyPrice : 'N/A',
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
