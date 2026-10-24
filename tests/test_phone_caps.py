"""O10 source scanner contracts; run on MBP via fleet-build."""
import itertools
import json
from pathlib import Path

import pytest

from z0int.fsm import guard as G, ops as P
from z0int.fsm import phone_caps as C


@pytest.mark.parametrize('bits', itertools.product((False, True), repeat=5))
def test_phone_resources_never_change_admission_or_placement(bits):
    assert P.phone_admits(*bits) is bits[0]
    assert P.phone_place(*bits) is (P.Lane.PHONE if bits[0] else None)


@pytest.mark.parametrize('name,source', [
    ('nodes.yaml', 'nodes:\n  spare:\n    kind: phone\n    lane: {default_mem: 512M}\n'),
    ('nodes.yaml', 'nodes:\n  spare:\n    kind: phone\n    health: {min_battery_pct: 50}\n'),
    ('nodes.yaml', 'modes:\n  phone:\n    cpus: 0-3\n'),
    ('nodes.yaml', 'defaults: &caps {mem_max: 2G}\nnodes:\n  spare: {kind: phone, lane: *caps}\n'),
    ('fleet.py', "if n['kind'] == 'phone':\n    if p['mem_available_mb'] < 1200:\n        reject()\n"),
    ('fleet.py', "if p.get('battery_pct', 100) < 50:\n    reject()\n"),
    ('fleet.py', "available = p['mem_available_mb']\nif available < need:\n    reject()\n"),
    ('fleet.py', "if n['kind'] == 'phone':\n    pass\nelse:\n    pass\nif p['cpu_load'] > 2:\n    reject()\n"),
    ('fleet.py', "n = merge({'health': reg['defaults']['health']}, n)\n"),
    ('worker.py', "mem = spec.get('mem_mb', 512)\n"),
    ('worker.py', "def run(mem_mb=512):\n    pass\n"),
    ('worker.py', "parser.add_argument('--mem', default='512M')\n"),
    ('worker.py', "cmd = ['taskset', '-c', '0-3']\n"),
    ('fleet-build', 'case $mode in\n  phone) args+=(--mem "${mem:-512M}") ;;\nesac\n'),
    ('fleet-polite-run', 'MEM_MB=1024; CPUS=0-3\n'),
    ('fleet-polite-run', '[ "$thermal" -le "$MAX_THERMAL" ] || exit 75\n'),
    ('fleet-polite-run', 'taskset -c 0-3 "$@"\n'),
    ('fleet-build', 'case $mode in\n phone) mem=512M ;;\nesac\n'),
    ('worker.py', 'MEM_MB = 1024\n'),
    ('worker.py', "while battery_pct >= 50:\n    run_job()\n"),
    ('phone.md', 'Phone jobs get half the cores at nice 19.\n'),
    ('phone.md', 'Phone admission: battery >= 50 percent.\n'),
    ('phone.md', 'Phones have a default memory cap of 512 MiB.\n'),
])
def test_reintroduced_cap_red_then_clean_green(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source)
    red = C.phone_caps_findings(tmp_path, [Path(name)])
    assert red, (name, source)
    assert all(f['rule'] != 'scan-unavailable' for f in red), red
    path.write_text('# Android manages resources; no agent resource caps.\n')
    assert C.phone_caps_findings(tmp_path, [path]) == []


@pytest.mark.parametrize('name,source', [
    ('nodes.yaml', 'nodes:\n  mbp: {lane: {default_mem: 2G, cpu_quota: 400%}}\n  spare: {kind: phone, lane: {}}\n'),
    ('nodes.yaml', 'nodes:\n  spare: {kind: phone, lane: {mem_max: null}}\n'),
    ('fleet.py', "if n['kind'] != 'phone':\n    need = n['lane']['default_mem']\n    if p['mem_available_mb'] < need:\n        reject()\n"),
    ('fleet.py', "need = n['lane']['default_mem'] if n['kind'] != 'phone' else None\n"),
    ('fleet.py', "if n['kind'] == 'phone':\n    return None\nif p['mem_available_mb'] < 1024:\n    reject()\n"),
    ('worker.py', "mem = spec.get('mem_mb')\nfloor = spec.get('floor_mb')\n"),
    ('worker.py', "parser.add_argument('--mem', default=None)\n"),
    ('worker.py', "if owner_paused or call_live:\n    reject()\nif elapsed > timeout:\n    cleanup()\n"),
    ('fleet.py', "thermal = p.get('max_temp_c')\nif n['kind'] == 'linux' and thermal > 95:\n    reject()\n"),
    ('fleet.py', "score = p['load1'] / max(p['ncpu'], 1)\nif best is None or score < best[0]:\n    best = score, n\n"),
    ('fleet.py', "if args.mem is not None:\n    spec['mem_mb'] = args.mem\n"),
    ('worker.py', "memory = json.loads(text)\nif memory.get('schema_version') != 1:\n    reject()\n"),
    ('worker.py', "spec['max_attempts'] = 3\n"),
    ('fleet-build', 'case $mode in\n android) args+=(--mem "${mem:-6G}" --cpus 4) ;;\n phone) args+=(--tolerate phone) ;;\nesac\n[ -n "$mem" ] && args+=(--mem "$mem")\n'),
    ('phone.md', 'Caller-explicit per-job --mem 512M is optional, never a default.\nPhones: no agent resource caps; Android manages battery/thermal/RAM.\n'),
])
def test_legitimate_owner_and_linux_policy_green(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source)
    assert C.phone_caps_findings(tmp_path, [path]) == []


def test_missing_malformed_and_empty_fail_closed(tmp_path):
    for source in ('nodes: [',):
        (tmp_path / 'nodes.yaml').write_text(source)
        assert C.phone_caps_findings(tmp_path, [Path('nodes.yaml')])[0]['rule'] == 'scan-unavailable'
    assert C.phone_caps_findings(tmp_path, [Path('absent')])[0]['rule'] == 'scan-unavailable'
    assert C.phone_caps_findings(tmp_path, [])[0]['rule'] == 'scan-unavailable'


def test_discovery_includes_present_known_paths_and_root(tmp_path, monkeypatch):
    external = tmp_path / 'known-phone.md'
    external.write_text('Phone battery >= 50 percent\n')
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/fleet-build').write_text('mode=phone\n')
    (tmp_path / 'scripts/fleet_phone_controller.py').write_text('mem_mb = None\n')
    monkeypatch.setattr(C, 'KNOWN_PATHS', (external, tmp_path / 'not-present'))
    paths = C.phone_caps_paths(tmp_path)
    assert external in paths
    assert tmp_path / 'scripts/fleet-build' in paths
    assert tmp_path / 'scripts/fleet_phone_controller.py' in paths
    assert all(p.is_file() for p in paths)
    assert C.phone_caps_findings(tmp_path)[0]['path'] == str(external)


def test_cli_requires_root_and_reports_red_green(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(G, 'STATE', tmp_path / 'state')
    monkeypatch.setenv('Z0_GUARD_O10', 'enforce')
    with pytest.raises(SystemExit) as exc:
        G.main(['phone-caps'])
    assert exc.value.code == 2
    capsys.readouterr()
    source = tmp_path / 'fleet-polite-run'
    source.write_text('MEM_MB=1024\n')
    argv = ['phone-caps', '--root', str(tmp_path), '--path', source.name]
    assert G.main(argv) == G.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)['status'] == 'refused'
    source.write_text('TIMEOUT=3600\n')
    assert G.main(argv) == 0
    assert json.loads(capsys.readouterr().out)['findings'] == []


@pytest.mark.parametrize('binding,clean', [
    ("worker = nodes['mbp']", True),
    ("selected = nodes['mbp']\nworker = selected", True),
    ("worker = nodes['s25']", False),
    ("worker = nodes['mbp']\nworker = nodes[target]", False),
    ("worker = nodes['mbp']\nif selected_phone:\n    worker = nodes['s25']", False),
])
def test_concrete_host_node_binding_not_function_name(tmp_path, binding, clean):
    path = tmp_path / 'fleet.py'
    # The wrapper deliberately has a neutral name; only dataflow identifies MBP.
    body = binding + "\nargv = ['fleet-kueue', '--mem', args.mem or worker['lane']['default_mem']]\n"
    path.write_text('def queued():\n' + ''.join('    ' + line + '\n' for line in body.splitlines()))
    assert bool(C.phone_caps_findings(tmp_path, [path])) is not clean


def test_nodes_yaml_inherited_default_battery_regression(tmp_path):
    source = Path(__file__).parent / 'fixtures/phone-caps/nodes.yaml'
    target = tmp_path / 'nodes.yaml'
    target.write_text(source.read_text())
    findings = C.phone_caps_findings(tmp_path, [target])
    assert len(findings) == 1
    assert findings[0]['line'] == 4
    assert 'inherited' in findings[0]['detail']
    assert 'min_battery_pct_on_battery=40' in findings[0]['detail']


@pytest.mark.parametrize('threshold,bad', [(0, False), (1, False), (2, False),
                                         (2.01, True), (40, True)])
@pytest.mark.parametrize('scope', ['defaults', 'phone'])
def test_phone_battery_only_below_two_percent(tmp_path, threshold, bad, scope):
    default = threshold if scope == 'defaults' else 40
    override = '' if scope == 'defaults' else f'    health: {{min_battery_pct_on_battery: {threshold}}}\n'
    path = tmp_path / 'nodes.yaml'
    path.write_text(f'defaults:\n  health: {{min_battery_pct_on_battery: {default}}}\n'
                    'nodes:\n  spare:\n    kind: phone\n' + override)
    assert bool(C.phone_caps_findings(tmp_path, [path])) is bad


@pytest.mark.parametrize('key,value,bad', [
    ('cpus', '0-3', True), ('cpus', 4, True), ('cpus', 'all', False),
    ('memory_pct', 50, True), ('memory_pct', 99, True), ('memory_pct', 100, False),
    ('cpu_pct', 99, True), ('cpu_pct', 100, False), ('memory', '2G', True),
])
def test_phone_worker_profiles_cannot_cap_resources(tmp_path, key, value, bad):
    path = tmp_path / 'nodes.yaml'
    path.write_text(f'worker_profiles:\n  neutral: {{{key}: {value}}}\n'
                    'nodes:\n  spare:\n    kind: phone\n    worker_profile: neutral\n')
    assert bool(C.phone_caps_findings(tmp_path, [path])) is bad


def test_defaults_with_no_phone_nodes_are_not_phone_caps(tmp_path):
    path = tmp_path / 'nodes.yaml'
    path.write_text('defaults:\n  health: {min_battery_pct_on_battery: 40}\n'
                    'nodes:\n  mbp: {kind: linux}\n')
    assert C.phone_caps_findings(tmp_path, [path]) == []


def test_scripts_directory_is_not_a_checkout_root(tmp_path, monkeypatch, capsys):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'fleet').write_text('# clean source\n')
    external = tmp_path / 'phone.md'
    external.write_text('# Phones\nAndroid manages resources.\n')
    monkeypatch.setattr(C, 'KNOWN_PATHS', (external,))
    monkeypatch.setattr(G, 'STATE', tmp_path / 'state')
    monkeypatch.setenv('Z0_GUARD_O10', 'enforce')
    assert G.main(['phone-caps', '--root', str(scripts)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report['findings'][0]['rule'] == 'scan-unavailable'
    assert 'checkout root' in report['findings'][0]['detail']


@pytest.mark.parametrize('expression', ["battery_pct", "power['batteries'][0]['pct']"])
@pytest.mark.parametrize('threshold,bad', [(1, False), (2, False), (2.01, True), (40, True)])
def test_native_phone_battery_floor_at_most_two(tmp_path, expression, threshold, bad):
    path = tmp_path / 'fleet-adb'
    path.write_text('#!/usr/bin/env python3\n'
                    f'if {expression} < {threshold} and not power["on_ac"]:\n'
                    '    raise RuntimeError("below owner battery floor")\n')
    assert bool(C.phone_caps_findings(tmp_path, [path])) is bad


def test_native_adb_helper_is_in_default_source_scope(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    helper = scripts / 'fleet-adb'
    helper.write_text('#!/usr/bin/env python3\n'
                      'if power["batteries"][0]["pct"] < 2 and not power["on_ac"]:\n'
                      '    raise RuntimeError("battery below 2% and not charging")\n')
    monkeypatch.setattr(C, 'KNOWN_PATHS', ())
    assert helper in C.phone_caps_paths(tmp_path)
    assert C.phone_caps_findings(tmp_path) == []


@pytest.mark.parametrize('source,bad', [
    ('power = p["power"]\npct = power["batteries"][0]["pct"]\n', False),
    ('power = p["power"]\nlevel = power["batteries"][0]["pct"]\npct = level\n', False),
    ('pct = p["cpu_load"]\n', True),
    ('pct = p["temperature"]\n', True),
    ('pct = p["mem_available_mb"]\n', True),
    ('pct = p["power"]["batteries"][0]["pct"]\npct = p["cpu_load"]\n', True),
    ('pct = p["power"]["batteries"][0]["pct"]\nif other:\n    pct = p["temperature"]\n', True),
])
def test_two_percent_exception_tracks_battery_alias_not_generic_resources(tmp_path, source, bad):
    path = tmp_path / 'fleet.py'
    path.write_text(source + 'if pct < 2 and not power["on_ac"]:\n    reject()\n')
    assert bool(C.phone_caps_findings(tmp_path, [path])) is bad


@pytest.mark.parametrize('kind,direct_cap,bad', [('phone', False, False),
                                               ('linux', False, True),
                                               ('phone', True, True)])
def test_registry_explicit_phone_empty_defaults_controls_yaml_inheritance(tmp_path, kind, direct_cap, bad):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'config/fleet').mkdir(parents=True)
    (tmp_path / 'scripts/fleet').write_text(
        'def registry():\n'
        '    reg = load()\n'
        '    nodes = {}\n'
        '    for name, n in reg["nodes"].items():\n'
        f'        defaults = {{}} if n.get("kind") == "{kind}" else {{\n'
        '            "health": reg["defaults"]["health"], "lane": reg["defaults"]["lane"]}\n'
        '        n = merge(defaults, n)\n'
        '        nodes[name] = n\n'
        '    return nodes\n')
    direct = ', memory_pct: 50' if direct_cap else ''
    registry = tmp_path / 'config/fleet/nodes.yaml'
    registry.write_text('defaults:\n  health: {min_battery_pct_on_battery: 40}\n'
                        '  lane: {}\nnodes:\n  spare: {kind: phone' + direct + '}\n')
    assert bool(C.phone_caps_findings(tmp_path, [registry])) is bad
