"""
Azure Functions v2 Programming Model - Flex Consumption Compatible
All HTTP and Timer triggered functions consolidated into a single file
"""
import logging
import json
import os
import sys
import time
import re
import functools
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
# CORS handling (app-level, dynamic)
#
# CORS is handled here in code rather than via the App Service platform CORS
# allowlist so that every Static Web App *preview* environment (which gets a
# unique origin per pull request) is accepted automatically, with no manual
# `az functionapp cors add` step and no elevated RBAC required.
#
# IMPORTANT: App Service strips app-emitted CORS headers whenever the platform
# CORS allowlist (Bicep `cors.allowedOrigins`) is non-empty. For these headers
# to reach the browser, that list MUST be left empty -- see
# web-app/infra/functions-app-flex.bicep.
# ============================================================================

# Matches: the Azure portal, the production SWA origin, any SWA preview slot for
# this app (black-sea-0784c5d0f-<PR#>.eastus2.1.azurestaticapps.net), and
# localhost for local development. Override via the CORS_ALLOWED_ORIGIN_REGEX
# app setting (a full regex) if the Static Web App hostname ever changes.
_DEFAULT_CORS_ORIGIN_REGEX = (
    r'^https?://('
    r'portal\.azure\.com'
    r'|black-sea-0784c5d0f(-\d+)?\.([a-z0-9]+\.)?1\.azurestaticapps\.net'
    r'|localhost(:\d+)?'
    r'|127\.0\.0\.1(:\d+)?'
    r')$'
)

_CORS_ORIGIN_RE = re.compile(
    os.environ.get('CORS_ALLOWED_ORIGIN_REGEX', _DEFAULT_CORS_ORIGIN_REGEX)
)

_CORS_ALLOW_METHODS = 'GET, POST, OPTIONS'
_CORS_ALLOW_HEADERS = 'Content-Type, Authorization, x-functions-key'
_CORS_MAX_AGE = '3600'


def _match_cors_origin(origin: Optional[str]) -> Optional[str]:
    """Return ``origin`` if it is allowed to make cross-origin calls, else ``None``."""
    if origin and _CORS_ORIGIN_RE.match(origin):
        return origin
    return None


def _cors_headers(req: func.HttpRequest) -> Dict[str, str]:
    """Build CORS response headers for the request's ``Origin`` (empty if not allowed)."""
    origin = _match_cors_origin(req.headers.get('Origin'))
    if not origin:
        return {}
    return {
        'Access-Control-Allow-Origin': origin,
        'Access-Control-Allow-Methods': _CORS_ALLOW_METHODS,
        'Access-Control-Allow-Headers': _CORS_ALLOW_HEADERS,
        'Access-Control-Max-Age': _CORS_MAX_AGE,
        'Vary': 'Origin',
    }


def with_cors(handler):
    """Decorator: answer CORS preflight (``OPTIONS``) and add CORS headers to responses.

    Routes using this decorator MUST include ``OPTIONS`` in their ``methods`` so the
    browser preflight request reaches the handler (the platform no longer answers it).
    """
    @functools.wraps(handler)
    def wrapper(req: func.HttpRequest) -> func.HttpResponse:
        cors = _cors_headers(req)
        if req.method == 'OPTIONS':
            return func.HttpResponse(status_code=204, headers=cors)
        resp = handler(req)
        for key, value in cors.items():
            resp.headers[key] = value
        return resp

    return wrapper


# ============================================================================
# HTTP Route: /compare_vms - Compare VM SKUs
# ============================================================================
@app.route(route="compare_vms", methods=["GET", "POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@with_cors
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
        max_results = req_body.get('maxResults')
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

                alt = {
                    'name': sku['name'],
                    'similarityScore': round(similarity_score, 2),
                    'cpuVendor': sku.get('cpuVendor', 'Intel'),
                    'architecture': sku.get('architecture', 'x64'),
                    'cpuPerfScore': sku.get('cpuPerfScore'),
                    'cpuGeneration': sku.get('cpuGeneration'),
                    'vCPUs': sku_capabilities['vCPUs'],
                    'memoryGB': sku_capabilities['memoryGB'],
                    'gpuCount': sku_capabilities['gpuCount'],
                    'gpuType': sku_capabilities['gpuType'],
                    'pricing': pricing,
                    'zones': ', '.join(zones) if zones else 'N/A',
                    'capabilities': sku_capabilities
                }
                _enrich_cpu_perf(alt, sku['name'])
                _enrich_network_bw(alt, sku['name'])

                # Retirement awareness: add status and apply ranking penalty
                retirement_info = _get_retirement_info(sku['name'])
                if retirement_info:
                    alt.update(retirement_info)
                    penalty = _retirement_penalty(sku['name'])
                    alt['originalSimilarityScore'] = alt['similarityScore']
                    alt['similarityScore'] = round(max(0, alt['similarityScore'] - penalty), 2)

                alternatives.append(alt)

        # Sort by similarity score
        alternatives.sort(key=lambda x: x['similarityScore'], reverse=True)

        # Total number of candidates above the floor, before any top-N capping
        total_matches = len(alternatives)

        # Cap to the closest N matches when requested (None = no cap, for back-compat)
        if isinstance(max_results, int) and max_results > 0:
            alternatives = alternatives[:max_results]

        logging.info(f'Found {total_matches} alternatives, returning {len(alternatives)}')

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
                        _compute_windows_ri(pricing_dict)

                logging.info(f'RI supplement complete: {len(ri_index)} SKUs with RI data found')
            except Exception as ri_error:
                logging.warning(f'Failed to supplement RI pricing: {ri_error}')

        # Return results
        # Determine cache freshness from target SKU's lastUpdated field
        data_last_updated = target_sku.get('lastUpdated')
        target_sku_data = {
                'name': target_sku['name'],
                'cpuVendor': target_sku.get('cpuVendor', 'Intel'),
                'architecture': target_sku.get('architecture', 'x64'),
                'cpuPerfScore': target_sku.get('cpuPerfScore'),
                'cpuGeneration': target_sku.get('cpuGeneration'),
                'vCPUs': target_capabilities['vCPUs'],
                'memoryGB': target_capabilities['memoryGB'],
                'gpuCount': target_capabilities['gpuCount'],
                'gpuType': target_capabilities['gpuType'],
                'pricing': target_pricing,
                'zones': ', '.join(target_zones) if target_zones else 'N/A',
                'capabilities': target_capabilities
        }
        _enrich_cpu_perf(target_sku_data, target_sku['name'])
        _enrich_network_bw(target_sku_data, target_sku['name'])
        # Add retirement info for target SKU
        target_retirement = _get_retirement_info(target_sku['name'])
        if target_retirement:
            target_sku_data.update(target_retirement)
        response_data = {
            'targetSku': target_sku_data,
            'alternatives': alternatives,
            'totalMatches': total_matches,
            'dataLastUpdated': data_last_updated,
            'dataSource': data_source,
            'searchParameters': {
                'location': location,
                'minSimilarityScore': min_similarity_score,
                'maxResults': max_results,
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
@app.route(route="health", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@with_cors
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
@app.route(route="telemetry_config", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@with_cors
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
@app.route(route="compare_details", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@with_cors
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
        
        # Supplement RI pricing if missing from cache
        need_ri = []
        if target_pricing and not _pricing_has_ri(target_pricing):
            need_ri.append((target_pricing, target_name))
        if alt_pricing and not _pricing_has_ri(alt_pricing):
            need_ri.append((alt_pricing, alternative_name))
        if need_ri:
            try:
                ri_url = 'https://prices.azure.com/api/retail/prices'
                sku_filters = ' or '.join(f"armSkuName eq '{n}'" for _, n in need_ri)
                ri_filter = f"serviceName eq 'Virtual Machines' and armRegionName eq '{location}' and type eq 'Reservation' and ({sku_filters})"
                ri_api_url = f"{ri_url}?currencyCode={currency_code}&$filter={ri_filter}"
                ri_resp = requests.get(ri_api_url, headers={'Accept': 'application/json'}, timeout=10)
                if ri_resp.ok:
                    for item in ri_resp.json().get('Items', []):
                        arm_sku = item.get('armSkuName', '')
                        term = item.get('reservationTerm', '')
                        if 'Spot' in item.get('skuName', '') or 'Low Priority' in item.get('skuName', ''):
                            continue
                        for pricing_dict, name in need_ri:
                            if arm_sku == name:
                                if term == '1 Year' and pricing_dict.get('ri1YearMonthly') is None:
                                    total = item['unitPrice']
                                    pricing_dict['ri1YearMonthly'] = round(total / 12, 2)
                                    pricing_dict['ri1YearHourly'] = round(total / (12 * 730), 4)
                                elif term == '3 Years' and pricing_dict.get('ri3YearMonthly') is None:
                                    total = item['unitPrice']
                                    pricing_dict['ri3YearMonthly'] = round(total / 36, 2)
                                    pricing_dict['ri3YearHourly'] = round(total / (36 * 730), 4)
                # Compute Windows RI for supplemented pricing
                for pricing_dict, _ in need_ri:
                    _compute_windows_ri(pricing_dict)
            except Exception as ri_err:
                logging.warning(f'Failed to supplement RI in compare_details: {ri_err}')
        
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
            'differences': differences,
            'targetRetirement': _get_retirement_info(target_name),
            'alternativeRetirement': _get_retirement_info(alternative_name)
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


# ============================================================================
# HTTP Route: /check_region_availability - Check SKU availability in a region
# ============================================================================
@app.route(route="check_region_availability", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@with_cors
def check_region_availability(req: func.HttpRequest) -> func.HttpResponse:
    """
    Check whether a list of VM SKUs are available in a specified region.
    Uses the SKU cache (Azure Table Storage) for fast lookups.

    POST Body:
    - skuNames: list of SKU names to check (max 200)
    - region: target region to check availability in
    """
    logging.info('Processing check_region_availability request')

    try:
        try:
            req_body = req.get_json()
        except ValueError:
            return func.HttpResponse(
                json.dumps({'error': 'Invalid JSON in request body'}),
                mimetype='application/json',
                status_code=400
            )

        sku_names = req_body.get('skuNames', [])
        region = req_body.get('region', '')

        # Validate inputs
        if not sku_names or not isinstance(sku_names, list):
            return func.HttpResponse(
                json.dumps({'error': 'skuNames must be a non-empty array'}),
                mimetype='application/json',
                status_code=400
            )

        if not region or not isinstance(region, str):
            return func.HttpResponse(
                json.dumps({'error': 'region is required and must be a string'}),
                mimetype='application/json',
                status_code=400
            )

        # Enforce max count to prevent abuse
        if len(sku_names) > 200:
            return func.HttpResponse(
                json.dumps({'error': 'Maximum 200 SKU names per request'}),
                mimetype='application/json',
                status_code=400
            )

        # Validate region format (lowercase letters, numbers, no special chars)
        if not re.match(r'^[a-z][a-z0-9]+$', region):
            return func.HttpResponse(
                json.dumps({'error': 'Invalid region format'}),
                mimetype='application/json',
                status_code=400
            )

        # Get storage account
        storage_account_name = os.environ.get('SKU_CACHE_STORAGE_ACCOUNT')
        if not storage_account_name:
            return func.HttpResponse(
                json.dumps({'error': 'SKU cache not configured'}),
                mimetype='application/json',
                status_code=500
            )

        credential = DefaultAzureCredential()
        table_service = TableServiceClient(
            endpoint=f"https://{storage_account_name}.table.core.windows.net",
            credential=credential
        )
        table_client = table_service.get_table_client("vmskus")

        # Use individual point lookups for each SKU (safe and efficient)
        availability = {}
        deduplicated = list(set(sku_names))

        for sku_name in deduplicated:
            try:
                table_client.get_entity(partition_key=region, row_key=sku_name)
                availability[sku_name] = True
            except Exception:
                availability[sku_name] = False

        available_count = sum(1 for v in availability.values() if v)

        response_data = {
            'region': region,
            'availability': availability,
            'availableCount': available_count,
            'totalChecked': len(deduplicated),
            'source': 'cache'
        }

        return func.HttpResponse(
            json.dumps(response_data),
            mimetype='application/json',
            status_code=200
        )

    except Exception as e:
        logging.error(f'Error in check_region_availability: {str(e)}')
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
                'ri1YearHourlyWindows': entity.get('ri1YearHourlyUSDWindows'),
                'ri1YearMonthlyWindows': entity.get('ri1YearMonthlyUSDWindows'),
                'ri3YearHourlyWindows': entity.get('ri3YearHourlyUSDWindows'),
                'ri3YearMonthlyWindows': entity.get('ri3YearMonthlyUSDWindows'),
                'currency': entity.get('pricingCurrency', 'USD')
            }
        
        return entity
        
    except Exception as e:
        logging.error(f'Error fetching SKU from cache: {str(e)}')
        return None


# ============================================================================
# HTTP Route: /skus - List available VM SKUs from cache
# ============================================================================
@app.route(route="skus", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@with_cors
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
            sku_data = {
                'name': entity['name'],
                'vCPUs': entity['vCPUs'],
                'memoryGB': entity['memoryGB'],
                'cpuVendor': entity.get('cpuVendor', 'Intel'),
                'architecture': entity.get('architecture', 'x64'),
                'cpuPerfScore': entity.get('cpuPerfScore'),
                'cpuGeneration': entity.get('cpuGeneration'),
            }
            _enrich_cpu_perf(sku_data, entity['name'])
            retirement_info = _get_retirement_info(entity['name'])
            if retirement_info:
                sku_data.update(retirement_info)
            skus.append(sku_data)
        
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
    all_region_coverage = []

    # Fetch network bandwidth data once before parallel region processing
    network_bw = fetch_network_bandwidth()
    # Merge static fallback values for previous-gen SKUs not in azure-compute-docs
    for sku_name, bw in PREVIOUS_GEN_BANDWIDTH.items():
        network_bw.setdefault(sku_name, bw)

    # Seed CPU performance reference table
    seed_cpu_performance_table(table_service)

    def process_region(region):
        logging.info(f"Processing region: {region}")
        count, coverage = refresh_region(region, subscription_id, token, table_client, network_bw)
        logging.info(f"Updated {count} SKUs for region {region}")
        return count, region, coverage

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_region = {executor.submit(process_region, r): r for r in regions}
        for future in as_completed(future_to_region):
            region = future_to_region[future]
            try:
                count, reg, coverage = future.result()
                total_updated += count
                if coverage:
                    all_region_coverage.append(coverage)
            except Exception as e:
                logging.error(f"Error processing region {region}: {e}")
                total_errors += 1

    # Delayed retry: re-process regions that got 0 pricing (likely transient API failure)
    failed_regions = [
        c['region'] for c in all_region_coverage
        if c.get('paygLinux', 0) == 0 and c.get('totalSkus', 0) > 0
    ]
    if failed_regions:
        logging.warning(f"Retrying {len(failed_regions)} regions with 0 pricing after 30s delay: {failed_regions}")
        time.sleep(30)
        for cov in list(all_region_coverage):
            if cov['region'] in failed_regions:
                all_region_coverage.remove(cov)
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_region = {executor.submit(process_region, r): r for r in failed_regions}
            for future in as_completed(future_to_region):
                region = future_to_region[future]
                try:
                    count, reg, coverage = future.result()
                    total_updated += count
                    if coverage:
                        all_region_coverage.append(coverage)
                    logging.info(f"Retry succeeded for {region}: {coverage.get('paygLinux', 0)} SKUs with pricing")
                except Exception as e:
                    logging.error(f"Retry failed for region {region}: {e}")
                    total_errors += 1

    logging.info(f"SKU cache refresh completed. Updated: {total_updated}, Errors: {total_errors}")

    # Emit coverage telemetry for workbook visualization
    _emit_coverage_telemetry(all_region_coverage)


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
        count, coverage = refresh_region(region, subscription_id, token, table_client)
        
        return func.HttpResponse(
            json.dumps({'region': region, 'skusUpdated': count, 'status': 'success', 'coverage': coverage}),
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


def _asymmetric_score(target_val: float, candidate_val: float, overshoot_factor: float = 1.0) -> float:
    """Score one numeric capability dimension on a 0-100 scale.

    Shortfall (candidate below target) is always penalized at full rate.
    Overshoot (candidate at or above target) is penalized at ``overshoot_factor``
    of the full rate, so ``overshoot_factor=0.0`` means meeting-or-exceeding the
    target is not penalized at all (more of a resource is never "worse").
    ``overshoot_factor=1.0`` reproduces the original symmetric scoring.
    """
    if target_val <= 0:
        return 100.0
    diff = candidate_val - target_val
    if diff >= 0:
        penalty = (diff / target_val) * overshoot_factor
    else:
        penalty = (-diff) / target_val
    return max(0.0, 100.0 - penalty * 100.0)


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

    # Storage comparison — a candidate that meets or exceeds the target's IOPS is
    # not "worse", so overshoot is not penalized (overshoot_factor=0.0).
    if target['uncachedDiskIOPS'] > 0:
        iops_score = _asymmetric_score(target['uncachedDiskIOPS'], candidate['uncachedDiskIOPS'], overshoot_factor=0.0)
        total_score += iops_score * weights['weightStorage']
        total_weight += weights['weightStorage']

    # Network comparison — prefer bandwidth (Mbps) when available, fall back to NIC
    # count. Exceeding the target's bandwidth/NICs is not penalized (overshoot_factor=0.0).
    target_bw = target.get('networkBandwidthMbps')
    candidate_bw = candidate.get('networkBandwidthMbps')
    if target_bw and target_bw > 0 and candidate_bw is not None:
        network_score = _asymmetric_score(target_bw, candidate_bw, overshoot_factor=0.0)
        total_score += network_score * weights['weightNetwork']
        total_weight += weights['weightNetwork']
    elif target['maxNics'] > 0:
        nic_score = _asymmetric_score(target['maxNics'], candidate['maxNics'], overshoot_factor=0.0)
        total_score += nic_score * weights['weightNetwork']
        total_weight += weights['weightNetwork']

    # Features comparison — only penalize for features the target has that the
    # candidate lacks; extra capabilities on the candidate are not "worse".
    features = ['premiumIO', 'acceleratedNetworking', 'encryptionAtHost', 'ephemeralOSDisk']
    target_features = [f for f in features if target[f]]
    if target_features:
        feature_matches = sum(1 for f in target_features if candidate[f])
        feature_score = (feature_matches / len(target_features)) * 100
    else:
        feature_score = 100
    total_score += feature_score * weights['weightFeatures']
    total_weight += weights['weightFeatures']

    return total_score / total_weight if total_weight > 0 else 0


def _pricing_has_ri(pricing: Optional[Dict]) -> bool:
    """Check if pricing dict includes RI data (vs stale cache without RI fields)."""
    if not pricing:
        return False
    return pricing.get('ri1YearMonthly') is not None or pricing.get('ri3YearMonthly') is not None


def _compute_windows_ri(pricing: Dict) -> None:
    """Compute Windows RI pricing in-place: RI compute + Windows license surcharge.
    Windows license surcharge = Windows PAYG hourly - Linux PAYG hourly."""
    linux_hourly = pricing.get('hourlyPrice')
    windows_hourly = pricing.get('hourlyPriceWindows')
    if linux_hourly is None or windows_hourly is None:
        return
    license_surcharge = windows_hourly - linux_hourly
    if license_surcharge <= 0:
        return

    ri1_hourly = pricing.get('ri1YearHourly')
    if ri1_hourly is not None:
        pricing['ri1YearHourlyWindows'] = round(ri1_hourly + license_surcharge, 4)
        pricing['ri1YearMonthlyWindows'] = round((ri1_hourly + license_surcharge) * 730, 2)

    ri3_hourly = pricing.get('ri3YearHourly')
    if ri3_hourly is not None:
        pricing['ri3YearHourlyWindows'] = round(ri3_hourly + license_surcharge, 4)
        pricing['ri3YearMonthlyWindows'] = round((ri3_hourly + license_surcharge) * 730, 2)


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

        # Select Linux PAYG: exclude DedicatedHost/Cloud items and Windows,
        # and skuName does NOT contain "Spot" or "Low Priority"
        linux_item = next((
            item for item in items
            if item.get('type') == 'Consumption'
            and 'productName' in item
            and 'dedicatedhost' not in item['productName'].lower()
            and 'cloud' not in item['productName'].lower()
            and 'windows' not in item['productName'].lower()
            and 'Spot' not in item.get('skuName', '')
            and 'Low Priority' not in item.get('skuName', '')
        ), None)

        # Select Windows PAYG: exclude DedicatedHost/Cloud items, require Windows,
        # and skuName does NOT contain "Spot" or "Low Priority"
        windows_item = next((
            item for item in items
            if item.get('type') == 'Consumption'
            and 'productName' in item
            and 'dedicatedhost' not in item['productName'].lower()
            and 'cloud' not in item['productName'].lower()
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
            and 'dedicatedhost' not in item.get('productName', '').lower()
            and 'cloud' not in item.get('productName', '').lower()
        ), None)
        ri_3yr = next((
            item for item in items
            if item.get('type') == 'Reservation'
            and item.get('reservationTerm') == '3 Years'
            and 'dedicatedhost' not in item.get('productName', '').lower()
            and 'cloud' not in item.get('productName', '').lower()
        ), None)

        if ri_1yr:
            total_1yr = ri_1yr['unitPrice']
            pricing['ri1YearMonthly'] = round(total_1yr / 12, 2)
            pricing['ri1YearHourly'] = round(total_1yr / (12 * 730), 4)
        if ri_3yr:
            total_3yr = ri_3yr['unitPrice']
            pricing['ri3YearMonthly'] = round(total_3yr / 36, 2)
            pricing['ri3YearHourly'] = round(total_3yr / (36 * 730), 4)

        _compute_windows_ri(pricing)

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
                    'cpuPerfScore': entity.get('cpuPerfScore'),
                    'cpuGeneration': entity.get('cpuGeneration'),
                    'lastUpdated': entity.get('lastUpdated'),
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
                        'ri1YearHourlyWindows': entity.get('ri1YearHourlyUSDWindows'),
                        'ri1YearMonthlyWindows': entity.get('ri1YearMonthlyUSDWindows'),
                        'ri3YearHourlyWindows': entity.get('ri3YearHourlyUSDWindows'),
                        'ri3YearMonthlyWindows': entity.get('ri3YearMonthlyUSDWindows'),
                        'currency': entity.get('pricingCurrency', 'USD')
                    } if entity.get('hourlyPriceUSD') is not None else None,
                    'networkBandwidthMbps': entity.get('networkBandwidthMbps')
                }
                _enrich_cpu_perf(sku, entity['name'])
                _enrich_network_bw(sku, entity['name'])
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
    Includes retry logic with exponential backoff to handle transient failures.
    """
    api_url = 'https://prices.azure.com/api/retail/prices'
    filter_str = f"serviceName eq 'Virtual Machines' and armRegionName eq '{location}' and (type eq 'Consumption' or type eq 'Reservation')"
    url = f"{api_url}?currencyCode={currency}&$filter={filter_str}"

    all_items = []
    page = 0
    max_retries = 3
    while url:
        # Retry loop with exponential backoff for each page
        success = False
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers={'Accept': 'application/json'}, timeout=45)
                if response.ok:
                    success = True
                    break
                else:
                    logging.warning(f'Bulk pricing fetch for {location} page {page} returned {response.status_code} (attempt {attempt + 1}/{max_retries})')
            except requests.exceptions.RequestException as e:
                logging.warning(f'Bulk pricing fetch for {location} page {page} error: {e} (attempt {attempt + 1}/{max_retries})')
            if attempt < max_retries - 1:
                backoff = 2 ** (attempt + 1)  # 2s, 4s
                time.sleep(backoff)

        if not success:
            logging.error(f'Bulk pricing fetch FAILED for {location} after {max_retries} attempts on page {page}. Returning partial data.')
            break

        data = response.json()
        all_items.extend(data.get('Items', []))
        url = data.get('NextPageLink')
        page += 1
        # Small delay between pages to avoid rate limiting
        if url:
            time.sleep(0.2)

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

        # Skip non-VM items (DedicatedHost, Cloud Services) that share the
        # 'Virtual Machines' serviceName but aren't actual VM SKU pricing
        if 'dedicatedhost' in product_lower or 'cloud' in product_lower:
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

        _compute_windows_ri(pricing)

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

    def parse_network_table(content: str) -> Dict[str, int]:
        """Extract Size Name -> Max Network Bandwidth (Mb/s) from a series markdown file."""
        bw: Dict[str, int] = {}
        # Find the Network tab section
        network_match = re.search(r'###\s+\[Network\].*?(?=###|\Z)', content, re.DOTALL | re.IGNORECASE)
        if not network_match:
            # Fallback: parse inline bandwidth from combined tables (e.g., dv2-dsv2-series-memory.md)
            # Format: | Standard_D11_v2 | ... | 2|1500 | or | ... | Expected network bandwidth (Mbps) |
            return _parse_inline_bandwidth(content)
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

    def _parse_inline_bandwidth(content: str) -> Dict[str, int]:
        """
        Fallback parser for docs that embed bandwidth inline in combined tables.
        Handles format: | Standard_D11_v2 | 2 | 14 | ... | 2|1500 |
        where the last column contains NICs|Bandwidth or just the bandwidth number.
        Also handles: | Standard_DS11_v2 <sup>3</sup> | ... | 2|1500 |
        """
        bw: Dict[str, int] = {}
        # Only parse if the file mentions "network bandwidth" in a header
        if 'network bandwidth' not in content.lower():
            return bw
        for line in content.splitlines():
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) < 3:
                continue
            # Extract SKU name (may have <sup> tags)
            size_raw = re.sub(r'<sup>.*?</sup>', '', cols[0]).strip()
            if not size_raw.startswith('Standard_') and not size_raw.startswith('Basic_'):
                continue
            # Look for the last column that matches the NIC|BW or NIC/BW pattern or a plain number
            last_col = re.sub(r'<sup>.*?</sup>', '', cols[-1]).replace(',', '').replace(' ', '')
            # Pattern: "2|1500" or "8|12000" or "2/1000" or "8/25000"
            nics_bw_match = re.match(r'^\d+[|/](\d+)', last_col)
            if nics_bw_match:
                bw[size_raw] = int(nics_bw_match.group(1))
            elif last_col.isdigit():
                bw[size_raw] = int(last_col)
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
            if (item['path'].startswith('articles/virtual-machines/sizes/') or
                (item['path'].startswith('articles/virtual-machines/') and
                 not item['path'].startswith('articles/virtual-machines/sizes/') and
                 '/includes/' not in item['path']))
            and (item['path'].endswith('-series.md') or item['path'].endswith('-series-memory.md'))
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


# Static fallback bandwidth table for previous-gen SKUs whose docs pages no longer
# exist in azure-compute-docs.  Values sourced from archived Microsoft docs (Wayback
# Machine snapshots of learn.microsoft.com/azure/virtual-machines/sizes-previous-gen,
# captured 2023-06-01 and 2023-12-01).
PREVIOUS_GEN_BANDWIDTH: Dict[str, int] = {
    # F-series
    'Standard_F1': 750, 'Standard_F2': 1500, 'Standard_F4': 3000,
    'Standard_F8': 6000, 'Standard_F16': 12000,
    # Fs-series
    'Standard_F1s': 750, 'Standard_F2s': 1500, 'Standard_F4s': 3000,
    'Standard_F8s': 6000, 'Standard_F16s': 12000,
    # D-series (compute)
    'Standard_D1': 500, 'Standard_D2': 1000, 'Standard_D3': 2000, 'Standard_D4': 4000,
    # D-series (memory-optimized)
    'Standard_D11': 1000, 'Standard_D12': 2000, 'Standard_D13': 4000, 'Standard_D14': 8000,
    # DS-series (compute)
    'Standard_DS1': 500, 'Standard_DS2': 1000, 'Standard_DS3': 2000, 'Standard_DS4': 4000,
    # DS-series (memory-optimized)
    'Standard_DS11': 1000, 'Standard_DS12': 2000, 'Standard_DS13': 4000, 'Standard_DS14': 8000,
    # G-series
    'Standard_G1': 2000, 'Standard_G2': 4000, 'Standard_G3': 8000,
    'Standard_G4': 16000, 'Standard_G5': 20000,
    # GS-series
    'Standard_GS1': 2000, 'Standard_GS2': 4000, 'Standard_GS3': 8000,
    'Standard_GS4': 16000, 'Standard_GS5': 20000,
    # Ls-series (storage-optimized v1)
    'Standard_L4s': 4000, 'Standard_L8s': 8000,
    'Standard_L16s': 16000, 'Standard_L32s': 20000,
    # Av2-series
    'Standard_A1_v2': 250, 'Standard_A2_v2': 500, 'Standard_A4_v2': 1000,
    'Standard_A8_v2': 2000, 'Standard_A2m_v2': 500, 'Standard_A4m_v2': 1000,
    'Standard_A8m_v2': 2000,
}


# ============================================================================
# CPU Performance Reference Table
# ============================================================================

# Per-vCPU relative performance scores normalized to Ice Lake (8370C) = 100.
# Calibrated from Azure CoreMark benchmark data (articles/virtual-machines/linux/compute-benchmark-scores.md)
# and extended using public benchmark ratios (Geekbench 6, SPEC CPU 2017) for newer CPUs.
CPU_PERFORMANCE_TABLE = {
    # Intel - older generations (CoreMark-calibrated)
    'E5-2673 v3': {'score': 96, 'generation': 'Haswell', 'year': 2014},
    'E5-2673 v4': {'score': 89, 'generation': 'Broadwell', 'year': 2016},
    '8171M': {'score': 85, 'generation': 'Skylake', 'year': 2017},
    '8168': {'score': 97, 'generation': 'Skylake', 'year': 2017},
    'E-2288G': {'score': 187, 'generation': 'Coffee Lake', 'year': 2019},
    'E-2176G': {'score': 176, 'generation': 'Coffee Lake', 'year': 2018},
    '8272CL': {'score': 96, 'generation': 'Cascade Lake', 'year': 2019},
    '8280M': {'score': 73, 'generation': 'Cascade Lake', 'year': 2019},
    '6246R': {'score': 108, 'generation': 'Cascade Lake', 'year': 2020},
    '8370C': {'score': 100, 'generation': 'Ice Lake', 'year': 2021},
    # Intel - newer generations (public benchmark ratios applied to Ice Lake baseline)
    '8473C': {'score': 115, 'generation': 'Sapphire Rapids', 'year': 2023},
    '8488C': {'score': 115, 'generation': 'Sapphire Rapids', 'year': 2023},
    '8573C': {'score': 120, 'generation': 'Emerald Rapids', 'year': 2024},
    '8592+': {'score': 120, 'generation': 'Emerald Rapids', 'year': 2024},
    # AMD - older generations (CoreMark-calibrated)
    '7551': {'score': 72, 'generation': 'Naples (Zen 1)', 'year': 2017},
    '7452': {'score': 101, 'generation': 'Rome (Zen 2)', 'year': 2019},
    '7V12': {'score': 121, 'generation': 'Rome (Zen 2)', 'year': 2020},
    '7763': {'score': 106, 'generation': 'Milan (Zen 3)', 'year': 2021},
    '7V13': {'score': 136, 'generation': 'Milan (Zen 3)', 'year': 2021},
    '7V73X': {'score': 141, 'generation': 'Milan-X (Zen 3)', 'year': 2022},
    # AMD - newer generations (public benchmark ratios)
    '9004': {'score': 122, 'generation': 'Genoa (Zen 4)', 'year': 2023},
    '9V004': {'score': 122, 'generation': 'Genoa (Zen 4)', 'year': 2023},
    '9005': {'score': 135, 'generation': 'Turin (Zen 5)', 'year': 2024},
    '9754': {'score': 95, 'generation': 'Bergamo (Zen 4c)', 'year': 2023},
    # ARM — Cobalt 100 score estimated at ~1.25× Ampere Altra based on Microsoft GA blog
    # ("up to 1.4× CPU performance" vs Altra; customer testimonials show 37-40% gains).
    # Conservative 1.26× applied: 95 × 1.26 ≈ 120.
    'Cobalt 100': {'score': 120, 'generation': 'Cobalt 100 (Neoverse N2)', 'year': 2023},
    'Ampere Altra': {'score': 95, 'generation': 'Ampere Altra (Neoverse N1)', 'year': 2022},
}

# Maps VM series prefixes to their CPU model identifiers (from azure-compute-docs specs files).
# Each series may land on multiple CPU models; we list them for averaging.
SERIES_CPU_MAP = {
    # General Purpose - Intel
    'Dv3': ['8272CL', '8171M', 'E5-2673 v4'],
    'Dsv3': ['8272CL', '8171M', 'E5-2673 v4'],
    'Dv4': ['8272CL'],
    'Dsv4': ['8272CL'],
    'Ddv4': ['8272CL'],
    'Ddsv4': ['8272CL'],
    'Dv5': ['8370C'],
    'Dsv5': ['8473C', '8370C', '8573C'],
    'Ddv5': ['8370C'],
    'Ddsv5': ['8370C'],
    'Dlsv5': ['8370C'],
    'Dldsv5': ['8370C'],
    'Dsv6': ['8473C', '8573C'],
    'Ddsv6': ['8473C', '8573C'],
    'Dlsv6': ['8473C', '8573C'],
    'Dldsv6': ['8473C', '8573C'],
    'Dsv7': ['8573C'],
    'Ddsv7': ['8573C'],
    'Dlsv7': ['8573C'],
    'Dldsv7': ['8573C'],
    # General Purpose - AMD
    'Dav4': ['7452'],
    'Dasv4': ['7452'],
    'Dasv5': ['7763'],
    'Dadsv5': ['7763'],
    'Dasv6': ['9004'],
    'Dadsv6': ['9004'],
    'Dalsv6': ['9004'],
    'Daldsv6': ['9004'],
    'Dasv7': ['9005'],
    'Dadsv7': ['9005'],
    'Dalsv7': ['9005'],
    'Daldsv7': ['9005'],
    # General Purpose - ARM
    'Dpsv5': ['Ampere Altra'],
    'Dpdsv5': ['Ampere Altra'],
    'Dplsv5': ['Ampere Altra'],
    'Dpldsv5': ['Ampere Altra'],
    'Dpsv6': ['Cobalt 100'],
    'Dpdsv6': ['Cobalt 100'],
    'Dplsv6': ['Cobalt 100'],
    'Dpldsv6': ['Cobalt 100'],
    # Memory Optimized - Intel
    'Ev3': ['8272CL', '8171M', 'E5-2673 v4'],
    'Esv3': ['8272CL', '8171M', 'E5-2673 v4'],
    'Ev4': ['8272CL'],
    'Esv4': ['8272CL'],
    'Edv4': ['8272CL'],
    'Edsv4': ['8272CL'],
    'Ev5': ['8370C'],
    'Esv5': ['8473C', '8370C', '8573C'],
    'Edv5': ['8370C'],
    'Edsv5': ['8370C'],
    'Esv6': ['8473C', '8573C'],
    'Edsv6': ['8473C', '8573C'],
    'Ensv6': ['8473C', '8573C'],
    'Endsv6': ['8473C', '8573C'],
    'Esv7': ['8573C'],
    'Edsv7': ['8573C'],
    # Memory Optimized - AMD
    'Eav4': ['7452'],
    'Easv4': ['7452'],
    'Easv5': ['7763'],
    'Eadsv5': ['7763'],
    'Easv6': ['9004'],
    'Eadsv6': ['9004'],
    'Easv7': ['9005'],
    'Eadsv7': ['9005'],
    # Memory Optimized - ARM
    'Epsv5': ['Ampere Altra'],
    'Epdsv5': ['Ampere Altra'],
    'Epsv6': ['Cobalt 100'],
    'Epdsv6': ['Cobalt 100'],
    # Memory Optimized - Specialty
    'Ebsv5': ['8370C'],
    'Ebdsv5': ['8370C', '8573C'],
    'Ebsv6': ['8573C'],
    'Ebdsv6': ['8573C'],
    'Msv2': ['8280M'],
    'Mdsv2': ['8280M'],
    'Msv3': ['8473C'],
    'Mdsv3': ['8473C'],
    'Mbsv3': ['8473C'],
    'Mbdsv3': ['8473C'],
    'Mv2': ['8280M'],
    # Compute Optimized - Intel
    'Fsv2': ['8272CL', '8370C', '8168'],
    'FXsv2': ['8370C'],
    'FXmdsv2': ['8370C'],
    # Compute Optimized - AMD
    'Fasv6': ['9004'],
    'Famsv6': ['9004'],
    'Falsv6': ['9004'],
    'Fasv7': ['9005'],
    'Fadsv7': ['9005'],
    'Falsv7': ['9005'],
    'Faldsv7': ['9005'],
    'Famsv7': ['9005'],
    'Famdsv7': ['9005'],
    # Storage Optimized
    'Lsv2': ['7551'],
    'Lasv3': ['7763'],
    'Lsv3': ['8370C'],
    'Lasv4': ['9004'],
    'Lsv4': ['8473C'],
    # Confidential - Intel
    'DCsv2': ['E-2288G'],
    'DCsv3': ['8370C'],
    'DCdsv3': ['8370C'],
    'DCesv6': ['8573C'],
    'DCedsv6': ['8573C'],
    # Confidential - AMD
    'DCasv5': ['7763'],
    'DCadsv5': ['7763'],
    'DCasv6': ['9004'],
    'DCadsv6': ['9004'],
    # Confidential - EC (AMD SEV-SNP)
    'ECasv5': ['7763'],
    'ECadsv5': ['7763'],
    'ECasv6': ['9004'],
    'ECadsv6': ['9004'],
    # B-series (burstable)
    'Bsv2': ['8370C'],
    'Basv2': ['7763'],
    'Bpsv2': ['Ampere Altra'],
    'Balsv2': ['7763'],
    'Blsv2': ['8370C'],
    'Batsv2': ['7763'],
    'Btsv2': ['8370C'],
    'Bplsv2': ['Ampere Altra'],
    'Bptsv2': ['Ampere Altra'],
    # HPC (including constrained-vCPU 'rs' variants)
    'HBv3': ['7V13'],
    'HBv4': ['9V004'],
    'HBv2': ['7V12'],
    'HBrsv3': ['7V13'],
    'HBrsv4': ['9V004'],
    'HBrsv2': ['7V12'],
    'HC': ['8168'],
    'HX': ['7V13'],
    'HXrs': ['7V13'],
    # Compute Optimized - FX additional
    'FXmsv2': ['8370C'],
    # Storage Optimized - additional
    'Laosv4': ['9004'],
    # Memory Optimized - M additional (including M-medium-memory variants)
    'Mmsv2': ['8280M'],
    'Mmsv3': ['8473C'],
    # Memory Optimized - v7
    'Ensv7': ['8573C'],
    'Endsv7': ['8573C'],
    'Epsv7': ['Cobalt 100'],
    'Epdsv7': ['Cobalt 100'],
    # Previous gen (for reference)
    'Dv2': ['8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3'],
    'DSv2': ['8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3'],
    'Av2': ['8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3'],
    # Old non-versioned series
    'D': ['E5-2673 v3'],
    'DS': ['E5-2673 v3'],
    'F': ['E5-2673 v3', 'E5-2673 v4'],
    'Fs': ['E5-2673 v3', 'E5-2673 v4'],
    'M': ['E5-2673 v4'],
    'Mms': ['E5-2673 v4'],
    'Ms': ['E5-2673 v4'],
    'G': ['E5-2673 v3'],
    'GS': ['E5-2673 v3'],
    'Gs': ['E5-2673 v3'],
    'L': ['E5-2673 v3'],
    'Ls': ['E5-2673 v3'],
    'B': ['E5-2673 v4', '8171M'],
    'Bms': ['E5-2673 v4', '8171M'],
    'Bs': ['E5-2673 v4', '8171M'],
    'Bls': ['E5-2673 v4', '8171M'],
    'DC': ['E-2176G'],
    'DCs': ['E-2176G'],
    'DCv2': ['E-2288G'],
    'EC': ['7763'],
    'FX': ['8370C'],
    'FXmds': ['8370C'],
    # Previous gen additional
    'Amv2': ['8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3'],
    'HCrs': ['8168'],
}

# Special CPU model identifiers for matching specs file content
_CPU_SPEC_PATTERNS = {
    'E-2288G': ['E-2288G'],
    'E-2176G': ['E-2176G'],
}


def _get_series_prefix(sku_name: str) -> Optional[str]:
    """
    Extract the series prefix from a SKU name for CPU mapping.
    E.g., 'Standard_D2s_v5' -> 'Dsv5', 'Standard_E96-24ads_v6' -> 'Eadsv6'
    """
    name = sku_name.replace('Standard_', '').replace('Basic_', '')
    # Remove constrained vCPU prefix (e.g., E96-24ads_v6 -> Eads_v6, HB120-16rs_v3 -> HBrs_v3)
    name = re.sub(r'^([A-Z]+)\d+-\d+', lambda m: m.group(1), name)
    # Match pattern: letter(s) + optional digits + letter modifiers + _v + version
    match = re.match(r'^([A-Z]+)[0-9]*([a-z]*)_v(\d+)', name, re.IGNORECASE)
    if match:
        family = match.group(1)
        modifiers = match.group(2)
        version = match.group(3)
        return f"{family}{modifiers}v{version}"
    # Handle non-versioned series (HC, HB, M, etc.)
    match = re.match(r'^([A-Z]+)[0-9]*([a-z]*)', name, re.IGNORECASE)
    if match:
        family = match.group(1)
        modifiers = match.group(2)
        prefix = f"{family}{modifiers}"
        if prefix in SERIES_CPU_MAP:
            return prefix
        # Try without modifiers (HC44rs -> HC)
        if family in SERIES_CPU_MAP:
            return family
    return None


def get_cpu_performance(sku_name: str) -> Optional[Dict]:
    """
    Get CPU performance data for a SKU.
    Returns dict with 'score', 'generation', 'year', 'cpuModels' or None if unknown.
    """
    series = _get_series_prefix(sku_name)
    if not series or series not in SERIES_CPU_MAP:
        return None

    cpu_ids = SERIES_CPU_MAP[series]
    scores = []
    generations = []
    for cpu_id in cpu_ids:
        if cpu_id in CPU_PERFORMANCE_TABLE:
            entry = CPU_PERFORMANCE_TABLE[cpu_id]
            scores.append(entry['score'])
            generations.append(entry['generation'])

    if not scores:
        return None

    avg_score = round(sum(scores) / len(scores))
    # Use the most common/newest generation name
    primary_gen = generations[0] if len(set(generations)) > 1 else generations[0]

    return {
        'score': avg_score,
        'generation': primary_gen,
        'year': CPU_PERFORMANCE_TABLE[cpu_ids[0]]['year'],
        'cpuModels': cpu_ids,
    }


def _enrich_cpu_perf(data: Dict, sku_name: str) -> None:
    """Fill in cpuPerfScore/cpuGeneration on a response dict, always using the code mapping as source of truth."""
    cpu_perf = get_cpu_performance(sku_name)
    if cpu_perf:
        data['cpuPerfScore'] = cpu_perf['score']
        data['cpuGeneration'] = cpu_perf['generation']


def _enrich_network_bw(data: Dict, sku_name: str) -> None:
    """Fill in networkBandwidthMbps from the static fallback table if missing from cache."""
    if data.get('networkBandwidthMbps') is None:
        bw = PREVIOUS_GEN_BANDWIDTH.get(sku_name)
        if bw is not None:
            data['networkBandwidthMbps'] = bw
            # Also enrich capabilities dict if present
            caps = data.get('capabilities')
            if isinstance(caps, dict) and caps.get('networkBandwidthMbps') is None:
                caps['networkBandwidthMbps'] = bw


def seed_cpu_performance_table(table_service: TableServiceClient) -> None:
    """Seed the cpuperf table in Azure Table Storage with the reference data."""
    table_name = "cpuperf"
    try:
        table_service.create_table_if_not_exists(table_name)
    except Exception as e:
        logging.warning(f"Failed to create cpuperf table: {e}")
        return

    table_client = table_service.get_table_client(table_name)

    # Upsert all CPU model entries
    for cpu_id, data in CPU_PERFORMANCE_TABLE.items():
        entity = {
            'PartitionKey': 'cpumodel',
            'RowKey': cpu_id,
            'score': data['score'],
            'generation': data['generation'],
            'year': data['year'],
        }
        try:
            table_client.upsert_entity(entity)
        except Exception as e:
            logging.warning(f"Failed to upsert CPU perf entry {cpu_id}: {e}")

    # Upsert series mapping entries
    for series, cpu_ids in SERIES_CPU_MAP.items():
        entity = {
            'PartitionKey': 'series',
            'RowKey': series,
            'cpuModels': json.dumps(cpu_ids),
        }
        try:
            table_client.upsert_entity(entity)
        except Exception as e:
            logging.warning(f"Failed to upsert series mapping {series}: {e}")

    logging.info(f"Seeded cpuperf table: {len(CPU_PERFORMANCE_TABLE)} CPU models, {len(SERIES_CPU_MAP)} series mappings")


def refresh_region(region: str, subscription_id: str, token: str, table_client, network_bw: Dict[str, int] = None) -> tuple:
    """
    Refresh SKU data for a specific region
    Fetches SKU data and pricing concurrently for performance
    Returns (number of SKUs updated, coverage stats dict)
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
    
    # Filter out Promo SKUs — identical hardware to base SKU, adds noise without value
    skus = [s for s in skus if not s.get('name', '').endswith('_Promo')]
    
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
                'ri1YearHourlyUSDWindows': pricing.get('ri1YearHourlyWindows') if pricing else None,
                'ri1YearMonthlyUSDWindows': pricing.get('ri1YearMonthlyWindows') if pricing else None,
                'ri3YearHourlyUSDWindows': pricing.get('ri3YearHourlyWindows') if pricing else None,
                'ri3YearMonthlyUSDWindows': pricing.get('ri3YearMonthlyWindows') if pricing else None,
                'pricingCurrency': pricing['currency'] if pricing else 'USD',
                'pricingLastUpdated': timestamp,
                'availabilityZones': ','.join(zones) if zones else '',
                'lastUpdated': timestamp
            }
            # Only include networkBandwidthMbps when we have actual data —
            # writing None to Table Storage coerces to integer 0
            bw = network_bw.get(sku['name'])
            # Constrained-vCPU variants share the same NIC as the base SKU
            if bw is None:
                base_name = _get_constrained_base_sku(sku['name'])
                if base_name:
                    bw = network_bw.get(base_name)
            if bw is not None:
                entity['networkBandwidthMbps'] = bw
            # Add CPU performance data
            cpu_perf = get_cpu_performance(sku['name'])
            if cpu_perf:
                entity['cpuPerfScore'] = cpu_perf['score']
                entity['cpuGeneration'] = cpu_perf['generation']
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
    
    # Compute coverage stats for this region
    coverage = _compute_region_coverage(region, entities)
    
    return count, coverage


def _get_constrained_base_sku(name: str) -> Optional[str]:
    """
    For constrained-vCPU SKUs (e.g., Standard_E96-24ads_v6), return the base SKU name
    (e.g., Standard_E96ads_v6). Returns None if not a constrained variant.
    Constrained SKUs have the pattern: Standard_{family}{totalCPU}-{constrainedCPU}{suffix}
    """
    match = re.match(r'^(Standard_[A-Z]+\d+)-\d+(.+)$', name)
    if match:
        return match.group(1) + match.group(2)
    return None


def _is_previous_gen_sku(name: str) -> bool:
    """Check if a SKU is previous-gen/retiring with no official network BW docs."""
    # Original D-series (D1-D14, not Dv2+) — retirement announced
    if re.match(r'^Standard_D\d+$', name):
        return True
    # Original DS-series (DS1-DS14)
    if re.match(r'^Standard_DS\d+$', name):
        return True
    # Original B1ls (only B1ls2 in current docs)
    if name == 'Standard_B1ls':
        return True
    return False


def _is_retired_no_pricing_sku(name: str) -> bool:
    """Check if a SKU is retired/restricted with no pricing in any API source."""
    # G-series (Standard_G1-G5, GS1-GS5) — limited regional availability
    if re.match(r'^Standard_G\d+$', name) or re.match(r'^Standard_GS\d+$', name):
        return True
    # G-series constrained variants (GS4-4, GS4-8, GS5-8, GS5-16)
    if re.match(r'^Standard_GS\d+-\d+$', name):
        return True
    # NV v2 series (NV6s_v2, NV12s_v2, NV24s_v2)
    if re.match(r'^Standard_NV\d+s_v2$', name):
        return True
    # Old L-series (L4s-L32s, not Lsv2/Lsv3+)
    if re.match(r'^Standard_L\d+s$', name):
        return True
    # E96ias_v4 — isolated/dedicated, no public pricing
    if name == 'Standard_E96ias_v4':
        return True
    return False


# ============================================================================
# VM SKU Retirement Data
# Source: https://github.com/MicrosoftDocs/azure-compute-docs/blob/main/articles/virtual-machines/sizes/retirement/retired-sizes-list.md
# ============================================================================
VM_RETIREMENT_INFO = [
    # General Purpose
    {'pattern': r'^Standard_D\d+$', 'status': 'Announced', 'retirementDate': '2028-05-01',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_DS\d+$', 'status': 'Announced', 'retirementDate': '2028-05-01',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_D\d+_v2$', 'status': 'Announced', 'retirementDate': '2028-05-01',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_DS\d+_v2$', 'status': 'Announced', 'retirementDate': '2028-05-01',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_A\d+m?_v2$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_B\d+[a-z]*s$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_B\d+ls$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    # Compute Optimized
    {'pattern': r'^Standard_F\d+$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_F\d+s$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_F\d+s_v2$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    # Memory Optimized
    {'pattern': r'^Standard_G\d+$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_GS\d+(-\d+)?$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_M192idms_v2$', 'status': 'Announced', 'retirementDate': '2027-03-31',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement'},
    {'pattern': r'^Standard_M192ids_v2$', 'status': 'Announced', 'retirementDate': '2027-03-31',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement'},
    {'pattern': r'^Standard_M192ims_v2$', 'status': 'Announced', 'retirementDate': '2027-03-31',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement'},
    {'pattern': r'^Standard_M192is_v2$', 'status': 'Announced', 'retirementDate': '2027-03-31',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement'},
    # Storage Optimized
    {'pattern': r'^Standard_L\d+s$', 'status': 'Announced', 'retirementDate': '2028-05-01',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    {'pattern': r'^Standard_L\d+s_v2$', 'status': 'Announced', 'retirementDate': '2028-11-15',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide'},
    # GPU - Retired
    {'pattern': r'^Standard_NC24rs_v3$', 'status': 'Retired', 'retirementDate': '2025-09-30',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/ncv3-nc24rs-retirement'},
    {'pattern': r'^Standard_NC\d+s?_v3$', 'status': 'Retired', 'retirementDate': '2025-09-30',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/ncv3-retirement'},
    # GPU - Announced
    {'pattern': r'^Standard_NV\d+s?_v3$', 'status': 'Announced', 'retirementDate': '2026-09-30',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nvv3-series-retirement'},
    {'pattern': r'^Standard_NV\d+as_v4$', 'status': 'Announced', 'retirementDate': '2026-09-30',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nvv4-retirement'},
    # FPGA
    {'pattern': r'^Standard_NP\d+s$', 'status': 'Announced', 'retirementDate': '2027-05-31',
     'migrationGuideUrl': 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/np-series-retirement'},
]


def _get_retirement_info(sku_name: str) -> Optional[Dict]:
    """
    Check if a SKU is announced for retirement or already retired.
    Returns dict with {status, retirementDate, migrationGuideUrl} or None.
    """
    for entry in VM_RETIREMENT_INFO:
        if re.match(entry['pattern'], sku_name):
            return {
                'retirementStatus': entry['status'],
                'retirementDate': entry['retirementDate'],
                'migrationGuideUrl': entry['migrationGuideUrl']
            }
    return None


def _retirement_penalty(sku_name: str) -> float:
    """
    Calculate a similarity score penalty for retiring/retired SKUs.
    Returns a negative value to subtract from the similarity score.
    """
    info = _get_retirement_info(sku_name)
    if not info:
        return 0.0

    if info['retirementStatus'] == 'Retired':
        return 15.0

    # For 'Announced' status, scale penalty by time until retirement
    try:
        retirement_date = datetime.strptime(info['retirementDate'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        months_remaining = (retirement_date - now).days / 30.44
        if months_remaining <= 6:
            return 10.0
        elif months_remaining <= 12:
            return 5.0
        else:
            return 2.0
    except (ValueError, TypeError):
        return 2.0


def _is_promo_sku(name: str) -> bool:
    """Check if a SKU is a Promo variant (no RI available)."""
    return name.endswith('_Promo')


def _is_cc_sku(name: str) -> bool:
    """Check if a SKU is a Confidential Computing (CC) variant — no RI available."""
    return '_cc_' in name.lower()


def _compute_region_coverage(region: str, entities: List[Dict]) -> Dict:
    """Compute data coverage stats for a set of cache entities in a region."""
    total = len(entities)
    if total == 0:
        return {'region': region, 'totalSkus': 0}

    # Eligible SKU counts for smarter denominators
    ri_eligible = [e for e in entities if not _is_promo_sku(e['name']) and not _is_cc_sku(e['name'])]
    # Exclude completely unpriced SKUs from RI denominator (too new, no pricing anywhere)
    ri_eligible = [e for e in ri_eligible if e.get('hourlyPriceUSD') is not None]
    # For 3yr RI: exclude SKUs that have 1yr RI but not 3yr (Azure doesn't offer 3yr for them)
    ri3_eligible = [e for e in ri_eligible if not (e.get('ri1YearHourlyUSD') is not None and e.get('ri3YearHourlyUSD') is None)]
    bw_eligible = [e for e in entities if not _is_previous_gen_sku(e['name'])]
    payg_eligible = [e for e in entities if not _is_retired_no_pricing_sku(e['name'])]

    # Pricing coverage (use payg_eligible denominator)
    has_payg_linux = sum(1 for e in payg_eligible if e.get('hourlyPriceUSD') is not None)
    has_payg_windows = sum(1 for e in payg_eligible if e.get('hourlyPriceUSDWindows') is not None)
    has_ri1year = sum(1 for e in ri_eligible if e.get('ri1YearHourlyUSD') is not None)
    has_ri3year = sum(1 for e in ri3_eligible if e.get('ri3YearHourlyUSD') is not None)
    has_ri1year_win = sum(1 for e in ri_eligible if e.get('ri1YearHourlyUSDWindows') is not None)
    has_ri3year_win = sum(1 for e in ri3_eligible if e.get('ri3YearHourlyUSDWindows') is not None)

    # Capability coverage
    has_vcpus = sum(1 for e in entities if e.get('vCPUs', 0) > 0)
    has_memory = sum(1 for e in entities if e.get('memoryGB', 0) > 0)
    has_disk_iops = sum(1 for e in entities if e.get('uncachedDiskIOPS', 0) > 0)
    has_disk_throughput = sum(1 for e in entities if e.get('uncachedDiskBytesPerSecond', 0) > 0)
    has_network_bw = sum(1 for e in bw_eligible if e.get('networkBandwidthMbps') is not None)
    has_zones = sum(1 for e in entities if e.get('availabilityZones', ''))
    has_hyperv_gen = sum(1 for e in entities if e.get('hyperVGenerations', ''))

    # Collect SKU names missing key data (from eligible pools)
    missing_pricing = [e['name'] for e in payg_eligible if e.get('hourlyPriceUSD') is None]
    missing_ri = [e['name'] for e in ri_eligible if e.get('hourlyPriceUSD') is not None and e.get('ri1YearHourlyUSD') is None]
    missing_network = [e['name'] for e in bw_eligible if e.get('networkBandwidthMbps') is None]

    return {
        'region': region,
        'totalSkus': total,
        'paygEligibleSkus': len(payg_eligible),
        'riEligibleSkus': len(ri_eligible),
        'ri3EligibleSkus': len(ri3_eligible),
        'bwEligibleSkus': len(bw_eligible),
        'paygLinux': has_payg_linux,
        'paygWindows': has_payg_windows,
        'ri1Year': has_ri1year,
        'ri3Year': has_ri3year,
        'ri1YearWindows': has_ri1year_win,
        'ri3YearWindows': has_ri3year_win,
        'vCPUs': has_vcpus,
        'memory': has_memory,
        'diskIOPS': has_disk_iops,
        'diskThroughput': has_disk_throughput,
        'networkBandwidth': has_network_bw,
        'availabilityZones': has_zones,
        'hyperVGenerations': has_hyperv_gen,
        'missingPricingSkus': missing_pricing[:50],
        'missingRiSkus': missing_ri[:50],
        'missingNetworkSkus': missing_network[:50],
    }


def _emit_coverage_telemetry(all_region_coverage: List[Dict]) -> None:
    """Emit per-region and aggregate coverage stats as structured log events."""
    if not all_region_coverage:
        return

    # Per-region coverage events
    # NOTE: Flex Consumption ignores extra={'custom_dimensions': ...}.
    # Embed data as JSON in Message so KQL can parse_json(Message).
    for cov in all_region_coverage:
        total = cov.get('totalSkus', 0)
        if total == 0:
            continue
        payg_eligible = cov.get('paygEligibleSkus', total)
        ri_eligible = cov.get('riEligibleSkus', total)
        ri3_eligible = cov.get('ri3EligibleSkus', ri_eligible)
        bw_eligible = cov.get('bwEligibleSkus', total)
        logging.info(json.dumps({
            'event_type': 'sku_coverage_region',
            'region': cov['region'],
            'totalSkus': total,
            'paygEligibleSkus': payg_eligible,
            'riEligibleSkus': ri_eligible,
            'ri3EligibleSkus': ri3_eligible,
            'bwEligibleSkus': bw_eligible,
            'paygLinuxPct': round(cov['paygLinux'] / payg_eligible * 100, 1) if payg_eligible else 0,
            'paygWindowsPct': round(cov['paygWindows'] / payg_eligible * 100, 1) if payg_eligible else 0,
            'ri1YearPct': round(cov['ri1Year'] / ri_eligible * 100, 1) if ri_eligible else 0,
            'ri3YearPct': round(cov['ri3Year'] / ri3_eligible * 100, 1) if ri3_eligible else 0,
            'ri1YearWindowsPct': round(cov['ri1YearWindows'] / ri_eligible * 100, 1) if ri_eligible else 0,
            'ri3YearWindowsPct': round(cov['ri3YearWindows'] / ri3_eligible * 100, 1) if ri3_eligible else 0,
            'vCPUsPct': round(cov['vCPUs'] / total * 100, 1),
            'memoryPct': round(cov['memory'] / total * 100, 1),
            'diskIOPSPct': round(cov['diskIOPS'] / total * 100, 1),
            'diskThroughputPct': round(cov['diskThroughput'] / total * 100, 1),
            'networkBandwidthPct': round(cov['networkBandwidth'] / bw_eligible * 100, 1) if bw_eligible else 0,
            'availabilityZonesPct': round(cov['availabilityZones'] / total * 100, 1),
            'hyperVGenerationsPct': round(cov['hyperVGenerations'] / total * 100, 1),
            'paygLinuxCount': cov['paygLinux'],
            'paygWindowsCount': cov['paygWindows'],
            'ri1YearCount': cov['ri1Year'],
            'ri3YearCount': cov['ri3Year'],
            'networkBandwidthCount': cov['networkBandwidth'],
            'missingPricingSkus': cov.get('missingPricingSkus', []),
            'missingRiSkus': cov.get('missingRiSkus', [])[:20],
            'missingNetworkSkus': cov.get('missingNetworkSkus', [])[:20],
        }))

    # Aggregate summary across all regions
    totals = sum(c.get('totalSkus', 0) for c in all_region_coverage)
    if totals == 0:
        return

    payg_totals = sum(c.get('paygEligibleSkus', c.get('totalSkus', 0)) for c in all_region_coverage)
    ri_totals = sum(c.get('riEligibleSkus', c.get('totalSkus', 0)) for c in all_region_coverage)
    ri3_totals = sum(c.get('ri3EligibleSkus', c.get('riEligibleSkus', c.get('totalSkus', 0))) for c in all_region_coverage)
    bw_totals = sum(c.get('bwEligibleSkus', c.get('totalSkus', 0)) for c in all_region_coverage)

    agg = {
        'paygLinux': sum(c.get('paygLinux', 0) for c in all_region_coverage),
        'ri1Year': sum(c.get('ri1Year', 0) for c in all_region_coverage),
        'ri3Year': sum(c.get('ri3Year', 0) for c in all_region_coverage),
        'networkBandwidth': sum(c.get('networkBandwidth', 0) for c in all_region_coverage),
        'vCPUs': sum(c.get('vCPUs', 0) for c in all_region_coverage),
        'memory': sum(c.get('memory', 0) for c in all_region_coverage),
    }

    logging.info(json.dumps({
        'event_type': 'sku_coverage_summary',
        'totalRegions': len(all_region_coverage),
        'totalSkus': totals,
        'paygEligibleSkus': payg_totals,
        'riEligibleSkus': ri_totals,
        'ri3EligibleSkus': ri3_totals,
        'bwEligibleSkus': bw_totals,
        'overallPaygPct': round(agg['paygLinux'] / payg_totals * 100, 1) if payg_totals else 0,
        'overallRi1YearPct': round(agg['ri1Year'] / ri_totals * 100, 1) if ri_totals else 0,
        'overallRi3YearPct': round(agg['ri3Year'] / ri3_totals * 100, 1) if ri3_totals else 0,
        'overallNetworkBwPct': round(agg['networkBandwidth'] / bw_totals * 100, 1) if bw_totals else 0,
        'overallVCPUsPct': round(agg['vCPUs'] / totals * 100, 1),
        'overallMemoryPct': round(agg['memory'] / totals * 100, 1),
    }))


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
    
    # Price differences (PAYG + RI variants, Linux + Windows, for frontend toggle support)
    if target_pricing and alt_pricing:
        currency = target_pricing.get('currency', 'USD')
        differences['pricing'] = {
            'hourly': calculate_price_diff(
                target_pricing.get('hourlyPrice'),
                alt_pricing.get('hourlyPrice'),
                currency
            ),
            'monthly': calculate_price_diff(
                target_pricing.get('monthlyPrice'),
                alt_pricing.get('monthlyPrice'),
                currency
            ),
            'hourlyWindows': calculate_price_diff(
                target_pricing.get('hourlyPriceWindows'),
                alt_pricing.get('hourlyPriceWindows'),
                currency
            ) if target_pricing.get('hourlyPriceWindows') is not None else None,
            'monthlyWindows': calculate_price_diff(
                target_pricing.get('monthlyPriceWindows'),
                alt_pricing.get('monthlyPriceWindows'),
                currency
            ) if target_pricing.get('monthlyPriceWindows') is not None else None,
            'efficiency': calculate_cost_efficiency(
                target_sku, alternative_sku,
                target_pricing, alt_pricing
            ),
            'ri1Year': {
                'hourly': calculate_price_diff(
                    target_pricing.get('ri1YearHourly'),
                    alt_pricing.get('ri1YearHourly'),
                    currency
                ),
                'monthly': calculate_price_diff(
                    target_pricing.get('ri1YearMonthly'),
                    alt_pricing.get('ri1YearMonthly'),
                    currency
                )
            } if target_pricing.get('ri1YearMonthly') is not None else None,
            'ri1YearWindows': {
                'hourly': calculate_price_diff(
                    target_pricing.get('ri1YearHourlyWindows'),
                    alt_pricing.get('ri1YearHourlyWindows'),
                    currency
                ),
                'monthly': calculate_price_diff(
                    target_pricing.get('ri1YearMonthlyWindows'),
                    alt_pricing.get('ri1YearMonthlyWindows'),
                    currency
                )
            } if target_pricing.get('ri1YearMonthlyWindows') is not None else None,
            'ri3Year': {
                'hourly': calculate_price_diff(
                    target_pricing.get('ri3YearHourly'),
                    alt_pricing.get('ri3YearHourly'),
                    currency
                ),
                'monthly': calculate_price_diff(
                    target_pricing.get('ri3YearMonthly'),
                    alt_pricing.get('ri3YearMonthly'),
                    currency
                )
            } if target_pricing.get('ri3YearMonthly') is not None else None,
            'ri3YearWindows': {
                'hourly': calculate_price_diff(
                    target_pricing.get('ri3YearHourlyWindows'),
                    alt_pricing.get('ri3YearHourlyWindows'),
                    currency
                ),
                'monthly': calculate_price_diff(
                    target_pricing.get('ri3YearMonthlyWindows'),
                    alt_pricing.get('ri3YearMonthlyWindows'),
                    currency
                )
            } if target_pricing.get('ri3YearMonthlyWindows') is not None else None
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
