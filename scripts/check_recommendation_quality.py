#!/usr/bin/env python3
"""
Guard the *ordering* quality of /api/compare_vms recommendations.

Why this exists
---------------
Spec similarity alone cannot answer "what should I move to?". When vCPU and
memory match the target exactly, every similarity term saturates at 100 and the
score collapses -- production once returned 189 candidates sharing only 18
distinct scores, with 24 tied at exactly 100.00 and ordering falling back to
alphabetical. That put a burstable v2 (Standard_B2as_v2) at #1 for a
general-purpose Standard_D2_v3, and buried the correct Standard_F4alds_v7 answer
at #8 for a Standard_F4s_v2 source.

The recommendation score fixes that by blending similarity with generation
currency and workload-family affinity. These assertions encode the properties
that fix depends on, so a future scoring change cannot silently regress them.

The family-relative modernization cases matter most: an absolute "v7 is best"
scale permanently punishes families that never shipped a v7 (L-series tops out
at v4, B-series at v2), which sends those sources to unrelated families.

Usage:
    python scripts/check_recommendation_quality.py [--location eastus] [--api-base URL]

Exit codes:
    0 = every ranking assertion held
    1 = at least one assertion failed
    2 = the check itself could not run
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api"
TIMEOUT = 120

# How many leading results each structural assertion inspects.
TOP_N = 5

# Minimum distinct recommendation scores required across the whole returned set.
# Guards the tie-collapse that made ordering alphabetical: before generation-aware
# scoring a request like Standard_D2_v3 returned 25 results sharing just 2 distinct
# scores. Measured over the full result set rather than the top 5, because a tie
# among same-family same-generation siblings (four D-series v7s all at 100) is
# legitimate -- those are equally good answers and cpuPerfScore orders them.
MIN_DISTINCT_SCORES = 4

# (source size, family its own recommendations must lead with)
#
# L and B are the family-relative regression cases: neither ships a generation
# close to v7, so an absolute modernization scale sends them cross-family.
FAMILY_CASES = [
    ("Standard_D2_v3", "D"),
    ("Standard_D4s_v3", "D"),
    ("Standard_E8s_v4", "E"),
    ("Standard_F4s_v2", "F"),
    ("Standard_L8s_v2", "L"),
    ("Standard_B2s", "B"),
]

# Families whose performance model differs enough that they must never lead the
# list for a source outside that family.
BURSTABLE_FAMILY = "B"


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


def _identity(sku_name):
    """Family letters and generation, mirroring _parse_sku_identity in the API.

    Deliberately re-implemented rather than imported: importing the API module
    would make this guard pass whenever the API is self-consistently wrong.
    """
    if not sku_name:
        return None, 1
    name = re.sub(r"^(Standard_|Basic_)", "", sku_name)
    name = re.sub(r"^([A-Z]+)\d+-\d+", r"\1", name)
    name = re.sub(r"(?:_(?![Vv]\d+$)[A-Za-z0-9]+)+(?=_[Vv]\d+$)", "", name)
    m = re.match(r"^([A-Z]+)[0-9]*([a-z]*)(?:_[Vv](\d+))?", name)
    if not m:
        return None, 1
    # Sizes with no explicit _v<n> are generation 1 -- Standard_M32ls really is
    # the original M-series.
    return m.group(1).upper(), int(m.group(3)) if m.group(3) else 1


def _fetch(base, location, sku_name, priority_mode="balanced"):
    return _request(
        "{}/compare_vms".format(base),
        {
            "skuName": sku_name,
            "location": location,
            "minSimilarityScore": 60,
            "maxResults": 25,
            "currencyCode": "USD",
            "priorityMode": priority_mode,
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--location", default="eastus")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    args = parser.parse_args()

    base = args.api_base.rstrip("/")
    loc = args.location

    print("Recommendation quality check against {} ({})\n".format(base, loc))

    failures = []
    checked = 0

    for sku_name, expected_family in FAMILY_CASES:
        status, body = _fetch(base, loc, sku_name)

        if status != 200 or not isinstance(body, dict):
            failures.append((sku_name, "HTTP {} -- {}".format(status, str(body)[:200])))
            print("  FAIL  {:<24} HTTP {}".format(sku_name, status))
            continue

        alternatives = body.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            failures.append((sku_name, "no alternatives returned"))
            print("  FAIL  {:<24} no alternatives".format(sku_name))
            continue

        top = alternatives[:TOP_N]
        first = top[0]
        first_name = first.get("name", "")
        first_family, first_version = _identity(first_name)
        source_family, source_version = _identity(sku_name)

        problems = []

        # 1. The response must actually carry the new ranking field. Without it
        #    the frontend silently falls back to similarity and the list looks
        #    mis-sorted relative to the score it displays.
        if first.get("recommendationScore") is None:
            problems.append("missing recommendationScore on results")

        # 2. Results must be sorted by recommendationScore descending.
        scores = [a.get("recommendationScore") for a in alternatives]
        if all(s is not None for s in scores):
            if scores != sorted(scores, reverse=True):
                problems.append("alternatives are not sorted by recommendationScore")

        # 3. A source's own family must lead. This is the family-relative
        #    modernization guard -- L and B have no near-v7 generation, so an
        #    absolute scale sends them to E/F instead.
        if first_family != expected_family:
            problems.append(
                "#1 is {} (family {}), expected family {}".format(
                    first_name, first_family, expected_family
                )
            )

        # 4. Never lead with a burstable size for a non-burstable source -- the
        #    CPU-credit model is a different performance contract entirely.
        if source_family != BURSTABLE_FAMILY and first_family == BURSTABLE_FAMILY:
            problems.append("#1 is burstable ({}) for a non-burstable source".format(first_name))

        # 5. Never lead with an older generation than the source.
        if first_version < source_version:
            problems.append(
                "#1 {} is v{}, older than source v{}".format(
                    first_name, first_version, source_version
                )
            )

        # 6. Never lead with a size that is retired or capacity-limited -- we
        #    would be steering users onto a dead end.
        if first.get("retirementStatus") in ("Announced", "Retired"):
            problems.append("#1 {} is {}".format(first_name, first.get("retirementStatus")))
        if first.get("growthRestricted"):
            problems.append("#1 {} is growth-restricted".format(first_name))

        # 7. The list as a whole must not be a flat tie broken alphabetically.
        distinct = len({s for s in scores if s is not None})
        if distinct < MIN_DISTINCT_SCORES:
            problems.append(
                "only {} distinct scores across {} results (expected >= {})".format(
                    distinct, len(scores), MIN_DISTINCT_SCORES
                )
            )

        checked += 1

        if problems:
            failures.append((sku_name, "; ".join(problems)))
            print("  FAIL  {:<24} -> {}".format(sku_name, first_name))
            for p in problems:
                print("          {}".format(p))
        else:
            print(
                "  ok    {:<24} -> {:<24} score={}".format(
                    sku_name, first_name, first.get("recommendationScore")
                )
            )

    # Cost mode has to actually change something, or the toggle is decoration.
    cost_probe = "Standard_D4s_v3"
    status_b, body_b = _fetch(base, loc, cost_probe, "balanced")
    status_c, body_c = _fetch(base, loc, cost_probe, "cost")
    if status_b == 200 and status_c == 200 and isinstance(body_b, dict) and isinstance(body_c, dict):
        checked += 1
        names_b = [a.get("name") for a in (body_b.get("alternatives") or [])[:TOP_N]]
        names_c = [a.get("name") for a in (body_c.get("alternatives") or [])[:TOP_N]]
        if names_b and names_b == names_c:
            failures.append(("priorityMode=cost", "cost mode returned an identical top {}".format(TOP_N)))
            print("  FAIL  {:<24} cost mode changed nothing".format("priorityMode"))
        else:
            print("  ok    {:<24} cost mode reorders results".format("priorityMode"))
    else:
        failures.append(("priorityMode=cost", "probe request failed"))
        print("  FAIL  {:<24} probe request failed".format("priorityMode"))

    # The migration-effort block drives the "before you migrate" panel; if it
    # disappears the panel silently renders empty.
    status_e, body_e = _fetch(base, loc, "Standard_D2_v3")
    if status_e == 200 and isinstance(body_e, dict):
        checked += 1
        effort = body_e.get("migrationEffort")
        if not isinstance(effort, dict) or not effort.get("level"):
            failures.append(("migrationEffort", "missing or malformed on the response root"))
            print("  FAIL  {:<24} missing migrationEffort".format("migrationEffort"))
        else:
            print("  ok    {:<24} level={}".format("migrationEffort", effort.get("level")))

    print("\n  checked={}".format(checked))

    if failures:
        print("\nFAILED: {} of {} assertions did not hold.\n".format(len(failures), checked))
        for label, detail in failures:
            print("  {}\n    {}\n".format(label, detail))
        return 1

    print("\nPASSED: all {} ranking assertions held.".format(checked))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
