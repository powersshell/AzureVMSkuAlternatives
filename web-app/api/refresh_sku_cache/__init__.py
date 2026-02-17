"""
Timer-triggered Azure Function to refresh VM SKU cache
Runs daily at 2:00 AM UTC to populate Storage Table with latest SKU data
"""
import logging
import os
import requests
from typing import Dict, List, Optional
from datetime import datetime, timezone
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential


def main(timer: func.TimerRequest) -> None:
    """
    Timer trigger function to refresh SKU cache
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


def get_access_token() -> str:
    """Get access token for Azure Management API using managed identity"""
    msi_endpoint = os.environ.get('IDENTITY_ENDPOINT')
    msi_header = os.environ.get('IDENTITY_HEADER')
    
    if not msi_endpoint or not msi_header:
        raise Exception('Managed identity not configured')
    
    token_url = f"{msi_endpoint}?resource=https://management.azure.com/&api-version=2019-08-01"
    headers = {'X-IDENTITY-HEADER': msi_header}
    
    response = requests.get(token_url, headers=headers, timeout=10)
    
    if not response.ok:
        raise Exception(f'Failed to get access token: {response.status_code}')
    
    return response.json()['access_token']


def refresh_region(region: str, subscription_id: str, token: str, table_client) -> int:
    """
    Refresh SKU data for a specific region
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
    
    count = 0
    timestamp = datetime.now(timezone.utc).isoformat()
    
    for sku in skus:
        try:
            # Extract capabilities
            capabilities = extract_capabilities(sku)
            
            # Get pricing (with error handling)
            pricing = get_vm_pricing(sku['name'], region, 'USD')
            
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
                'hourlyPrice': pricing['hourlyPrice'] if pricing else 0.0,
                'monthlyPrice': pricing['monthlyPrice'] if pricing else 0.0,
                'currency': pricing['currency'] if pricing else 'USD',
                'availabilityZones': ','.join(zones) if zones else '',
                'lastUpdated': timestamp
            }
            
            # Upsert entity (insert or update)
            table_client.upsert_entity(entity)
            count += 1
            
        except Exception as e:
            logging.warning(f"Failed to process SKU {sku.get('name', 'unknown')}: {e}")
    
    return count


def extract_capabilities(sku: Dict) -> Dict:
    """Extract VM capabilities from SKU data"""
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


def get_vm_pricing(sku_name: str, location: str, currency_code: str) -> Optional[Dict]:
    """Get VM pricing from Azure Retail Prices API"""
    try:
        api_url = 'https://prices.azure.com/api/retail/prices'
        filter_str = f"serviceName eq 'Virtual Machines' and armSkuName eq '{sku_name}' and armRegionName eq '{location}' and type eq 'Consumption'"
        url = f"{api_url}?currencyCode={currency_code}&$filter={filter_str}"
        
        response = requests.get(url, headers={'Accept': 'application/json'}, timeout=10)
        
        if not response.ok:
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
    except Exception:
        return None


def get_availability_zones(sku: Dict, location: str) -> List[str]:
    """Extract availability zones for the SKU in the location"""
    for location_info in sku.get('locationInfo', []):
        if location_info.get('location', '').lower() == location.lower():
            return location_info.get('zones', [])
    return []
