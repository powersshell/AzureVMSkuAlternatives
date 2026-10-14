#!/usr/bin/env python3
"""Compare every published temporary-disk flag with Azure's Compute SKU data."""

import argparse
import math
import sys
import urllib.parse

from check_catalog_coverage import (
    API_BASE, ARM_BASE, SKUS_API_VERSION, fetch_json, get_arm_token, get_subscription_id,
)


def expected_presence(sku):
    caps = {c['name'].lower(): c['value'] for c in sku.get('capabilities', [])}
    sizes = {}
    for key in ('maxresourcevolumemb', 'nvmedisksizeinmib'):
        if key not in caps:
            continue
        try:
            size = float(caps[key])
        except (ValueError, TypeError):
            sizes[key] = None
        else:
            sizes[key] = size if math.isfinite(size) and size >= 0 else None
    if any(value is not None and value > 0 for value in sizes.values()):
        return True
    if sizes.get('maxresourcevolumemb') == 0 and (
        'nvmedisksizeinmib' not in caps or sizes.get('nvmedisksizeinmib') == 0
    ):
        return False
    return None


def check_region(location, subscription, token):
    query = urllib.parse.quote(f"location eq '{location}'")
    url = (
        f"{ARM_BASE}/subscriptions/{subscription}/providers/Microsoft.Compute/skus"
        f"?api-version={SKUS_API_VERSION}&$filter={query}"
    )
    expected = {}
    while url:
        data = fetch_json(url, headers={'Authorization': f'Bearer {token}'})
        for sku in data.get('value', []):
            if sku.get('resourceType') == 'virtualMachines' and not sku['name'].endswith('_Promo'):
                expected[sku['name']] = expected_presence(sku)
        url = data.get('nextLink')
    if not expected:
        raise RuntimeError(f'{location}: Compute returned no VM sizes')
    grid = fetch_json(f'{API_BASE}/grid?location={urllib.parse.quote(location)}')
    served = {row['name']: row for row in grid.get('skus', [])}
    failures = []
    for name, value in expected.items():
        row = served.get(name)
        if row is None or 'hasTempDisk' not in row or row['hasTempDisk'] is not value:
            actual = '<missing>' if row is None else row.get('hasTempDisk', '<missing>')
            failures.append(f'{name}: expected {value!r}, served {actual!r}')
    present = sum(value is True for value in expected.values())
    absent = sum(value is False for value in expected.values())
    unknown = len(expected) - present - absent
    print(f'{location}: checked={len(expected)}, present={present}, absent={absent}, '
          f'unknown={unknown}, mismatches={len(failures)}', flush=True)
    if not present or not absent:
        failures.append('No known present/absent sizes checked; cannot validate both cases')
    for failure in failures[:15]:
        print(f'  FAIL {failure}', flush=True)
    return len(failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--locations', nargs='+', default=['eastus', 'westeurope'])
    args = parser.parse_args()
    token, subscription = get_arm_token(), get_subscription_id()
    if not token or not subscription:
        print('ERROR: Azure credentials unavailable; use the existing OIDC workflow or az login.')
        return 2
    failures = 0
    for location in args.locations:
        try:
            failures += check_region(location, subscription, token)
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            print(f'ERROR: {location}: metadata verification could not complete: {exc}', flush=True)
            return 2
    print(f'Checked {len(args.locations)} regions: {failures} metadata failures.', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
