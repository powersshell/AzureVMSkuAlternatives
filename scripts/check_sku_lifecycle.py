#!/usr/bin/env python3
"""
SKU lifecycle data reconciliation check.

Guards the two hand-maintained lifecycle tables that drive ranking penalties,
badges, banners and filters across all three surfaces (web API, MCP server,
PowerShell script):

  * VM_RETIREMENT_INFO          -- "you must leave this size"
  * VM_GROWTH_RESTRICTION_INFO  -- "you may stay, but you cannot grow"

The two signals are ORTHOGONAL and both are permanent once published. When a
capacity-limited series is later announced for retirement you ADD a retirement
entry and LEAVE the growth-restriction entry in place -- see
docs/sku-lifecycle-runbook.md.

Checks performed:
  1. Python <-> PowerShell table parity (patterns must match exactly).
  2. Within-series retirement coverage consistency -- catches regex gaps such as
     constrained-vCPU variants (Standard_DS11-1_v2) being missed by a pattern
     that only allows Standard_DS<n>_v2.
  3. Unreachable patterns (shadowed by an earlier first-match-wins entry).
  4. Overlap census (how many SKUs carry both signals).
  5. Optional: documentation URL liveness (--check-urls).

Usage:
    python scripts/check_sku_lifecycle.py [--location eastus] [--check-urls]

Exits non-zero if any consistency problem is found, so it can gate CI.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
API_FILE = REPO_ROOT / "web-app" / "api" / "function_app.py"
PS_FILE = REPO_ROOT / "powershell-script" / "Compare-AzureVms.ps1"
SKUS_ENDPOINT = "https://vmsku-api-func-cus.azurewebsites.net/api/skus?location={location}"

PY_TABLES = ("VM_RETIREMENT_INFO", "VM_GROWTH_RESTRICTION_INFO")
PS_TABLES = ("VmRetirementInfo", "VmGrowthRestrictionInfo")


# ---------------------------------------------------------------- extraction


def load_python_tables() -> Dict[str, List[Dict]]:
    """Parse the two lifecycle tables out of function_app.py without importing it."""
    tree = ast.parse(API_FILE.read_text(encoding="utf-8"))
    tables: Dict[str, List[Dict]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in PY_TABLES:
                tables[target.id] = ast.literal_eval(node.value)
    missing = [t for t in PY_TABLES if t not in tables]
    if missing:
        raise SystemExit(f"ERROR: could not find {', '.join(missing)} in {API_FILE}")
    return tables


def load_powershell_patterns() -> Dict[str, List[str]]:
    """
    Extract just the Pattern values from the PowerShell mirror tables.

    The tables are `$script:<Name> = @( @{ Pattern = '...'; ... } ... )`, so we
    slice from each table header to the next one and pull the Pattern strings in
    document order.
    """
    text = PS_FILE.read_text(encoding="utf-8")
    starts: Dict[str, int] = {}
    for name in PS_TABLES:
        match = re.search(rf"\$script:{name}\s*=\s*@\(", text)
        if not match:
            raise SystemExit(f"ERROR: could not find $script:{name} in {PS_FILE}")
        starts[name] = match.end()

    ordered = sorted(starts.items(), key=lambda kv: kv[1])
    result: Dict[str, List[str]] = {}
    for index, (name, start) in enumerate(ordered):
        end = ordered[index + 1][1] if index + 1 < len(ordered) else len(text)
        segment = text[start:end]
        result[name] = re.findall(r"Pattern\s*=\s*'([^']+)'", segment)
    return result


def fetch_sku_names(location: str) -> List[str]:
    """Pull the live SKU catalogue so checks run against real size names."""
    url = SKUS_ENDPOINT.format(location=location)
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  !    could not fetch SKU list ({exc}); skipping catalogue checks")
        return []

    raw = payload.get("skus", payload if isinstance(payload, list) else [])
    names = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    return names


def match_entry(table: List[Dict], name: str) -> Optional[Dict]:
    """First-match-wins lookup, mirroring the runtime helpers."""
    for entry in table:
        if re.match(entry["pattern"], name):
            return entry
    return None


# ------------------------------------------------------------------- checks


def check_parity(py_tables: Dict[str, List[Dict]], ps_patterns: Dict[str, List[str]]) -> List[str]:
    """Python and PowerShell must carry identical pattern lists, in the same order."""
    problems: List[str] = []
    for py_name, ps_name in zip(PY_TABLES, PS_TABLES):
        py_list = [e["pattern"] for e in py_tables[py_name]]
        ps_list = ps_patterns[ps_name]
        if py_list == ps_list:
            print(f"  ok   {py_name} <-> ${ps_name}: {len(py_list)} patterns identical")
            continue

        problems.append(f"{py_name} and ${ps_name} are out of sync")
        only_py = [p for p in py_list if p not in ps_list]
        only_ps = [p for p in ps_list if p not in py_list]
        print(f"  FAIL {py_name} <-> ${ps_name}: {len(py_list)} vs {len(ps_list)} patterns")
        for pattern in only_py:
            print(f"         only in Python     : {pattern}")
        for pattern in only_ps:
            print(f"         only in PowerShell : {pattern}")
        if not only_py and not only_ps:
            print("         same set, different order (first-match-wins may diverge)")
    return problems


def check_series_consistency(py_tables: Dict[str, List[Dict]], names: List[str]) -> List[str]:
    """
    Within a growth-restricted series, retirement flagging must be all-or-nothing.

    A series where some sizes are retirement-flagged and others are not almost
    always means a regex missed a variant (constrained vCPU, suffix, etc.).
    """
    problems: List[str] = []
    retirement = py_tables["VM_RETIREMENT_INFO"]
    growth = py_tables["VM_GROWTH_RESTRICTION_INFO"]

    buckets: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: {"retiring": [], "not": []})
    for name in names:
        entry = match_entry(growth, name)
        if not entry:
            continue
        key = "retiring" if match_entry(retirement, name) else "not"
        buckets[entry["series"]][key].append(name)

    for series, sizes in sorted(buckets.items()):
        if sizes["retiring"] and sizes["not"]:
            problems.append(f"series {series} has inconsistent retirement coverage")
            print(f"  FAIL series {series}: {len(sizes['retiring'])} retiring-flagged, "
                  f"{len(sizes['not'])} not flagged")
            print(f"         unflagged: {', '.join(sorted(sizes['not'])[:10])}")

    if not problems:
        print(f"  ok   retirement coverage consistent across {len(buckets)} restricted series")
    return problems


def check_unreachable(py_tables: Dict[str, List[Dict]], names: List[str]) -> List[str]:
    """Flag patterns that can never win because an earlier pattern always shadows them."""
    problems: List[str] = []
    for table_name, table in py_tables.items():
        winners = set()
        for name in names:
            for index, entry in enumerate(table):
                if re.match(entry["pattern"], name):
                    winners.add(index)
                    break
        dead = [
            table[i]["pattern"]
            for i in range(len(table))
            if i not in winners and any(re.match(table[i]["pattern"], n) for n in names)
        ]
        for pattern in dead:
            problems.append(f"{table_name} pattern shadowed: {pattern}")
            print(f"  FAIL {table_name}: pattern never wins (shadowed): {pattern}")
        if not dead:
            print(f"  ok   {table_name}: no shadowed patterns")
    return problems


def report_overlap(py_tables: Dict[str, List[Dict]], names: List[str], location: str) -> None:
    """Informational census of the dual state -- not a failure condition."""
    retirement = py_tables["VM_RETIREMENT_INFO"]
    growth = py_tables["VM_GROWTH_RESTRICTION_INFO"]

    both: List[str] = []
    retiring_only: List[str] = []
    restricted_only: List[str] = []
    for name in names:
        has_retirement = match_entry(retirement, name) is not None
        has_growth = match_entry(growth, name) is not None
        if has_retirement and has_growth:
            both.append(name)
        elif has_retirement:
            retiring_only.append(name)
        elif has_growth:
            restricted_only.append(name)

    dual_series = sorted({match_entry(growth, n)["series"] for n in both})
    print(f"  {len(names)} SKUs evaluated in {location}")
    print(f"    retiring only          : {len(retiring_only)}")
    print(f"    growth-restricted only : {len(restricted_only)}")
    print(f"    BOTH (dual state)      : {len(both)} across {len(dual_series)} series")
    if dual_series:
        print(f"      {', '.join(dual_series)}")


def check_urls(py_tables: Dict[str, List[Dict]]) -> List[str]:
    """Verify every referenced Microsoft Learn document still resolves."""
    problems: List[str] = []
    urls = {e["migrationGuideUrl"] for e in py_tables["VM_RETIREMENT_INFO"]}
    doc_match = re.search(
        r"GROWTH_RESTRICTION_DOC_URL\s*=\s*'([^']+)'",
        API_FILE.read_text(encoding="utf-8"),
    )
    if doc_match:
        urls.add(doc_match.group(1))

    for url in sorted(urls):
        request = urllib.request.Request(url, headers={"User-Agent": "sku-lifecycle-check"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
            if status >= 400:
                problems.append(f"doc URL returned {status}: {url}")
                print(f"  FAIL {status} {url}")
            else:
                print(f"  ok   {status} {url}")
        except urllib.error.HTTPError as exc:
            problems.append(f"doc URL returned {exc.code}: {url}")
            print(f"  FAIL {exc.code} {url}")
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  !    unreachable ({exc}); not treated as a failure: {url}")
    return problems


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile VM SKU lifecycle data tables.")
    parser.add_argument("--location", default="eastus",
                        help="Azure region whose SKU catalogue is used for checks (default: eastus)")
    parser.add_argument("--check-urls", action="store_true",
                        help="Also verify that documentation URLs still resolve")
    args = parser.parse_args()

    py_tables = load_python_tables()
    ps_patterns = load_powershell_patterns()
    problems: List[str] = []

    print("\n== Python <-> PowerShell parity ==")
    problems += check_parity(py_tables, ps_patterns)

    print(f"\n== SKU catalogue ({args.location}) ==")
    names = fetch_sku_names(args.location)

    if names:
        print("\n== Retirement coverage consistency ==")
        problems += check_series_consistency(py_tables, names)

        print("\n== Pattern reachability ==")
        problems += check_unreachable(py_tables, names)

        print("\n== Lifecycle overlap census ==")
        report_overlap(py_tables, names, args.location)

    if args.check_urls:
        print("\n== Documentation URLs ==")
        problems += check_urls(py_tables)

    print()
    if problems:
        print(f"FAILED: {len(problems)} problem(s) found")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("PASSED: SKU lifecycle data is consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
