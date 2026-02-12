/**
 * Azure Function to compare VM SKUs (Functions v4) - REST API Version
 * This function retrieves Azure VM SKU information and finds similar alternatives
 */
const fetch = require('node-fetch');

module.exports = async function (context, req) {
    context.log('Processing VM comparison request', { method: req.method, url: req.url });

    // Add a simple GET handler for testing
    if (req.method === 'GET') {
        context.res = {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: 'compare-vms endpoint is running (v4 format - REST API)',
                timestamp: new Date().toISOString(),
                environment: process.env.AZURE_FUNCTIONS_ENVIRONMENT || 'Production'
            })
        };
        return;
    }

    // Wrap everything in try-catch to ensure we always return JSON
    try {
        // Parse request body
        let body;
        try {
            body = req.body;
            if (typeof body === 'string') {
                body = JSON.parse(body);
            }
            context.log('Request body parsed successfully', { skuName: body?.skuName, location: body?.location });
        } catch (parseError) {
            context.log.error('Failed to parse request body:', parseError);
            context.res = {
                status: 400,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ error: 'Invalid JSON in request body' })
            };
            return;
        }

        const {
            skuName,
            location,
            tolerance = 20,
            minSimilarityScore = 60,
            currencyCode = 'USD',
            weightCPU = 2.0,
            weightMemory = 2.0,
            weightGPU = 2.0,
            weightStorage = 1.0,
            weightNetwork = 1.0,
            weightFeatures = 0.5,
            requireNVMeMatch = false,
            requireGPUMatch = false
        } = body;

        // Validate inputs
        if (!skuName || !location) {
            context.res = {
                status: 400,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ error: 'skuName and location are required' })
            };
            return;
        }

        // Get subscription ID from environment
        const subscriptionId = process.env.AZURE_SUBSCRIPTION_ID;
        if (!subscriptionId) {
            context.log.error('AZURE_SUBSCRIPTION_ID environment variable is not set');
            context.res = {
                status: 500,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    error: 'Server configuration error',
                    details: 'Azure subscription ID is not configured. Please set AZURE_SUBSCRIPTION_ID in application settings.'
                })
            };
            return;
        }

        context.log(`Using subscription: ${subscriptionId}`);

        // Get access token (try managed identity first, fall back to service principal)
        let accessToken;
        try {
            accessToken = await getAccessToken(context);
            context.log('Access token obtained successfully');
        } catch (authError) {
            context.log.error('Authentication error:', authError);
            context.res = {
                status: 500,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    error: 'Authentication failed',
                    details: authError.message,
                    hint: 'Ensure service principal credentials are configured or managed identity is enabled'
                })
            };
            return;
        }

        // Get all VM SKUs for the location using REST API
        context.log(`Fetching VM SKUs for location: ${location}`);
        const allSkus = await getVmSkusForLocation(subscriptionId, location, accessToken, context);

        // Find target SKU
        const targetSku = allSkus.find(s => s.name === skuName);
        if (!targetSku) {
            context.res = {
                status: 404,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ error: `SKU '${skuName}' not found in location '${location}'` })
            };
            return;
        }

        // Extract target capabilities
        const targetCapabilities = extractCapabilities(targetSku);

        // Get pricing for target SKU
        const targetPricing = await getVmPricing(skuName, location, currencyCode, context);

        // Get availability zones
        const targetZones = getAvailabilityZones(targetSku, location);

        // Compare with all other SKUs
        const alternatives = [];
        for (const sku of allSkus) {
            if (sku.name === skuName) continue; // Skip the target itself

            const skuCapabilities = extractCapabilities(sku);

            // Apply filters
            if (requireNVMeMatch && targetCapabilities.nvme && !skuCapabilities.nvme) continue;
            if (requireGPUMatch && targetCapabilities.gpuCount > 0 && skuCapabilities.gpuCount === 0) continue;

            // Calculate similarity score
            const similarityScore = calculateSimilarity(
                targetCapabilities,
                skuCapabilities,
                {
                    weightCPU,
                    weightMemory,
                    weightGPU,
                    weightStorage,
                    weightNetwork,
                    weightFeatures
                },
                tolerance
            );

            if (similarityScore >= minSimilarityScore) {
                const pricing = await getVmPricing(sku.name, location, currencyCode, context);
                const zones = getAvailabilityZones(sku, location);

                alternatives.push({
                    name: sku.name,
                    similarityScore,
                    vCPUs: skuCapabilities.vCPUs,
                    memoryGB: skuCapabilities.memoryGB,
                    gpuCount: skuCapabilities.gpuCount,
                    gpuType: skuCapabilities.gpuType,
                    pricing,
                    zones: zones.join(', ') || 'N/A',
                    capabilities: skuCapabilities
                });
            }
        }

        // Sort by similarity score
        alternatives.sort((a, b) => b.similarityScore - a.similarityScore);

        context.log(`Found ${alternatives.length} alternatives`);

        // Return results
        context.res = {
            status: 200,
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                targetSku: {
                    name: targetSku.name,
                    vCPUs: targetCapabilities.vCPUs,
                    memoryGB: targetCapabilities.memoryGB,
                    gpuCount: targetCapabilities.gpuCount,
                    gpuType: targetCapabilities.gpuType,
                    pricing: targetPricing,
                    zones: targetZones.join(', ') || 'N/A',
                    capabilities: targetCapabilities
                },
                alternatives,
                searchParameters: {
                    location,
                    tolerance,
                    minSimilarityScore,
                    weights: {
                        cpu: weightCPU,
                        memory: weightMemory,
                        gpu: weightGPU,
                        storage: weightStorage,
                        network: weightNetwork,
                        features: weightFeatures
                    }
                }
            })
        };

    } catch (error) {
        context.log.error('Error processing request:', error);
        context.log.error('Error stack:', error.stack);
        context.res = {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                error: 'Internal server error',
                details: error.message,
                type: error.constructor.name
            })
        };
    }
};

/**
 * Extract capabilities from a SKU
 */
function extractCapabilities(sku) {
    const capabilities = {};

    if (sku.capabilities) {
        sku.capabilities.forEach(cap => {
            capabilities[cap.name] = cap.value;
        });
    }

    return {
        vCPUs: parseInt(capabilities.vCPUs) || 0,
        memoryGB: parseFloat(capabilities.MemoryGB) || 0,
        maxDataDiskCount: parseInt(capabilities.MaxDataDiskCount) || 0,
        maxNics: parseInt(capabilities.MaxNetworkInterfaces) || 0,
        premiumIO: capabilities.PremiumIO === 'True',
        ephemeralOSDisk: capabilities.EphemeralOSDiskSupported === 'True',
        acceleratedNetworking: capabilities.AcceleratedNetworkingEnabled === 'True',
        encryptionAtHost: capabilities.EncryptionAtHostSupported === 'True',
        gpuCount: parseInt(capabilities.GPUs) || 0,
        gpuType: capabilities.GPUName || null,
        nvme: capabilities.UncachedDiskIOPS && parseInt(capabilities.UncachedDiskIOPS) > 100000,
        uncachedDiskIOPS: parseInt(capabilities.UncachedDiskIOPS) || 0,
        uncachedDiskBytesPerSecond: parseInt(capabilities.UncachedDiskBytesPerSecond) || 0,
        cachedDiskIOPS: parseInt(capabilities.CachedDiskIOPS) || 0,
        cachedDiskBytesPerSecond: parseInt(capabilities.CachedDiskBytesPerSecond) || 0,
        maxWriteAcceleratorDisks: parseInt(capabilities.MaxWriteAcceleratorDisksAllowed) || 0
    };
}

/**
 * Calculate similarity score between two SKUs
 */
function calculateSimilarity(target, candidate, weights, tolerance) {
    let totalScore = 0;
    let totalWeight = 0;

    // CPU comparison
    if (target.vCPUs > 0) {
        const cpuDiff = Math.abs(target.vCPUs - candidate.vCPUs) / target.vCPUs;
        const cpuScore = Math.max(0, 100 - (cpuDiff * 100));
        totalScore += cpuScore * weights.weightCPU;
        totalWeight += weights.weightCPU;
    }

    // Memory comparison
    if (target.memoryGB > 0) {
        const memDiff = Math.abs(target.memoryGB - candidate.memoryGB) / target.memoryGB;
        const memScore = Math.max(0, 100 - (memDiff * 100));
        totalScore += memScore * weights.weightMemory;
        totalWeight += weights.weightMemory;
    }

    // GPU comparison
    if (target.gpuCount > 0 || candidate.gpuCount > 0) {
        const gpuMatch = target.gpuCount === candidate.gpuCount ? 100 : 0;
        totalScore += gpuMatch * weights.weightGPU;
        totalWeight += weights.weightGPU;
    }

    // Storage comparison
    if (target.uncachedDiskIOPS > 0) {
        const iopsDiff = Math.abs(target.uncachedDiskIOPS - candidate.uncachedDiskIOPS) / target.uncachedDiskIOPS;
        const iopsScore = Math.max(0, 100 - (iopsDiff * 100));
        totalScore += iopsScore * weights.weightStorage;
        totalWeight += weights.weightStorage;
    }

    // Network comparison
    if (target.maxNics > 0) {
        const nicDiff = Math.abs(target.maxNics - candidate.maxNics) / target.maxNics;
        const nicScore = Math.max(0, 100 - (nicDiff * 100));
        totalScore += nicScore * weights.weightNetwork;
        totalWeight += weights.weightNetwork;
    }

    // Features comparison
    const features = ['premiumIO', 'acceleratedNetworking', 'encryptionAtHost', 'ephemeralOSDisk'];
    let featureMatches = 0;
    features.forEach(feature => {
        if (target[feature] === candidate[feature]) featureMatches++;
    });
    const featureScore = (featureMatches / features.length) * 100;
    totalScore += featureScore * weights.weightFeatures;
    totalWeight += weights.weightFeatures;

    return totalWeight > 0 ? totalScore / totalWeight : 0;
}

/**
 * Get VM pricing from Azure Retail Prices API
 */
async function getVmPricing(skuName, location, currencyCode, context) {
    try {
        const apiUrl = 'https://prices.azure.com/api/retail/prices';
        const filter = `serviceName eq 'Virtual Machines' and armSkuName eq '${skuName}' and armRegionName eq '${location}' and type eq 'Consumption'`;
        const url = `${apiUrl}?currencyCode=${currencyCode}&$filter=${encodeURIComponent(filter)}`;

        const response = await fetch(url, {
            headers: {
                'Accept': 'application/json'
            }
        });

        if (!response.ok) {
            context.log.warn(`Failed to fetch pricing for ${skuName}: ${response.status} ${response.statusText}`);
            return null;
        }

        const data = await response.json();
        if (data.Items && data.Items.length > 0) {
            // Prefer Linux pricing
            let priceItem = data.Items.find(item =>
                item.productName && !item.productName.toLowerCase().includes('windows')
            );

            if (!priceItem) {
                priceItem = data.Items[0];
            }

            return {
                hourlyPrice: Math.round(priceItem.unitPrice * 10000) / 10000,
                monthlyPrice: Math.round(priceItem.unitPrice * 730 * 100) / 100,
                currency: priceItem.currencyCode
            };
        }

        return null;
    } catch (error) {
        context.log.warn(`Error fetching pricing for ${skuName}:`, error.message);
        return null;
    }
}

/**
 * Get availability zones for a SKU
 */
function getAvailabilityZones(sku, location) {
    const zones = [];
    if (sku.locationInfo) {
        for (const locInfo of sku.locationInfo) {
            if (locInfo.location === location && locInfo.zones) {
                return locInfo.zones.sort();
            }
        }
    }
    return zones;
}

/**
 * Get access token for Azure Resource Manager
 * Tries service principal first, falls back to managed identity
 */
async function getAccessToken(context) {
    const tenantId = process.env.AZURE_TENANT_ID;
    const clientId = process.env.AZURE_CLIENT_ID;
    const clientSecret = process.env.AZURE_CLIENT_SECRET;

    // Try service principal authentication first
    if (tenantId && clientId && clientSecret) {
        context.log('Using service principal authentication');
        return await getServicePrincipalToken(tenantId, clientId, clientSecret, context);
    }

    // Fall back to managed identity
    context.log('Attempting managed identity authentication');
    return await getManagedIdentityToken(context);
}

/**
 * Get token using service principal (client credentials flow)
 */
async function getServicePrincipalToken(tenantId, clientId, clientSecret, context) {
    const tokenUrl = `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`;

    const params = new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        scope: 'https://management.azure.com/.default',
        grant_type: 'client_credentials'
    });

    const response = await fetch(tokenUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: params
    });

    if (!response.ok) {
        const errorText = await response.text();
        context.log.error('Service principal auth failed:', errorText);
        throw new Error(`Failed to get service principal token: ${response.statusText}`);
    }

    const data = await response.json();
    return data.access_token;
}

/**
 * Get managed identity access token
 */
async function getManagedIdentityToken(context) {
    const msiEndpoint = process.env.MSI_ENDPOINT || process.env.IDENTITY_ENDPOINT;
    const msiSecret = process.env.MSI_SECRET || process.env.IDENTITY_HEADER;

    if (!msiEndpoint) {
        throw new Error('Managed identity not available. Configure service principal credentials.');
    }

    const tokenUrl = `${msiEndpoint}?resource=https://management.azure.com/&api-version=2019-08-01`;

    const response = await fetch(tokenUrl, {
        headers: {
            'X-IDENTITY-HEADER': msiSecret
        }
    });

    if (!response.ok) {
        throw new Error(`Failed to get managed identity token: ${response.statusText}`);
    }

    const data = await response.json();
    return data.access_token;
}

/**
 * Get VM SKUs for a location using REST API
 */
async function getVmSkusForLocation(subscriptionId, location, accessToken, context) {
    const apiVersion = '2021-07-01';
    const url = `https://management.azure.com/subscriptions/${subscriptionId}/providers/Microsoft.Compute/skus?api-version=${apiVersion}&$filter=location eq '${location}'`;

    const response = await fetch(url, {
        headers: {
            'Authorization': `Bearer ${accessToken}`,
            'Content-Type': 'application/json'
        }
    });

    if (!response.ok) {
        const errorText = await response.text();
        context.log.error(`Failed to fetch SKUs: ${response.status} ${errorText}`);
        throw new Error(`Failed to fetch VM SKUs: ${response.statusText}`);
    }

    const data = await response.json();
    const vmSkus = data.value.filter(sku => sku.resourceType === 'virtualMachines');

    context.log(`Found ${vmSkus.length} VM SKUs`);
    return vmSkus;
}
