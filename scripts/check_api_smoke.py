#!/usr/bin/env python3
"""
End-to-end smoke check for every public API endpoint.

Why this exists
---------------
The data-quality guards (pricing, vendor, vCPU, catalog coverage) all inspect
the *contents* of /api/grid. They can be completely green while the endpoints a
real user actually hits are returning HTTP 500 -- which is exactly what happened
when Edm.Int64 values began round-tripping out of Table Storage as
EntityProperty objects and blew up int()/str() conversions in compare_vms.

This guard exercises each endpoint the site and MCP server depend on and fails
on any non-200 response, any error payload, or any obviously empty result.

Usage:
    python scripts/check_api_smoke.py [--location eastus] [--api-base URL]

Exit codes:
    0 = all endpoints healthy
    1 = at least one endpoint failed
    2 = the check itself could not run
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api"
TIMEOUT = 120


def _request(url, payload=None):
    """Return (status, parsed_body_or_text). Never raises for HTTP errors."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        status = e.code
    except Exception as e:
        return 0, "request failed: {}".format(e)

    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _describe(body, limit=300):
    text = body if isinstance(body, str) else json.dumps(body)
    return text[:limit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--location", default="eastus")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    args = parser.parse_args()

    base = args.api_base.rstrip("/")
    loc = args.location

    # Each case: (label, url, payload_or_None, validator(body) -> error_str_or_None)
    def has_items(key, minimum=1):
        def check(body):
            if not isinstance(body, dict):
                return "expected a JSON object, got {}".format(type(body).__name__)
            items = body.get(key)
            if not isinstance(items, list):
                return "'{}' missing or not a list".format(key)
            if len(items) < minimum:
                return "'{}' had {} items, expected at least {}".format(key, len(items), minimum)
            return None
        return check

    def is_object(body):
        if not isinstance(body, dict):
            return "expected a JSON object, got {}".format(type(body).__name__)
        return None

    cases = [
        ("health", "{}/health".format(base), None, is_object),
        ("skus", "{}/skus?location={}".format(base, loc), None, has_items("skus", 100)),
        ("grid", "{}/grid?location={}".format(base, loc), None, has_items("skus", 100)),
        ("retirements", "{}/retirements".format(base), None, is_object),
        ("growth-restrictions", "{}/growth-restrictions".format(base), None, is_object),
        (
            "compare_regions",
            "{}/compare_regions?skuName=Standard_D8s_v5&currencyCode=USD".format(base),
            None,
            is_object,
        ),
        (
            "history",
            "{}/history?location={}&sku=Standard_D8s_v5".format(base, loc),
            None,
            is_object,
        ),
        (
            "compare_vms",
            "{}/compare_vms".format(base),
            {
                "skuName": "Standard_D8s_v5",
                "location": loc,
                "minSimilarityScore": 0,
                "maxResults": 10,
                "currencyCode": "USD",
            },
            has_items("alternatives", 1),
        ),
        (
            "compare_details",
            "{}/compare_details?target=Standard_D8s_v5&alternative=Standard_D8as_v5&location={}&currency=USD".format(base, loc),
            None,
            is_object,
        ),
    ]

    # A large size that only exists when Int64 disk-throughput values persist and
    # round-trip correctly -- the exact size class that silently vanished before.
    cases.append(
        (
            "compare_vms (large size)",
            "{}/compare_vms".format(base),
            {
                "skuName": "Standard_D128s_v6",
                "location": loc,
                "minSimilarityScore": 0,
                "maxResults": 10,
                "currencyCode": "USD",
            },
            has_items("alternatives", 1),
        )
    )

    print("API smoke check against {} ({})\n".format(base, loc))

    failures = []
    checked = 0

    for label, url, payload, validator in cases:
        method = "POST" if payload is not None else "GET"
        status, body = _request(url, payload)
        checked += 1

        if status != 200:
            failures.append((label, "HTTP {} -- {}".format(status, _describe(body))))
            print("  FAIL  {:<26} {} HTTP {}".format(label, method, status))
            continue

        # A 200 that still carries an error payload counts as a failure.
        if isinstance(body, dict) and body.get("error"):
            failures.append((label, "error payload -- {}".format(_describe(body))))
            print("  FAIL  {:<26} {} 200 but error payload".format(label, method))
            continue

        problem = validator(body)
        if problem:
            failures.append((label, problem))
            print("  FAIL  {:<26} {} {}".format(label, method, problem))
            continue

        print("  ok    {:<26} {} 200".format(label, method))

    print("\n  checked={}".format(checked))

    if failures:
        print("\nFAILED: {} of {} endpoints are unhealthy.\n".format(len(failures), checked))
        for label, detail in failures:
            print("  {}\n    {}\n".format(label, detail))
        return 1

    print("\nPASSED: all {} endpoints responded correctly.".format(checked))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
