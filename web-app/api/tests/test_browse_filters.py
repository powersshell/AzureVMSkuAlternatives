"""Browse capability contracts: source truth, legacy caches, and merge refreshes."""
import json
from types import SimpleNamespace

import pytest

from function_app import (
    _browse_source_capabilities, build_grid_row, extract_capabilities_for_cache,
)


def cached_row(caps, name='Standard_D8s_v5', **extra):
    source = {'name': name, 'capabilities': [
        {'name': key, 'value': value} for key, value in caps.items()
    ]}
    cache = extract_capabilities_for_cache(source)
    return build_grid_row({'name': name, **cache, **extra})


@pytest.mark.unit
class TestBrowseCapabilities:
    def test_missing_is_not_false_or_zero(self):
        caps = _browse_source_capabilities({})
        assert caps['schemaVersion'] == 1
        assert all(value is None for key, value in caps.items() if key != 'schemaVersion')

    def test_zero_false_and_unit_conversion(self):
        row = cached_row({
            'vCPUs': '32', 'vCPUsAvailable': '8', 'MemoryGB': '256',
            'PremiumIO': 'False', 'RdmaEnabled': 'false', 'GPUs': '0',
            'MaxDataDiskCount': '0', 'MaxNetworkInterfaces': '0',
            'UncachedDiskIOPS': '0', 'UncachedDiskBytesPerSecond': '125000000',
            'HyperVGenerations': 'V1,V2', 'TrustedLaunchDisabled': 'False',
            'DiskControllerTypes': 'SCSI, NVMe', 'MaxResourceVolumeMB': '0',
        })
        caps = row['browseCapabilities']
        assert caps['vCPUs'] == row['vCPUs'] == 8
        assert caps['memoryPerVcpu'] == 32
        assert caps['uncachedDiskMBps'] == 125
        assert caps['uncachedDiskIOPS'] == caps['gpuCount'] == caps['maxDataDisks'] == 0
        assert caps['premiumIO'] is False
        assert caps['rdmaEnabled'] is False
        assert caps['hasTempDisk'] is False
        assert caps['trustedLaunch'] is True
        assert caps['diskControllerTypes'] == ['SCSI', 'NVMe']
        assert caps['hyperVGenerations'] == ['V1', 'V2']

    @pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', True, '', None, 'bad'])
    def test_invalid_numbers_stay_unknown(self, value):
        caps = _browse_source_capabilities({'UncachedDiskIOPS': value})
        assert caps['uncachedDiskIOPS'] is None

    def test_invalid_vcpu_and_boolean_are_unknown(self):
        caps = _browse_source_capabilities({
            'vCPUs': '8', 'vCPUsAvailable': '0', 'PremiumIO': 'yes',
            'HyperVGenerations': 'V3', 'DiskControllerTypes': 'unknown',
        })
        assert caps['vCPUs'] is None
        assert caps['memoryPerVcpu'] is None
        assert caps['premiumIO'] is None
        assert caps['hyperVGenerations'] is None
        assert caps['diskControllerTypes'] is None

    def test_legacy_defaults_are_not_verified(self):
        caps = build_grid_row({
            'name': 'Standard_Unknown1', 'vCPUs': 8, 'memoryGB': 32,
            'architecture': 'x64', 'premiumIO': False, 'trustedLaunch': False,
            'maxNics': 0, 'uncachedDiskIOPS': 12000,
        })['browseCapabilities']
        assert caps['vCPUs'] == 8
        assert caps['memoryPerVcpu'] == 4
        assert caps['uncachedDiskIOPS'] == 12000
        assert caps['architecture'] is None
        assert caps['premiumIO'] is None
        assert caps['trustedLaunch'] is None
        assert caps['maxNics'] is None

    def test_snapshot_replacement_masks_stale_merge_properties(self):
        old = extract_capabilities_for_cache({
            'capabilities': [{'name': 'PremiumIO', 'value': 'True'},
                             {'name': 'MaxDataDiskCount', 'value': '16'}]})
        refreshed = extract_capabilities_for_cache({'capabilities': []})
        merged = {**old, **refreshed, 'name': 'Standard_Unknown1',
                  'premiumIO': True, 'maxDataDisks': 16}
        caps = build_grid_row(merged)['browseCapabilities']
        assert caps['premiumIO'] is None
        assert caps['maxDataDisks'] is None
        assert json.loads(merged['browseCapabilitiesJson'])['maxDataDisks'] is None

    def test_legacy_positive_support_and_gpu_count_are_defensible(self):
        caps = build_grid_row({
            'name': 'Standard_Unknown1', 'nvme': True, 'premiumIO': True,
            'architecture': 'Arm64', 'gpuCount': 1, 'gpuType': 'Published GPU',
        })['browseCapabilities']
        assert caps['nvme'] is True
        assert caps['premiumIO'] is True
        assert caps['architecture'] == 'arm64'
        assert caps['gpuCount'] == 1
        assert caps['gpuType'] == 'Published GPU'

    def test_arm_temp_and_security(self):
        caps = cached_row({
            'CpuArchitectureType': 'Arm64', 'NvmeDiskSizeInMiB': '65536',
            'HyperVGenerations': 'V2', 'EncryptionAtHostSupported': 'True',
            'ConfidentialComputingType': '',
        }, name='Standard_D2pds_v6')['browseCapabilities']
        assert caps['architecture'] == 'arm64'
        assert caps['cpuVendor'] == 'ARM'
        assert caps['hasTempDisk'] is True
        assert caps['nvme'] is True
        assert caps['encryptionAtHost'] is True
        assert caps['confidentialComputingType'] == 'None'
        assert caps['trustedLaunch'] is None

    def test_gpu_fraction_and_zone_known_empty(self):
        caps = cached_row(
            {'GPUs': '1'}, name='Standard_NV4as_v4',
            availabilityZones='', browseZonesKnown=True)['browseCapabilities']
        assert caps['gpuCount'] == 1
        assert caps['gpuAllocation'] == 0.125
        assert caps['gpuMemoryGB'] > 0
        assert caps['availabilityZones'] == []

    @pytest.mark.parametrize('raw', ['not json', '[]', '{"schemaVersion":2}'])
    def test_bad_snapshot_logged_and_legacy_is_conservative(self, raw, caplog):
        caps = build_grid_row({'name': 'Standard_Unknown1', 'browseCapabilitiesJson': raw})['browseCapabilities']
        assert caps['premiumIO'] is None
        assert 'Browse snapshot' in caplog.text

    def test_json_contract_has_no_nonfinite_numbers(self):
        row = cached_row({'MemoryGB': '32', 'vCPUs': '8', 'RdmaEnabled': 'True'},
                         networkBandwidthMbps=10000, availabilityZones='1,2')
        parsed = json.loads(json.dumps(row, allow_nan=False))
        assert parsed['browseCapabilities']['networkBandwidthMbps'] == 10000
        assert parsed['browseCapabilities']['availabilityZones'] == ['1', '2']

    def test_lost_bandwidth_does_not_revive_stale_cache_value(self):
        caps = build_grid_row({
            'name': 'Standard_Unknown1', 'networkBandwidthMbps': 10000,
            'browseNetworkBandwidthKnown': False,
        })['browseCapabilities']
        assert caps['networkBandwidthMbps'] is None

    @pytest.mark.parametrize('currency', ['USD', 'EUR'])
    def test_grid_route_preserves_snapshot_contract(self, monkeypatch, currency):
        import function_app as app

        source = {'capabilities': [
            {'name': 'vCPUs', 'value': '8'},
            {'name': 'MemoryGB', 'value': '32'},
            {'name': 'PremiumIO', 'value': 'False'},
        ]}
        entity = {'name': 'Standard_D8s_v5', **extract_capabilities_for_cache(source)}
        table = SimpleNamespace(query_entities=lambda **kwargs: [entity])
        service = SimpleNamespace(get_table_client=lambda name: table)
        monkeypatch.setenv('SKU_CACHE_STORAGE_ACCOUNT', 'fixture')
        monkeypatch.setattr(app, 'DefaultAzureCredential', lambda: object())
        monkeypatch.setattr(app, 'TableServiceClient', lambda **kwargs: service)
        monkeypatch.setattr(app, 'fetch_bulk_region_pricing', lambda *args: {})

        class Response:
            def __init__(self, body, status_code=200, **kwargs):
                self.body = json.loads(body)
                self.status_code = status_code

        monkeypatch.setattr(app.func, 'HttpResponse', Response)
        route = app.grid
        if hasattr(route, 'build'):
            route = route.build().get_user_function()
        response = route(SimpleNamespace(params={'location': 'eastus', 'currency': currency}))
        assert response.status_code == 200
        assert response.body['currency'] == currency
        caps = response.body['skus'][0]['browseCapabilities']
        assert caps['premiumIO'] is False
        assert caps['maxDataDisks'] is None
        assert caps['vCPUs'] == 8
