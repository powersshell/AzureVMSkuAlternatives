"""Replay frozen catalog scoring through production capacity assessment/partitioning.

python scripts\\benchmark_capacity.py --evidence-dir <frozen folder> --output <session json>
The evidence directory is read-only. No network calls or Azure dependencies.
"""
import argparse
from collections import Counter
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import time


def check_routes(api, frozen, region, catalog, families):
    """Execute real compare_vms serialization against frozen normalized facts."""
    from types import SimpleNamespace
    from unittest.mock import patch
    import os

    class Response:
        def __init__(self, body, status_code=200, **kwargs):
            self.body, self.status_code = json.loads(body), status_code

    sources = [row for row in catalog if row['name'] in (
        'Standard_D4s_v3', 'Standard_D14_v2', 'Standard_E16-4ds_v5')]
    sources += [row for row in catalog if not any(
        other['name'] != row['name'] and oracle(row, other) for other in catalog)]
    checked = 0
    with patch.dict(os.environ, AZURE_SUBSCRIPTION_ID='offline'), \
            patch.object(api.func, 'HttpResponse', Response), \
            patch.object(api, 'get_access_token', return_value='offline'), \
            patch.object(api, 'get_vm_skus_with_cache', return_value=(catalog, 'frozen_fixture')), \
            patch.object(api, 'extract_capabilities', side_effect=lambda row: dict(row['_caps'])), \
            patch.object(api, 'get_vm_pricing', side_effect=lambda name, *args: next(
                row.get('pricing') for row in catalog if row['name'] == name)), \
            patch.object(api.requests, 'get', return_value=SimpleNamespace(
                ok=True, json=lambda: dict(Items=[]))):
        for source in sources:
            scored = frozen.score_source(source, catalog, families)
            for mode, generation in (('balanced', None), ('cost', 1), ('balanced', 2)):
                baseline = frozen.ordered(scored, mode, generation)
                expected = [row[0] for row in baseline if oracle(source, row[0])]
                small = [row[0] for row in baseline if not oracle(source, row[0])]
                for optional, cap in ((False, None), (True, 5), (True, 1)):
                    payload = frozen.payload(region, source['name'], mode, generation)
                    if optional:
                        payload['includeDownsizingOptions'] = True
                    if cap:
                        payload['maxResults'] = cap
                    response = api.compare_vms(SimpleNamespace(method='POST', get_json=lambda: payload))
                    assert response.status_code == 200, response.body
                    body = response.body
                    assert body['capacityRequirements']['state'] == 'assessed'
                    assert body['targetSku']['vCPUs'] == source['vCPUs']
                    assert body['totalMatches'] == len(expected)
                    assert [row['name'] for row in body['alternatives']] == [row['name'] for row in expected[:cap]]
                    assert all(oracle(source, row) for row in body['alternatives'])
                    if optional:
                        assert body['totalDownsizingMatches'] == len(small)
                        assert [row['name'] for row in body['downsizingAlternatives']] == [row['name'] for row in small[:cap]]
                    else:
                        assert 'downsizingAlternatives' not in body
                    checked += 1
    return checked


def oracle(source, candidate):
    for key in ('vCPUs', 'memoryGB'):
        left, right = source.get(key), candidate.get(key)
        if type(left) not in (int, float) or type(right) not in (int, float):
            return False
        left, right = Decimal(str(left)), Decimal(str(right))
        if not left.is_finite() or not right.is_finite() or min(left, right) <= 0 or right < left:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--routes-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'web-app' / 'api' / 'tests'))
    import test_unit  # Existing offline Azure stubs, not a network client.
    import function_app as api
    spec = importlib.util.spec_from_file_location('frozen_benchmark', args.evidence_dir / 'benchmark.py')
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    counts = Counter()
    warnings = {'baseline': Counter(), 'protected': Counter()}
    costs = {'baseline': [], 'protected': [], 'changePercent': []}
    cpu_ratios = {'baseline': [], 'protected': []}
    group_sizes = {'qualified': [], 'downsizing': []}
    distinct_names = set()
    no_matches = []
    regions = {}
    started = time.monotonic()
    for region in frozen.REGIONS:
        body = json.loads((args.evidence_dir / f'catalog-{region}.json').read_text(encoding='utf-8'))
        catalog = frozen.prepare(body)
        distinct_names.update(row['name'] for row in catalog)
        counts['catalogEntries'] += len(catalog)
        families = frozen.P['build_family_generation_map'](catalog)
        if args.routes_only:
            counts['mockedRouteResponses'] += check_routes(api, frozen, region, catalog, families)
            print(f'{region}: frozen route contracts verified', flush=True)
            continue
        counts['mockedRouteResponses'] += check_routes(api, frozen, region, catalog, families)
        rc = Counter()
        for source in catalog:
            requirements = api.capacity_requirements_for(source)
            assert requirements['state'] == 'assessed'
            scored = frozen.score_source(source, catalog, families)
            assessments = {}
            oracle_fits = {}
            for candidate, *_ in scored:
                assessment = api.assess_capacity(requirements, candidate)
                oracle_fits[candidate['name']] = oracle(source, candidate)
                assert (assessment['state'] == 'meets_minimums') == oracle_fits[candidate['name']]
                assessments[candidate['name']] = assessment
            decorated = {candidate['name']: dict(candidate, capacityAssessment=assessments[candidate['name']])
                         for candidate, *_ in scored}
            counts['pairs'] += len(scored)
            pool_counts = Counter(a['state'] for a in assessments.values())
            pool_counts = {key: pool_counts[key] for key in ('meets_minimums', 'below_minimums', 'unassessed')}
            for mode in ('balanced', 'cost'):
                for generation in (None, 1, 2):
                    baseline = frozen.ordered(scored, mode, generation)
                    ranked = [decorated[row[0]['name']] for row in baseline]
                    qualified, below, summary = api.partition_capacity_results(ranked, pool_counts, 5, True)
                    expected = [row for row in ranked if oracle_fits[row['name']]]
                    assert [row['name'] for row in qualified] == [row['name'] for row in expected[:5]]
                    assert all(not oracle(source, row) for row in below)
                    assert summary['meets_minimums']['passingSimilarity'] == len(expected)
                    assert summary['below_minimums']['passingSimilarity'] == len(ranked) - len(expected)
                    assert summary['unassessed']['assessedCandidates'] == 0
                    group_sizes['qualified'].append(len(expected))
                    group_sizes['downsizing'].append(len(ranked) - len(expected))
                    for target in (counts, rc):
                        target['configurations'] += 1
                        target['baselineFirstShortfalls'] += not oracle(source, baseline[0][0])
                        target['qualifiedSlots'] += len(qualified)
                        target['qualifiedPoolTotal'] += len(expected)
                        target['downsizingPoolTotal'] += len(ranked) - len(expected)
                        target['protectedFirstShortfalls'] += bool(qualified) and not oracle(source, qualified[0])
                        if oracle(source, baseline[0][0]):
                            assert qualified[0]['name'] == baseline[0][0]['name']
                            target['preservedAlreadyFitFirst'] += 1
                        if len(expected) >= 5:
                            target['atLeastFiveEligible'] += 1
                            target['protectedAllFiveFit'] += len(qualified) == 5 and all(oracle(source, row) for row in qualified)
                            target['baselineAllFiveFit'] += all(oracle(source, row[0]) for row in baseline[:5])
                    if not qualified:
                        counts['resourceNoMatch'] += 1
                        rc['resourceNoMatch'] += 1
                        no_matches.append(dict(region=region, source=source['name'], mode=mode, generation=generation))
                    for label, candidate in [('baseline', baseline[0][0]), ('protected', qualified[0] if qualified else None)]:
                        if candidate:
                            warnings[label].update({k: int(v) for k, v in frozen.flags(source, candidate, generation).items()})
                            p = frozen.price(candidate)
                            if p:
                                costs[label].append(p)
                            cpu_ratios[label].append(candidate['vCPUs'] / source['vCPUs'])
                    before, after = frozen.price(baseline[0][0]), frozen.price(qualified[0]) if qualified else None
                    if before and after:
                        costs['changePercent'].append((after - before) / before * 100)
        regions[region] = dict(rc)
        print(f'{region}: {rc["configurations"]} configurations verified', flush=True)
    if args.routes_only:
        args.output.write_text(json.dumps(dict(counts=counts), indent=2), encoding='utf-8')
        print(dict(counts))
        return
    assert counts['configurations'] == 31986
    assert counts['catalogEntries'] == 5331 and counts['pairs'] == 7115640
    assert counts['baselineFirstShortfalls'] == 3591
    assert counts['preservedAlreadyFitFirst'] == 28395
    assert counts['resourceNoMatch'] == 6
    assert counts['protectedAllFiveFit'] == counts['atLeastFiveEligible'] == 31878
    assert counts['baselineAllFiveFit'] == 16862
    assert len(distinct_names) == 1516
    result = dict(counts=counts, regions=regions, firstChoiceWarnings=warnings,
                  firstChoicePrices={k: frozen.distribution(v) for k, v in costs.items()},
                  groupSizes={k: frozen.distribution(v) for k, v in group_sizes.items()},
                  firstChoiceCpuRatio={k: frozen.distribution(v) for k, v in cpu_ratios.items()},
                  distinctSkuNames=len(distinct_names),
                  noMatches=no_matches, seconds=round(time.monotonic() - started, 2),
                  sourceSHA256=frozen.P['sourceHash'],
                  caveats='Frozen catalog, not tested migrations. CPU/cost outliers and boot/disk/GPU/architecture warnings are not fixed.')
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
