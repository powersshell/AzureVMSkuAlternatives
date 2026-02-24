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
                    'cpuVendor': sku.get('cpuVendor', 'Intel'),
                    'architecture': sku.get('architecture', 'x64'),
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
                'cpuVendor': target_sku.get('cpuVendor', 'Intel'),
                'architecture': target_sku.get('architecture', 'x64'),
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
# HTTP Route: /compare_details - Get detailed comparison between two SKUs
# ============================================================================
@app.route(route="compare_details", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def compare_details(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get detailed comparison between two specific SKUs.
    Called when user expands a row to see full details.
    
    Query Parameters:
    - target: Target SKU name
    - alternative: Alternative SKU name
    - location: Azure region
    - currency: (optional) Currency code, default USD
    """
    logging.info('Processing compare_details request')
    
    try:
        # Get parameters
        target_name = req.params.get('target')
        alternative_name = req.params.get('alternative')
        location = req.params.get('location')
        currency_code = req.params.get('currency', 'USD')
        
        if not target_name or not alternative_name or not location:
            return func.HttpResponse(
                json.dumps({'error': 'Missing required parameters: target, alternative, location'}),
                mimetype='application/json',
                status_code=400
            )
        
        # Get SKUs from cache
        target_sku = get_sku_from_cache(target_name, location)
        alt_sku = get_sku_from_cache(alternative_name, location)
        
        if not target_sku:
            return func.HttpResponse(
                json.dumps({'error': f'Target SKU not found: {target_name}'}),
                mimetype='application/json',
                status_code=404
            )
        
        if not alt_sku:
            return func.HttpResponse(
                json.dumps({'error': f'Alternative SKU not found: {alternative_name}'}),
                mimetype='application/json',
                status_code=404
            )
        
        # Get pricing
        if currency_code == 'USD' and target_sku.get('pricing'):
            target_pricing = target_sku.get('pricing')
        else:
            target_pricing = get_vm_pricing(target_name, location, currency_code)
        
        if currency_code == 'USD' and alt_sku.get('pricing'):
            alt_pricing = alt_sku.get('pricing')
        else:
            alt_pricing = get_vm_pricing(alternative_name, location, currency_code)
        
        # Calculate detailed differences
        differences = calculate_detailed_differences(
            target_sku, alt_sku,
            target_pricing, alt_pricing
        )
        
        # Return response
        response_data = {
            'target': target_name,
            'alternative': alternative_name,
            'location': location,
            'differences': differences
        }
        
        return func.HttpResponse(
            json.dumps(response_data),
            mimetype='application/json',
            status_code=200
        )
        
    except Exception as e:
        logging.error(f'Error in compare_details: {str(e)}')
        return func.HttpResponse(
            json.dumps({'error': f'Internal server error: {str(e)}'}),
            mimetype='application/json',
            status_code=500
        )


def get_sku_from_cache(sku_name: str, location: str) -> dict:
    """Get single SKU details from cache."""
    try:
        storage_account_name = os.environ.get('SKU_CACHE_STORAGE_ACCOUNT')
        if not storage_account_name:
            return None
        
        credential = DefaultAzureCredential()
        table_service_client = TableServiceClient(
            endpoint=f"https://{storage_account_name}.table.core.windows.net/",
            credential=credential
        )
        table_client = table_service_client.get_table_client(table_name="vmskus")
        
        # Query for specific SKU
        entity = table_client.get_entity(partition_key=location, row_key=sku_name)
        
        # Parse capabilities JSON if present
        if 'capabilities' in entity and entity['capabilities']:
            try:
                entity['capabilities'] = json.loads(entity['capabilities'])
            except:
                entity['capabilities'] = {}
        
        # Parse pricing if present
        if 'pricing' in entity and entity['pricing']:
            try:
                entity['pricing'] = json.loads(entity['pricing'])
            except:
                entity['pricing'] = None
        
        return entity
        
    except Exception as e:
        logging.error(f'Error fetching SKU from cache: {str(e)}')
        return None


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
                'memoryGB': entity['memoryGB'],
                'cpuVendor': entity.get('cpuVendor', 'Intel'),  # Default to Intel if missing
                'architecture': entity.get('architecture', 'x64')  # Default to x64 if missing
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
        'nvme': int(capabilities.get('NvmeDiskSizeInMiB', 0)) > 0,
        'uncachedDiskIOPS': int(capabilities.get('UncachedDiskIOPS', 0)),
        'uncachedDiskBytesPerSecond': int(capabilities.get('UncachedDiskBytesPerSecond', 0)),
        'maxWriteAcceleratorDisks': int(capabilities.get('MaxWriteAcceleratorDisksAllowed', 0)),
        'osVhdSizeMB': int(capabilities.get('OSVhdSizeMB', 0)),
        'capacityReservationSupported': capabilities.get('CapacityReservationSupported') == 'True'
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
                    'cpuVendor': entity.get('cpuVendor', 'Intel'),
                    'architecture': entity.get('architecture', 'x64'),
                    'capabilities': [
                        {'name': 'vCPUs', 'value': str(entity['vCPUs'])},
                        {'name': 'MemoryGB', 'value': str(entity['memoryGB'])},
                        {'name': 'MaxDataDiskCount', 'value': str(entity['maxDataDisks'])},
                        {'name': 'MaxNetworkInterfaces', 'value': str(entity['maxNics'])},
                        {'name': 'UncachedDiskIOPS', 'value': str(entity['uncachedDiskIOPS'])},
                        {'name': 'UncachedDiskBytesPerSecond', 'value': str(entity.get('uncachedDiskBytesPerSecond', 0))},
                        {'name': 'GPUs', 'value': str(entity['gpuCount'])},
                        {'name': 'GPUType', 'value': entity.get('gpuType', '')},
                        {'name': 'PremiumIO', 'value': 'True' if entity['premiumIO'] else 'False'},
                        {'name': 'AcceleratedNetworkingEnabled', 'value': 'True' if entity['acceleratedNetworking'] else 'False'},
                        {'name': 'EncryptionAtHostSupported', 'value': 'True' if entity['encryptionAtHost'] else 'False'},
                        {'name': 'EphemeralOSDiskSupported', 'value': 'True' if entity['ephemeralOSDisk'] else 'False'},
                        {'name': 'NvmeDiskSizeInMiB', 'value': '1' if entity.get('nvme', False) else '0'},
                        {'name': 'OSVhdSizeMB', 'value': str(entity.get('osVhdSizeMB', 0))},
                        {'name': 'CapacityReservationSupported', 'value': 'True' if entity.get('capacityReservationSupported', False) else 'False'}
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
            # Extract capabilities (includes architecture from Azure API)
            capabilities = extract_capabilities_for_cache(sku)
            
            # Detect CPU vendor from SKU name + architecture
            cpu_vendor = detect_cpu_vendor(sku['name'], capabilities.get('architecture', 'x64'))
            
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
                'uncachedDiskBytesPerSecond': capabilities['uncachedDiskBytesPerSecond'],
                'gpuCount': capabilities['gpuCount'],
                'gpuType': capabilities['gpuType'] or '',
                'premiumIO': capabilities['premiumIO'],
                'acceleratedNetworking': capabilities['acceleratedNetworking'],
                'encryptionAtHost': capabilities['encryptionAtHost'],
                'ephemeralOSDisk': capabilities['ephemeralOSDisk'],
                'nvme': capabilities['nvme'],
                'architecture': capabilities['architecture'],
                'cpuVendor': cpu_vendor,
                'osVhdSizeMB': capabilities['osVhdSizeMB'],
                'capacityReservationSupported': capabilities['capacityReservationSupported'],
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
        'uncachedDiskBytesPerSecond': int(capabilities.get('UncachedDiskBytesPerSecond', 0)),
        'gpuCount': int(capabilities.get('GPUs', 0)),
        'gpuType': capabilities.get('GPUType'),
        'premiumIO': capabilities.get('PremiumIO', '').lower() == 'true',
        'acceleratedNetworking': capabilities.get('AcceleratedNetworkingEnabled', '').lower() == 'true',
        'encryptionAtHost': capabilities.get('EncryptionAtHostSupported', '').lower() == 'true',
        'ephemeralOSDisk': capabilities.get('EphemeralOSDiskSupported', '').lower() == 'true',
        'nvme': int(capabilities.get('NvmeDiskSizeInMiB', 0)) > 0,
        'architecture': capabilities.get('CpuArchitectureType', 'x64'),
        'osVhdSizeMB': int(capabilities.get('OSVhdSizeMB', 0)),
        'capacityReservationSupported': capabilities.get('CapacityReservationSupported', '').lower() == 'true'
    }


def detect_cpu_vendor(sku_name: str, architecture: str) -> str:
    """
    Detect CPU vendor from SKU name and architecture
    Returns: 'Intel', 'AMD', or 'ARM'
    """
    import re
    
    sku_lower = sku_name.lower()
    
    # ARM - Architecture is Arm64
    if architecture and architecture.lower() in ['arm64', 'arm']:
        return 'ARM'
    
    # AMD - Contains 'a' before 's' in suffix
    # Patterns: 
    #   - Standard_D2as_v5 (AMD standard storage)
    #   - Standard_D2ads_v5 (AMD with disk storage)
    #   - Standard_D2als_v6 (AMD with local storage)
    #   - Standard_D2alds_v6 (AMD with local + disk storage)
    #   - Standard_D2adls_v6 (AMD with disk + local storage)
    #   - Standard_D2a_v4 (AMD without storage suffix)
    # Match: 'a' + zero or more ('d' or 'l') + 's' + '_v' + version number
    if re.search(r'a[dl]*s_v\d', sku_lower) or re.search(r'_a_v\d', sku_lower):
        return 'AMD'
    
    # Intel - Default for x64 that don't match AMD pattern
    return 'Intel'


# ============================================================================
# Helper Functions for SKU Comparison Details (Phase 2)
# ============================================================================

def extract_capabilities_for_diff(sku: dict) -> dict:
    """Extract capabilities from cached SKU for detailed comparison."""
    caps = {}
    
    # The cached SKUs already have parsed capabilities at the top level
    # Not nested in a 'capabilities' field
    caps['maxDataDisks'] = sku.get('maxDataDisks')
    caps['uncachedDiskIOPS'] = sku.get('uncachedDiskIOPS')
    raw_uncached_bps = sku.get('uncachedDiskBytesPerSecond') or 0
    caps['uncachedDiskThroughput'] = raw_uncached_bps // (1024 * 1024) if raw_uncached_bps > 0 else 0
    caps['premiumIO'] = sku.get('premiumIO', False)
    caps['ephemeralOSDisk'] = sku.get('ephemeralOSDisk', False)
    caps['nvme'] = sku.get('nvme', False)
    caps['osVhdSizeMB'] = sku.get('osVhdSizeMB')
    caps['maxNics'] = sku.get('maxNics')
    caps['acceleratedNetworking'] = sku.get('acceleratedNetworking', False)
    caps['capacityReservationSupported'] = sku.get('capacityReservationSupported', False)
    
    # Ensure numeric fields are numbers, not strings (Table Storage may return strings)
    for key in ['maxDataDisks', 'uncachedDiskIOPS', 'uncachedDiskThroughput', 'maxNics', 'osVhdSizeMB']:
        if caps.get(key) is None:
            caps[key] = None
        elif not isinstance(caps[key], (int, float)):
            try:
                caps[key] = float(caps[key])
            except:
                caps[key] = None
    
    return caps


def calculate_numeric_diff(target_val, alt_val, unit: str = '') -> dict:
    """Calculate difference for numeric values with percentage change."""
    if target_val is None or alt_val is None:
        return {
            'target': target_val,
            'alternative': alt_val,
            'changed': False,
            'unit': unit
        }
    
    delta = alt_val - target_val
    if target_val == 0:
        percent_change = None
    else:
        percent_change = (delta / target_val) * 100
    
    return {
        'target': target_val,
        'alternative': alt_val,
        'delta': delta,
        'percentChange': round(percent_change, 1) if percent_change is not None else None,
        'direction': 'upgrade' if delta > 0 else 'downgrade' if delta < 0 else 'same',
        'changed': delta != 0,
        'unit': unit
    }


def calculate_price_diff(target_price, alt_price, currency: str) -> dict:
    """Calculate price difference (higher = negative, lower = positive)."""
    if target_price is None or alt_price is None:
        return {
            'target': target_price,
            'alternative': alt_price,
            'changed': False,
            'currency': currency
        }
    
    delta = alt_price - target_price
    percent_change = (delta / target_price) * 100 if target_price != 0 else None
    
    return {
        'target': round(target_price, 4),
        'alternative': round(alt_price, 4),
        'delta': round(delta, 4),
        'percentChange': round(percent_change, 1) if percent_change is not None else None,
        'direction': 'higher' if delta > 0 else 'lower' if delta < 0 else 'same',
        'changed': delta != 0,
        'currency': currency,
        'isPositive': delta < 0,  # Lower price = positive
        'isNegative': delta > 0   # Higher price = negative
    }


def calculate_cost_efficiency(target_sku, alt_sku, target_pricing, alt_pricing) -> dict:
    """Calculate cost per vCPU and cost per GB metrics."""
    target_vcpus = target_sku.get('vCPUs', 0)
    alt_vcpus = alt_sku.get('vCPUs', 0)
    target_memory = target_sku.get('memoryGB', 0)
    alt_memory = alt_sku.get('memoryGB', 0)
    target_price = target_pricing.get('hourlyPrice', 0)
    alt_price = alt_pricing.get('hourlyPrice', 0)
    
    efficiency = {}
    
    if target_vcpus > 0 and alt_vcpus > 0:
        target_cost_per_vcpu = target_price / target_vcpus
        alt_cost_per_vcpu = alt_price / alt_vcpus
        efficiency['costPerVCPU'] = {
            'target': round(target_cost_per_vcpu, 4),
            'alternative': round(alt_cost_per_vcpu, 4),
            'delta': round(alt_cost_per_vcpu - target_cost_per_vcpu, 4),
            'betterEfficiency': alt_cost_per_vcpu <= target_cost_per_vcpu
        }
    
    if target_memory > 0 and alt_memory > 0:
        target_cost_per_gb = target_price / target_memory
        alt_cost_per_gb = alt_price / alt_memory
        efficiency['costPerGB'] = {
            'target': round(target_cost_per_gb, 4),
            'alternative': round(alt_cost_per_gb, 4),
            'delta': round(alt_cost_per_gb - target_cost_per_gb, 4),
            'betterEfficiency': alt_cost_per_gb <= target_cost_per_gb
        }
    
    return efficiency


def calculate_boolean_diff(target_val: bool, alt_val: bool, feature_name: str) -> dict:
    """Calculate difference for boolean features."""
    changed = target_val != alt_val
    
    if changed:
        if not target_val and alt_val:
            direction = 'added'
        elif target_val and not alt_val:
            direction = 'removed'
        else:
            direction = 'changed'
    else:
        direction = 'same'
    
    return {
        'target': target_val,
        'alternative': alt_val,
        'changed': changed,
        'direction': direction,
        'feature': feature_name
    }


def calculate_detailed_differences(target_sku: dict, alternative_sku: dict, 
                                   target_pricing: dict, alt_pricing: dict) -> dict:
    """
    Calculate comprehensive differences between target and alternative SKU.
    Returns structured difference object with deltas, percentages, and directions.
    """
    differences = {}
    
    # Extract capabilities for comparison (used in compute, storage, and network sections)
    target_caps = extract_capabilities_for_diff(target_sku)
    alt_caps = extract_capabilities_for_diff(alternative_sku)
    
    # Compute differences
    differences['compute'] = {
        'vCPUs': calculate_numeric_diff(
            target_sku.get('vCPUs'), 
            alternative_sku.get('vCPUs'),
            'cores'
        ),
        'memory': calculate_numeric_diff(
            target_sku.get('memoryGB'), 
            alternative_sku.get('memoryGB'),
            'GB'
        ),
        'capacityReservation': calculate_boolean_diff(
            target_caps.get('capacityReservationSupported', False),
            alt_caps.get('capacityReservationSupported', False),
            'Capacity Reservation'
        )
    }
    
    # Price differences
    if target_pricing and alt_pricing:
        differences['pricing'] = {
            'hourly': calculate_price_diff(
                target_pricing.get('hourlyPrice'),
                alt_pricing.get('hourlyPrice'),
                target_pricing.get('currency', 'USD')
            ),
            'monthly': calculate_price_diff(
                target_pricing.get('monthlyPrice'),
                alt_pricing.get('monthlyPrice'),
                target_pricing.get('currency', 'USD')
            ),
            'efficiency': calculate_cost_efficiency(
                target_sku, alternative_sku,
                target_pricing, alt_pricing
            )
        }
    
    # Storage differences
    differences['storage'] = {
        'maxDataDisks': calculate_numeric_diff(
            target_caps.get('maxDataDisks'),
            alt_caps.get('maxDataDisks'),
            'disks'
        ),
        'uncachedIOPS': calculate_numeric_diff(
            target_caps.get('uncachedDiskIOPS'),
            alt_caps.get('uncachedDiskIOPS'),
            'IOPS'
        ),
        'uncachedThroughput': calculate_numeric_diff(
            target_caps.get('uncachedDiskThroughput'),
            alt_caps.get('uncachedDiskThroughput'),
            'MB/s'
        ),
        'osVhdSizeMB': calculate_numeric_diff(
            target_caps.get('osVhdSizeMB'),
            alt_caps.get('osVhdSizeMB'),
            'MB'
        ),
        'premiumIO': calculate_boolean_diff(
            target_caps.get('premiumIO', False),
            alt_caps.get('premiumIO', False),
            'Premium IO'
        ),
        'ephemeralOSDisk': calculate_boolean_diff(
            target_caps.get('ephemeralOSDisk', False),
            alt_caps.get('ephemeralOSDisk', False),
            'Ephemeral OS Disk'
        ),
        'nvmeSupport': calculate_boolean_diff(
            target_caps.get('nvme', False),
            alt_caps.get('nvme', False),
            'NVMe Support'
        )
    }
    
    # Network differences
    differences['network'] = {
        'maxNics': calculate_numeric_diff(
            target_caps.get('maxNics'),
            alt_caps.get('maxNics'),
            'NICs'
        ),
        'acceleratedNetworking': calculate_boolean_diff(
            target_caps.get('acceleratedNetworking', False),
            alt_caps.get('acceleratedNetworking', False),
            'Accelerated Networking'
        )
    }
    
    # Feature differences
    target_features = set(target_sku.get('capabilities', {}).keys() if isinstance(target_sku.get('capabilities'), dict) else [])
    alt_features = set(alternative_sku.get('capabilities', {}).keys() if isinstance(alternative_sku.get('capabilities'), dict) else [])
    
    differences['features'] = {
        'added': sorted(list(alt_features - target_features)),
        'removed': sorted(list(target_features - alt_features)),
        'unchanged': sorted(list(target_features & alt_features))
    }
    
    return differences
