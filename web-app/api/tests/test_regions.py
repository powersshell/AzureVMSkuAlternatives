"""Public region discovery, persistence and independent reconciliation regressions."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import region_catalog as catalog

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
from check_region_coverage import check_coverage

pytestmark = pytest.mark.unit


HTML = '''
<table><tr><th>Region</th><th>Paired region</th><th>Programmatic name</th></tr>
<tr><td>Belgium Central</td><td>N/A</td><td>belgiumcentral</td></tr>
<tr><td><img alt="Access to this region is restricted">Restricted North</td>
<td><img alt="This region is coming soon">A paired region</td><td>restrictednorth</td></tr>
<tr><td><img alt="This region is coming soon">Future East</td><td>N/A</td><td>futureeast</td></tr>
</table>
<table><tr><th>Region</th><th>Programmatic name</th></tr>
<tr><td>Ignore repeated tab</td><td>ignored</td></tr></table>
'''


class Table:
    def __init__(self):
        self.rows = {}

    def query_entities(self, query_filter):
        partition = query_filter.split("'")[1]
        return [dict(row) for (pk, _), row in self.rows.items() if pk == partition]

    def upsert_entity(self, row):
        key = row['PartitionKey'], row['RowKey']
        self.rows.setdefault(key, {}).update(row)


def response(data=None, text='', status=200):
    return SimpleNamespace(
        ok=status == 200, status_code=status, headers={}, text=text, json=lambda: data,
        raise_for_status=lambda: None,
    )


def test_published_inventory_distinguishes_restricted_and_announced():
    rows = catalog.published_regions(HTML)
    assert set(rows) == {'belgiumcentral', 'restrictednorth', 'futureeast'}
    assert rows['restrictednorth']['restricted']
    assert rows['restrictednorth']['lifecycle'] == 'active'
    assert rows['futureeast']['lifecycle'] == 'announced'
    with pytest.raises(RuntimeError, match='missing or incomplete'):
        catalog.published_regions('<table><tr><td>No inventory</td></tr></table>')
    with pytest.raises(RuntimeError, match='missing or incomplete'):
        catalog.published_regions(HTML.split('</table>')[0])


def test_discovery_unions_published_and_arm_without_recommended_filter(monkeypatch):
    locations = [
        {'name': 'belgiumcentral', 'displayName': 'Belgium Central',
         'metadata': {'regionType': 'Physical', 'regionCategory': 'Recommended'}},
        {'name': 'brandnewwest', 'displayName': 'Brand New West',
         'metadata': {'regionType': 'Physical', 'regionCategory': 'Other'}},
        {'name': 'global', 'metadata': {'regionType': 'Logical'}},
        {'name': 'eastus2euap', 'metadata': {'regionType': 'Physical'}},
        {'name': 'eastusstg', 'metadata': {'regionType': 'Physical'}},
        {'name': 'futureeast', 'displayName': 'Future East',
         'metadata': {'regionType': 'Physical'}},
    ]
    monkeypatch.setattr(catalog.requests, 'get', lambda url, **kw:
                        response(text=HTML) if url == catalog.REGIONS_URL
                        else response({'value': locations}))
    rows = {r['name']: r for r in catalog.discover_public_regions('subscription', 'token')}
    assert set(rows) == {'belgiumcentral', 'brandnewwest', 'restrictednorth', 'futureeast'}
    assert rows['brandnewwest']['published'] is False
    assert rows['restrictednorth']['armVisible'] is False
    assert rows['belgiumcentral']['armVisible'] is True
    assert rows['futureeast']['lifecycle'] == 'active'


def test_arm_pagination_complete_or_error_and_credentials_stay_on_origin(monkeypatch):
    urls = []
    start = 'https://management.azure.com/first'
    continuation = 'https://management.azure.com/second'

    def get(url, headers, **kwargs):
        urls.append(url)
        assert headers == {'Authorization': 'Bearer token'}
        return response({'value': [1], 'nextLink': continuation}) if url == start else response({'value': [2]})

    monkeypatch.setattr(catalog.requests, 'get', get)
    assert catalog.arm_collection(start, 'token') == [1, 2]
    assert urls == [start, continuation]
    for next_link, expected_error in [
        (start, 'Repeated'), ('https://untrusted.invalid/page', 'origin'),
    ]:
        monkeypatch.setattr(catalog.requests, 'get', lambda *a, **k:
                            response({'value': [1], 'nextLink': next_link}))
        with pytest.raises(RuntimeError, match=expected_error):
            catalog.arm_collection(start, 'token')
    monkeypatch.setattr(catalog.requests, 'get', lambda url, **kw:
                        response({'value': [1], 'nextLink': continuation})
                        if url == start else response(status=403))
    with pytest.raises(RuntimeError, match='HTTP 403'):
        catalog.arm_collection(start, 'token')


@pytest.mark.parametrize('skus, active', [
    ([], False),
    ([{'resourceType': 'disks', 'name': 'disk', 'locations': ['futureeast']}], False),
    ([{'resourceType': 'virtualMachines', 'name': 'Standard_D2s_v5',
       'locations': ['belgiumcentral']}], False),
    ([{'resourceType': 'virtualMachines', 'name': 'Standard_D2s_v5',
       'locations': ['futureeast']}], True),
    ([{'resourceType': 'virtualMachines', 'name': 'Standard_D2s_v5',
       'locations': ['FutureEast']}], True),
])
def test_announced_region_uses_compute_when_arm_location_is_not_visible(monkeypatch, skus, active):
    def get(url, **kwargs):
        if url == catalog.REGIONS_URL:
            return response(text=HTML)
        if '/providers/Microsoft.Compute/skus?' in url:
            assert 'futureeast' in url
            return response({'value': skus})
        return response({'value': [
            {'name': 'belgiumcentral', 'displayName': 'Belgium Central',
             'metadata': {'regionType': 'Physical'}},
        ]})

    monkeypatch.setattr(catalog.requests, 'get', get)
    rows = {r['name']: r for r in catalog.discover_public_regions('subscription', 'token')}
    future = rows['futureeast']
    assert future['lifecycle'] == ('active' if active else 'announced')
    assert future['armVisible'] is False
    assert bool(future.get('computeVisible')) is active


def test_announced_region_compute_probe_requires_complete_pagination(monkeypatch):
    def get(url, **kwargs):
        if url == catalog.REGIONS_URL:
            return response(text=HTML)
        if '/providers/Microsoft.Compute/skus?' in url:
            return response({'value': [
                {'resourceType': 'virtualMachines', 'name': 'Standard_D2s_v5',
                 'locations': ['futureeast']},
            ], 'nextLink': 'https://management.azure.com/next-skus'})
        if url.endswith('/next-skus'):
            return response(status=403)
        return response({'value': [
            {'name': 'belgiumcentral', 'displayName': 'Belgium Central',
             'metadata': {'regionType': 'Physical'}},
        ]})

    monkeypatch.setattr(catalog.requests, 'get', get)
    with pytest.raises(RuntimeError, match='HTTP 403'):
        catalog.discover_public_regions('subscription', 'token')


def test_new_regions_bootstrap_and_discovery_failure_preserves_catalog(monkeypatch):
    table = Table()
    discovered = list(catalog.published_regions(HTML).values())
    monkeypatch.setattr(catalog, 'discover_public_regions', lambda *args: discovered)
    initial = catalog.sync_catalog(table, 'subscription', 'token')
    assert set(catalog.refresh_targets(initial, True)) == {'belgiumcentral', 'restrictednorth'}
    catalog.record_refresh(table, 'belgiumcentral', coverage={'totalSkus': 2, 'paygLinux': 1})
    ready = catalog.read_catalog(table)
    assert catalog.refresh_targets(ready, True) == ['restrictednorth']
    assert set(catalog.refresh_targets(ready)) == {'belgiumcentral', 'restrictednorth'}
    previous_success = ready['discoveredAt']

    def fail(*args):
        raise RuntimeError('upstream unavailable')

    monkeypatch.setattr(catalog, 'discover_public_regions', fail)
    with pytest.raises(RuntimeError, match='upstream unavailable'):
        catalog.sync_catalog(table, 'subscription', 'token')
    preserved = catalog.read_catalog(table)
    assert preserved['discoveredAt'] == previous_success
    assert preserved['discoveryError'] == 'upstream unavailable'
    assert len(preserved['regions']) == 3
    catalog.record_refresh(table, 'belgiumcentral', error=RuntimeError('refresh failed'))
    failed = next(r for r in catalog.read_catalog(table)['regions'] if r['name'] == 'belgiumcentral')
    assert failed['dataStatus'] == 'failed' and failed['skuCount'] == 2
    assert failed['lastSuccessfulRefresh']


def test_disappearing_region_is_retained_and_uninitialized_catalog_fails(monkeypatch):
    table = Table()
    with pytest.raises(RuntimeError, match='not been initialized'):
        catalog.read_catalog(table)
    discovered = list(catalog.published_regions(HTML).values())
    monkeypatch.setattr(catalog, 'discover_public_regions', lambda *args: discovered)
    catalog.sync_catalog(table, 'subscription', 'token')
    monkeypatch.setattr(catalog, 'discover_public_regions', lambda *args: discovered[:1])
    updated = catalog.sync_catalog(table, 'subscription', 'token')
    assert len(updated['regions']) == 3
    assert sum(bool(r.get('notSeen')) for r in updated['regions']) == 2
    snapshot = table.rows['public', 'catalog']
    snapshot['discoveredAt'] = '2020-01-01T00:00:00+00:00'
    assert catalog.read_catalog(table)['stale']


def test_independent_guard_detects_omitted_new_regions_and_incomplete_data():
    expected = list(catalog.published_regions(HTML).values())
    served = {'cloud': 'AzureCloud', 'count': 1, 'regions': [
        {'name': 'belgiumcentral', 'lifecycle': 'active', 'dataStatus': 'ready',
         'skuCount': 2, 'lastSuccessfulRefresh': catalog.utc_now()},
    ]}
    issues = check_coverage(expected, served)
    assert 'restrictednorth: missing from served region inventory' in issues
    assert 'futureeast: missing from served region inventory' in issues
    served['regions'][0]['dataStatus'] = 'pending'
    assert 'belgiumcentral: VM coverage is pending' in check_coverage(expected, served)
