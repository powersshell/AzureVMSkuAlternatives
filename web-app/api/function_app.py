"""
Azure Functions v2 Programming Model - Flex Consumption Compatible
All HTTP and Timer triggered functions consolidated into a single file
"""
import logging
import json
import os
import sys
import requests
from typing import Dict, List, Optional
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential

# Create the Function App instance
app = func.FunctionApp()


# ============================================================================
# HTTP Route: /compare_vms - Compare VM SKUs
# ============================================================================
@app.route(route="compare_vms", methods=["GET", "POST"], auth_level=func.AuthLevel.ANONYMOUS)
def compare_vms(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure Function to compare VM SKUs (Python)
    Retrieves Azure VM SKU information and finds similar alternatives
    Now with caching support via Azure Storage Tables
    """
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

        # Get all VM SKUs for the location (from cache or live API)
        logging.info(f'Fetching VM SKUs for location: {location}')
        all_skus, data_source = get_vm_skus_with_cache(subscription_id, location, access_token)
        logging.info(f'Retrieved {len(all_skus)} SKUs from {data_source}')

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
        # If USD requested and pricing in cache, use it; otherwise fetch from API
        if currency_code == 'USD' and target_sku.get('pricing'):
            target_pricing = target_sku.get('pricing')
            logging.info(f'Using cached USD pricing for target SKU: {sku_name}')
        else:
            if currency_code != 'USD':
                logging.info(f'Non-USD currency ({currency_code}) requested, fetching from API')
            else:
                logging.info(f'Pricing not in cache for {sku_name}, fetching from API')
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
                }
            )

            if similarity_score >= min_similarity_score:
                # Get pricing - use cache for USD, API for other currencies
                if currency_code == 'USD' and sku.get('pricing'):
                    pricing = sku.get('pricing')
                else:
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


# ============================================================================
# HTTP Route: /health - Health check endpoint
# ============================================================================
@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    """
    Health check endpoint for Azure Functions (Python)
    """
    logging.info('Health check endpoint called')

    response_data = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'message': 'API is running',
        'runtime': 'Python',
        'pythonVersion': sys.version
    }

    return func.HttpResponse(
        json.dumps(response_data),
        mimetype='application/json',
        status_code=200
    )


# ============================================================================
# HTTP Route: /skus - List available VM SKUs from cache
# ============================================================================
@app.route(route="skus", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def list_skus(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure Function to list available VM SKUs from cache
    Used to populate dropdown in frontend
    """
    logging.info('Processing list SKUs request')
    
    # Get location from query parameters
    location = req.params.get('location')
    
    if not location:
        return func.HttpResponse(
            json.dumps({'error': 'location parameter is required'}),
            mimetype='application/json',
            status_code=400
        )
    
    # Get storage account name from environment
    storage_account_name = os.environ.get('SKU_CACHE_STORAGE_ACCOUNT')
    
    if not storage_account_name:
        return func.HttpResponse(
            json.dumps({'error': 'SKU cache not configured'}),
            mimetype='application/json',
            status_code=500
        )
    
    try:
        # Initialize Table Service with managed identity
        credential = DefaultAzureCredential()
        table_service = TableServiceClient(
            endpoint=f"https://{storage_account_name}.table.core.windows.net",
            credential=credential
        )
        
        table_client = table_service.get_table_client("vmskus")
        
        # Query all SKUs for the location
        query_filter = f"PartitionKey eq '{location}'"
        entities = table_client.query_entities(query_filter=query_filter)
        
        # Format for frontend dropdown - minimal payload for performance
        skus = []
        for entity in entities:
            skus.append({
                'name': entity['name'],
                'vCPUs': entity['vCPUs'],
                'memoryGB': entity['memoryGB']
            })
        
        # Sort by vCPUs then memory
        skus.sort(key=lambda x: (x['vCPUs'], x['memoryGB']))
        
        response_data = {
            'location': location,
            'count': len(skus),
            'skus': skus
        }
        
        return func.HttpResponse(
            json.dumps(response_data),
            mimetype='application/json',
            status_code=200
        )
        
    except Exception as e:
        logging.error(f'Error listing SKUs: {e}')
        return func.HttpResponse(
            json.dumps({
                'error': 'Failed to retrieve SKU list',
                'details': str(e)
            }),
            mimetype='application/json',
            status_code=500
        )


# ============================================================================
# Timer Trigger: refresh_sku_cache - Daily SKU cache refresh
# ============================================================================
@app.timer_trigger(schedule="0 0 2 * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
def refresh_sku_cache(timer: func.TimerRequest) -> None:
    """
    Timer-triggered Azure Function to refresh VM SKU cache
    Runs daily at 2:00 AM UTC to populate Storage Table with latest SKU data
    Schedule: 0 0 2 * * * (Daily at 2:00 AM UTC)
    """
    logging.info('Starting SKU cache refresh...')
    
    # Get configuration
    storage_account_name = os.environ.get('SKU_CACHE_STORAGE_ACCOUNT')
    subscription_id = os.environ.get('AZURE_SUBSCRIPTION_ID')
    
    if not storage_account_name or not subscription_id:
        logging.error('Missing required environment variables: SKU_CACHE_STORAGE_ACCOUNT or AZURE_SUBSCRIPTION_ID')
        return
    
    # Initialize Table Service with managed identity
    credential = DefaultAzureCredential()
    table_service = TableServiceClient(
        endpoint=f"https://{storage_account_name}.table.core.windows.net",
        credential=credential
    )
    
    # Create table if not exists
    table_name = "vmskus"
    try:
        table_service.create_table_if_not_exists(table_name)
        logging.info(f"Table '{table_name}' is ready")
    except Exception as e:
        logging.error(f"Failed to create table: {e}")
        return
    
    table_client = table_service.get_table_client(table_name)
    
    # Get access token for Azure Management API
    try:
        token = get_access_token()
    except Exception as e:
        logging.error(f"Failed to get access token: {e}")
        return
    
    # List of key Azure regions to cache
    # Full list can be expanded, starting with most common regions
    regions = [
        'eastus', 'eastus2', 'westus', 'westus2', 'westus3',
        'centralus', 'northcentralus', 'southcentralus', 'westcentralus',
        'canadacentral', 'canadaeast',
        'brazilsouth',
        'northeurope', 'westeurope', 'uksouth', 'ukwest',
        'francecentral', 'germanywestcentral', 'norwayeast', 'switzerlandnorth',
        'swedencentral',
        'eastasia', 'southeastasia',
        'japaneast', 'japanwest',
        'australiaeast', 'australiasoutheast',
        'centralindia', 'southindia', 'westindia',
        'koreacentral', 'koreasouth',
        'uaenorth',
        'southafricanorth'
    ]
    
    total_updated = 0
    total_errors = 0
    
    for region in regions:
        try:
            logging.info(f"Processing region: {region}")
            count = refresh_region(region, subscription_id, token, table_client)
            total_updated += count
            logging.info(f"Updated {count} SKUs for region {region}")
        except Exception as e:
            logging.error(f"Error processing region {region}: {e}")
            total_errors += 1
    
    logging.info(f"SKU cache refresh completed. Updated: {total_updated}, Errors: {total_errors}")


# ============================================================================
# Helper Functions (shared across all functions)
# ============================================================================

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


def calculate_similarity(target: Dict, candidate: Dict, weights: Dict) -> float:
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


def get_vm_skus_with_cache(subscription_id: str, location: str, access_token: str) -> tuple[List[Dict], str]:
    """
    Get VM SKUs from cache with fallback to live API
    Returns: (list of SKUs, data_source)
    data_source: 'cache' or 'live_api'
    """
    storage_account_name = os.environ.get('SKU_CACHE_STORAGE_ACCOUNT')
    
    # Try cache first
    if storage_account_name:
        try:
            logging.info('Attempting to load SKUs from cache...')
            credential = DefaultAzureCredential()
            table_service = TableServiceClient(
                endpoint=f"https://{storage_account_name}.table.core.windows.net",
                credential=credential
            )
            
            table_client = table_service.get_table_client("vmskus")
            query_filter = f"PartitionKey eq '{location}'"
            entities = table_client.query_entities(query_filter=query_filter)
            
            skus = []
            for entity in entities:
                # Convert cached entity to SKU format
                sku = {
                    'name': entity['name'],
                    'capabilities': [
                        {'name': 'vCPUs', 'value': str(entity['vCPUs'])},
                        {'name': 'MemoryGB', 'value': str(entity['memoryGB'])},
                        {'name': 'MaxDataDiskCount', 'value': str(entity['maxDataDisks'])},
                        {'name': 'MaxNetworkInterfaces', 'value': str(entity['maxNics'])},
                        {'name': 'UncachedDiskIOPS', 'value': str(entity['uncachedDiskIOPS'])},
                        {'name': 'GPUs', 'value': str(entity['gpuCount'])},
                        {'name': 'GPUType', 'value': entity.get('gpuType', '')},
                        {'name': 'PremiumIO', 'value': 'True' if entity['premiumIO'] else 'False'},
                        {'name': 'AcceleratedNetworkingEnabled', 'value': 'True' if entity['acceleratedNetworking'] else 'False'},
                        {'name': 'EncryptionAtHostSupported', 'value': 'True' if entity['encryptionAtHost'] else 'False'},
                        {'name': 'EphemeralOSDiskSupported', 'value': 'True' if entity['ephemeralOSDisk'] else 'False'},
                        {'name': 'NVMe', 'value': 'True' if entity.get('nvme', False) else 'False'}
                    ],
                    'locationInfo': [{
                        'location': location,
                        'zones': entity.get('availabilityZones', '').split(',') if entity.get('availabilityZones') else []
                    }],
                    'pricing': {
                        'hourlyPrice': entity.get('hourlyPriceUSD'),
                        'monthlyPrice': entity.get('monthlyPriceUSD'),
                        'currency': entity.get('pricingCurrency', 'USD')
                    } if entity.get('hourlyPriceUSD') is not None else None
                }
                skus.append(sku)
            
            if skus:
                logging.info(f'Loaded {len(skus)} SKUs from cache')
                return skus, 'cache'
            else:
                logging.warning('Cache is empty, falling back to live API')
        except Exception as e:
            logging.warning(f'Failed to load from cache: {e}, falling back to live API')
    else:
        logging.info('Cache not configured, using live API')
    
    # Fallback to live API
    skus = get_vm_skus_for_location(subscription_id, location, access_token)
    return skus, 'live_api'


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


def fetch_pricing_concurrent(sku_names: List[str], location: str, max_workers: int = 20) -> Dict[str, Optional[Dict]]:
    """
    Fetch pricing for multiple SKUs concurrently using ThreadPoolExecutor
    Returns dict mapping SKU name to pricing data
    """
    pricing_results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all pricing fetch tasks
        future_to_sku = {
            executor.submit(get_vm_pricing, sku_name, location, 'USD'): sku_name 
            for sku_name in sku_names
        }
        
        # Collect results as they complete
        completed = 0
        total = len(sku_names)
        for future in as_completed(future_to_sku):
            sku_name = future_to_sku[future]
            completed += 1
            try:
                pricing = future.result()
                pricing_results[sku_name] = pricing
                if completed % 100 == 0:
                    logging.info(f"Fetched pricing for {completed}/{total} SKUs")
            except Exception as e:
                logging.warning(f'Failed to fetch pricing for {sku_name}: {e}')
                pricing_results[sku_name] = None
    
    logging.info(f"Completed pricing fetch: {completed}/{total} SKUs")
    return pricing_results


def refresh_region(region: str, subscription_id: str, token: str, table_client) -> int:
    """
    Refresh SKU data for a specific region
    Fetches SKU data and pricing concurrently for performance
    Returns number of SKUs updated
    """
    # Get VM SKUs from Azure Management API
    api_version = '2021-07-01'
    url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/skus?api-version={api_version}&$filter=location eq '{region}'"
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    response = requests.get(url, headers=headers, timeout=30)
    
    if not response.ok:
        raise Exception(f'Failed to fetch SKUs: {response.status_code}')
    
    data = response.json()
    skus = [s for s in data.get('value', []) if s.get('resourceType') == 'virtualMachines']
    
    logging.info(f"Fetching pricing for {len(skus)} SKUs in {region} concurrently...")
    
    # Fetch pricing concurrently for all SKUs
    pricing_data = fetch_pricing_concurrent([sku['name'] for sku in skus], region, max_workers=20)
    
    count = 0
    timestamp = datetime.now(timezone.utc).isoformat()
    
    for sku in skus:
        try:
            # Extract capabilities
            capabilities = extract_capabilities_for_cache(sku)
            
            # Get pricing from concurrent fetch results
            pricing = pricing_data.get(sku['name'])
            
            # Get availability zones
            zones = get_availability_zones(sku, region)
            
            # Create entity for Storage Table
            entity = {
                'PartitionKey': region,
                'RowKey': sku['name'],
                'name': sku['name'],
                'vCPUs': capabilities['vCPUs'],
                'memoryGB': capabilities['memoryGB'],
                'maxDataDisks': capabilities['maxDataDisks'],
                'maxNics': capabilities['maxNics'],
                'uncachedDiskIOPS': capabilities['uncachedDiskIOPS'],
                'gpuCount': capabilities['gpuCount'],
                'gpuType': capabilities['gpuType'] or '',
                'premiumIO': capabilities['premiumIO'],
                'acceleratedNetworking': capabilities['acceleratedNetworking'],
                'encryptionAtHost': capabilities['encryptionAtHost'],
                'ephemeralOSDisk': capabilities['ephemeralOSDisk'],
                'nvme': capabilities['nvme'],
                'hourlyPriceUSD': pricing['hourlyPrice'] if pricing else None,
                'monthlyPriceUSD': pricing['monthlyPrice'] if pricing else None,
                'pricingCurrency': pricing['currency'] if pricing else 'USD',
                'pricingLastUpdated': timestamp,
                'availabilityZones': ','.join(zones) if zones else '',
                'lastUpdated': timestamp
            }
            
            # Upsert entity (insert or update)
            table_client.upsert_entity(entity)
            count += 1
            
        except Exception as e:
            logging.warning(f"Failed to process SKU {sku.get('name', 'unknown')}: {e}")
    
    return count


def extract_capabilities_for_cache(sku: Dict) -> Dict:
    """Extract VM capabilities from SKU data (for cache refresh)"""
    capabilities = {cap['name']: cap['value'] for cap in sku.get('capabilities', [])}
    
    return {
        'vCPUs': int(capabilities.get('vCPUs', 0)),
        'memoryGB': float(capabilities.get('MemoryGB', 0)),
        'maxDataDisks': int(capabilities.get('MaxDataDiskCount', 0)),
        'maxNics': int(capabilities.get('MaxNetworkInterfaces', 0)),
        'uncachedDiskIOPS': int(capabilities.get('UncachedDiskIOPS', 0)),
        'gpuCount': int(capabilities.get('GPUs', 0)),
        'gpuType': capabilities.get('GPUType'),
        'premiumIO': capabilities.get('PremiumIO', '').lower() == 'true',
        'acceleratedNetworking': capabilities.get('AcceleratedNetworkingEnabled', '').lower() == 'true',
        'encryptionAtHost': capabilities.get('EncryptionAtHostSupported', '').lower() == 'true',
        'ephemeralOSDisk': capabilities.get('EphemeralOSDiskSupported', '').lower() == 'true',
        'nvme': capabilities.get('NVMe', '').lower() == 'true'
    }
