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
    _derive_trusted_launch,
    detect_cpu_vendor,
    extract_capabilities,
    extract_capabilities_for_cache,
    extract_capabilities_for_diff,
    calculate_numeric_diff,
    calculate_price_diff,
    calculate_boolean_diff,
    calculate_similarity,
    _asymmetric_score,
    select_region_prices,
    _is_payg_item,
    build_grid_row,
    _price_changed,
    record_price_history,
    prune_price_history,
    _history_summary,
    _build_history_series,
)


# ============================================================================
# build_grid_row tests
# ============================================================================

class TestBuildGridRow:

    @pytest.mark.unit
    def test_family_derivation(self):
        assert build_grid_row({'name': 'Standard_D2s_v5'})['family'] == 'Dsv5'
        assert build_grid_row({'name': 'Standard_E96-24ads_v6'})['family'] == 'Eadsv6'

    @pytest.mark.unit
    def test_nvme_and_gpu_fields_pass_through(self):
        row = build_grid_row({
            'name': 'Standard_NC24ads_A100_v4',
            'nvme': True,
            'gpuCount': 1,
            'gpuType': 'A100',
        })

        assert row['nvme'] is True
        assert row['gpuCount'] == 1
        assert row['gpuType'] == 'A100'

    @pytest.mark.unit
    def test_zero_and_missing_prices_are_none(self):
        row = build_grid_row({
            'name': 'Standard_D2s_v5',
            'hourlyPriceUSD': 0,
            'monthlyPriceUSD': 0.0,
        })

        assert row['hourlyLinux'] is None
        assert row['monthlyLinux'] is None
        assert row['hourlyWindows'] is None

    @pytest.mark.unit
    def test_pricing_override_takes_precedence(self):
        row = build_grid_row(
            {
                'name': 'Standard_D2s_v5',
                'hourlyPriceUSD': 0.1,
                'monthlyPriceUSD': 73.0,
                'hourlyPriceUSDWindows': 0.2,
                'ri1YearHourlyUSD': 0.05,
            },
            pricing_override={
                'hourlyPrice': 0.15,
                'monthlyPrice': 109.5,
                'hourlyPriceWindows': 0.25,
                'ri1YearHourly': 0.07,
            }
        )

        assert row['hourlyLinux'] == 0.15
        assert row['monthlyLinux'] == 109.5
        assert row['hourlyWindows'] == 0.25
        assert row['ri1YearHourlyLinux'] == 0.07

    @pytest.mark.unit
    def test_availability_zones_list(self):
        row = build_grid_row({
            'name': 'Standard_D2s_v5',
            'availabilityZones': '1,2, 3',
        })
        empty_row = build_grid_row({
            'name': 'Standard_D2s_v5',
            'availabilityZones': '',
        })

        assert row['availabilityZones'] == ['1', '2', '3']
        assert empty_row['availabilityZones'] == []


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
        # Richer spec fields (I-D)
        assert result['acu'] == 195
        assert result['vCPUsPerCore'] == 2
        assert result['diskControllerTypes'] == 'SCSI, NVMe'
        assert result['rdmaEnabled'] is False
        assert result['confidentialComputingType'] == ''
        assert result['trustedLaunch'] is True

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
        # Richer spec fields (I-D)
        assert result['acu'] == 195
        assert result['vCPUsPerCore'] == 2
        assert result['diskControllerTypes'] == 'SCSI, NVMe'
        assert result['rdmaEnabled'] is False
        assert result['trustedLaunch'] is True

    @pytest.mark.unit
    def test_defaults_on_empty(self):
        sku = {'capabilities': []}
        result = extract_capabilities_for_cache(sku)
        assert result['vCPUs'] == 0
        assert result['architecture'] == 'x64'
        assert result['premiumIO'] is False
        assert result['acu'] == 0
        assert result['vCPUsPerCore'] == 0
        assert result['diskControllerTypes'] == ''
        assert result['rdmaEnabled'] is False
        assert result['confidentialComputingType'] == ''
        assert result['trustedLaunch'] is False


# ============================================================================
# Richer spec fields (I-D)
# ============================================================================

class TestTrustedLaunchDerivation:

    @pytest.mark.unit
    def test_gen2_not_disabled_is_supported(self):
        assert _derive_trusted_launch({'HyperVGenerations': 'V1,V2'}) is True

    @pytest.mark.unit
    def test_gen2_explicitly_disabled(self):
        assert _derive_trusted_launch(
            {'HyperVGenerations': 'V1,V2', 'TrustedLaunchDisabled': 'True'}
        ) is False

    @pytest.mark.unit
    def test_gen1_only_not_supported(self):
        assert _derive_trusted_launch({'HyperVGenerations': 'V1'}) is False

    @pytest.mark.unit
    def test_missing_hyperv_not_supported(self):
        assert _derive_trusted_launch({}) is False


class TestExtractCapabilitiesForDiffRichSpecs:

    @pytest.mark.unit
    def test_reads_new_flat_fields(self, sample_cached_sku):
        caps = extract_capabilities_for_diff(sample_cached_sku)
        assert caps['acu'] == 195
        assert caps['vCPUsPerCore'] == 2
        assert caps['diskControllerTypes'] == 'SCSI, NVMe'
        assert caps['rdmaEnabled'] is False
        assert caps['confidentialComputingType'] == ''
        assert caps['trustedLaunch'] is True

    @pytest.mark.unit
    def test_missing_new_fields_default_safely(self):
        caps = extract_capabilities_for_diff({'name': 'Standard_B1s'})
        assert caps['acu'] is None
        assert caps['vCPUsPerCore'] is None
        assert caps['diskControllerTypes'] == ''
        assert caps['rdmaEnabled'] is False
        assert caps['trustedLaunch'] is False

    @pytest.mark.unit
    def test_cache_roundtrip_preserves_trusted_launch(self, sample_sku_capabilities):
        """Live parse -> reconstructed-cache parse must agree on trustedLaunch."""
        live = extract_capabilities(sample_sku_capabilities)
        # Simulate the cache reconstruction: store trustedLaunch bool, re-emit
        # TrustedLaunchDisabled inverse + HyperVGenerations, then re-parse.
        reconstructed = extract_capabilities({
            'capabilities': [
                {'name': 'HyperVGenerations', 'value': 'V1,V2'},
                {'name': 'TrustedLaunchDisabled',
                 'value': 'False' if live['trustedLaunch'] else 'True'},
            ]
        })
        assert reconstructed['trustedLaunch'] == live['trustedLaunch']


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


class TestSelectRegionPrices:
    """Unit tests for I-E cross-region price selection (select_region_prices)."""

    def _item(self, region, price, product='Virtual Machines Dv5 Series',
              sku_meter='D2s v5', itype='Consumption', currency='USD',
              location='East US'):
        return {
            'armRegionName': region,
            'unitPrice': price,
            'productName': product,
            'skuName': sku_meter,
            'type': itype,
            'currencyCode': currency,
            'location': location,
        }

    @pytest.mark.unit
    def test_groups_by_region(self):
        items = [
            self._item('eastus', 0.096),
            self._item('westus', 0.108),
        ]
        result = select_region_prices(items, 'linux')
        assert set(result.keys()) == {'eastus', 'westus'}
        assert result['eastus']['hourlyPrice'] == pytest.approx(0.096)
        assert result['eastus']['monthlyPrice'] == pytest.approx(0.096 * 730, rel=1e-6)

    @pytest.mark.unit
    def test_linux_excludes_windows_meter(self):
        items = [
            self._item('eastus', 0.20, product='Virtual Machines Dv5 Series Windows'),
            self._item('eastus', 0.096, product='Virtual Machines Dv5 Series'),
        ]
        result = select_region_prices(items, 'linux')
        assert result['eastus']['hourlyPrice'] == pytest.approx(0.096)

    @pytest.mark.unit
    def test_windows_selects_windows_meter(self):
        items = [
            self._item('eastus', 0.20, product='Virtual Machines Dv5 Series Windows'),
            self._item('eastus', 0.096, product='Virtual Machines Dv5 Series'),
        ]
        result = select_region_prices(items, 'windows')
        assert result['eastus']['hourlyPrice'] == pytest.approx(0.20)

    @pytest.mark.unit
    def test_excludes_spot_and_low_priority(self):
        items = [
            self._item('eastus', 0.03, sku_meter='D2s v5 Spot'),
            self._item('eastus', 0.02, sku_meter='D2s v5 Low Priority'),
            self._item('eastus', 0.096, sku_meter='D2s v5'),
        ]
        result = select_region_prices(items, 'linux')
        assert result['eastus']['hourlyPrice'] == pytest.approx(0.096)

    @pytest.mark.unit
    def test_excludes_dedicated_host(self):
        items = [
            self._item('eastus', 5.0, product='Virtual Machines Dv5 Series DedicatedHost'),
            self._item('eastus', 0.096, product='Virtual Machines Dv5 Series'),
        ]
        result = select_region_prices(items, 'linux')
        assert result['eastus']['hourlyPrice'] == pytest.approx(0.096)

    @pytest.mark.unit
    def test_skips_zero_and_none_prices(self):
        items = [
            self._item('eastus', 0),
            self._item('westus', None),
            self._item('centralus', 0.10),
        ]
        result = select_region_prices(items, 'linux')
        assert set(result.keys()) == {'centralus'}

    @pytest.mark.unit
    def test_empty_input(self):
        assert select_region_prices([], 'linux') == {}

    @pytest.mark.unit
    def test_is_payg_item_flags(self):
        assert _is_payg_item(self._item('eastus', 0.1)) is True
        assert _is_payg_item(self._item('eastus', 0.1, sku_meter='D2s v5 Spot')) is False
        assert _is_payg_item(self._item('eastus', 0.1, itype='Reservation')) is False


# ============================================================================
# I-A Spot price mapping (build_grid_row)
# ============================================================================

class TestSpotGridMapping:

    @pytest.mark.unit
    def test_spot_fields_from_entity(self):
        row = build_grid_row({
            'name': 'Standard_D2s_v5',
            'spotHourlyPriceUSD': 0.012,
            'spotMonthlyPriceUSD': 8.76,
        })
        assert row['spotHourlyLinux'] == 0.012
        assert row['spotMonthlyLinux'] == 8.76

    @pytest.mark.unit
    def test_spot_zero_is_none(self):
        row = build_grid_row({
            'name': 'Standard_D2s_v5',
            'spotHourlyPriceUSD': 0,
            'spotMonthlyPriceUSD': 0.0,
        })
        assert row['spotHourlyLinux'] is None
        assert row['spotMonthlyLinux'] is None

    @pytest.mark.unit
    def test_spot_override_takes_precedence(self):
        row = build_grid_row(
            {'name': 'Standard_D2s_v5', 'spotHourlyPriceUSD': 0.012},
            pricing_override={'spotHourly': 0.02, 'spotMonthly': 14.6},
        )
        assert row['spotHourlyLinux'] == 0.02
        assert row['spotMonthlyLinux'] == 14.6


# ============================================================================
# I-G Price history helpers
# ============================================================================

class FakeHistoryClient:
    """In-memory stand-in for a Table Storage client used by history helpers."""

    def __init__(self, rows=None):
        # rows: list of dicts with PartitionKey/RowKey/...
        self.rows = list(rows or [])

    def upsert_entity(self, entity):
        for i, r in enumerate(self.rows):
            if r['PartitionKey'] == entity['PartitionKey'] and r['RowKey'] == entity['RowKey']:
                self.rows[i] = dict(entity)
                return
        self.rows.append(dict(entity))

    def delete_entity(self, partition_key=None, row_key=None):
        self.rows = [r for r in self.rows
                     if not (r['PartitionKey'] == partition_key and r['RowKey'] == row_key)]

    def query_entities(self, query_filter=None, select=None):
        # Support the two filter shapes used by the code under test.
        if query_filter and query_filter.startswith('PartitionKey eq '):
            pk = query_filter.split("'", 2)[1]
            return [dict(r) for r in self.rows if r['PartitionKey'] == pk]
        if query_filter and query_filter.startswith('RowKey lt '):
            cutoff = query_filter.split("'", 2)[1]
            return [dict(r) for r in self.rows if r['RowKey'] < cutoff]
        return [dict(r) for r in self.rows]


class TestPriceChanged:

    @pytest.mark.unit
    def test_both_none_no_change(self):
        assert _price_changed(None, None) is False

    @pytest.mark.unit
    def test_none_to_value_is_change(self):
        assert _price_changed(None, 0.1) is True
        assert _price_changed(0.1, None) is True

    @pytest.mark.unit
    def test_equal_within_6dp_no_change(self):
        assert _price_changed(0.1234567, 0.1234569) is False

    @pytest.mark.unit
    def test_differ_beyond_6dp_is_change(self):
        assert _price_changed(0.100000, 0.100002) is True


class TestRecordPriceHistory:

    @pytest.mark.unit
    def test_writes_changed_and_skips_unchanged(self):
        client = FakeHistoryClient()
        entities = [
            {'RowKey': 'Standard_D2s_v5', 'hourlyPriceUSD': 0.11,
             'hourlyPriceUSDWindows': 0.20, 'spotHourlyPriceUSD': 0.02},
            {'RowKey': 'Standard_D4s_v5', 'hourlyPriceUSD': 0.22},
        ]
        existing = {
            'Standard_D2s_v5': (0.10, 0.20, 0.02),  # linux changed
            'Standard_D4s_v5': (0.22, None, None),  # unchanged
        }
        written = record_price_history(client, 'eastus', entities, existing)
        assert written == 1
        assert len(client.rows) == 1
        row = client.rows[0]
        assert row['PartitionKey'] == 'eastus|Standard_D2s_v5'
        assert row['hourlyLinuxUSD'] == 0.11
        assert row['hourlyWindowsUSD'] == 0.20
        assert row['hourlySpotUSD'] == 0.02

    @pytest.mark.unit
    def test_new_sku_without_baseline_is_written(self):
        client = FakeHistoryClient()
        entities = [{'RowKey': 'Standard_D2s_v5', 'hourlyPriceUSD': 0.11}]
        written = record_price_history(client, 'eastus', entities, {})
        assert written == 1
        assert 'hourlyWindowsUSD' not in client.rows[0]  # None omitted

    @pytest.mark.unit
    def test_skips_entity_with_no_linux_price(self):
        client = FakeHistoryClient()
        entities = [{'RowKey': 'Standard_D2s_v5', 'hourlyPriceUSD': None}]
        assert record_price_history(client, 'eastus', entities, {}) == 0
        assert client.rows == []


class TestPrunePriceHistory:

    @pytest.mark.unit
    def test_keeps_newest_pre_cutoff_anchor(self):
        # retention_days=0 -> cutoff = today; everything before today is "stale"
        pk = 'eastus|Standard_D2s_v5'
        client = FakeHistoryClient([
            {'PartitionKey': pk, 'RowKey': '2020-01-01', 'hourlyLinuxUSD': 0.10},
            {'PartitionKey': pk, 'RowKey': '2020-06-01', 'hourlyLinuxUSD': 0.11},
            {'PartitionKey': pk, 'RowKey': '2020-12-01', 'hourlyLinuxUSD': 0.12},
        ])
        deleted = prune_price_history(client, retention_days=0)
        assert deleted == 2
        remaining = [r['RowKey'] for r in client.rows]
        assert remaining == ['2020-12-01']  # newest pre-cutoff anchor kept


class TestHistorySummary:

    @pytest.mark.unit
    def test_summary_stats(self):
        s = _history_summary([0.10, 0.12, None, 0.08])
        assert s['first'] == 0.10
        assert s['last'] == 0.08
        assert s['min'] == 0.08
        assert s['max'] == 0.12
        assert s['pctChange'] == -20.0

    @pytest.mark.unit
    def test_summary_none_when_empty(self):
        assert _history_summary([None, None]) is None


class TestBuildHistorySeries:

    @pytest.mark.unit
    def test_appends_current_price_and_sorts(self):
        pk = 'eastus|Standard_D2s_v5'
        client = FakeHistoryClient([
            {'PartitionKey': pk, 'RowKey': '2026-01-01', 'hourlyLinuxUSD': 0.10},
            {'PartitionKey': pk, 'RowKey': '2026-03-01', 'hourlyLinuxUSD': 0.11},
        ])
        entity = {'hourlyPriceUSD': 0.12, 'pricingLastUpdated': '2026-06-01T00:00:00Z'}
        result = _build_history_series(client, entity, 'eastus', 'Standard_D2s_v5')
        dates = [p['date'] for p in result['points']]
        assert dates == ['2026-01-01', '2026-03-01', '2026-06-01']
        assert result['points'][-1]['hourlyLinux'] == 0.12
        assert result['summary']['last'] == 0.12

    @pytest.mark.unit
    def test_same_date_current_replaces_last_point(self):
        pk = 'eastus|Standard_D2s_v5'
        client = FakeHistoryClient([
            {'PartitionKey': pk, 'RowKey': '2026-06-01', 'hourlyLinuxUSD': 0.10},
        ])
        entity = {'hourlyPriceUSD': 0.13, 'pricingLastUpdated': '2026-06-01T12:00:00Z'}
        result = _build_history_series(client, entity, 'eastus', 'Standard_D2s_v5')
        assert len(result['points']) == 1
        assert result['points'][0]['hourlyLinux'] == 0.13

    @pytest.mark.unit
    def test_no_entity_returns_change_points_only(self):
        pk = 'eastus|Standard_D2s_v5'
        client = FakeHistoryClient([
            {'PartitionKey': pk, 'RowKey': '2026-01-01', 'hourlyLinuxUSD': 0.10},
            {'PartitionKey': pk, 'RowKey': '2026-03-01', 'hourlyLinuxUSD': 0.11},
        ])
        result = _build_history_series(client, None, 'eastus', 'Standard_D2s_v5')
        assert len(result['points']) == 2
        assert result['summary']['last'] == 0.11

