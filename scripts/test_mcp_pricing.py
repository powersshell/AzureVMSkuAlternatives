"""Offline regression checks for MCP savings plan forwarding."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))
import mcp_server


class SavingsPlanForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_preferences_and_assessment_are_forwarded(self):
        data = {
            "generationContext": {"sourceGeneration": 1, "targetGeneration": 2},
            "migrationEffort": {"level": "Candidate-dependent"},
            "alternatives": [{"migrationAssessment": {
                "destinationGeneration": None, "preferenceSupport": "unknown",
                "pathStatus": "unassessed", "effort": "Cannot assess effort",
                "eligibilityAssessed": False,
                "checklist": ["Verify actual VM prerequisites."],
            }}],
        }
        response = Mock()
        response.json.return_value = data
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with patch.object(mcp_server.httpx, "AsyncClient", return_value=client):
            result = await mcp_server.find_alternative_skus(
                "Standard_D4s_v5", "East US", source_gen="gen1", target_gen="gen2")
        self.assertEqual(result, data)
        request = client.post.call_args.kwargs["json"]
        self.assertEqual(request["sourceGen"], "gen1")
        self.assertEqual(request["targetGen"], "gen2")
        self.assertEqual(request["location"], "eastus")
        response.raise_for_status.assert_called_once()

    async def test_regional_selection_and_precise_rates_are_forwarded(self):
        data = {
            "pricingModel": "sp3year",
            "regions": [{"sp3YearHourly": 0.0883584, "sp3YearHourlyWindows": None}],
        }
        response = Mock()
        response.json.return_value = data
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        with patch.object(mcp_server.httpx, "AsyncClient", return_value=client):
            result = await mcp_server.compare_regions_for_sku(
                "Standard_D4s_v5", "windows", "EUR", "sp3year")
        self.assertEqual(result, data)
        self.assertEqual(client.get.call_args.kwargs["params"], {
            "skuName": "Standard_D4s_v5", "os": "windows", "currency": "EUR",
            "pricingModel": "sp3year",
        })
        response.raise_for_status.assert_called_once()

    async def test_grid_preserves_nulls_status_and_estimates(self):
        data = {"skus": [{
            "sp1YearHourlyLinux": 0, "sp3YearHourlyWindows": None,
            "savingsPlanPricingStatus": "not_refreshed",
            "savingsPlanWindowsEstimated": True,
        }]}
        response = Mock()
        response.json.return_value = data
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        with patch.object(mcp_server.httpx, "AsyncClient", return_value=client):
            result = await mcp_server.list_region_vm_grid("Central US", "USD")
        self.assertEqual(result, data)
        self.assertEqual(client.get.call_args.kwargs["params"]["location"], "centralus")


if __name__ == "__main__":
    unittest.main()
