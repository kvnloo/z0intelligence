python3 - <<'PY'
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

root = Path.cwd()
evidence = root / 'evidence/current-z21'
sources = evidence / 'sources'
manifest = (evidence / 'discovery.log').read_text().splitlines()
checked = 0
for line in manifest:
    parts = line.split('  ', 1)
    if len(parts) == 2 and len(parts[0]) == 64:
        assert hashlib.sha256((root / parts[1]).read_bytes()).hexdigest() == parts[0], parts[1]
        checked += 1
assert checked == 15
fields = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')
units = (20, 100, 2, 25)
def weight(row):
    return sum(row[field] * unit for field, unit in zip(fields, units))
assert weight(dict(zip(fields, (100, 10, 1000, 20)))) / 20 == 275
rows = json.loads((sources / 'budget-7d.json').read_text())
for row in rows:
    for field in fields + ('api_call_count', 'sessions', 'straddle_sessions'):
        assert type(row[field]) is int and row[field] >= 0
opus = [row for row in rows if row['family'] == 'opus']
total_units = sum(map(weight, opus))
totals = {field: sum(row[field] for row in opus) for field in fields}
by_source, by_profile = defaultdict(int), defaultdict(int)
for row in opus:
    by_source[row['source']] += weight(row)
    by_profile[row['profile']] += weight(row)
def shares(groups):
    return {str(k): {'weighted_tokens': v / 20, 'share_pct': round(100 * v / total_units, 4)}
            for k, v in sorted(groups.items(), key=lambda pair: -pair[1])}
component_shares = {field: round(100 * totals[field] * unit / total_units, 4)
                    for field, unit in zip(fields, units)}
assert sum(totals.values()) > 0
assert abs(sum(component_shares.values()) - 100) < .001
ship = 1794097200
windows = {}
for filename in ('z21-before-24h.json', 'z21-after-24h.json'):
    data = json.loads((sources / filename).read_text())
    start = data['since_epoch']
    end = start + data['window_hours'] * 3600
    assert start < ship < end, (filename, start, ship, end)
    matched = data['finished_tasks_with_opus_tokens']
    done = data['finished_tasks_in_window']
    windows[filename] = {
        'start_utc': datetime.fromtimestamp(start, timezone.utc).isoformat(),
        'end_utc': datetime.fromtimestamp(end, timezone.utc).isoformat(),
        'is_full_post_ship_window': False,
        'pre_ship_hours': round((ship - start) / 3600, 4),
        'post_ship_hours': round((end - ship) / 3600, 4),
        'finished_tasks': done,
        'opus_matched_tasks': matched,
        'opus_match_pct': round(100 * matched / done, 4),
        'reported_tokens_per_matched_opus_task': data['opus_weighted_per_finished_task'],
        'unattributed_finished_tasks': done - matched,
    }
    assert data['opus_weighted_per_finished_task'] == round(data['opus_weighted_total_on_finished_tasks'] / matched)
route = json.loads((sources / 'route-summary.json').read_text())
effort = json.loads((sources / 'effort-summary.json').read_text())
assert sum(route['labels'].values()) == route['calls'] == 2909
assert sum(effort['labels'].values()) == effort['calls'] == 540
result = {
    'source_hashes_verified': checked,
    'opus_budget': {
        'raw_tokens': sum(totals.values()),
        'components': totals,
        'weighted_tokens_proxy': total_units / 20,
        'component_weight_share_pct': component_shares,
        'api_calls': sum(row['api_call_count'] for row in opus),
        'session_model_task_rows': sum(row['sessions'] for row in opus),
        'straddling_session_model_task_rows': sum(row['straddle_sessions'] for row in opus),
        'profiles': by_profile.keys().__len__(),
        'by_profile': shares(by_profile),
        'by_source': shares(by_source),
    },
    'windows': windows,
    'summary_counts': {'route': route['calls'], 'effort': effort['calls'],
                       'calibration_route': 2919, 'calibration_effort': 2978},
    'acceptance': {
        'done': False,
        'gaps': ['24h shadow bounds and trim shadow absent from inspected artifacts',
                 'frozen-task A/A and equal-pass Opus A/B absent from inspected artifacts',
                 'after-24h filename is a mixed pre/post-ship window, not a full post-ship day',
                 'lifetime cumulative rows are not window token deltas',
                 'task-to-usage join incomplete; objective pass labels absent',
                 'budget and after-query token weights differ'],
    },
}
(evidence / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
print('PASS: 15 frozen hashes; aggregate arithmetic; historical window rejection; denominator and summary-count checks.')
print('INCOMPLETE: no production replay, 24h shadow, frozen-task A/A, or equal-pass Opus A/B executed.')
PY
