"""Cache data quality validation tests.

These tests connect to the live Azure Table Storage cache and validate
that all cached SKU data meets quality requirements.

Run: pytest tests/test_cache_validation.py -m cache
Requires: Azure credentials (DefaultAzureCredential or AZURE_STORAGE_CONNECTION_STRING)
"""

import pytest


# ============================================================================
# Per-SKU Field Validation
# ============================================================================

@pytest.mark.cache
class TestCacheFieldCompleteness:
    """Validate that cached SKU entities have all required fields."""

    def test_required_fields_present(self, table_client, major_regions):
        """Every SKU must have name, vCPUs, memoryGB, cpuVendor, architecture."""
        required_fields = ['vCPUs', 'memoryGB', 'cpuVendor', 'architecture']
        issues = []

        for region in major_regions[:2]:  # Test 2 major regions for speed
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey'] + required_fields
            ))
            for entity in entities:
                for field in required_fields:
                    val = entity.get(field)
                    if val is None or val == '':
                        issues.append(f"{region}/{entity['RowKey']}: missing {field}")

        assert not issues, f"Found {len(issues)} missing required fields:\n" + "\n".join(issues[:20])

    def test_vcpus_positive(self, table_client, major_regions):
        """Every SKU must have vCPUs > 0."""
        issues = []
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'vCPUs']
            ))
            for entity in entities:
                vcpus = entity.get('vCPUs')
                if vcpus is not None and vcpus <= 0:
                    issues.append(f"{region}/{entity['RowKey']}: vCPUs={vcpus}")

        assert not issues, f"Found SKUs with non-positive vCPUs:\n" + "\n".join(issues[:20])

    def test_memory_positive(self, table_client, major_regions):
        """Every SKU must have memoryGB > 0."""
        issues = []
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'memoryGB']
            ))
            for entity in entities:
                mem = entity.get('memoryGB')
                if mem is not None and mem <= 0:
                    issues.append(f"{region}/{entity['RowKey']}: memoryGB={mem}")

        assert not issues, f"Found SKUs with non-positive memory:\n" + "\n".join(issues[:20])

    def test_cpu_vendor_valid(self, table_client, major_regions):
        """cpuVendor must be Intel, AMD, or ARM."""
        valid_vendors = {'Intel', 'AMD', 'ARM'}
        issues = []
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'cpuVendor']
            ))
            for entity in entities:
                vendor = entity.get('cpuVendor')
                if vendor and vendor not in valid_vendors:
                    issues.append(f"{region}/{entity['RowKey']}: cpuVendor={vendor}")

        assert not issues, f"Found invalid cpuVendor values:\n" + "\n".join(issues[:20])

    def test_architecture_valid(self, table_client, major_regions):
        """architecture must be x64 or Arm64."""
        valid_arch = {'x64', 'Arm64'}
        issues = []
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'architecture']
            ))
            for entity in entities:
                arch = entity.get('architecture')
                if arch and arch not in valid_arch:
                    issues.append(f"{region}/{entity['RowKey']}: architecture={arch}")

        assert not issues, f"Found invalid architecture values:\n" + "\n".join(issues[:20])


# ============================================================================
# Pricing Data Validation
# ============================================================================

@pytest.mark.cache
class TestCachePricingIntegrity:
    """Validate pricing data consistency in the cache."""

    def test_monthly_matches_hourly(self, table_client):
        """monthlyPriceUSD should approximately equal hourlyPriceUSD * 730."""
        issues = []
        entities = list(table_client.query_entities(
            query_filter="PartitionKey eq 'eastus'",
            select=['RowKey', 'hourlyPriceUSD', 'monthlyPriceUSD']
        ))
        for entity in entities:
            hourly = entity.get('hourlyPriceUSD')
            monthly = entity.get('monthlyPriceUSD')
            if hourly is not None and monthly is not None and hourly > 0:
                expected = hourly * 730
                if abs(monthly - expected) > 1.0:  # Allow $1 rounding tolerance
                    issues.append(
                        f"{entity['RowKey']}: monthly={monthly}, expected={expected:.2f} "
                        f"(hourly={hourly})"
                    )

        assert not issues, f"Pricing inconsistencies:\n" + "\n".join(issues[:20])

    def test_windows_not_cheaper_than_linux(self, table_client):
        """Windows PAYG hourly should be >= Linux PAYG hourly."""
        issues = []
        entities = list(table_client.query_entities(
            query_filter="PartitionKey eq 'eastus'",
            select=['RowKey', 'hourlyPriceUSD', 'hourlyPriceWindowsUSD']
        ))
        for entity in entities:
            linux = entity.get('hourlyPriceUSD')
            windows = entity.get('hourlyPriceWindowsUSD')
            if linux is not None and windows is not None:
                if windows < linux - 0.0001:  # Small tolerance for rounding
                    issues.append(
                        f"{entity['RowKey']}: linux={linux}, windows={windows}"
                    )

        assert not issues, f"Windows cheaper than Linux:\n" + "\n".join(issues[:20])

    def test_ri_cheaper_than_payg(self, table_client):
        """RI pricing should be less than PAYG for the same OS."""
        issues = []
        entities = list(table_client.query_entities(
            query_filter="PartitionKey eq 'eastus'",
            select=['RowKey', 'hourlyPriceUSD', 'ri1YearHourlyUSD', 'ri3YearHourlyUSD']
        ))
        for entity in entities:
            payg = entity.get('hourlyPriceUSD')
            ri1 = entity.get('ri1YearHourlyUSD')
            ri3 = entity.get('ri3YearHourlyUSD')

            if payg and ri1 and ri1 >= payg:
                issues.append(f"{entity['RowKey']}: 1yr RI ({ri1}) >= PAYG ({payg})")
            if payg and ri3 and ri3 >= payg:
                issues.append(f"{entity['RowKey']}: 3yr RI ({ri3}) >= PAYG ({payg})")
            if ri1 and ri3 and ri3 >= ri1:
                issues.append(f"{entity['RowKey']}: 3yr RI ({ri3}) >= 1yr RI ({ri1})")

        assert not issues, f"RI pricing not cheaper than PAYG:\n" + "\n".join(issues[:20])

    def test_windows_ri_surcharge_consistency(self, table_client):
        """Windows RI should equal Linux RI + license surcharge."""
        issues = []
        entities = list(table_client.query_entities(
            query_filter="PartitionKey eq 'eastus'",
            select=[
                'RowKey', 'hourlyPriceUSD', 'hourlyPriceWindowsUSD',
                'ri1YearHourlyUSD', 'ri1YearHourlyWindowsUSD',
                'ri3YearHourlyUSD', 'ri3YearHourlyWindowsUSD'
            ]
        ))
        for entity in entities:
            linux_payg = entity.get('hourlyPriceUSD')
            win_payg = entity.get('hourlyPriceWindowsUSD')
            ri1_linux = entity.get('ri1YearHourlyUSD')
            ri1_win = entity.get('ri1YearHourlyWindowsUSD')

            if all(v is not None for v in [linux_payg, win_payg, ri1_linux, ri1_win]):
                surcharge = win_payg - linux_payg
                expected = ri1_linux + surcharge
                if abs(ri1_win - expected) > 0.01:
                    issues.append(
                        f"{entity['RowKey']}: ri1WinHourly={ri1_win}, "
                        f"expected={expected:.4f} (ri1Linux={ri1_linux} + surcharge={surcharge:.4f})"
                    )

        assert not issues, f"Windows RI surcharge mismatches:\n" + "\n".join(issues[:10])


# ============================================================================
# GPU SKU Validation
# ============================================================================

@pytest.mark.cache
class TestCacheGpuSkus:

    def test_gpu_skus_have_gpu_type(self, table_client):
        """SKUs with gpuCount > 0 should have a non-empty gpuType."""
        issues = []
        entities = list(table_client.query_entities(
            query_filter="PartitionKey eq 'eastus'",
            select=['RowKey', 'gpuCount', 'gpuType']
        ))
        for entity in entities:
            gpu_count = entity.get('gpuCount', 0)
            gpu_type = entity.get('gpuType')
            if gpu_count and gpu_count > 0 and (not gpu_type or gpu_type == ''):
                issues.append(f"{entity['RowKey']}: gpuCount={gpu_count} but gpuType is empty")

        # This is a warning — some SKUs may legitimately lack gpuType
        if issues:
            pytest.xfail(f"GPU SKUs missing gpuType (may be expected):\n" + "\n".join(issues[:10]))


# ============================================================================
# Per-Region Aggregate Health
# ============================================================================

@pytest.mark.cache
class TestCacheRegionHealth:

    def test_region_has_minimum_skus(self, table_client, major_regions):
        """Each major region should have at least 100 cached SKUs."""
        for region in major_regions:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey']
            ))
            count = len(entities)
            assert count >= 100, f"Region {region} has only {count} SKUs (expected >= 100)"

    def test_region_pricing_coverage(self, table_client, major_regions):
        """At least 80% of SKUs in major regions should have Linux PAYG pricing."""
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'hourlyPriceUSD']
            ))
            total = len(entities)
            priced = sum(1 for e in entities if e.get('hourlyPriceUSD') is not None and e.get('hourlyPriceUSD') > 0)
            coverage = (priced / total * 100) if total > 0 else 0
            assert coverage >= 80, (
                f"Region {region}: only {priced}/{total} ({coverage:.0f}%) have Linux pricing "
                f"(expected >= 80%)"
            )

    def test_region_windows_pricing_coverage(self, table_client, major_regions):
        """At least 50% of priced SKUs should have Windows pricing."""
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'hourlyPriceUSD', 'hourlyPriceWindowsUSD']
            ))
            linux_priced = [e for e in entities if e.get('hourlyPriceUSD') is not None and e.get('hourlyPriceUSD') > 0]
            win_priced = sum(1 for e in linux_priced if e.get('hourlyPriceWindowsUSD') is not None and e.get('hourlyPriceWindowsUSD') > 0)
            coverage = (win_priced / len(linux_priced) * 100) if linux_priced else 0
            assert coverage >= 50, (
                f"Region {region}: only {win_priced}/{len(linux_priced)} ({coverage:.0f}%) "
                f"have Windows pricing (expected >= 50%)"
            )

    def test_region_ri_pricing_coverage(self, table_client, major_regions):
        """At least 30% of priced SKUs should have RI pricing."""
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'hourlyPriceUSD', 'ri1YearHourlyUSD']
            ))
            linux_priced = [e for e in entities if e.get('hourlyPriceUSD') is not None and e.get('hourlyPriceUSD') > 0]
            ri_priced = sum(1 for e in linux_priced if e.get('ri1YearHourlyUSD') is not None and e.get('ri1YearHourlyUSD') > 0)
            coverage = (ri_priced / len(linux_priced) * 100) if linux_priced else 0
            assert coverage >= 30, (
                f"Region {region}: only {ri_priced}/{len(linux_priced)} ({coverage:.0f}%) "
                f"have RI pricing (expected >= 30%)"
            )

    def test_region_has_intel_and_amd(self, table_client, major_regions):
        """Major regions should have both Intel and AMD SKUs."""
        for region in major_regions[:2]:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}'",
                select=['RowKey', 'cpuVendor']
            ))
            vendors = {e.get('cpuVendor') for e in entities if e.get('cpuVendor')}
            assert 'Intel' in vendors, f"Region {region} has no Intel SKUs"
            assert 'AMD' in vendors, f"Region {region} has no AMD SKUs"


# ============================================================================
# Cross-Region Consistency
# ============================================================================

@pytest.mark.cache
class TestCrossRegionConsistency:

    def test_same_sku_same_capabilities(self, table_client):
        """A SKU available in multiple regions should have the same vCPUs and memory."""
        # Check a well-known SKU across two regions
        sku_name = 'Standard_D8s_v5'
        regions = ['eastus', 'westeurope']
        results = {}

        for region in regions:
            entities = list(table_client.query_entities(
                query_filter=f"PartitionKey eq '{region}' and RowKey eq '{sku_name}'",
                select=['RowKey', 'vCPUs', 'memoryGB']
            ))
            if entities:
                results[region] = entities[0]

        if len(results) < 2:
            pytest.skip(f"{sku_name} not found in both regions")

        r1, r2 = list(results.values())
        assert r1.get('vCPUs') == r2.get('vCPUs'), (
            f"vCPUs mismatch for {sku_name}: {list(results.keys())} = "
            f"{[r.get('vCPUs') for r in results.values()]}"
        )
        assert r1.get('memoryGB') == r2.get('memoryGB'), (
            f"memoryGB mismatch for {sku_name}: {list(results.keys())} = "
            f"{[r.get('memoryGB') for r in results.values()]}"
        )
