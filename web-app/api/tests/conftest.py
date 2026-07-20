"""Shared test fixtures for Azure VM SKU Alternatives test suite."""

import os
import sys
import pytest

# Add the api directory to the path so we can import function_app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def table_client():
    """Create Azure Table Storage client for cache validation tests.
    
    Requires either:
    - AZURE_STORAGE_CONNECTION_STRING env var, or
    - DefaultAzureCredential with AZURE_STORAGE_ACCOUNT_NAME env var
    """
    conn_str = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
    account_name = os.environ.get('AZURE_STORAGE_ACCOUNT_NAME', 'vmskualternatives')
    table_name = os.environ.get('AZURE_TABLE_NAME', 'vmskus')

    if conn_str:
        from azure.data.tables import TableServiceClient
        service = TableServiceClient.from_connection_string(conn_str)
        return service.get_table_client(table_name)
    else:
        try:
            from azure.data.tables import TableServiceClient
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
            endpoint = f"https://{account_name}.table.core.windows.net"
            service = TableServiceClient(endpoint=endpoint, credential=credential)
            return service.get_table_client(table_name)
        except Exception as e:
            pytest.skip(f"Azure credentials not available: {e}")


@pytest.fixture
def api_base_url():
    """Base URL for the live API. Defaults to the deployed Functions app."""
    return os.environ.get(
        'API_BASE_URL',
        'https://vmsku-api-func-cus.azurewebsites.net/api'
    )


@pytest.fixture
def major_regions():
    """Major Azure regions that should have comprehensive SKU coverage."""
    return ['eastus', 'eastus2', 'westus2', 'westeurope', 'northeurope']


@pytest.fixture
def all_cached_regions():
    """All regions included in the daily cache refresh."""
    return [
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


# Sample mock data for unit tests (no network needed)

@pytest.fixture
def sample_sku_capabilities():
    """Raw Azure SKU API capability array (as returned by Resource SKUs API)."""
    return {
        'name': 'Standard_D8s_v5',
        'capabilities': [
            {'name': 'vCPUs', 'value': '8'},
            {'name': 'MemoryGB', 'value': '32'},
            {'name': 'MaxDataDiskCount', 'value': '16'},
            {'name': 'MaxNetworkInterfaces', 'value': '4'},
            {'name': 'PremiumIO', 'value': 'True'},
            {'name': 'AcceleratedNetworkingEnabled', 'value': 'True'},
            {'name': 'EncryptionAtHostSupported', 'value': 'True'},
            {'name': 'EphemeralOSDiskSupported', 'value': 'True'},
            {'name': 'GPUs', 'value': '0'},
            {'name': 'NvmeDiskSizeInMiB', 'value': '0'},
            {'name': 'UncachedDiskIOPS', 'value': '12800'},
            {'name': 'UncachedDiskBytesPerSecond', 'value': '290000000'},
            {'name': 'MaxWriteAcceleratorDisksAllowed', 'value': '0'},
            {'name': 'OSVhdSizeMB', 'value': '1047552'},
            {'name': 'HyperVGenerations', 'value': 'V1,V2'},
            {'name': 'CpuArchitectureType', 'value': 'x64'},
            {'name': 'ACUs', 'value': '195'},
            {'name': 'vCPUsPerCore', 'value': '2'},
            {'name': 'DiskControllerTypes', 'value': 'SCSI, NVMe'},
            {'name': 'RdmaEnabled', 'value': 'False'},
            {'name': 'ConfidentialComputingType', 'value': ''},
            {'name': 'TrustedLaunchDisabled', 'value': 'False'},
        ]
    }


@pytest.fixture
def sample_pricing():
    """Sample pricing dict as returned by get_vm_pricing."""
    return {
        'hourlyPrice': 0.384,
        'monthlyPrice': 280.32,
        'hourlyPriceWindows': 0.752,
        'monthlyPriceWindows': 548.96,
        'ri1YearHourly': 0.2266,
        'ri1YearMonthly': 165.42,
        'ri3YearHourly': 0.1421,
        'ri3YearMonthly': 103.72,
        'ri1YearHourlyWindows': 0.5946,
        'ri1YearMonthlyWindows': 434.06,
        'ri3YearHourlyWindows': 0.5101,
        'ri3YearMonthlyWindows': 372.37,
        'currency': 'USD'
    }


@pytest.fixture
def sample_cached_sku():
    """Sample cached SKU entity as stored in Azure Table Storage."""
    return {
        'PartitionKey': 'eastus',
        'RowKey': 'Standard_D8s_v5',
        'name': 'Standard_D8s_v5',
        'vCPUs': 8,
        'memoryGB': 32.0,
        'maxDataDisks': 16,
        'maxNics': 4,
        'cpuVendor': 'Intel',
        'architecture': 'x64',
        'premiumIO': True,
        'acceleratedNetworking': True,
        'encryptionAtHost': True,
        'ephemeralOSDisk': True,
        'nvme': False,
        'gpuCount': 0,
        'gpuType': None,
        'uncachedDiskIOPS': 12800,
        'uncachedDiskBytesPerSecond': 290000000,
        'osVhdSizeMB': 1047552,
        'hyperVGenerations': 'V1,V2',
        'acu': 195,
        'vCPUsPerCore': 2,
        'diskControllerTypes': 'SCSI, NVMe',
        'rdmaEnabled': False,
        'confidentialComputingType': '',
        'trustedLaunch': True,
        'hourlyPriceUSD': 0.384,
        'monthlyPriceUSD': 280.32,
        'hourlyPriceWindowsUSD': 0.752,
        'monthlyPriceWindowsUSD': 548.96,
        'ri1YearHourlyUSD': 0.2266,
        'ri1YearMonthlyUSD': 165.42,
        'ri3YearHourlyUSD': 0.1421,
        'ri3YearMonthlyUSD': 103.72,
        'ri1YearHourlyWindowsUSD': 0.5946,
        'ri1YearMonthlyWindowsUSD': 434.06,
        'ri3YearHourlyWindowsUSD': 0.5101,
        'ri3YearMonthlyWindowsUSD': 372.37,
        'availabilityZones': '1,2,3',
        'lastUpdated': '2026-05-01T02:00:00Z'
    }
