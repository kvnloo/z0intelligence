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
    monkeypatch.setattr(C, 'KNOWN_PATHS', (external, tmp_path / 'not-present'))
    paths = C.phone_caps_paths(tmp_path)
    assert external in paths
    assert tmp_path / 'scripts/fleet-build' in paths
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
