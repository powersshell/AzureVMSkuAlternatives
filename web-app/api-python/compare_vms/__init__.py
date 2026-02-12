"""
Azure Function to compare VM SKUs (Python)
Retrieves Azure VM SKU information and finds similar alternatives
"""
import logging
import json
import os
import requests
from typing import Dict, List, Optional
import azure.functions as func


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing VM comparison request')

    # Handle GET request
    if req.method == 'GET':
        return func.HttpResponse(
            json.dumps({
                'message': 'compare-vms endpoint is running (Python)',
                'timestamp': '2026-02-12T00:00:00Z',
                'environment': os.environ.get('AZURE_FUNCTIONS_ENVIRONMENT', 'Production')
            }),
            mimetype='application/json',
            status_code=200
        )

    # Handle POST request
    try:
        # Parse request body
        try:
            req_body = req.get_json()
        except ValueError:
            return func.HttpResponse(
                json.dumps({'error': 'Invalid JSON in request body'}),
                mimetype='application/json',
                status_code=400
            )

        # Extract parameters with defaults
        sku_name = req_body.get('skuName')
        location = req_body.get('location')
        tolerance = req_body.get('tolerance', 20)
        min_similarity_score = req_body.get('minSimilarityScore', 60)
        currency_code = req_body.get('currencyCode', 'USD')
        weight_cpu = req_body.get('weightCPU', 2.0)
        weight_memory = req_body.get('weightMemory', 2.0)
        weight_gpu = req_body.get('weightGPU', 2.0)
        weight_storage = req_body.get('weightStorage', 1.0)
        weight_network = req_body.get('weightNetwork', 1.0)
        weight_features = req_body.get('weightFeatures', 0.5)
        require_nvme_match = req_body.get('requireNVMeMatch', False)
        require_gpu_match = req_body.get('requireGPUMatch', False)

        # Validate inputs
        if not sku_name or not location:
            return func.HttpResponse(
                json.dumps({'error': 'skuName and location are required'}),
                mimetype='application/json',
                status_code=400
            )

        # Get subscription ID from environment
        subscription_id = os.environ.get('AZURE_SUBSCRIPTION_ID')
        if not subscription_id:
            logging.error('AZURE_SUBSCRIPTION_ID environment variable is not set')
            return func.HttpResponse(
                json.dumps({
                    'error': 'Server configuration error',
                    'details': 'Azure subscription ID is not configured'
                }),
                mimetype='application/json',
                status_code=500
            )

        logging.info(f'Using subscription: {subscription_id}')

        # Get access token
        try:
            access_token = get_access_token()
            logging.info('Access token obtained successfully')
        except Exception as auth_error:
            logging.error(f'Authentication error: {auth_error}')
            return func.HttpResponse(
                json.dumps({
                    'error': 'Authentication failed',
                    'details': str(auth_error)
                }),
                mimetype='application/json',
                status_code=500
            )

        # Get all VM SKUs for the location
        logging.info(f'Fetching VM SKUs for location: {location}')
        all_skus = get_vm_skus_for_location(subscription_id, location, access_token)

        # Find target SKU
        target_sku = next((s for s in all_skus if s['name'] == sku_name), None)
        if not target_sku:
            return func.HttpResponse(
                json.dumps({'error': f"SKU '{sku_name}' not found in location '{location}'"}),
                mimetype='application/json',
                status_code=404
            )

        # Extract target capabilities
        target_capabilities = extract_capabilities(target_sku)

        # Get pricing for target SKU
        target_pricing = get_vm_pricing(sku_name, location, currency_code)

        # Get availability zones
        target_zones = get_availability_zones(target_sku, location)

        # Compare with all other SKUs
        alternatives = []
        for sku in all_skus:
            if sku['name'] == sku_name:
                continue  # Skip the target itself

            sku_capabilities = extract_capabilities(sku)

            # Apply filters
            if require_nvme_match and target_capabilities['nvme'] and not sku_capabilities['nvme']:
                continue
            if require_gpu_match and target_capabilities['gpuCount'] > 0 and sku_capabilities['gpuCount'] == 0:
                continue

            # Calculate similarity score
            similarity_score = calculate_similarity(
                target_capabilities,
                sku_capabilities,
                {
                    'weightCPU': weight_cpu,
                    'weightMemory': weight_memory,
                    'weightGPU': weight_gpu,
                    'weightStorage': weight_storage,
                    'weightNetwork': weight_network,
                    'weightFeatures': weight_features
                },
                tolerance
            )

            if similarity_score >= min_similarity_score:
                pricing = get_vm_pricing(sku['name'], location, currency_code)
                zones = get_availability_zones(sku, location)

                alternatives.append({
                    'name': sku['name'],
                    'similarityScore': round(similarity_score, 2),
                    'vCPUs': sku_capabilities['vCPUs'],
                    'memoryGB': sku_capabilities['memoryGB'],
                    'gpuCount': sku_capabilities['gpuCount'],
                    'gpuType': sku_capabilities['gpuType'],
                    'pricing': pricing,
                    'zones': ', '.join(zones) if zones else 'N/A',
                    'capabilities': sku_capabilities
                })

        # Sort by similarity score
        alternatives.sort(key=lambda x: x['similarityScore'], reverse=True)

        logging.info(f'Found {len(alternatives)} alternatives')

        # Return results
        response_data = {
            'targetSku': {
                'name': target_sku['name'],
                'vCPUs': target_capabilities['vCPUs'],
                'memoryGB': target_capabilities['memoryGB'],
                'gpuCount': target_capabilities['gpuCount'],
                'gpuType': target_capabilities['gpuType'],
                'pricing': target_pricing,
                'zones': ', '.join(target_zones) if target_zones else 'N/A',
                'capabilities': target_capabilities
            },
            'alternatives': alternatives,
            'searchParameters': {
                'location': location,
                'tolerance': tolerance,
                'minSimilarityScore': min_similarity_score,
                'weights': {
                    'cpu': weight_cpu,
                    'memory': weight_memory,
                    'gpu': weight_gpu,
                    'storage': weight_storage,
                    'network': weight_network,
                    'features': weight_features
                }
            }
        }

        return func.HttpResponse(
            json.dumps(response_data),
            mimetype='application/json',
            status_code=200
        )

    except Exception as error:
        logging.error(f'Error processing request: {error}', exc_info=True)
        return func.HttpResponse(
            json.dumps({
                'error': 'Internal server error',
                'details': str(error),
                'type': type(error).__name__
            }),
            mimetype='application/json',
            status_code=500
        )


def extract_capabilities(sku: Dict) -> Dict:
    """Extract capabilities from a SKU"""
    capabilities = {}
    if 'capabilities' in sku:
        for cap in sku['capabilities']:
            capabilities[cap['name']] = cap['value']

    return {
        'vCPUs': int(capabilities.get('vCPUs', 0)),
        'memoryGB': float(capabilities.get('MemoryGB', 0)),
        'maxDataDiskCount': int(capabilities.get('MaxDataDiskCount', 0)),
        'maxNics': int(capabilities.get('MaxNetworkInterfaces', 0)),
        'premiumIO': capabilities.get('PremiumIO') == 'True',
        'ephemeralOSDisk': capabilities.get('EphemeralOSDiskSupported') == 'True',
        'acceleratedNetworking': capabilities.get('AcceleratedNetworkingEnabled') == 'True',
        'encryptionAtHost': capabilities.get('EncryptionAtHostSupported') == 'True',
        'gpuCount': int(capabilities.get('GPUs', 0)),
        'gpuType': capabilities.get('GPUName'),
        'nvme': int(capabilities.get('UncachedDiskIOPS', 0)) > 100000,
        'uncachedDiskIOPS': int(capabilities.get('UncachedDiskIOPS', 0)),
        'uncachedDiskBytesPerSecond': int(capabilities.get('UncachedDiskBytesPerSecond', 0)),
        'cachedDiskIOPS': int(capabilities.get('CachedDiskIOPS', 0)),
        'cachedDiskBytesPerSecond': int(capabilities.get('CachedDiskBytesPerSecond', 0)),
        'maxWriteAcceleratorDisks': int(capabilities.get('MaxWriteAcceleratorDisksAllowed', 0))
    }


def calculate_similarity(target: Dict, candidate: Dict, weights: Dict, tolerance: int) -> float:
    """Calculate similarity score between two SKUs"""
    total_score = 0.0
    total_weight = 0.0

    # CPU comparison
    if target['vCPUs'] > 0:
        cpu_diff = abs(target['vCPUs'] - candidate['vCPUs']) / target['vCPUs']
        cpu_score = max(0, 100 - (cpu_diff * 100))
        total_score += cpu_score * weights['weightCPU']
        total_weight += weights['weightCPU']

    # Memory comparison
    if target['memoryGB'] > 0:
        mem_diff = abs(target['memoryGB'] - candidate['memoryGB']) / target['memoryGB']
        mem_score = max(0, 100 - (mem_diff * 100))
        total_score += mem_score * weights['weightMemory']
        total_weight += weights['weightMemory']

    # GPU comparison
    if target['gpuCount'] > 0 or candidate['gpuCount'] > 0:
        gpu_match = 100 if target['gpuCount'] == candidate['gpuCount'] else 0
        total_score += gpu_match * weights['weightGPU']
        total_weight += weights['weightGPU']

    # Storage comparison
    if target['uncachedDiskIOPS'] > 0:
        iops_diff = abs(target['uncachedDiskIOPS'] - candidate['uncachedDiskIOPS']) / target['uncachedDiskIOPS']
        iops_score = max(0, 100 - (iops_diff * 100))
        total_score += iops_score * weights['weightStorage']
        total_weight += weights['weightStorage']

    # Network comparison
    if target['maxNics'] > 0:
        nic_diff = abs(target['maxNics'] - candidate['maxNics']) / target['maxNics']
        nic_score = max(0, 100 - (nic_diff * 100))
        total_score += nic_score * weights['weightNetwork']
        total_weight += weights['weightNetwork']

    # Features comparison
    features = ['premiumIO', 'acceleratedNetworking', 'encryptionAtHost', 'ephemeralOSDisk']
    feature_matches = sum(1 for f in features if target[f] == candidate[f])
    feature_score = (feature_matches / len(features)) * 100
    total_score += feature_score * weights['weightFeatures']
    total_weight += weights['weightFeatures']

    return total_score / total_weight if total_weight > 0 else 0


def get_vm_pricing(sku_name: str, location: str, currency_code: str) -> Optional[Dict]:
    """Get VM pricing from Azure Retail Prices API"""
    try:
        api_url = 'https://prices.azure.com/api/retail/prices'
        filter_str = f"serviceName eq 'Virtual Machines' and armSkuName eq '{sku_name}' and armRegionName eq '{location}' and type eq 'Consumption'"
        url = f"{api_url}?currencyCode={currency_code}&$filter={filter_str}"

        response = requests.get(url, headers={'Accept': 'application/json'}, timeout=10)

        if not response.ok:
            logging.warning(f'Failed to fetch pricing for {sku_name}: {response.status_code}')
            return None

        data = response.json()
        if data.get('Items'):
            # Prefer Linux pricing
            price_item = next((item for item in data['Items'] 
                             if 'productName' in item and 'windows' not in item['productName'].lower()), None)

            if not price_item:
                price_item = data['Items'][0]

            return {
                'hourlyPrice': round(price_item['unitPrice'], 4),
                'monthlyPrice': round(price_item['unitPrice'] * 730, 2),
                'currency': price_item['currencyCode']
            }

        return None
    except Exception as error:
        logging.warning(f'Error fetching pricing for {sku_name}: {error}')
        return None


def get_availability_zones(sku: Dict, location: str) -> List[str]:
    """Get availability zones for a SKU"""
    if 'locationInfo' in sku:
        for loc_info in sku['locationInfo']:
            if loc_info.get('location') == location and loc_info.get('zones'):
                return sorted(loc_info['zones'])
    return []


def get_access_token() -> str:
    """Get access token for Azure Resource Manager using Managed Identity"""
    msi_endpoint = os.environ.get('MSI_ENDPOINT') or os.environ.get('IDENTITY_ENDPOINT')
    msi_secret = os.environ.get('MSI_SECRET') or os.environ.get('IDENTITY_HEADER')

    if not msi_endpoint:
        raise Exception('Managed identity not available')

    token_url = f"{msi_endpoint}?resource=https://management.azure.com/&api-version=2019-08-01"

    response = requests.get(
        token_url,
        headers={'X-IDENTITY-HEADER': msi_secret},
        timeout=10
    )

    if not response.ok:
        raise Exception(f'Failed to get managed identity token: {response.status_code}')

    data = response.json()
    return data['access_token']


def get_vm_skus_for_location(subscription_id: str, location: str, access_token: str) -> List[Dict]:
    """Get VM SKUs for a location using REST API"""
    api_version = '2021-07-01'
    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/skus?api-version={api_version}&$filter=location eq '{location}'"

    response = requests.get(
        url,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        },
        timeout=30
    )

    if not response.ok:
        error_text = response.text
        logging.error(f'Failed to fetch SKUs: {response.status_code} {error_text}')
        raise Exception(f'Failed to fetch VM SKUs: {response.status_code}')

    data = response.json()
    vm_skus = [sku for sku in data.get('value', []) if sku.get('resourceType') == 'virtualMachines']

    logging.info(f'Found {len(vm_skus)} VM SKUs')
    return vm_skus
