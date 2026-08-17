"""Exercise the hosted MCP server over the real MCP protocol.

Every other guard in scripts/ calls the HTTP API directly. None of them touch the
MCP server, which is a separately built and separately deployed container app --
so it can drift, serve stale code, or fail to expose a tool while all seven
existing checks stay green.

This connects as a genuine MCP client (initialize -> tools/list -> tools/call)
against the deployed endpoint and asserts each tool returns usable data, not just
HTTP 200. It re-derives expectations independently rather than trusting the
server's own framing.
"""

import argparse
import asyncio
import json
import re
import sys

from fastmcp import Client

DEFAULT_URL = (
    "https://vmsku-mcp-server.braveriver-1558541d."
    "southcentralus.azurecontainerapps.io/mcp"
)

EXPECTED_TOOLS = {
    "health_check",
    "list_vm_skus",
    "find_alternative_skus",
    "compare_sku_details",
    "check_region_availability",
    "compare_regions_for_sku",
    "list_region_vm_grid",
    "get_sku_price_history",
    "list_retiring_skus",
    "list_growth_restricted_skus",
}

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" -- " + detail) if detail else ""))
    return bool(ok)


def payload(res):
    """Unwrap a CallToolResult into the dict the tool actually returned."""
    if getattr(res, "structured_content", None):
        sc = res.structured_content
        # FastMCP wraps non-dict returns under "result"
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    if getattr(res, "data", None) is not None:
        return res.data
    txt = res.content[0].text
    return json.loads(txt)


def gen_of(name):
    """Independently parse the generation number out of a size name."""
    m = re.search(r"_v(\d+)$", name)
    if m:
        return int(m.group(1))
    m = re.search(r"_v(\d+)_", name)
    return int(m.group(1)) if m else 1


def family_of(name):
    m = re.match(r"Standard_([A-Za-z]+)", name)
    return m.group(1).upper() if m else ""


async def main(url, location):
    print("MCP server: " + url)
    print("Region:     " + location + "\n")

    async with Client(url) as client:
        # ---- protocol: tool discovery -------------------------------------
        tools = await client.list_tools()
        names = {t.name for t in tools}
        missing = EXPECTED_TOOLS - names
        check("tools/list exposes all 10 tools", not missing,
              ("missing: " + ", ".join(sorted(missing))) if missing else str(len(names)) + " tools")

        undocumented = [t.name for t in tools if not (t.description or "").strip()]
        check("every tool has a description", not undocumented, ", ".join(undocumented))

        noschema = [t.name for t in tools if not getattr(t, "inputSchema", None)]
        check("every tool has an input schema", not noschema, ", ".join(noschema))

        # ---- health -------------------------------------------------------
        h = payload(await client.call_tool("health_check", {}))
        check("health_check reports healthy",
              str(h.get("status", "")).lower() in ("healthy", "ok"), json.dumps(h)[:120])

        # ---- list_vm_skus -------------------------------------------------
        r = payload(await client.call_tool("list_vm_skus", {"location": location}))
        skus = r.get("skus") or []
        check("list_vm_skus returns a populated catalog", len(skus) > 500,
              str(len(skus)) + " sizes")
        if skus:
            bad = [s for s in skus if not (s.get("vCPUs") or 0) > 0]
            check("every size reports a positive vCPU count", not bad,
                  str(len(bad)) + " with vCPUs<=0")
            vendors = {s.get("cpuVendor") for s in skus}
            check("multiple CPU vendors present", len({v for v in vendors if v}) >= 2,
                  ", ".join(sorted(str(v) for v in vendors if v)))

        # ---- region name normalization ------------------------------------
        # Regression guard for the Scout failure: the SKU cache is partitioned on
        # the exact ARM slug, so a portal display name ("Central US") used to miss
        # the cache and fall through to a ~40-50s live Azure lookup -- close enough
        # to the server's 60s HTTP timeout that the call frequently failed outright.
        # This suite only ever ran "eastus", so a region-shaped bug was invisible.
        canonical = None
        for variant in ("centralus", "Central US", "central us", "CentralUS", "Central-US"):
            v = payload(await client.call_tool("list_vm_skus", {"location": variant}))
            count = len(v.get("skus") or [])
            if canonical is None:
                canonical = count
                check("region slug 'centralus' resolves", count > 500, str(count) + " sizes")
            else:
                check("region variant %r matches the slug" % variant,
                      count == canonical,
                      "%d vs %d sizes" % (count, canonical))

        # ---- find_alternative_skus: today's scoring surface ---------------
        target = "Standard_D2_v3"
        r = payload(await client.call_tool("find_alternative_skus", {
            "target_sku": target, "location": location, "min_similarity_score": 60}))
        alts = r.get("alternatives") or []
        check("find_alternative_skus returns alternatives", len(alts) >= 10,
              str(len(alts)) + " for " + target)

        if alts:
            top = alts[0]
            check("recommendationScore is exposed via MCP",
                  top.get("recommendationScore") is not None,
                  "top=" + str(top.get("name")) + " score=" + str(top.get("recommendationScore")))
            check("similarityScore still exposed (back-compat)",
                  top.get("similarityScore") is not None,
                  str(top.get("similarityScore")))

            # ranking properties, re-derived here rather than trusted
            top_name = top.get("name") or ""
            check("#1 is not an older generation than the source",
                  gen_of(top_name) >= gen_of(target),
                  top_name + " v" + str(gen_of(top_name)) +
                  " vs source v" + str(gen_of(target)))
            check("#1 is not retired",
                  str(top.get("retirementStatus") or "").lower() != "retired",
                  str(top.get("retirementStatus")))
            check("#1 is not growth restricted",
                  not top.get("growthRestricted"),
                  "growthRestricted=" + str(top.get("growthRestricted")))
            check("#1 matches the source family (D-series)",
                  family_of(top_name).startswith("D"), top_name)
            # Measured across the FULL result set, threshold 4, to match
            # scripts/check_recommendation_quality.py. A tie among same-family
            # same-generation siblings (four D-series v7s all at 100) is
            # legitimate -- those are equally good answers.
            all_scores = [a.get("recommendationScore") for a in alts]
            distinct = len({s for s in all_scores if s is not None})
            check("tie-collapse guard: >=4 distinct scores across result set",
                  distinct >= 4,
                  str(distinct) + " distinct across " + str(len(all_scores)))

        # ---- priority_mode actually changes the answer --------------------
        rc = payload(await client.call_tool("find_alternative_skus", {
            "target_sku": target, "location": location, "priority_mode": "cost"}))
        cost_alts = rc.get("alternatives") or []
        bal5 = [a.get("name") for a in alts[:5]]
        cost5 = [a.get("name") for a in cost_alts[:5]]
        check("priority_mode=cost reorders results", bal5 != cost5,
              "balanced=" + str(bal5[:3]) + " cost=" + str(cost5[:3]))

        # ---- architecture_filter is honoured ------------------------------
        ra = payload(await client.call_tool("find_alternative_skus", {
            "target_sku": target, "location": location, "architecture_filter": "arm64"}))
        arm = ra.get("alternatives") or []
        nonarm = [a.get("name") for a in arm
                  if str(a.get("architecture", "")).lower() not in ("arm64", "aarch64")]
        check("architecture_filter=arm64 returns only Arm64",
              arm and not nonarm, str(len(arm)) + " results, " + str(len(nonarm)) + " non-Arm")

        # ---- compare_sku_details ------------------------------------------
        r = payload(await client.call_tool("compare_sku_details", {
            "target_sku": "Standard_D4s_v5", "alternative_sku": "Standard_D4as_v5",
            "location": location}))
        check("compare_sku_details returns a comparison",
              bool(r) and not r.get("error"), ", ".join(list(r.keys())[:6]))

        # ---- check_region_availability ------------------------------------
        r = payload(await client.call_tool("check_region_availability", {
            "sku_names": ["Standard_D4s_v5", "Standard_NOT_A_REAL_SIZE"],
            "region": location}))
        blob = json.dumps(r)
        check("check_region_availability distinguishes real from fake",
              "Standard_D4s_v5" in blob and "NOT_A_REAL_SIZE" in blob, blob[:140])

        # ---- compare_regions_for_sku --------------------------------------
        r = payload(await client.call_tool("compare_regions_for_sku", {
            "target_sku": "Standard_D8s_v5"}))
        regions = r.get("regions") or []
        check("compare_regions_for_sku returns multiple regions", len(regions) > 5,
              str(len(regions)) + " regions")
        if regions:
            prices = [x.get("hourlyPrice") or x.get("linuxHourly") for x in regions]
            prices = [p for p in prices if isinstance(p, (int, float))]
            check("region prices sorted cheapest-first",
                  prices == sorted(prices), str(prices[:4]))

        # ---- list_region_vm_grid ------------------------------------------
        r = payload(await client.call_tool("list_region_vm_grid", {"location": location}))
        grid = r.get("skus") or []
        check("list_region_vm_grid returns the full grid", len(grid) > 500,
              str(len(grid)) + " rows")

        # ---- get_sku_price_history ----------------------------------------
        r = payload(await client.call_tool("get_sku_price_history", {
            "location": location, "skus": ["Standard_D2s_v5", "Standard_D4s_v5"]}))
        check("get_sku_price_history responds for a batch",
              bool(r) and not r.get("error"), json.dumps(r)[:140])

        # ---- lifecycle catalogs -------------------------------------------
        r = payload(await client.call_tool("list_retiring_skus", {}))
        ret = r.get("retirements") or []
        check("list_retiring_skus returns the retirement catalog", len(ret) > 0,
              str(len(ret)) + " entries")

        r = payload(await client.call_tool("list_retiring_skus", {"sku": "Standard_D2_v2"}))
        check("list_retiring_skus flags a known retiring size",
              "retirementStatus" in json.dumps(r), json.dumps(r)[:140])

        r = payload(await client.call_tool("list_growth_restricted_skus", {}))
        gr = r.get("growthRestrictions") or []
        check("list_growth_restricted_skus returns the capacity catalog", len(gr) > 0,
              str(len(gr)) + " entries")

        r = payload(await client.call_tool("list_growth_restricted_skus",
                                           {"sku": "Standard_D4s_v3"}))
        check("Standard_D4s_v3 is flagged growth restricted",
              r.get("growthRestricted") is True, json.dumps(r)[:180])

        r = payload(await client.call_tool("list_growth_restricted_skus",
                                           {"sku": "Standard_D4s_v5"}))
        check("Standard_D4s_v5 is NOT flagged growth restricted",
              r.get("growthRestricted") is False, json.dumps(r)[:180])

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 62)
    print("PASSED " + str(len(results) - len(failed)) + "/" + str(len(results)))
    if failed:
        print("FAILED:")
        for n in failed:
            print("  - " + n)
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--location", default="eastus")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.url, a.location)))
