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

        # Supplement RI pricing for target and alternatives if missing from cache
        # Uses a single bulk API call instead of per-SKU fetches
        all_need_ri = []
        if not _pricing_has_ri(target_pricing):
            all_need_ri.append(('target', target_pricing, sku_name))
        for alt in alternatives:
            if alt.get('pricing') and not _pricing_has_ri(alt['pricing']):
                all_need_ri.append(('alt', alt['pricing'], alt['name']))

        if all_need_ri:
            logging.info(f'Supplementing RI pricing for {len(all_need_ri)} SKUs')
            try:
                ri_url = 'https://prices.azure.com/api/retail/prices'
                ri_filter = f"serviceName eq 'Virtual Machines' and armRegionName eq '{location}' and type eq 'Reservation'"
                ri_api_url = f"{ri_url}?currencyCode={currency_code}&$filter={ri_filter}"

                ri_items = []
                while ri_api_url:
                    ri_resp = requests.get(ri_api_url, headers={'Accept': 'application/json'}, timeout=15)
                    if not ri_resp.ok:
                        break
                    ri_data = ri_resp.json()
                    ri_items.extend(ri_data.get('Items', []))
                    ri_api_url = ri_data.get('NextPageLink')

                # Index RI items by armSkuName and term
                ri_index: Dict[str, Dict[str, Dict]] = {}
                for item in ri_items:
                    arm_sku = item.get('armSkuName', '')
                    term = item.get('reservationTerm', '')
                    if not arm_sku or term not in ('1 Year', '3 Years'):
                        continue
                    if 'Spot' in item.get('skuName', '') or 'Low Priority' in item.get('skuName', ''):
                        continue
                    if arm_sku not in ri_index:
                        ri_index[arm_sku] = {}
                    if term not in ri_index[arm_sku]:
                        ri_index[arm_sku][term] = item

                # Apply RI data to pricing dicts
                for _, pricing_dict, name in all_need_ri:
                    if pricing_dict and name in ri_index:
                        ri_data_sku = ri_index[name]
                        ri_1yr = ri_data_sku.get('1 Year')
                        ri_3yr = ri_data_sku.get('3 Years')
                        if ri_1yr:
                            total_1yr = ri_1yr['unitPrice']
                            pricing_dict['ri1YearMonthly'] = round(total_1yr / 12, 2)
                            pricing_dict['ri1YearHourly'] = round(total_1yr / (12 * 730), 4)
                        if ri_3yr:
                            total_3yr = ri_3yr['unitPrice']
                            pricing_dict['ri3YearMonthly'] = round(total_3yr / 36, 2)
                            pricing_dict['ri3YearHourly'] = round(total_3yr / (36 * 730), 4)

                logging.info(f'RI supplement complete: {len(ri_index)} SKUs with RI data found')
            except Exception as ri_error:
                logging.warning(f'Failed to supplement RI pricing: {ri_error}')

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
# HTTP Route: /telemetry_config - Frontend telemetry bootstrap (anonymous only)
# ============================================================================
@app.route(route="telemetry_config", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def telemetry_config(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns minimal frontend telemetry configuration.
    No PII is included; only connection string and enable flag.
    """
    connection_string = os.environ.get('APPLICATIONINSIGHTS_CONNECTION_STRING')
    response_data = {
        'enabled': bool(connection_string),
        'connectionString': connection_string or '',
        'provider': 'application_insights'
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
        
        # Reconstruct pricing from separate fields if no JSON pricing field
        if not entity.get('pricing') and entity.get('hourlyPriceUSD') is not None:
            entity['pricing'] = {
                'hourlyPrice': entity.get('hourlyPriceUSD'),
                'monthlyPrice': entity.get('monthlyPriceUSD'),
                'hourlyPriceWindows': entity.get('hourlyPriceUSDWindows'),
                'monthlyPriceWindows': entity.get('monthlyPriceUSDWindows'),
                'ri1YearHourly': entity.get('ri1YearHourlyUSD'),
                'ri1YearMonthly': entity.get('ri1YearMonthlyUSD'),
                'ri3YearHourly': entity.get('ri3YearHourlyUSD'),
                'ri3YearMonthly': entity.get('ri3YearMonthlyUSD'),
                'currency': entity.get('pricingCurrency', 'USD')
            }
        
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
@app.timer_trigger(schedule="0 0 2 * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
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

    # Fetch network bandwidth data once before parallel region processing
    network_bw = fetch_network_bandwidth()

    def process_region(region):
        logging.info(f"Processing region: {region}")
        count = refresh_region(region, subscription_id, token, table_client, network_bw)
        logging.info(f"Updated {count} SKUs for region {region}")
        return count

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_region = {executor.submit(process_region, r): r for r in regions}
        for future in as_completed(future_to_region):
            region = future_to_region[future]
            try:
                total_updated += future.result()
            except Exception as e:
                logging.error(f"Error processing region {region}: {e}")
                total_errors += 1

    logging.info(f"SKU cache refresh completed. Updated: {total_updated}, Errors: {total_errors}")


# ============================================================================
# HTTP Route: /admin/refresh-region - Manual cache refresh for a region
# ============================================================================
@app.route(route="admin/refresh-region", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def admin_refresh_region(req: func.HttpRequest) -> func.HttpResponse:
    """
    Manually trigger a cache refresh for a specific region.
    Useful for correcting stale pricing data without waiting for the daily timer.
    
    Query Parameters:
    - region: Azure region name (e.g., 'eastus')
    """
    logging.info('Processing manual cache refresh request')
    
    region = req.params.get('region')
    if not region:
        return func.HttpResponse(
            json.dumps({'error': 'region parameter is required'}),
            mimetype='application/json',
            status_code=400
        )
    
    storage_account_name = os.environ.get('SKU_CACHE_STORAGE_ACCOUNT')
    subscription_id = os.environ.get('AZURE_SUBSCRIPTION_ID')
    
    if not storage_account_name or not subscription_id:
        return func.HttpResponse(
            json.dumps({'error': 'Missing required environment variables'}),
            mimetype='application/json',
            status_code=500
        )
    
    try:
        credential = DefaultAzureCredential()
        table_service = TableServiceClient(
            endpoint=f"https://{storage_account_name}.table.core.windows.net",
            credential=credential
        )
        table_service.create_table_if_not_exists("vmskus")
        table_client = table_service.get_table_client("vmskus")
        
        token = get_access_token()
        count = refresh_region(region, subscription_id, token, table_client)
        
        return func.HttpResponse(
            json.dumps({'region': region, 'skusUpdated': count, 'status': 'success'}),
            mimetype='application/json',
            status_code=200
        )
    except Exception as e:
        logging.error(f'Error refreshing region {region}: {e}')
        return func.HttpResponse(
            json.dumps({'error': str(e), 'region': region}),
            mimetype='application/json',
            status_code=500
        )


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
        'hyperVGenerations': capabilities.get('HyperVGenerations', '')
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

    # Network comparison — prefer bandwidth (Mbps) when available, fall back to NIC count
    target_bw = target.get('networkBandwidthMbps')
    candidate_bw = candidate.get('networkBandwidthMbps')
    if target_bw and target_bw > 0 and candidate_bw is not None:
        bw_diff = abs(target_bw - candidate_bw) / target_bw
        network_score = max(0, 100 - (bw_diff * 100))
        total_score += network_score * weights['weightNetwork']
        total_weight += weights['weightNetwork']
    elif target['maxNics'] > 0:
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


def _pricing_has_ri(pricing: Optional[Dict]) -> bool:
    """Check if pricing dict includes RI data (vs stale cache without RI fields)."""
    if not pricing:
        return False
    return pricing.get('ri1YearMonthly') is not None or pricing.get('ri3YearMonthly') is not None


def get_vm_pricing(sku_name: str, location: str, currency_code: str) -> Optional[Dict]:
    """Get VM pricing from Azure Retail Prices API"""
    try:
        api_url = 'https://prices.azure.com/api/retail/prices'
        filter_str = f"serviceName eq 'Virtual Machines' and armSkuName eq '{sku_name}' and armRegionName eq '{location}' and (type eq 'Consumption' or type eq 'Reservation')"
        url = f"{api_url}?currencyCode={currency_code}&$filter={filter_str}"

        items = []
        while url:
            response = requests.get(url, headers={'Accept': 'application/json'}, timeout=10)

            if not response.ok:
                logging.warning(f'Failed to fetch pricing for {sku_name}: {response.status_code}')
                return None

            data = response.json()
            items.extend(data.get('Items', []))
            url = data.get('NextPageLink')
        if not items:
            return None

        # Select Linux PAYG: productName contains "Virtual Machines" but NOT "windows",
        # and skuName does NOT contain "Spot" or "Low Priority"
        linux_item = next((
            item for item in items
            if item.get('type') == 'Consumption'
            and 'productName' in item
            and 'virtual machines' in item['productName'].lower()
            and 'windows' not in item['productName'].lower()
            and 'Spot' not in item.get('skuName', '')
            and 'Low Priority' not in item.get('skuName', '')
        ), None)

        # Select Windows PAYG: productName contains "Virtual Machines" AND "windows",
        # and skuName does NOT contain "Spot" or "Low Priority"
        windows_item = next((
            item for item in items
            if item.get('type') == 'Consumption'
            and 'productName' in item
            and 'virtual machines' in item['productName'].lower()
            and 'windows' in item['productName'].lower()
            and 'Spot' not in item.get('skuName', '')
            and 'Low Priority' not in item.get('skuName', '')
        ), None)

        # Fall back to first non-Spot/non-Low Priority Consumption item if no Linux item found
        if not linux_item:
            linux_item = next((
                item for item in items
                if item.get('type') == 'Consumption'
                and 'Spot' not in item.get('skuName', '')
                and 'Low Priority' not in item.get('skuName', '')
            ), None)

        if not linux_item:
            return None

        currency = linux_item.get('currencyCode', currency_code)
        pricing = {
            'hourlyPrice': linux_item['unitPrice'],
            'monthlyPrice': round(linux_item['unitPrice'] * 730, 2),
            'hourlyPriceWindows': windows_item['unitPrice'] if windows_item else None,
            'monthlyPriceWindows': round(windows_item['unitPrice'] * 730, 2) if windows_item else None,
            'currency': currency
        }

        # Collect reserved instance pricing (unitPrice = total term cost)
        ri_1yr = next((
            item for item in items
            if item.get('type') == 'Reservation'
            and item.get('reservationTerm') == '1 Year'
            and 'virtual machines' in item.get('productName', '').lower()
        ), None)
        ri_3yr = next((
            item for item in items
            if item.get('type') == 'Reservation'
            and item.get('reservationTerm') == '3 Years'
            and 'virtual machines' in item.get('productName', '').lower()
        ), None)

        if ri_1yr:
            total_1yr = ri_1yr['unitPrice']
            pricing['ri1YearMonthly'] = round(total_1yr / 12, 2)
            pricing['ri1YearHourly'] = round(total_1yr / (12 * 730), 4)
        if ri_3yr:
            total_3yr = ri_3yr['unitPrice']
            pricing['ri3YearMonthly'] = round(total_3yr / 36, 2)
            pricing['ri3YearHourly'] = round(total_3yr / (36 * 730), 4)

        return pricing

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
                        {'name': 'HyperVGenerations', 'value': entity.get('hyperVGenerations', '')}
                    ],
                    'locationInfo': [{
                        'location': location,
                        'zones': entity.get('availabilityZones', '').split(',') if entity.get('availabilityZones') else []
                    }],
                    'pricing': {
                        'hourlyPrice': entity.get('hourlyPriceUSD'),
                        'monthlyPrice': entity.get('monthlyPriceUSD'),
                        'hourlyPriceWindows': entity.get('hourlyPriceUSDWindows'),
                        'monthlyPriceWindows': entity.get('monthlyPriceUSDWindows'),
                        'ri1YearHourly': entity.get('ri1YearHourlyUSD'),
                        'ri1YearMonthly': entity.get('ri1YearMonthlyUSD'),
                        'ri3YearHourly': entity.get('ri3YearHourlyUSD'),
                        'ri3YearMonthly': entity.get('ri3YearMonthlyUSD'),
                        'currency': entity.get('pricingCurrency', 'USD')
                    } if entity.get('hourlyPriceUSD') is not None else None,
                    'networkBandwidthMbps': entity.get('networkBandwidthMbps')
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


def fetch_bulk_region_pricing(location: str, currency: str = 'USD') -> Dict[str, Dict]:
    """
    Fetch ALL VM pricing for a region in one paginated API call.
    ~20x fewer HTTP calls vs fetching per-SKU.
    Returns dict keyed by armSkuName -> pricing dict.
    """
    api_url = 'https://prices.azure.com/api/retail/prices'
    filter_str = f"serviceName eq 'Virtual Machines' and armRegionName eq '{location}' and (type eq 'Consumption' or type eq 'Reservation')"
    url = f"{api_url}?currencyCode={currency}&$filter={filter_str}"

    all_items = []
    page = 0
    while url:
        response = requests.get(url, headers={'Accept': 'application/json'}, timeout=30)
        if not response.ok:
            logging.warning(f'Bulk pricing fetch failed for {location} on page {page}: {response.status_code}')
            break
        data = response.json()
        all_items.extend(data.get('Items', []))
        url = data.get('NextPageLink')
        page += 1

    # Index by armSkuName, tracking best Linux, Windows, and fallback items per SKU
    sku_linux: Dict[str, Dict] = {}
    sku_windows: Dict[str, Dict] = {}
    sku_fallback: Dict[str, Dict] = {}
    # RI items keyed by (armSkuName, reservationTerm)
    sku_ri: Dict[str, Dict[str, Dict]] = {}

    for item in all_items:
        sku_name = item.get('armSkuName', '')
        if not sku_name:
            continue
        product_lower = item.get('productName', '').lower()
        sku_label = item.get('skuName', '')
        item_type = item.get('type', '')

        if 'virtual machines' not in product_lower:
            continue
        if 'Spot' in sku_label or 'Low Priority' in sku_label:
            continue

        # Reservation items (RI) — compute-only, no Windows distinction
        if item_type == 'Reservation':
            term = item.get('reservationTerm', '')
            if term in ('1 Year', '3 Years'):
                if sku_name not in sku_ri:
                    sku_ri[sku_name] = {}
                if term not in sku_ri[sku_name]:
                    sku_ri[sku_name][term] = item
            continue

        is_windows = 'windows' in product_lower

        if sku_name not in sku_fallback:
            sku_fallback[sku_name] = item
        if not is_windows and sku_name not in sku_linux:
            sku_linux[sku_name] = item
        if is_windows and sku_name not in sku_windows:
            sku_windows[sku_name] = item

    result: Dict[str, Dict] = {}
    all_sku_names = sku_fallback.keys()
    for sku_name in all_sku_names:
        linux_item = sku_linux.get(sku_name) or sku_fallback.get(sku_name)
        windows_item = sku_windows.get(sku_name)
        if not linux_item:
            continue
        linux_price = linux_item['unitPrice']
        windows_price = windows_item['unitPrice'] if windows_item else None

        pricing = {
            'hourlyPrice': linux_price,
            'monthlyPrice': round(linux_price * 730, 2),
            'hourlyPriceWindows': windows_price,
            'monthlyPriceWindows': round(windows_price * 730, 2) if windows_price else None,
            'currency': linux_item.get('currencyCode', currency)
        }

        # Add reserved instance pricing (unitPrice = total term cost)
        ri_data = sku_ri.get(sku_name, {})
        ri_1yr = ri_data.get('1 Year')
        ri_3yr = ri_data.get('3 Years')
        if ri_1yr:
            total_1yr = ri_1yr['unitPrice']
            pricing['ri1YearMonthly'] = round(total_1yr / 12, 2)
            pricing['ri1YearHourly'] = round(total_1yr / (12 * 730), 4)
        if ri_3yr:
            total_3yr = ri_3yr['unitPrice']
            pricing['ri3YearMonthly'] = round(total_3yr / 36, 2)
            pricing['ri3YearHourly'] = round(total_3yr / (36 * 730), 4)

        result[sku_name] = pricing

    logging.info(f"Bulk pricing fetch for {location}: {len(result)} SKUs from {len(all_items)} price items ({page} pages)")
    return result


def fetch_network_bandwidth() -> Dict[str, int]:
    """
    Fetch max network bandwidth (Mbps) for all Azure VM SKUs from the public azure-compute-docs repo.
    Uses one GitHub git tree API call to enumerate all *-series.md files, then fetches each file
    concurrently via raw.githubusercontent.com (CDN, not subject to the 60 req/hr REST rate limit).
    Returns dict: {"Standard_D2_v5": 12500, ...}
    """
    import re

    def parse_network_table(content: str) -> Dict[str, int]:
        """Extract Size Name -> Max Network Bandwidth (Mb/s) from a series markdown file."""
        bw: Dict[str, int] = {}
        # Find the Network tab section
        network_match = re.search(r'###\s+\[Network\].*?(?=###|\Z)', content, re.DOTALL | re.IGNORECASE)
        if not network_match:
            return bw
        section = network_match.group(0)
        # Parse markdown table rows: | Size Name | ... | <number> |
        for line in section.splitlines():
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) < 2:
                continue
            size_name = cols[0]
            # Skip header/separator rows
            if not size_name.startswith('Standard_') and not size_name.startswith('Basic_'):
                continue
            # Last numeric column is the bandwidth
            for col in reversed(cols[1:]):
                cleaned = col.replace(',', '').replace(' ', '')
                if cleaned.isdigit():
                    bw[size_name] = int(cleaned)
                    break
        return bw

    try:
        # Step 1: Get full file tree in one API call
        tree_url = "https://api.github.com/repos/MicrosoftDocs/azure-compute-docs/git/trees/main?recursive=1"
        tree_resp = requests.get(tree_url, headers={'Accept': 'application/vnd.github+json'}, timeout=30)
        if not tree_resp.ok:
            logging.warning(f"Failed to fetch azure-compute-docs file tree: {tree_resp.status_code}")
            return {}

        series_paths = [
            item['path'] for item in tree_resp.json().get('tree', [])
            if item['path'].startswith('articles/virtual-machines/sizes/')
            and item['path'].endswith('-series.md')
        ]
        logging.info(f"Found {len(series_paths)} series markdown files to parse for network bandwidth")

        # Step 2: Fetch and parse all files concurrently
        base_url = "https://raw.githubusercontent.com/MicrosoftDocs/azure-compute-docs/main"

        def fetch_and_parse(path: str) -> Dict[str, int]:
            try:
                resp = requests.get(f"{base_url}/{path}", timeout=15)
                if resp.ok:
                    return parse_network_table(resp.text)
            except Exception as e:
                logging.debug(f"Failed to fetch {path}: {e}")
            return {}

        result: Dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=20) as executor:
            for bw_map in executor.map(fetch_and_parse, series_paths):
                result.update(bw_map)

        logging.info(f"Network bandwidth loaded for {len(result)} SKUs from {len(series_paths)} series files")
        return result

    except Exception as e:
        logging.warning(f"fetch_network_bandwidth failed: {e}")
        return {}


def refresh_region(region: str, subscription_id: str, token: str, table_client, network_bw: Dict[str, int] = None) -> int:
    """
    Refresh SKU data for a specific region
    Fetches SKU data and pricing concurrently for performance
    Returns number of SKUs updated
    """
    if network_bw is None:
        network_bw = {}
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
    
    logging.info(f"Fetching pricing for {len(skus)} SKUs in {region} via bulk API call...")

    # Fetch all pricing for the region in one paginated bulk call
    pricing_data = fetch_bulk_region_pricing(region)

    entities = []
    timestamp = datetime.now(timezone.utc).isoformat()

    for sku in skus:
        try:
            capabilities = extract_capabilities_for_cache(sku)
            cpu_vendor = detect_cpu_vendor(sku['name'], capabilities.get('architecture', 'x64'))
            pricing = pricing_data.get(sku['name'])
            zones = get_availability_zones(sku, region)

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
                'hyperVGenerations': capabilities['hyperVGenerations'],
                'hourlyPriceUSD': pricing['hourlyPrice'] if pricing else None,
                'monthlyPriceUSD': pricing['monthlyPrice'] if pricing else None,
                'hourlyPriceUSDWindows': pricing.get('hourlyPriceWindows') if pricing else None,
                'monthlyPriceUSDWindows': pricing.get('monthlyPriceWindows') if pricing else None,
                'ri1YearHourlyUSD': pricing.get('ri1YearHourly') if pricing else None,
                'ri1YearMonthlyUSD': pricing.get('ri1YearMonthly') if pricing else None,
                'ri3YearHourlyUSD': pricing.get('ri3YearHourly') if pricing else None,
                'ri3YearMonthlyUSD': pricing.get('ri3YearMonthly') if pricing else None,
                'pricingCurrency': pricing['currency'] if pricing else 'USD',
                'pricingLastUpdated': timestamp,
                'availabilityZones': ','.join(zones) if zones else '',
                'lastUpdated': timestamp
            }
            # Only include networkBandwidthMbps when we have actual data —
            # writing None to Table Storage coerces to integer 0
            bw = network_bw.get(sku['name'])
            if bw is not None:
                entity['networkBandwidthMbps'] = bw
            entities.append(entity)

        except Exception as e:
            logging.warning(f"Failed to process SKU {sku.get('name', 'unknown')}: {e}")

    # Batch upsert in groups of 100 (Azure Table Storage transaction limit)
    count = 0
    BATCH_SIZE = 100
    for i in range(0, len(entities), BATCH_SIZE):
        batch = entities[i:i + BATCH_SIZE]
        try:
            table_client.submit_transaction([("upsert", e) for e in batch])
            count += len(batch)
        except Exception as e:
            logging.warning(f"Batch upsert failed for {region} batch {i // BATCH_SIZE}, falling back to individual upserts: {e}")
            for entity in batch:
                try:
                    table_client.upsert_entity(entity)
                    count += 1
                except Exception as e2:
                    logging.warning(f"Failed to upsert {entity.get('RowKey')}: {e2}")
    
    # Prune SKUs that no longer exist in the Azure API for this region
    api_sku_names = {s['name'] for s in skus}
    try:
        cached_entities = table_client.query_entities(
            query_filter=f"PartitionKey eq '{region}'",
            select=['RowKey']
        )
        pruned = 0
        for entity in cached_entities:
            if entity['RowKey'] not in api_sku_names:
                table_client.delete_entity(partition_key=region, row_key=entity['RowKey'])
                logging.info(f"Pruned retired SKU: {entity['RowKey']} in {region}")
                pruned += 1
        if pruned:
            logging.info(f"Pruned {pruned} retired SKUs from {region}")
    except Exception as e:
        logging.warning(f"Failed to prune stale SKUs for {region}: {e}")
    
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
        'hyperVGenerations': capabilities.get('HyperVGenerations', '')
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
    caps['networkBandwidthMbps'] = sku.get('networkBandwidthMbps')
    caps['acceleratedNetworking'] = sku.get('acceleratedNetworking', False)
    caps['hyperVGen2'] = 'V2' in (sku.get('hyperVGenerations') or '')
    
    # Ensure numeric fields are numbers, not strings (Table Storage may return strings)
    for key in ['maxDataDisks', 'uncachedDiskIOPS', 'uncachedDiskThroughput', 'maxNics', 'networkBandwidthMbps', 'osVhdSizeMB']:
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
        'hyperVGen2': calculate_boolean_diff(
            target_caps.get('hyperVGen2', False),
            alt_caps.get('hyperVGen2', False),
            'Hyper-V Gen 2 Supported'
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
        'networkBandwidthMbps': calculate_numeric_diff(
            target_caps.get('networkBandwidthMbps'),
            alt_caps.get('networkBandwidthMbps'),
            'Mbps'
        ),
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
