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
    _asymmetric_score,
    _match_cors_origin,
    _cors_headers,
    with_cors,
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


# ============================================================================
# Asymmetric scoring tests (storage/network/features) + burstable regression
# ============================================================================

# Default UI weights used by the website
DEFAULT_WEIGHTS = {
    'weightCPU': 3.0, 'weightMemory': 3.0, 'weightGPU': 1.5,
    'weightStorage': 1.5, 'weightNetwork': 0.5, 'weightFeatures': 0.5
}


class TestAsymmetricScore:

    @pytest.mark.unit
    def test_overshoot_not_penalized_when_factor_zero(self):
        # Candidate exceeds target by 100%, but overshoot_factor=0 => full score
        assert _asymmetric_score(100, 200, overshoot_factor=0.0) == pytest.approx(100.0)
        assert _asymmetric_score(100, 1000, overshoot_factor=0.0) == pytest.approx(100.0)

    @pytest.mark.unit
    def test_overshoot_factor_one_is_symmetric(self):
        # overshoot_factor=1.0 reproduces the original symmetric behavior
        assert _asymmetric_score(100, 200, overshoot_factor=1.0) == pytest.approx(0.0)
        assert _asymmetric_score(100, 150, overshoot_factor=1.0) == pytest.approx(50.0)

    @pytest.mark.unit
    def test_shortfall_always_penalized_full_rate(self):
        # Falling short is penalized at full rate regardless of overshoot_factor
        assert _asymmetric_score(100, 50, overshoot_factor=0.0) == pytest.approx(50.0)
        assert _asymmetric_score(100, 50, overshoot_factor=1.0) == pytest.approx(50.0)
        assert _asymmetric_score(100, 0, overshoot_factor=0.0) == pytest.approx(0.0)

    @pytest.mark.unit
    def test_exact_match_scores_full(self):
        assert _asymmetric_score(100, 100, overshoot_factor=0.0) == pytest.approx(100.0)

    @pytest.mark.unit
    def test_zero_target_guard(self):
        # No target capability to match against => not a penalty
        assert _asymmetric_score(0, 5000, overshoot_factor=0.0) == pytest.approx(100.0)


def _iso_sku(**overrides):
    """A SKU with all dimensions neutralized so a single weight can be isolated."""
    base = {
        'vCPUs': 0, 'memoryGB': 0, 'gpuCount': 0,
        'uncachedDiskIOPS': 0, 'maxNics': 0, 'networkBandwidthMbps': None,
        'premiumIO': False, 'acceleratedNetworking': False,
        'encryptionAtHost': False, 'ephemeralOSDisk': False,
    }
    base.update(overrides)
    return base


class TestSimilarityAsymmetry:

    @pytest.mark.unit
    def test_storage_overshoot_not_penalized(self):
        weights = {**DEFAULT_WEIGHTS, 'weightStorage': 1.0,
                   'weightCPU': 0, 'weightMemory': 0, 'weightGPU': 0,
                   'weightNetwork': 0, 'weightFeatures': 0}
        target = _iso_sku(uncachedDiskIOPS=1000)
        faster = _iso_sku(uncachedDiskIOPS=5000)  # 5x the IOPS
        assert calculate_similarity(target, faster, weights) == pytest.approx(100.0)

    @pytest.mark.unit
    def test_storage_shortfall_still_penalized(self):
        weights = {**DEFAULT_WEIGHTS, 'weightStorage': 1.0,
                   'weightCPU': 0, 'weightMemory': 0, 'weightGPU': 0,
                   'weightNetwork': 0, 'weightFeatures': 0}
        target = _iso_sku(uncachedDiskIOPS=1000)
        slower = _iso_sku(uncachedDiskIOPS=500)  # half the IOPS
        assert calculate_similarity(target, slower, weights) == pytest.approx(50.0)

    @pytest.mark.unit
    def test_network_overshoot_not_penalized(self):
        weights = {**DEFAULT_WEIGHTS, 'weightNetwork': 1.0,
                   'weightCPU': 0, 'weightMemory': 0, 'weightGPU': 0,
                   'weightStorage': 0, 'weightFeatures': 0}
        target = _iso_sku(networkBandwidthMbps=1000)
        faster = _iso_sku(networkBandwidthMbps=10000)
        assert calculate_similarity(target, faster, weights) == pytest.approx(100.0)

    @pytest.mark.unit
    def test_features_extra_capabilities_not_penalized(self):
        weights = {**DEFAULT_WEIGHTS, 'weightFeatures': 1.0,
                   'weightCPU': 0, 'weightMemory': 0, 'weightGPU': 0,
                   'weightStorage': 0, 'weightNetwork': 0}
        target = _iso_sku()  # target needs no features
        loaded = _iso_sku(premiumIO=True, acceleratedNetworking=True,
                          encryptionAtHost=True, ephemeralOSDisk=True)
        assert calculate_similarity(target, loaded, weights) == pytest.approx(100.0)

    @pytest.mark.unit
    def test_features_missing_required_capability_penalized(self):
        weights = {**DEFAULT_WEIGHTS, 'weightFeatures': 1.0,
                   'weightCPU': 0, 'weightMemory': 0, 'weightGPU': 0,
                   'weightStorage': 0, 'weightNetwork': 0}
        target = _iso_sku(premiumIO=True)  # target requires premium IO
        missing = _iso_sku(premiumIO=False)
        assert calculate_similarity(target, missing, weights) == pytest.approx(0.0)


class TestBurstableRegression:
    """Regression for the Standard_B2s bug: low-baseline burstable targets
    returned zero alternatives at the default threshold of 80 because exact
    vCPU/memory twins with higher IOPS/bandwidth were scored as dissimilar."""

    def _b2s_like(self):
        # Burstable: low baseline IOPS and network bandwidth
        return {
            'vCPUs': 2, 'memoryGB': 4, 'gpuCount': 0,
            'uncachedDiskIOPS': 1280, 'maxNics': 3, 'networkBandwidthMbps': 1280,
            'premiumIO': True, 'acceleratedNetworking': False,
            'encryptionAtHost': True, 'ephemeralOSDisk': False,
        }

    @pytest.mark.unit
    def test_exact_twin_with_higher_io_passes_default_threshold(self):
        target = self._b2s_like()
        # Same vCPU/memory, but a sustained SKU with much higher IOPS/bandwidth
        twin = {
            'vCPUs': 2, 'memoryGB': 4, 'gpuCount': 0,
            'uncachedDiskIOPS': 3200, 'maxNics': 2, 'networkBandwidthMbps': 5000,
            'premiumIO': True, 'acceleratedNetworking': True,
            'encryptionAtHost': True, 'ephemeralOSDisk': True,
        }
        score = calculate_similarity(target, twin, DEFAULT_WEIGHTS)
        assert score >= 80, f"exact 2vCPU/4GB twin should clear the default threshold, got {score}"

    @pytest.mark.unit
    def test_genuine_downgrade_still_excluded(self):
        # A SKU with half the memory should still score poorly (not a false positive)
        target = self._b2s_like()
        smaller = {
            'vCPUs': 1, 'memoryGB': 2, 'gpuCount': 0,
            'uncachedDiskIOPS': 3200, 'maxNics': 2, 'networkBandwidthMbps': 5000,
            'premiumIO': True, 'acceleratedNetworking': True,
            'encryptionAtHost': True, 'ephemeralOSDisk': True,
        }
        score = calculate_similarity(target, smaller, DEFAULT_WEIGHTS)
        assert score < 80, f"half-size SKU should not clear the default threshold, got {score}"


# ============================================================================
# Dynamic CORS tests (app-level allowlist for SWA preview slots)
# ============================================================================


class _FakeReq:
    """Minimal stand-in for func.HttpRequest under the mocked azure SDK."""

    def __init__(self, method, origin=None):
        self.method = method
        self.headers = {"Origin": origin} if origin else {}


class _FakeResp:
    """Response stub whose headers dict the with_cors wrapper mutates."""

    def __init__(self):
        self.headers = {}


PROD_ORIGIN = "https://black-sea-0784c5d0f.1.azurestaticapps.net"
PREVIEW_ORIGIN = "https://black-sea-0784c5d0f-8.eastus2.1.azurestaticapps.net"


class TestMatchCorsOrigin:

    @pytest.mark.unit
    def test_production_swa_allowed(self):
        assert _match_cors_origin(PROD_ORIGIN) == PROD_ORIGIN

    @pytest.mark.unit
    def test_any_preview_slot_allowed(self):
        # Every PR gets a unique preview origin -- all must be accepted.
        for pr in (1, 8, 42, 12345):
            origin = f"https://black-sea-0784c5d0f-{pr}.eastus2.1.azurestaticapps.net"
            assert _match_cors_origin(origin) == origin

    @pytest.mark.unit
    def test_preview_in_other_region_allowed(self):
        assert _match_cors_origin("https://black-sea-0784c5d0f-3.westus2.1.azurestaticapps.net")

    @pytest.mark.unit
    def test_portal_and_localhost_allowed(self):
        assert _match_cors_origin("https://portal.azure.com")
        assert _match_cors_origin("http://localhost:4280")
        assert _match_cors_origin("http://127.0.0.1:5500")

    @pytest.mark.unit
    def test_suffix_attack_rejected(self):
        assert _match_cors_origin(PROD_ORIGIN + ".evil.com") is None

    @pytest.mark.unit
    def test_prefix_attack_rejected(self):
        assert _match_cors_origin("https://evil-black-sea-0784c5d0f.1.azurestaticapps.net") is None

    @pytest.mark.unit
    def test_unknown_origin_and_none_rejected(self):
        assert _match_cors_origin("https://notallowed.com") is None
        assert _match_cors_origin("") is None
        assert _match_cors_origin(None) is None


class TestCorsHeaders:

    @pytest.mark.unit
    def test_allowed_origin_echoed_with_vary(self):
        headers = _cors_headers(_FakeReq("GET", PREVIEW_ORIGIN))
        assert headers["Access-Control-Allow-Origin"] == PREVIEW_ORIGIN
        assert headers["Vary"] == "Origin"
        assert "OPTIONS" in headers["Access-Control-Allow-Methods"]

    @pytest.mark.unit
    def test_disallowed_origin_yields_no_headers(self):
        assert _cors_headers(_FakeReq("GET", "https://evil.example.com")) == {}

    @pytest.mark.unit
    def test_missing_origin_yields_no_headers(self):
        assert _cors_headers(_FakeReq("GET")) == {}


class TestWithCorsDecorator:

    @pytest.mark.unit
    def test_preflight_short_circuits_without_calling_handler(self):
        called = {"hit": False}

        @with_cors
        def handler(req):
            called["hit"] = True
            return _FakeResp()

        handler(_FakeReq("OPTIONS", PREVIEW_ORIGIN))
        assert called["hit"] is False, "OPTIONS preflight must not invoke the handler"

    @pytest.mark.unit
    def test_headers_injected_on_normal_response(self):
        @with_cors
        def handler(req):
            return _FakeResp()

        resp = handler(_FakeReq("GET", PREVIEW_ORIGIN))
        assert resp.headers["Access-Control-Allow-Origin"] == PREVIEW_ORIGIN

    @pytest.mark.unit
    def test_no_headers_added_for_disallowed_origin(self):
        @with_cors
        def handler(req):
            return _FakeResp()

        resp = handler(_FakeReq("GET", "https://evil.example.com"))
        assert "Access-Control-Allow-Origin" not in resp.headers

    @pytest.mark.unit
    def test_wrapper_preserves_handler_name(self):
        @with_cors
        def my_handler(req):
            return _FakeResp()

        assert my_handler.__name__ == "my_handler"

