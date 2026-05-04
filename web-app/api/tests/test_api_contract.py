"""API contract tests for the deployed Azure Functions API.

These tests call the live API and validate response schemas and data consistency.

Run: pytest tests/test_api_contract.py -m api
Requires: Live API accessible at API_BASE_URL (defaults to deployed Functions app)
"""

import pytest
import requests


# ============================================================================
# Health Endpoint
# ============================================================================

@pytest.mark.api
class TestHealthEndpoint:

    def test_health_returns_200(self, api_base_url):
        resp = requests.get(f"{api_base_url}/health", timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        assert 'status' in data or 'message' in data


# ============================================================================
# SKUs Endpoint
# ============================================================================

@pytest.mark.api
class TestSkusEndpoint:

    def test_skus_returns_list(self, api_base_url):
        """GET /api/skus?location=eastus should return a non-empty list."""
        resp = requests.get(f"{api_base_url}/skus", params={'location': 'eastus'}, timeout=30)
        assert resp.status_code == 200
        data = resp.json()
        skus = data if isinstance(data, list) else data.get('skus', data.get('data', []))
        assert len(skus) > 0, "Expected at least one SKU from eastus"

    def test_sku_has_required_fields(self, api_base_url):
        """Each SKU should have name, vCPUs, memoryGB, and pricing info."""
        resp = requests.get(f"{api_base_url}/skus", params={'location': 'eastus'}, timeout=30)
        data = resp.json()
        skus = data if isinstance(data, list) else data.get('skus', data.get('data', []))

        # Check first 5 SKUs
        required = ['name', 'vCPUs', 'memoryGB']
        issues = []
        for sku in skus[:5]:
            for field in required:
                if field not in sku:
                    issues.append(f"{sku.get('name', '?')}: missing {field}")
        assert not issues, f"Missing fields:\n" + "\n".join(issues)


# ============================================================================
# Compare VMs Endpoint
# ============================================================================

@pytest.mark.api
class TestCompareVmsEndpoint:

    def test_compare_returns_results(self, api_base_url):
        """POST /api/compare_vms should return targetSku and alternatives."""
        resp = requests.post(
            f"{api_base_url}/compare_vms",
            json={
                'skuName': 'Standard_D8s_v5',
                'location': 'eastus',
                'currencyCode': 'USD'
            },
            timeout=60
        )
        assert resp.status_code == 200
        data = resp.json()
        assert 'targetSku' in data, "Response missing 'targetSku'"
        assert 'alternatives' in data, "Response missing 'alternatives'"
        assert len(data['alternatives']) > 0, "Expected at least one alternative"

    def test_compare_target_has_pricing(self, api_base_url):
        """Target SKU in compare response should have pricing data."""
        resp = requests.post(
            f"{api_base_url}/compare_vms",
            json={
                'skuName': 'Standard_D8s_v5',
                'location': 'eastus',
                'currencyCode': 'USD'
            },
            timeout=60
        )
        data = resp.json()
        target = data['targetSku']
        pricing = target.get('pricing', {})

        assert pricing.get('hourlyPrice') is not None, "Target missing Linux hourly price"
        assert pricing.get('monthlyPrice') is not None, "Target missing Linux monthly price"
        assert pricing.get('hourlyPrice') > 0, "Target Linux hourly price should be > 0"

    def test_compare_has_ri_pricing(self, api_base_url):
        """Target and alternatives should include RI pricing fields."""
        resp = requests.post(
            f"{api_base_url}/compare_vms",
            json={
                'skuName': 'Standard_D8s_v5',
                'location': 'eastus',
                'currencyCode': 'USD'
            },
            timeout=60
        )
        data = resp.json()
        target = data['targetSku']
        pricing = target.get('pricing', {})

        # RI pricing may come from cache or bulk supplement
        ri_fields = ['ri1YearHourly', 'ri1YearMonthly', 'ri3YearHourly', 'ri3YearMonthly']
        has_ri = any(pricing.get(f) is not None for f in ri_fields)
        assert has_ri, f"Target missing RI pricing. Available fields: {list(pricing.keys())}"

    def test_compare_alternative_has_similarity_score(self, api_base_url):
        """Each alternative should have a similarity score between 0-100."""
        resp = requests.post(
            f"{api_base_url}/compare_vms",
            json={
                'skuName': 'Standard_D8s_v5',
                'location': 'eastus',
                'currencyCode': 'USD'
            },
            timeout=60
        )
        data = resp.json()

        for alt in data['alternatives'][:5]:
            score = alt.get('similarityScore', alt.get('similarity'))
            assert score is not None, f"Alternative {alt.get('name')}: missing similarity score"
            assert 0 <= score <= 100, f"Alternative {alt.get('name')}: score {score} out of range"

    def test_compare_pricing_monthly_consistency(self, api_base_url):
        """Monthly price should be approximately hourly * 730."""
        resp = requests.post(
            f"{api_base_url}/compare_vms",
            json={
                'skuName': 'Standard_D8s_v5',
                'location': 'eastus',
                'currencyCode': 'USD'
            },
            timeout=60
        )
        data = resp.json()
        target = data['targetSku']
        pricing = target.get('pricing', {})

        hourly = pricing.get('hourlyPrice')
        monthly = pricing.get('monthlyPrice')
        if hourly and monthly:
            expected = hourly * 730
            assert abs(monthly - expected) < 1.0, (
                f"Monthly ({monthly}) != hourly*730 ({expected:.2f})"
            )


# ============================================================================
# Compare Details Endpoint
# ============================================================================

@pytest.mark.api
class TestCompareDetailsEndpoint:

    def test_compare_details_returns_differences(self, api_base_url):
        """GET /api/compare_details should return structured differences."""
        resp = requests.get(
            f"{api_base_url}/compare_details",
            params={
                'target': 'Standard_D8s_v5',
                'alternative': 'Standard_D8as_v5',
                'location': 'eastus',
                'currency': 'USD'
            },
            timeout=60
        )
        assert resp.status_code == 200
        data = resp.json()
        diffs = data.get('differences', data)

        # Should have compute and pricing sections
        assert 'compute' in diffs, "Missing 'compute' section"
        assert 'pricing' in diffs, "Missing 'pricing' section"

    def test_compare_details_has_ri_variants(self, api_base_url):
        """Detail comparison should include RI pricing variants."""
        resp = requests.get(
            f"{api_base_url}/compare_details",
            params={
                'target': 'Standard_D8s_v5',
                'alternative': 'Standard_D8as_v5',
                'location': 'eastus',
                'currency': 'USD'
            },
            timeout=60
        )
        data = resp.json()
        pricing = data.get('differences', data).get('pricing', {})

        # Should have RI variants
        ri_keys = ['ri1Year', 'ri3Year']
        has_ri = any(pricing.get(k) is not None for k in ri_keys)
        assert has_ri, f"Detail pricing missing RI variants. Keys: {list(pricing.keys())}"

    def test_compare_details_has_windows_ri(self, api_base_url):
        """Detail comparison should include Windows RI pricing variants."""
        resp = requests.get(
            f"{api_base_url}/compare_details",
            params={
                'target': 'Standard_D8s_v5',
                'alternative': 'Standard_D8as_v5',
                'location': 'eastus',
                'currency': 'USD'
            },
            timeout=60
        )
        data = resp.json()
        pricing = data.get('differences', data).get('pricing', {})

        # Windows RI keys should be present (may be null if surcharge calc can't run)
        win_ri_keys = ['ri1YearWindows', 'ri3YearWindows']
        has_win_ri_keys = all(k in pricing for k in win_ri_keys)
        assert has_win_ri_keys, f"Detail pricing missing Windows RI keys. Keys: {list(pricing.keys())}"
