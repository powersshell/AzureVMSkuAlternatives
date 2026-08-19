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

Corruption vs staleness
-----------------------
Our prices come from a cache refreshed once a day at 02:00 UTC; this script
compares them against the retail feed as it is *right now*. Azure reprices
meters -- Spot especially -- continuously, so a published price can be the
right meter and still not equal today's number. That is cache lag, not
corruption, and it clears at the next refresh.

Demanding exact equality conflates the two. On 2026-08-19 Azure republished a
batch of Spot meters (all backdated to effectiveStartDate 2026-08-01) between
one run and the next, and the check failed on 146 eastus / 59 westeurope sizes
with no defect present.

So a mismatch is triaged rather than simply failed:

  * If the published value equals a meter of a DIFFERENT kind (Low Priority,
    Windows, PAYG, reservation), we stored the wrong meter -- that is the bug
    this script exists to catch, and it fails hard.
  * If it is wildly out of band for its kind, it fails hard.
  * If it matches no current meter but is otherwise a plausible price of the
    right kind, it is reported as STALE and only fails once an implausible
    share of the region is affected -- which is what a genuinely broken
    refresh would look like.

Checks performed:
  1. Spot <= pay-as-you-go for every size (a Spot price above PAYG is
     definitionally wrong).
  2. No published Spot price is another kind of meter -- in particular not the
     size's deprecated 'Low Priority' price.
  3. Every published Spot price is a plausible fraction of pay-as-you-go.
  4. No published Linux PAYG price is another kind of meter (Windows, Spot,
     Low Priority or a reservation term total).
  5. No hourly price is implausibly large relative to its size's PAYG price
     (catches a reservation term-total leaking into an hourly field).
  6. Spot and Linux prices are reasonably current (staleness stays below
     STALE_SHARE_THRESHOLD of the region).

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
# so an equal price should be bit-identical; allow a hair of slack for JSON
# round-trips. This tolerance is deliberately NOT widened into a percentage
# band: the 2026-08-19 Spot repricing moved prices by exactly 20%, which is the
# same order as a genuine meter mix-up, so any band wide enough to absorb
# repricing is wide enough to hide the bug this script exists to catch.
# Cache lag is handled by triage (see module docstring), not by loosening this.
TOLERANCE = 1e-9

# A published price that matches no current meter is treated as stale rather
# than corrupt, but a *broken* refresh would leave most of the region stale.
# Observed normal drift after an Azure repricing is ~4-10% of a region.
STALE_SHARE_THRESHOLD = 0.25

# Floor for spot / pay-as-you-go. Measured across 2,597 sizes in eastus and
# westeurope the real minimum is 0.1848 and nothing falls below 0.10, so 0.05
# leaves a wide margin while still catching the Low Priority conflation that
# prompted this script (that bug produced ratios around 0.002).
SPOT_PAYG_RATIO_MIN = 0.05

# Human-readable names for the meter kinds a price can be mistaken for.
BUCKET_LABELS = {
    "lowpriority": "the deprecated LOW PRIORITY price",
    "windows": "a WINDOWS meter",
    "spot-windows": "a WINDOWS SPOT meter",
    "spot": "a SPOT meter",
    "linux": "the pay-as-you-go LINUX price",
    "reservation": "a RESERVATION term total",
}


def matching_bucket(value, buckets, exclude):
    """Name the meter kind a value came from, ignoring the kind it should be.

    This is what separates corruption from staleness: a wrong-meter bug leaves
    a value that is exactly some *other* meter for the same size, while a stale
    value matches nothing at all.
    """
    for kind, prices in buckets.items():
        if kind == exclude or kind == "excluded":
            continue
        if any(abs(value - p) <= TOLERANCE for p in prices):
            return BUCKET_LABELS.get(kind, f"a {kind} meter")
    return None


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
    spot_wrong_meter = []
    spot_implausible = []
    linux_wrong_meter = []
    implausible_hourly = []
    spot_stale = []
    linux_stale = []
    spot_comparable = 0
    linux_comparable = 0
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

        # 2/3/6. Triage a published Spot price: wrong meter, implausible, or
        #        merely superseded by a repricing since the last cache refresh.
        real_spot = buckets.get("spot", [])
        if spot is not None and real_spot:
            spot_comparable += 1
            if not any(abs(spot - p) <= TOLERANCE for p in real_spot):
                culprit = matching_bucket(spot, buckets, exclude="spot")
                if culprit:
                    spot_wrong_meter.append(
                        f"{name}: published {spot} is {culprit}; "
                        f"true Spot is {real_spot[0]}")
                elif linux and spot / linux < SPOT_PAYG_RATIO_MIN:
                    spot_implausible.append(
                        f"{name}: published {spot} is {spot / linux:.4f}x the PAYG price "
                        f"{linux}, below the {SPOT_PAYG_RATIO_MIN} floor for a real Spot rate")
                else:
                    spot_stale.append(
                        f"{name}: published {spot}, current Spot meter {real_spot[0]}")

        # 4/6. Same triage for the published Linux pay-as-you-go price.
        real_linux = buckets.get("linux", [])
        if linux is not None and real_linux:
            linux_comparable += 1
            if not any(abs(linux - p) <= TOLERANCE for p in real_linux):
                culprit = matching_bucket(linux, buckets, exclude="linux")
                if culprit:
                    linux_wrong_meter.append(
                        f"{name}: published {linux} is {culprit}, not the Linux price "
                        f"{real_linux[0]}")
                else:
                    linux_stale.append(
                        f"{name}: published {linux}, current Linux meter {real_linux[0]}")

        # 5. Reservation totals leaking into hourly fields.
        for field in ("hourlyLinux", "hourlyWindows", "spotHourlyLinux",
                      "ri1YearHourly", "ri3YearHourly"):
            value = row.get(field)
            if value is not None and linux and value > linux * 100:
                implausible_hourly.append(
                    f"{name}.{field} = {value} is >100x the PAYG hourly price {linux}")

    checks = [
        ("spot <= pay-as-you-go", spot_above_payg),
        ("spot price is not another meter kind", spot_wrong_meter),
        ("spot price is a plausible fraction of pay-as-you-go", spot_implausible),
        ("linux price is not another meter kind", linux_wrong_meter),
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

    # Staleness is expected between refreshes, so it is reported with a share
    # and only fails once it looks like a broken refresh rather than a repricing.
    for label, stale, comparable in (
        ("spot prices are current", spot_stale, spot_comparable),
        ("linux prices are current", linux_stale, linux_comparable),
    ):
        if not stale:
            print(f"  ok   {label}")
            continue
        stale_count = len(stale)
        share = stale_count / comparable if comparable else 0.0
        verdict = "FAIL" if share > STALE_SHARE_THRESHOLD else "note"
        print(f"  {verdict} {label}: {stale_count}/{comparable} "
              f"({share:.1%}) match no current meter")
        for example in stale[:args.max_report]:
            print(f"         - {example}")
        if stale_count > args.max_report:
            print(f"         ... and {stale_count - args.max_report} more")
        if share > STALE_SHARE_THRESHOLD:
            print(f"         over the {STALE_SHARE_THRESHOLD:.0%} threshold -- "
                  f"this looks like a failed cache refresh, not a repricing")
            record(label, stale_count)
        else:
            print(f"         within the {STALE_SHARE_THRESHOLD:.0%} threshold -- "
                  f"treated as cache lag, not corruption. These are the right "
                  f"meters at a superseded price and clear at the next refresh.")

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
