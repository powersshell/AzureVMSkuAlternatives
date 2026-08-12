#!/usr/bin/env python3
"""
Pricing accuracy invariant check.

Guards the price-selection logic in web-app/api/function_app.py against a class
of silent-corruption bugs where the wrong Azure retail *meter* is stored in a
price field. These bugs never raise -- they just publish a plausible-looking
number that is wrong -- so they can persist indefinitely without a guard.

Background: Azure publishes several meters per VM size that all share the same
armSkuName. Picking the wrong one is easy and invisible:

  * "Low Priority" is the DEPRECATED, Batch-only tier. It is a different
    product from Spot at a different price. Conflating the two made the Spot
    price wrong for 472 sizes in eastus (worst case 99.8% off) until it was
    fixed on 2026-08-12.
  * "Spot" meters must never land in a pay-as-you-go field, and PAYG meters
    must never land in the Spot field.
  * Reservation meters carry a FULL TERM TOTAL, not an hourly rate. One landing
    in an hourly field would overstate the price by ~4-5 orders of magnitude.
  * Windows meters must never be reported as the Linux price.

Rather than re-implement the selection logic (which would just duplicate any
bug), this validates the LIVE API output against the retail price list as
ground truth: every price the API publishes must be traceable to a meter of the
correct kind.

Checks performed:
  1. Spot <= pay-as-you-go for every size (a Spot price above PAYG is
     definitionally wrong).
  2. Every published Spot price matches a genuine 'Spot' meter -- and is not
     the size's 'Low Priority' price.
  3. Every published Linux PAYG price matches a genuine non-Windows,
     non-Spot, non-Reservation Consumption meter.
  4. No hourly price is implausibly large relative to its size's PAYG price
     (catches a reservation term-total leaking into an hourly field).

Usage:
    python scripts/check_pricing_invariants.py [--location eastus]
                                               [--api-base URL]
                                               [--max-report N]

Exits non-zero if any invariant is violated, so it can gate CI.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

RETAIL_API = "https://prices.azure.com/api/retail/prices"
DEFAULT_API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api"
USER_AGENT = "AzureVMSkuAlternatives-pricing-check/1.0"
# Prices are compared as floats that both sides derived from the same source,
# so they should be bit-identical; allow a hair of slack for JSON round-trips.
TOLERANCE = 1e-9


def _get_json(url: str, timeout: int = 90, retries: int = 4):
    """GET with a User-Agent and exponential backoff.

    prices.azure.com drops connections from clients that send no User-Agent,
    and both endpoints are occasionally flaky under CI.
    """
    last_error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after {retries} attempts: {url} ({last_error})")


def fetch_retail_meters(location: str):
    """Every Virtual Machines meter for a region, keyed by armSkuName."""
    url = (
        f"{RETAIL_API}?currencyCode=USD&$filter=serviceName eq 'Virtual Machines'"
        f" and armRegionName eq '{location}'"
        f" and (type eq 'Consumption' or type eq 'Reservation')"
    ).replace(" ", "%20")

    by_sku = defaultdict(list)
    total = 0
    while url:
        payload = _get_json(url)
        for item in payload.get("Items", []):
            name = item.get("armSkuName")
            if name:
                by_sku[name].append(item)
                total += 1
        url = payload.get("NextPageLink")
        if url:
            time.sleep(0.2)
    return by_sku, total


def classify(item):
    """Bucket a retail meter the way the API is supposed to."""
    product = (item.get("productName") or "").lower()
    label = item.get("skuName") or ""
    if "dedicatedhost" in product or "cloud" in product:
        return "excluded"
    if "Low Priority" in label:
        return "lowpriority"
    if item.get("type") == "Reservation":
        return "reservation"
    if "Spot" in label:
        return "spot-windows" if "windows" in product else "spot"
    return "windows" if "windows" in product else "linux"


def fetch_grid(api_base: str, location: str):
    payload = _get_json(f"{api_base}/grid?location={location}", timeout=240)
    if isinstance(payload, dict):
        for key in ("skus", "vms", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    if isinstance(payload, list):
        return payload
    raise RuntimeError("Unrecognised /api/grid response shape")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--location", default="eastus", help="Azure region (default: eastus)")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="API base URL")
    parser.add_argument("--max-report", type=int, default=10,
                        help="Max example violations to print per check (default: 10)")
    args = parser.parse_args()

    print(f"== Pricing invariants ({args.location}) ==\n")

    print("Fetching retail price list (ground truth)...")
    meters, meter_count = fetch_retail_meters(args.location)
    print(f"  {meter_count} meters across {len(meters)} sizes")

    print("Fetching live API grid...")
    rows = fetch_grid(args.api_base, args.location)
    print(f"  {len(rows)} sizes published by the API\n")

    problems = []

    def record(check, detail):
        problems.append((check, detail))

    spot_above_payg = []
    spot_is_lowpriority = []
    spot_not_a_spot_meter = []
    linux_not_a_linux_meter = []
    implausible_hourly = []
    unmatched = 0

    for row in rows:
        name = row.get("name") or row.get("skuName")
        if not name:
            continue
        linux = row.get("hourlyLinux")
        spot = row.get("spotHourlyLinux")

        buckets = defaultdict(list)
        for item in meters.get(name, []):
            buckets[classify(item)].append(item["unitPrice"])
        if not buckets:
            unmatched += 1
            continue

        # 1. Spot must never exceed pay-as-you-go.
        if spot is not None and linux is not None and spot > linux + TOLERANCE:
            spot_above_payg.append(f"{name}: spot {spot} > payg {linux}")

        # 2. A published Spot price must be a real Spot meter, and specifically
        #    must not be the deprecated Low Priority price.
        if spot is not None:
            real_spot = buckets.get("spot", [])
            low_priority = buckets.get("lowpriority", [])
            matches_spot = any(abs(spot - p) <= TOLERANCE for p in real_spot)
            matches_lp = any(abs(spot - p) <= TOLERANCE for p in low_priority)
            if not matches_spot:
                if matches_lp:
                    spot_is_lowpriority.append(
                        f"{name}: published {spot} is the Low Priority price; "
                        f"true Spot is {real_spot[0] if real_spot else 'n/a'}")
                elif real_spot:
                    spot_not_a_spot_meter.append(
                        f"{name}: published {spot} matches no Spot meter {real_spot[:3]}")

        # 3. A published Linux price must be a real Linux Consumption meter.
        if linux is not None:
            real_linux = buckets.get("linux", [])
            if real_linux and not any(abs(linux - p) <= TOLERANCE for p in real_linux):
                culprit = "unknown meter"
                if any(abs(linux - p) <= TOLERANCE for p in buckets.get("windows", [])):
                    culprit = "a WINDOWS meter"
                elif any(abs(linux - p) <= TOLERANCE for p in buckets.get("reservation", [])):
                    culprit = "a RESERVATION term total"
                elif any(abs(linux - p) <= TOLERANCE for p in buckets.get("lowpriority", [])):
                    culprit = "a LOW PRIORITY meter"
                linux_not_a_linux_meter.append(
                    f"{name}: published {linux} is {culprit}, not the Linux price "
                    f"{real_linux[0]}")

        # 4. Reservation totals leaking into hourly fields.
        for field in ("hourlyLinux", "hourlyWindows", "spotHourlyLinux",
                      "ri1YearHourly", "ri3YearHourly"):
            value = row.get(field)
            if value is not None and linux and value > linux * 100:
                implausible_hourly.append(
                    f"{name}.{field} = {value} is >100x the PAYG hourly price {linux}")

    checks = [
        ("spot <= pay-as-you-go", spot_above_payg),
        ("spot price is a real Spot meter (not Low Priority)", spot_is_lowpriority),
        ("spot price traceable to a Spot meter", spot_not_a_spot_meter),
        ("linux price is a real Linux meter", linux_not_a_linux_meter),
        ("hourly fields are hourly (not term totals)", implausible_hourly),
    ]

    for label, violations in checks:
        if violations:
            print(f"  FAIL {label}: {len(violations)} violation(s)")
            for example in violations[:args.max_report]:
                print(f"         - {example}")
            if len(violations) > args.max_report:
                print(f"         ... and {len(violations) - args.max_report} more")
            record(label, len(violations))
        else:
            print(f"  ok   {label}")

    if unmatched:
        print(f"\n  note: {unmatched} published size(s) had no retail meter to verify against")

    print()
    if problems:
        total = sum(count for _, count in problems)
        print(f"FAILED: {total} pricing invariant violation(s) across "
              f"{len(problems)} check(s)")
        return 1

    print("PASSED: all pricing invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
