#!/usr/bin/env python3
"""
Catalog coverage guard.

Asserts that every VM size Azure enumerates in a region is actually served by
/api/grid. A size that Azure offers but we never persist is invisible to users:
it can't be found in Browse all VMs and can never be recommended as an
alternative.

This exists because of a real, silent outage of exactly that kind. Every size
whose uncached disk throughput exceeded the Int32 ceiling failed to persist to
Table Storage; the failure was logged as a warning and swallowed, and the refresh
still reported success. 324 sizes in East US -- 24% of the catalog, including the
flagship D128/D160/D192 v6 and v7 families -- were missing from production.

Truth source is the Microsoft.Compute SKUs API (the same list the refresh reads),
not the Retail Prices API. Retail still carries meters for long-dead families such
as Basic_A0 and Standard_A10 that Azure no longer enumerates or offers, so it
overstates the catalog by hundreds of sizes and cannot be used as truth.

Requires an ARM token. Provide one of:
    ARM_ACCESS_TOKEN=<token>            (set by CI after azure/login)
    or simply be logged in with the Azure CLI (az login)
Also requires a subscription id via AZURE_SUBSCRIPTION_ID or the CLI default.

Usage:
    python scripts/check_catalog_coverage.py --location eastus
    python scripts/check_catalog_coverage.py --location eastus --max-missing 5

Exit codes:
    0 = every enumerated size is served
    1 = sizes are offered by Azure but missing from the catalog
    2 = the check could not run (no credentials, or an API failure)
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api"
ARM_BASE = "https://management.azure.com"
SKUS_API_VERSION = "2021-07-01"

# _Promo sizes are deliberately excluded from the catalog: identical hardware to
# the base size, so they add noise without adding choice.
EXCLUDED_SUFFIXES = ("_Promo",)


def fetch_json(url, timeout=120, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("User-Agent", "vmsku-coverage-guard")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _run_az(args):
    try:
        result = subprocess.run(
            ["az"] + args,
            capture_output=True,
            text=True,
            timeout=90,
            shell=(os.name == "nt"),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_arm_token():
    token = os.environ.get("ARM_ACCESS_TOKEN")
    if token:
        return token.strip()
    return _run_az(["account", "get-access-token", "--query", "accessToken", "-o", "tsv"])


def get_subscription_id():
    sub = os.environ.get("AZURE_SUBSCRIPTION_ID")
    if sub:
        return sub.strip()
    return _run_az(["account", "show", "--query", "id", "-o", "tsv"])


def fetch_served_skus(location):
    url = f"{API_BASE}/grid?location={urllib.parse.quote(location)}"
    data = fetch_json(url)
    return {row["name"] for row in data.get("skus", []) if row.get("name")}


def fetch_enumerated_skus(location, subscription_id, token):
    """Every deployable VM size Azure enumerates in the region."""
    location_filter = urllib.parse.quote("location eq '{}'".format(location))
    url = (
        "{}/subscriptions/{}/providers/Microsoft.Compute/skus"
        "?api-version={}&$filter={}".format(ARM_BASE, subscription_id, SKUS_API_VERSION, location_filter)
    )
    headers = {"Authorization": "Bearer {}".format(token)}
    names = set()
    pages = 0
    # A single-location query has so far always returned one page, but following
    # nextLink costs nothing and prevents a silent truncation of the truth source.
    while url and pages < 100:
        data = fetch_json(url, headers=headers)
        for sku in data.get("value", []):
            if sku.get("resourceType") != "virtualMachines":
                continue
            name = sku.get("name")
            if name and not name.endswith(EXCLUDED_SUFFIXES):
                names.add(name)
        url = data.get("nextLink")
        pages += 1
    return names


def main():
    parser = argparse.ArgumentParser(
        description="Verify the served catalog covers every VM size Azure offers in a region."
    )
    parser.add_argument("--location", default="eastus", help="Azure region slug (default: eastus)")
    parser.add_argument(
        "--max-missing",
        type=int,
        default=0,
        help="Tolerated number of enumerated-but-missing sizes before failing (default: 0)",
    )
    args = parser.parse_args()
    location = args.location

    print("Checking catalog coverage for {}...\n".format(location))

    token = get_arm_token()
    subscription_id = get_subscription_id()
    if not token or not subscription_id:
        print("ERROR: no Azure credentials available.")
        print("       Set ARM_ACCESS_TOKEN and AZURE_SUBSCRIPTION_ID, or run 'az login'.")
        return 2

    try:
        served = fetch_served_skus(location)
        enumerated = fetch_enumerated_skus(location, subscription_id, token)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print("ERROR: could not complete the check: {}".format(exc))
        return 2

    if not served:
        print("ERROR: /api/grid returned no sizes for {}.".format(location))
        return 2
    if not enumerated:
        print("ERROR: the Compute SKUs API returned no VM sizes for {}.".format(location))
        return 2

    missing = sorted(enumerated - served)
    stale = sorted(served - enumerated)

    print("  sizes enumerated by Azure : {}".format(len(enumerated)))
    print("  sizes served by /api/grid : {}".format(len(served)))
    print("  enumerated but NOT served : {}".format(len(missing)))
    print("  served but NOT enumerated : {}".format(len(stale)))
    print("  checked={}\n".format(len(enumerated)))

    if len(missing) > args.max_missing:
        print("FAILED: {} size(s) are offered by Azure in {} but missing from the catalog.".format(len(missing), location))
        print("        These are invisible in Browse all VMs and can never be recommended.\n")
        for name in missing[:40]:
            print("  missing  {}".format(name))
        if len(missing) > 40:
            print("  ... and {} more".format(len(missing) - 40))
        print("\nLikely cause: rows failed to persist during the last refresh. Look for")
        print("'failed to persist' errors in the Function App logs, then re-run")
        print("POST /api/refresh-region?region=<region> and check skusFailedToPersist.")
        return 1

    if missing:
        print("  note: {} missing size(s), within the tolerance of {}".format(len(missing), args.max_missing))
        for name in missing:
            print("    {}".format(name))
    if stale:
        print("  note: {} served size(s) are no longer enumerated by Azure (pending prune)".format(len(stale)))

    print("PASSED: the {} catalog covers every size Azure offers there.".format(location))
    return 0


if __name__ == "__main__":
    sys.exit(main())
