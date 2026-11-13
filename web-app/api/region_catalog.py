"""Discover public Azure regions without a hand-maintained regional allowlist."""

import json
import logging
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests

ARM_BASE = 'https://management.azure.com'
REGIONS_URL = 'https://learn.microsoft.com/en-us/azure/reliability/regions-list'
REGION_TABLE = 'vmskuregions'
DISCOVERY_MAX_AGE_HOURS = 8


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def arm_collection(url, token):
    """Read every page, rejecting truncated, cyclic or off-origin continuations."""
    items, seen = [], set()
    while url:
        if urlsplit(url).scheme != 'https' or urlsplit(url).netloc != 'management.azure.com':
            raise RuntimeError('Unexpected ARM pagination origin')
        if url in seen:
            raise RuntimeError('Repeated ARM pagination link')
        seen.add(url)
        for attempt in range(3):
            response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=60)
            if response.ok or response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                break
            logging.warning('ARM request throttled or unavailable (%s); retrying', response.status_code)
            delay = response.headers.get('Retry-After', '')
            time.sleep(min(int(delay), 60) if delay.isdigit() else 2 ** (attempt + 1))
        if not response.ok:
            raise RuntimeError(f'ARM inventory request failed: HTTP {response.status_code}')
        data = response.json()
        if not isinstance(data.get('value'), list):
            raise RuntimeError('ARM inventory response has no value array')
        items.extend(data['value'])
        url = data.get('nextLink')
    return items


class _RegionTableParser(HTMLParser):
    """Read the first programmatic-name table, not repeated geography tabs."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = []
        self.cell = None
        self.table_depth = 0
        self.matches = False
        self.finished = False

    def handle_starttag(self, tag, attrs):
        if self.finished:
            return
        if tag == 'table':
            self.table_depth += 1
        if not self.table_depth:
            return
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th'):
            self.cell = {'text': '', 'icons': ''}
        elif tag == 'img' and self.cell is not None:
            self.cell['icons'] += ' ' + dict(attrs).get('alt', '')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data

    def handle_endtag(self, tag):
        if self.finished:
            return
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr' and self.row:
            headers = [cell['text'].strip().lower() for cell in self.row]
            if 'programmatic name' in headers:
                self.matches = True
                self.slug_column = headers.index('programmatic name')
                self.name_column = headers.index('region')
            elif self.matches:
                self.rows.append(self.row)
        elif tag == 'table':
            self.table_depth -= 1
            if self.matches:
                self.finished = True


def published_regions(html):
    parser = _RegionTableParser()
    parser.feed(html)
    if not parser.finished or not parser.rows:
        raise RuntimeError('Microsoft region inventory table is missing or incomplete')
    regions = {}
    for row in parser.rows:
        slug = row[parser.slug_column]['text'].strip()
        name = row[parser.name_column]
        if not re.fullmatch(r'[a-z][a-z0-9]+', slug):
            raise RuntimeError(f'Invalid published region identifier: {slug!r}')
        icons = name['icons'].lower()
        regions[slug] = {
            'name': slug,
            'displayName': name['text'].strip(),
            'lifecycle': 'announced' if 'coming soon' in icons else 'active',
            'restricted': 'restricted' in icons,
            'published': True,
            'armVisible': False,
        }
    return regions


def discover_public_regions(subscription_id, token):
    response = requests.get(REGIONS_URL, timeout=60)
    response.raise_for_status()
    regions = published_regions(response.text)
    locations = arm_collection(
        f'{ARM_BASE}/subscriptions/{subscription_id}/locations?api-version=2022-12-01',
        token,
    )
    physical = 0
    for location in locations:
        metadata = location.get('metadata') or {}
        if metadata.get('regionType', '').lower() != 'physical':
            continue
        slug = location['name']
        # ARM also exposes internal staging and Early Updates Access Program sites.
        if slug.endswith(('euap', 'stage', 'stg')):
            continue
        physical += 1
        if not re.fullmatch(r'[a-z][a-z0-9]+', slug):
            raise RuntimeError(f'Invalid ARM region identifier: {slug!r}')
        entry = regions.setdefault(slug, {
            'name': slug, 'displayName': location['displayName'],
            'lifecycle': 'active', 'restricted': False, 'published': False,
        })
        entry['armVisible'] = True
        entry['lifecycle'] = 'active'
    if not physical:
        raise RuntimeError('ARM returned no physical public regions')
    return sorted(regions.values(), key=lambda entry: entry['displayName'])


def read_catalog(table):
    rows = list(table.query_entities(query_filter="PartitionKey eq 'public'"))
    snapshot = next((row for row in rows if row['RowKey'] == 'catalog'), {})
    if not snapshot.get('regionsJson'):
        raise RuntimeError('Region catalog has not been initialized')
    statuses = {
        row['RowKey']: row
        for row in table.query_entities(query_filter="PartitionKey eq 'status'")
    }
    regions = []
    for entry in json.loads(snapshot['regionsJson']):
        status = statuses.get(entry['name'], {})
        regions.append({
            **entry,
            **{key: status[key] for key in (
                'dataStatus', 'skuCount', 'pricedSkuCount', 'lastRefreshAttempt',
                'lastSuccessfulRefresh', 'refreshError',
            ) if key in status},
        })
        regions[-1].setdefault(
            'dataStatus', 'announced' if entry['lifecycle'] == 'announced' else 'pending')
        refreshed_at = status.get('lastSuccessfulRefresh')
        regions[-1]['dataStale'] = bool(refreshed_at) and (
            datetime.now(timezone.utc) - datetime.fromisoformat(refreshed_at)
        ).total_seconds() > 27 * 3600
    discovered_at = snapshot['discoveredAt']
    age = datetime.now(timezone.utc) - datetime.fromisoformat(discovered_at)
    return {
        'cloud': 'AzureCloud',
        'discoveredAt': discovered_at,
        'lastDiscoveryAttempt': snapshot.get('lastDiscoveryAttempt', discovered_at),
        'discoveryError': snapshot.get('discoveryError') or None,
        'stale': age.total_seconds() > DISCOVERY_MAX_AGE_HOURS * 3600,
        'sources': [REGIONS_URL, f'{ARM_BASE}/subscriptions/{{subscriptionId}}/locations'],
        'count': len(regions),
        'regions': regions,
    }


def sync_catalog(table, subscription_id, token):
    attempted_at = utc_now()
    try:
        discovered = discover_public_regions(subscription_id, token)
        previous = list(table.query_entities(query_filter="PartitionKey eq 'public'"))
        snapshot = next((row for row in previous if row['RowKey'] == 'catalog'), {})
        old = json.loads(snapshot.get('regionsJson', '[]'))
        names = {entry['name'] for entry in discovered}
        missing = [dict(entry, notSeen=True) for entry in old if entry['name'] not in names]
        if missing:
            logging.error('Previously discovered regions absent from both sources: %s',
                          [entry['name'] for entry in missing])
        catalog_json = json.dumps(discovered + missing)
        if len(catalog_json.encode('utf-16-le')) > 64000:
            raise RuntimeError('Region catalog exceeds Table Storage property size limit')
        table.upsert_entity({
            'PartitionKey': 'public', 'RowKey': 'catalog', 'regionsJson': catalog_json,
            'discoveredAt': attempted_at, 'lastDiscoveryAttempt': attempted_at,
            'discoveryError': '',
        })
    except Exception as exc:
        logging.exception('Public region discovery failed; preserving the last successful catalog')
        table.upsert_entity({
            'PartitionKey': 'public', 'RowKey': 'catalog',
            'lastDiscoveryAttempt': attempted_at, 'discoveryError': str(exc)[:500],
        })
        raise
    return read_catalog(table)


def record_refresh(table, region, coverage=None, error=None):
    now = utc_now()
    status = {
        'PartitionKey': 'status', 'RowKey': region, 'lastRefreshAttempt': now,
        'dataStatus': 'failed', 'refreshError': str(error)[:500] if error else '',
    }
    if coverage is not None:
        total = coverage['totalSkus']
        status.update({
            'dataStatus': 'ready' if total else 'empty',
            'skuCount': total, 'pricedSkuCount': coverage.get('paygLinux', 0),
        })
        if total:
            status['lastSuccessfulRefresh'] = now
    table.upsert_entity(status)


def refresh_targets(catalog, bootstrap_only=False):
    return [
        region['name'] for region in catalog['regions']
        if region['lifecycle'] == 'active'
        and (not bootstrap_only or region['dataStatus'] in ('pending', 'failed', 'empty'))
    ]
