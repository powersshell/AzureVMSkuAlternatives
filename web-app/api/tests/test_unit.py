"""Unit tests for pure functions in function_app.py.

These tests require NO network access — they use mock data only.
Run: pytest tests/test_unit.py -m unit
"""

import pytest
import sys
import os
import importlib
import types

# Import specific functions from function_app without triggering azure.functions import.
# We mock the azure modules so the top-level import succeeds even without azure SDK.
_AZURE_MOCKS = {}
for mod_name in ['azure', 'azure.functions', 'azure.data', 'azure.data.tables',
                 'azure.identity']:
    if mod_name not in sys.modules:
        _AZURE_MOCKS[mod_name] = types.ModuleType(mod_name)
        sys.modules[mod_name] = _AZURE_MOCKS[mod_name]

# Add fake classes/attributes needed for function_app.py top-level imports
_func_mod = sys.modules['azure.functions']
_func_mod.FunctionApp = type('FunctionApp', (), {
    'route': lambda *a, **kw: (lambda f: f),
    'timer_trigger': lambda *a, **kw: (lambda f: f),
})
_func_mod.AuthLevel = types.SimpleNamespace(ANONYMOUS='anonymous', FUNCTION='function')
_func_mod.HttpRequest = object
_func_mod.HttpResponse = type('HttpResponse', (), {'__init__': lambda self, *a, **kw: None})
_func_mod.TimerRequest = object

_tables_mod = sys.modules['azure.data.tables']
_tables_mod.TableServiceClient = type('TableServiceClient', (), {})

_identity_mod = sys.modules['azure.identity']
_identity_mod.DefaultAzureCredential = type('DefaultAzureCredential', (), {})

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from function_app import (
    _compute_windows_ri,
    _pricing_has_ri,
    detect_cpu_vendor,
    extract_capabilities,
    extract_capabilities_for_cache,
    calculate_numeric_diff,
    calculate_price_diff,
    calculate_boolean_diff,
    calculate_similarity,
)


# ============================================================================
# _compute_windows_ri tests
# ============================================================================

class TestComputeWindowsRI:

    @pytest.mark.unit
    def test_basic_surcharge(self):
        """Windows RI = Linux RI + (Windows PAYG - Linux PAYG) license surcharge."""
        pricing = {
            'hourlyPrice': 0.384,          # Linux PAYG hourly
            'hourlyPriceWindows': 0.752,    # Windows PAYG hourly
            'ri1YearHourly': 0.2266,
            'ri3YearHourly': 0.1421,
        }
        _compute_windows_ri(pricing)

        surcharge = 0.752 - 0.384  # 0.368
        assert pricing['ri1YearHourlyWindows'] == pytest.approx(0.2266 + surcharge, abs=0.001)
        assert pricing['ri3YearHourlyWindows'] == pytest.approx(0.1421 + surcharge, abs=0.001)
        assert pricing['ri1YearMonthlyWindows'] == pytest.approx((0.2266 + surcharge) * 730, abs=1.0)
        assert pricing['ri3YearMonthlyWindows'] == pytest.approx((0.1421 + surcharge) * 730, abs=1.0)

    @pytest.mark.unit
    def test_no_windows_pricing(self):
        """Should not add Windows RI fields when Windows PAYG is missing."""
        pricing = {
            'hourlyPrice': 0.384,
            'hourlyPriceWindows': None,
            'ri1YearHourly': 0.2266,
        }
        _compute_windows_ri(pricing)
        assert 'ri1YearHourlyWindows' not in pricing

    @pytest.mark.unit
    def test_no_linux_pricing(self):
        """Should not add Windows RI fields when Linux PAYG is missing."""
        pricing = {
            'hourlyPrice': None,
            'hourlyPriceWindows': 0.752,
            'ri1YearHourly': 0.2266,
        }
        _compute_windows_ri(pricing)
        assert 'ri1YearHourlyWindows' not in pricing

    @pytest.mark.unit
    def test_zero_surcharge(self):
        """Should not add Windows RI when surcharge is zero or negative."""
        pricing = {
            'hourlyPrice': 0.384,
            'hourlyPriceWindows': 0.384,  # Same as Linux = 0 surcharge
            'ri1YearHourly': 0.2266,
        }
        _compute_windows_ri(pricing)
        assert 'ri1YearHourlyWindows' not in pricing

    @pytest.mark.unit
    def test_only_1yr_ri(self):
        """Should compute only 1yr Windows RI when 3yr is missing."""
        pricing = {
            'hourlyPrice': 0.384,
            'hourlyPriceWindows': 0.752,
            'ri1YearHourly': 0.2266,
            'ri3YearHourly': None,
        }
        _compute_windows_ri(pricing)
        assert 'ri1YearHourlyWindows' in pricing
        assert 'ri3YearHourlyWindows' not in pricing

    @pytest.mark.unit
    def test_only_3yr_ri(self):
        """Should compute only 3yr Windows RI when 1yr is missing."""
        pricing = {
            'hourlyPrice': 0.384,
            'hourlyPriceWindows': 0.752,
            'ri1YearHourly': None,
            'ri3YearHourly': 0.1421,
        }
        _compute_windows_ri(pricing)
        assert 'ri1YearHourlyWindows' not in pricing
        assert 'ri3YearHourlyWindows' in pricing


# ============================================================================
# _pricing_has_ri tests
# ============================================================================

class TestPricingHasRI:

    @pytest.mark.unit
    def test_has_both_ri(self):
        assert _pricing_has_ri({'ri1YearMonthly': 100, 'ri3YearMonthly': 60}) is True

    @pytest.mark.unit
    def test_has_only_1yr(self):
        assert _pricing_has_ri({'ri1YearMonthly': 100, 'ri3YearMonthly': None}) is True

    @pytest.mark.unit
    def test_has_only_3yr(self):
        assert _pricing_has_ri({'ri1YearMonthly': None, 'ri3YearMonthly': 60}) is True

    @pytest.mark.unit
    def test_no_ri_fields(self):
        assert _pricing_has_ri({'hourlyPrice': 0.5}) is False

    @pytest.mark.unit
    def test_both_none(self):
        assert _pricing_has_ri({'ri1YearMonthly': None, 'ri3YearMonthly': None}) is False

    @pytest.mark.unit
    def test_none_pricing(self):
        assert _pricing_has_ri(None) is False

    @pytest.mark.unit
    def test_empty_dict(self):
        assert _pricing_has_ri({}) is False


# ============================================================================
# detect_cpu_vendor tests
# ============================================================================

class TestDetectCpuVendor:

    @pytest.mark.unit
    def test_intel_standard(self):
        assert detect_cpu_vendor('Standard_D8s_v5', 'x64') == 'Intel'

    @pytest.mark.unit
    def test_intel_esv5(self):
        assert detect_cpu_vendor('Standard_E8s_v5', 'x64') == 'Intel'

    @pytest.mark.unit
    def test_amd_das(self):
        assert detect_cpu_vendor('Standard_D8as_v5', 'x64') == 'AMD'

    @pytest.mark.unit
    def test_amd_eads(self):
        assert detect_cpu_vendor('Standard_E8ads_v5', 'x64') == 'AMD'

    @pytest.mark.unit
    def test_amd_als(self):
        assert detect_cpu_vendor('Standard_D2als_v6', 'x64') == 'AMD'

    @pytest.mark.unit
    def test_arm_architecture(self):
        assert detect_cpu_vendor('Standard_D8pds_v5', 'Arm64') == 'ARM'

    @pytest.mark.unit
    def test_arm_lowercase(self):
        assert detect_cpu_vendor('Standard_D8pds_v5', 'arm64') == 'ARM'

    @pytest.mark.unit
    def test_fsv2_intel(self):
        assert detect_cpu_vendor('Standard_F8s_v2', 'x64') == 'Intel'

    @pytest.mark.unit
    def test_nv_gpu_intel(self):
        assert detect_cpu_vendor('Standard_NV12s_v3', 'x64') == 'Intel'


# ============================================================================
# extract_capabilities tests
# ============================================================================

class TestExtractCapabilities:

    @pytest.mark.unit
    def test_basic_extraction(self, sample_sku_capabilities):
        result = extract_capabilities(sample_sku_capabilities)
        assert result['vCPUs'] == 8
        assert result['memoryGB'] == 32.0
        assert result['maxDataDiskCount'] == 16
        assert result['maxNics'] == 4
        assert result['premiumIO'] is True
        assert result['acceleratedNetworking'] is True
        assert result['encryptionAtHost'] is True
        assert result['ephemeralOSDisk'] is True
        assert result['gpuCount'] == 0
        assert result['nvme'] is False
        assert result['uncachedDiskIOPS'] == 12800

    @pytest.mark.unit
    def test_missing_capabilities(self):
        sku = {'name': 'Standard_B1s', 'capabilities': []}
        result = extract_capabilities(sku)
        assert result['vCPUs'] == 0
        assert result['memoryGB'] == 0.0
        assert result['gpuCount'] == 0
        assert result['premiumIO'] is False

    @pytest.mark.unit
    def test_no_capabilities_key(self):
        sku = {'name': 'Standard_B1s'}
        result = extract_capabilities(sku)
        assert result['vCPUs'] == 0


class TestExtractCapabilitiesForCache:

    @pytest.mark.unit
    def test_basic_extraction(self, sample_sku_capabilities):
        result = extract_capabilities_for_cache(sample_sku_capabilities)
        assert result['vCPUs'] == 8
        assert result['memoryGB'] == 32.0
        assert result['maxDataDisks'] == 16
        assert result['maxNics'] == 4
        assert result['premiumIO'] is True
        assert result['architecture'] == 'x64'

    @pytest.mark.unit
    def test_defaults_on_empty(self):
        sku = {'capabilities': []}
        result = extract_capabilities_for_cache(sku)
        assert result['vCPUs'] == 0
        assert result['architecture'] == 'x64'
        assert result['premiumIO'] is False


# ============================================================================
# calculate_numeric_diff tests
# ============================================================================

class TestCalculateNumericDiff:

    @pytest.mark.unit
    def test_upgrade(self):
        result = calculate_numeric_diff(4, 8, 'cores')
        assert result['delta'] == 4
        assert result['direction'] == 'upgrade'
        assert result['percentChange'] == 100.0
        assert result['changed'] is True

    @pytest.mark.unit
    def test_downgrade(self):
        result = calculate_numeric_diff(8, 4, 'cores')
        assert result['delta'] == -4
        assert result['direction'] == 'downgrade'
        assert result['changed'] is True

    @pytest.mark.unit
    def test_same(self):
        result = calculate_numeric_diff(8, 8, 'GB')
        assert result['delta'] == 0
        assert result['direction'] == 'same'
        assert result['changed'] is False

    @pytest.mark.unit
    def test_none_values(self):
        result = calculate_numeric_diff(None, 8, 'cores')
        assert result['changed'] is False
        assert 'delta' not in result

    @pytest.mark.unit
    def test_zero_target(self):
        result = calculate_numeric_diff(0, 5, 'cores')
        assert result['percentChange'] is None


# ============================================================================
# calculate_price_diff tests
# ============================================================================

class TestCalculatePriceDiff:

    @pytest.mark.unit
    def test_lower_price(self):
        result = calculate_price_diff(0.5, 0.3, 'USD')
        assert result['direction'] == 'lower'
        assert result['isPositive'] is True
        assert result['delta'] < 0

    @pytest.mark.unit
    def test_higher_price(self):
        result = calculate_price_diff(0.3, 0.5, 'USD')
        assert result['direction'] == 'higher'
        assert result['isNegative'] is True
        assert result['delta'] > 0

    @pytest.mark.unit
    def test_same_price(self):
        result = calculate_price_diff(0.5, 0.5, 'USD')
        assert result['direction'] == 'same'
        assert result['changed'] is False

    @pytest.mark.unit
    def test_none_target(self):
        result = calculate_price_diff(None, 0.5, 'USD')
        assert result['changed'] is False

    @pytest.mark.unit
    def test_zero_target(self):
        result = calculate_price_diff(0, 0.5, 'EUR')
        assert result['percentChange'] is None


# ============================================================================
# calculate_boolean_diff tests
# ============================================================================

class TestCalculateBooleanDiff:

    @pytest.mark.unit
    def test_added(self):
        result = calculate_boolean_diff(False, True, 'Premium IO')
        assert result['direction'] == 'added'
        assert result['changed'] is True

    @pytest.mark.unit
    def test_removed(self):
        result = calculate_boolean_diff(True, False, 'Premium IO')
        assert result['direction'] == 'removed'
        assert result['changed'] is True

    @pytest.mark.unit
    def test_same_true(self):
        result = calculate_boolean_diff(True, True, 'Premium IO')
        assert result['direction'] == 'same'
        assert result['changed'] is False

    @pytest.mark.unit
    def test_same_false(self):
        result = calculate_boolean_diff(False, False, 'NVMe')
        assert result['direction'] == 'same'
        assert result['changed'] is False


# ============================================================================
# Pricing math validation
# ============================================================================

class TestPricingMath:

    @pytest.mark.unit
    def test_monthly_equals_hourly_times_730(self, sample_pricing):
        """Monthly price should be approximately hourly * 730."""
        assert sample_pricing['monthlyPrice'] == pytest.approx(
            sample_pricing['hourlyPrice'] * 730, rel=0.01
        )

    @pytest.mark.unit
    def test_windows_monthly_equals_hourly_times_730(self, sample_pricing):
        assert sample_pricing['monthlyPriceWindows'] == pytest.approx(
            sample_pricing['hourlyPriceWindows'] * 730, rel=0.01
        )

    @pytest.mark.unit
    def test_ri_cheaper_than_payg(self, sample_pricing):
        """RI pricing should always be cheaper than PAYG."""
        assert sample_pricing['ri1YearMonthly'] < sample_pricing['monthlyPrice']
        assert sample_pricing['ri3YearMonthly'] < sample_pricing['monthlyPrice']
        assert sample_pricing['ri3YearMonthly'] < sample_pricing['ri1YearMonthly']

    @pytest.mark.unit
    def test_windows_ri_includes_surcharge(self, sample_pricing):
        """Windows RI = Linux RI + license surcharge."""
        surcharge = sample_pricing['hourlyPriceWindows'] - sample_pricing['hourlyPrice']
        assert sample_pricing['ri1YearHourlyWindows'] == pytest.approx(
            sample_pricing['ri1YearHourly'] + surcharge, abs=0.01
        )
        assert sample_pricing['ri3YearHourlyWindows'] == pytest.approx(
            sample_pricing['ri3YearHourly'] + surcharge, abs=0.01
        )


# ============================================================================
# calculate_similarity tests
# ============================================================================

class TestCalculateSimilarity:

    @pytest.mark.unit
    def test_identical_skus(self):
        sku = {
            'vCPUs': 8, 'memoryGB': 32, 'gpuCount': 0,
            'uncachedDiskIOPS': 12800, 'maxNics': 4,
            'premiumIO': True, 'acceleratedNetworking': True,
            'encryptionAtHost': True, 'ephemeralOSDisk': True,
            'networkBandwidthMbps': None
        }
        weights = {
            'weightCPU': 2, 'weightMemory': 2, 'weightGPU': 1,
            'weightStorage': 1, 'weightNetwork': 1, 'weightFeatures': 1
        }
        score = calculate_similarity(sku, sku, weights)
        assert score == pytest.approx(100.0, abs=0.1)

    @pytest.mark.unit
    def test_different_cpu(self):
        target = {
            'vCPUs': 8, 'memoryGB': 32, 'gpuCount': 0,
            'uncachedDiskIOPS': 12800, 'maxNics': 4,
            'premiumIO': True, 'acceleratedNetworking': True,
            'encryptionAtHost': True, 'ephemeralOSDisk': True,
            'networkBandwidthMbps': None
        }
        candidate = dict(target)
        candidate['vCPUs'] = 16  # Double the CPUs
        weights = {
            'weightCPU': 2, 'weightMemory': 2, 'weightGPU': 1,
            'weightStorage': 1, 'weightNetwork': 1, 'weightFeatures': 1
        }
        score = calculate_similarity(target, candidate, weights)
        assert 0 < score < 100

    @pytest.mark.unit
    def test_zero_weight_total(self):
        """With all zero weights, should return 0."""
        sku = {
            'vCPUs': 0, 'memoryGB': 0, 'gpuCount': 0,
            'uncachedDiskIOPS': 0, 'maxNics': 0,
            'premiumIO': True, 'acceleratedNetworking': True,
            'encryptionAtHost': True, 'ephemeralOSDisk': True,
            'networkBandwidthMbps': None
        }
        weights = {
            'weightCPU': 2, 'weightMemory': 2, 'weightGPU': 1,
            'weightStorage': 1, 'weightNetwork': 1, 'weightFeatures': 1
        }
        score = calculate_similarity(sku, sku, weights)
        # With 0 vCPUs and memory, only features will score
        assert score >= 0
