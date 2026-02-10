// API Configuration
const API_BASE_URL = 'https://vmsku-api-functions.azurewebsites.net/api';

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

// Handle Compare Button Click
async function handleCompare() {
    const skuName = document.getElementById('skuName').value.trim();
    const location = document.getElementById('location').value;

    if (!skuName || !location) {
        showError('Please provide both SKU name and location');
        return;
    }

    const params = {
        skuName,
        location,
        tolerance: parseInt(document.getElementById('tolerance').value),
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
        const response = await fetch(`${API_BASE_URL}/compare-vms`, {
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
