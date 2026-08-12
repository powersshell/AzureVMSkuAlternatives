#!/usr/bin/env python3
"""
Constrained-vCPU accuracy invariant check.

Guards the vCPU count published for constrained-vCPU VM sizes.

Background: Azure's Microsoft.Compute/skus API publishes two separate
capabilities:

  * ``vCPUs``          -- the physical core count of the underlying parent size
  * ``vCPUsAvailable`` -- the number of cores actually usable by the guest OS

For ordinary sizes these are identical. For the 246 *constrained* sizes in
eastus (Standard_E16-4s_v5, Standard_FX48-12ms_v2, Standard_HB368-48rs_v5,
Standard_DS14-8_v2, ...) they differ, and the difference is the entire point of
the size: a constrained size deliberately exposes fewer cores while keeping the
parent's memory, disk and network throughput, so software licensed per-core
(SQL Server, Oracle) costs proportionally less to run.

Until 2026-08-12 the API read ``vCPUs``, so every constrained size reported its
parent's full core count -- Standard_E16-4s_v5 published 16 vCPUs instead of 4.
That is wrong three times over: the displayed spec is wrong, the cost-per-vCPU
metric derived from it is wrong, and the similarity ranking matches candidates
on vCPU count, so a constrained size was scored as though it were its parent.

A wrong-but-plausible integer never raises, which is why this needs an
invariant check rather than a test of the extraction code (re-implementing the
extraction would just duplicate any bug in it).

The truth source here is deliberately *independent* of the Azure capability
payload: Azure's documented naming grammar is

    [Family][#vCPUs][-#ConstrainedVCPUs][AdditiveFeatures]_[Version]

so for any size whose name contains the ``-N`` segment, N *is* the usable vCPU
count by definition. Verified against the eastus catalog on 2026-08-12:
``vCPUsAvailable`` matched the name for 246 of 246 constrained sizes.

Note this checks only the constrained ``-N`` segment. Neither the leading number
nor the constrained number can be compared to anything else, because legacy
names such as Standard_D11 / Standard_D3 / Standard_GS5 use a series index
rather than a core count (Standard_D11 has 2 vCPUs, Standard_GS5 has 32). So
"leading number == vCPUs" would produce 52 false positives in eastus, and
"constrained count <= leading number" would wrongly flag Standard_GS5-8.
Only the ``-N`` segment is defined as a real core count.

Checks performed:
  1. Every size publishes a positive vCPU count.
  2. Every constrained size ("-N" in the name) publishes exactly N vCPUs.

Usage:
    python scripts/check_vcpu_consistency.py [--location eastus]
                                             [--api-base URL]
                                             [--max-report N]

Exits non-zero if any invariant is violated, so it can gate CI.
"""

import argparse
import json
import sys
import re
import urllib.error
import urllib.request

DEFAULT_API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api"
USER_AGENT = "AzureVMSkuAlternatives-vcpu-check/1.0"

# [Family][#vCPUs][-#ConstrainedVCPUs][AdditiveFeatures]_[Version].
# Family letters are uppercase and feature letters lowercase, so this is
# deliberately case-sensitive -- a case-insensitive match lets the family group
# swallow the feature letters.
CONSTRAINED_NAME = re.compile(r"^(?:Standard|Basic)_([A-Z]+)(\d+)-(\d+)([a-z]*)")


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
    parser = argparse.ArgumentParser(description="Validate published vCPU counts.")
    parser.add_argument("--location", default="eastus")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--max-report", type=int, default=15)
    args = parser.parse_args()

    print(f"Checking vCPU invariants for {args.location}...")
    rows = fetch_grid(args.api_base, args.location)
    print(f"  fetched {len(rows)} sizes from the live API\n")

    positive_checked = 0
    positive_bad = []
    constrained_checked = 0
    constrained_bad = []

    for row in rows:
        name = row.get("name") or "<unnamed>"
        vcpus = row.get("vCPUs")

        # 1. A published size always has cores.
        positive_checked += 1
        if not isinstance(vcpus, (int, float)) or vcpus <= 0:
            positive_bad.append(f"{name}: vCPUs={vcpus!r}")
            continue

        match = CONSTRAINED_NAME.match(name)
        if not match:
            continue
        parent_cores = int(match.group(2))
        constrained_cores = int(match.group(3))

        # 2. The name states the usable core count for constrained sizes.
        constrained_checked += 1
        if int(vcpus) != constrained_cores:
            constrained_bad.append(
                f"{name}: name declares {constrained_cores} usable vCPUs, published {int(vcpus)}"
                + (
                    " (this is the parent's core count -- reading 'vCPUs' instead of 'vCPUsAvailable')"
                    if int(vcpus) == parent_cores
                    else ""
                )
            )

    print("Results:")
    failures = 0
    failures += report("every size has a positive vCPU count", positive_checked, positive_bad, args.max_report)
    failures += report("constrained sizes match their name", constrained_checked, constrained_bad, args.max_report)

    # A check that inspected nothing is not a pass. Every Azure region publishes
    # constrained sizes, so a zero here means the /grid response shape changed
    # and the check quietly stopped testing anything.
    if constrained_checked == 0:
        print(
            "\nFAILED: no constrained sizes were inspected -- the /grid response "
            "shape or the 'vCPUs' field name likely changed.",
            file=sys.stderr,
        )
        return 1

    if failures:
        print(f"\nFAILED: {failures} vCPU invariant violation(s) in {args.location}.", file=sys.stderr)
        return 1

    print(f"\nPASSED: vCPU counts in {args.location} are consistent with Azure's naming grammar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
