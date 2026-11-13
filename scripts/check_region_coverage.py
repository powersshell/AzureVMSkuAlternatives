#!/usr/bin/env python3
"""Reconcile the served public region inventory with independent live discovery."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web-app' / 'api'))

from region_catalog import discover_public_regions
from check_catalog_coverage import API_BASE, fetch_json, get_arm_token, get_subscription_id


def check_coverage(expected, served):
    problems = []
    if served.get('cloud') != 'AzureCloud':
        problems.append('API returned the wrong cloud')
    if served.get('stale') or served.get('discoveryError'):
        problems.append('Region discovery is stale or failed')
    rows = served.get('regions', [])
    actual = {region['name']: region for region in rows}
    if len(actual) != len(rows) or served.get('count') != len(rows):
        problems.append('Region catalog has duplicate identifiers or an incorrect count')
    for region in expected:
        name = region['name']
        row = actual.get(name)
        if row is None:
            problems.append(f'{name}: missing from served region inventory')
            continue
        if row.get('lifecycle') != region['lifecycle']:
            problems.append(f'{name}: lifecycle does not match live discovery')
        if region['lifecycle'] != 'active':
            continue
        if row.get('dataStatus') != 'ready' or not row.get('skuCount'):
            problems.append(f"{name}: VM coverage is {row.get('dataStatus', 'unknown')}")
        if row.get('dataStale') or not row.get('lastSuccessfulRefresh'):
            problems.append(f'{name}: no fresh successful VM refresh')
        if row.get('notSeen'):
            problems.append(f'{name}: region needs upstream reconciliation')
    expected_names = {region['name'] for region in expected}
    for name in actual.keys() - expected_names:
        problems.append(f'{name}: served region absent from both discovery sources')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover-only', action='store_true',
                        help='Read live sources without requiring a deployed region endpoint')
    parser.add_argument('--api-base', default=API_BASE)
    args = parser.parse_args()
    token, subscription = get_arm_token(), get_subscription_id()
    if not token or not subscription:
        print('ERROR: Azure read credentials are required for independent region discovery.')
        return 2
    try:
        expected = discover_public_regions(subscription, token)
        summary = {
            'count': len(expected),
            'active': sum(region['lifecycle'] == 'active' for region in expected),
            'regions': expected,
        }
        print(json.dumps(summary, indent=2))
        if args.discover_only:
            return 0
        served = fetch_json(f'{args.api_base.rstrip("/")}/regions')
        problems = check_coverage(expected, served)
        for problem in problems:
            print(f'FAIL: {problem}')
        print(f'Reconciled {len(expected)} regions: {len(problems)} coverage failures.')
        return 1 if problems else 0
    except Exception as exc:
        print(f'ERROR: region reconciliation could not complete: {exc}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
