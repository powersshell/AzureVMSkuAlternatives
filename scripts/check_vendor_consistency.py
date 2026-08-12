#!/usr/bin/env python3
"""
CPU vendor accuracy invariant check.

Guards `detect_cpu_vendor` in web-app/api/function_app.py against silent
misclassification. Like the pricing bugs, a wrong vendor never raises -- it just
publishes a plausible-looking label -- so it can persist indefinitely unnoticed.

Background: vendor used to be inferred from a single regex over the SKU name
(`a[dl]*s_v\\d`), which only recognised a fixed suffix shape. That missed every
AMD size whose name did not match it, mislabelling 107 sizes in eastus as Intel
on 2026-08-12, including:

  * additive-feature letters between 'a' and 's' -- Standard_F2ams_v6,
    Standard_F1amds_v7, Standard_B2ats_v2, Standard_L2aos_v4
  * no 's' at all -- Standard_D2a_v4, Standard_E2a_v4
  * an accelerator suffix instead of '_v' -- Standard_NC4as_T4_v3,
    Standard_EC4ads_cc_v5
  * AMD EPYC families whose names carry no vendor letter at all --
    Standard_HB120rs_v3, Standard_HX176rs

Vendor is a user-facing filter on the site, in the MCP server and in the
PowerShell script, so a mislabelled size is both displayed wrongly and silently
omitted from "show me AMD" results.

Rather than re-implement the detection logic (which would duplicate any bug),
this validates the LIVE API output against two independent sources of truth:

  1. The CPU generation the API itself publishes. Generations are resolved from
     a curated series -> CPU model mapping, so they are independent of the name
     heuristic. No Intel CPU is "Zen" and no AMD CPU is "Neoverse", so
     generation and vendor disagreeing proves one of them is wrong.
  2. Azure's documented VM name grammar
     [Family][#vCPUs][-Constrained][AdditiveFeatures]_[Version], in which the
     additive-feature letter 'a' means AMD and 'p' means Arm.

Checks performed:
  1. Every vendor is one of Intel / AMD / ARM.
  2. Vendor agrees with the published CPU generation.
  3. Every size whose name carries the 'a' feature letter is AMD.
  4. Every size whose architecture is Arm64 is ARM (and vice versa).

Usage:
    python scripts/check_vendor_consistency.py [--location eastus]
                                               [--api-base URL]
                                               [--max-report N]

Exits non-zero if any invariant is violated, so it can gate CI.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api"
USER_AGENT = "AzureVMSkuAlternatives-vendor-check/1.0"

VALID_VENDORS = {"Intel", "AMD", "ARM"}

# Microarchitecture families are vendor-exclusive, which is what makes this a
# usable cross-check: AMD server parts are Zen, Azure's Arm parts are Neoverse.
AMD_GENERATION = re.compile(r"zen|epyc", re.IGNORECASE)
ARM_GENERATION = re.compile(r"neoverse|ampere|cobalt", re.IGNORECASE)

# Family letters are uppercase and additive-feature letters lowercase, so this is
# deliberately case-sensitive: a case-insensitive match lets the family group
# swallow the feature letters and silently return "no features".
SKU_NAME_PARTS = re.compile(r"^(?:Standard|Basic)_([A-Z]+)(\d+)(?:-\d+)?([a-z]*)")


def fetch_grid(api_base, location):
    url = f"{api_base.rstrip('/')}/grid?location={location}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"ERROR: could not fetch {url}: {exc}", file=sys.stderr)
        sys.exit(2)
    rows = payload.get("skus") if isinstance(payload, dict) else payload
    if not rows:
        print(f"ERROR: {url} returned no sizes", file=sys.stderr)
        sys.exit(2)
    return rows


def feature_letters(sku_name):
    """Additive-feature segment of a VM size name ('' when unparsable)."""
    match = SKU_NAME_PARTS.match(sku_name)
    return match.group(3) if match else ""


def report(check_name, checked, violations, max_report):
    """Print one check's result. `checked` is printed so a check that silently
    inspected nothing cannot masquerade as a pass."""
    if not violations:
        print(f"  ok   {check_name} (checked {checked})")
        return 0
    print(f"  FAIL {check_name} (checked {checked}, {len(violations)} violations)")
    for line in violations[:max_report]:
        print(f"         {line}")
    if len(violations) > max_report:
        print(f"         ... and {len(violations) - max_report} more")
    return len(violations)


def main():
    parser = argparse.ArgumentParser(description="Validate published CPU vendors.")
    parser.add_argument("--location", default="eastus")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--max-report", type=int, default=15)
    args = parser.parse_args()

    print(f"Checking CPU vendor invariants for {args.location}...")
    rows = fetch_grid(args.api_base, args.location)
    print(f"  fetched {len(rows)} sizes from the live API\n")

    valid_checked = 0
    valid_bad = []
    gen_checked = 0
    gen_bad = []
    name_checked = 0
    name_bad = []
    arch_checked = 0
    arch_bad = []

    for row in rows:
        name = row.get("name") or "<unnamed>"
        vendor = row.get("cpuVendor")
        generation = row.get("cpuGeneration") or ""
        architecture = (row.get("architecture") or "").lower()

        # 1. Vendor is a known value.
        valid_checked += 1
        if vendor not in VALID_VENDORS:
            valid_bad.append(f"{name}: cpuVendor={vendor!r}")

        # 2. Vendor agrees with the published CPU generation.
        if generation:
            gen_checked += 1
            if AMD_GENERATION.search(generation):
                expected = "AMD"
            elif ARM_GENERATION.search(generation):
                expected = "ARM"
            else:
                expected = "Intel"
            if vendor != expected:
                gen_bad.append(
                    f"{name}: generation {generation!r} implies {expected}, published {vendor}"
                )

        # 3. The 'a' additive-feature letter means AMD. Arm sizes are excluded:
        #    architecture is authoritative and settles those independently.
        if architecture not in ("arm64", "arm"):
            features = feature_letters(name)
            if "a" in features:
                name_checked += 1
                if vendor != "AMD":
                    name_bad.append(
                        f"{name}: feature letters {features!r} contain 'a' (AMD), published {vendor}"
                    )

        # 4. Architecture and vendor agree about Arm.
        if architecture:
            arch_checked += 1
            is_arm_arch = architecture in ("arm64", "arm")
            if is_arm_arch and vendor != "ARM":
                arch_bad.append(f"{name}: architecture {architecture!r} but published {vendor}")
            elif not is_arm_arch and vendor == "ARM":
                arch_bad.append(f"{name}: published ARM but architecture is {architecture!r}")

    print("Results:")
    failures = 0
    failures += report("vendor values are Intel/AMD/ARM", valid_checked, valid_bad, args.max_report)
    failures += report("vendor matches CPU generation", gen_checked, gen_bad, args.max_report)
    failures += report("'a' feature letter implies AMD", name_checked, name_bad, args.max_report)
    failures += report("vendor matches architecture", arch_checked, arch_bad, args.max_report)

    # A check that inspected nothing is not a pass. The grid always contains AMD
    # sizes and at least one generation, so zeroes here mean the response shape
    # changed and the checks quietly stopped testing anything.
    if gen_checked == 0 or name_checked == 0:
        print(
            f"\nFAILED: checks inspected nothing (generation={gen_checked}, "
            f"name={name_checked}) -- the /grid response shape likely changed.",
            file=sys.stderr,
        )
        return 1

    if failures:
        print(f"\nFAILED: {failures} vendor invariant violation(s) in {args.location}.", file=sys.stderr)
        return 1

    print(f"\nPASSED: CPU vendors in {args.location} are self-consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
