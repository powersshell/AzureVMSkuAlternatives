const { app } = require('@azure/functions');
const { DefaultAzureCredential } = require('@azure/identity');
const { ComputeManagementClient } = require('@azure/arm-compute');

/**
 * Azure Function to compare VM SKUs
 * This function retrieves Azure VM SKU information and finds similar alternatives
 */
app.http('compare-vms', {
    methods: ['POST'],
    authLevel: 'anonymous',
    handler: async (request, context) => {
        context.log('Processing VM comparison request');

        // Wrap everything in try-catch to ensure we always return JSON
        try {
            // Parse request body
            let body;
            try {
                body = await request.json();
            } catch (parseError) {
                context.log.error('Failed to parse request body:', parseError);
                return {
                    status: 400,
                    headers: { 'Content-Type': 'application/json' },
                    jsonBody: { error: 'Invalid JSON in request body' }
                };
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
                return {
                    status: 400,
                    headers: { 'Content-Type': 'application/json' },
                    jsonBody: { error: 'skuName and location are required' }
                };
            }

            // Get subscription ID from environment or use default
            const subscriptionId = process.env.AZURE_SUBSCRIPTION_ID || 'e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7';
            context.log(`Using subscription: ${subscriptionId}`);

            // Initialize Azure SDK with retry logic
            let credential, computeClient;
            try {
                credential = new DefaultAzureCredential({
                    managedIdentityClientId: process.env.AZURE_CLIENT_ID,
                    additionallyAllowedTenants: ['*']
                });
                computeClient = new ComputeManagementClient(credential, subscriptionId);
                context.log('Azure credentials initialized successfully');
            } catch (authError) {
                context.log.error('Authentication error:', authError);
                return {
                    status: 500,
                    headers: { 'Content-Type': 'application/json' },
                    jsonBody: { 
                        error: 'Authentication failed. Managed identity may not be configured.',
                        details: authError.message,
                        hint: 'Ensure the Static Web App has a system-assigned managed identity with Reader permissions.'
                    }
                };
            }

            // Get all VM SKUs for the location
            context.log(`Fetching VM SKUs for location: ${location}`);
            const skusIterator = computeClient.resourceSkus.list({ filter: `location eq '${location}'` });
            const allSkus = [];

            for await (const sku of skusIterator) {
                if (sku.resourceType === 'virtualMachines') {
                    allSkus.push(sku);
                }
            }

            context.log(`Found ${allSkus.length} VM SKUs`);

            // Find target SKU
            const targetSku = allSkus.find(s => s.name === skuName);
            if (!targetSku) {
                return {
                    status: 404,
                    headers: { 'Content-Type': 'application/json' },
                    jsonBody: { error: `SKU '${skuName}' not found in location '${location}'` }
                };
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
            return {
                status: 200,
                headers: {
                    'Content-Type': 'application/json'
                },
                jsonBody: {
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
                }
            };

        } catch (error) {
            context.log.error('Error processing request:', error);
            context.log.error('Error stack:', error.stack);
            return {
                status: 500,
                headers: { 'Content-Type': 'application/json' },
                jsonBody: {
                    error: 'Internal server error',
                    details: error.message,
                    type: error.constructor.name
                }
            };
        }
    }
});

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
        const iopsff = Math.abs(target.uncachedDiskIOPS - candidate.uncachedDiskIOPS) / target.uncachedDiskIOPS;
        const iopScore = Math.max(0, 100 - (iopsff * 100));
        totalScore += iopScore * weights.weightStorage;
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

        const response = await fetch(url);
        if (!response.ok) {
            context.log.warn(`Failed to fetch pricing for ${skuName}: ${response.statusText}`);
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
