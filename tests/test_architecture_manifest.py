"""Validate that the exported architecture remains navigable after stacked merges."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_inventory_resolves():
    architecture = yaml.safe_load((ROOT / 'zer0.repo.yaml').read_text())['architecture']
    subsystems = architecture['subsystems']
    ids = {item['id'] for item in subsystems}
    assert len(ids) == len(subsystems), 'subsystem IDs must be unique'
    for item in subsystems:
        assert set(item.get('depends_on', ())) <= ids, item['id']
        for path in item['paths']:
            assert (ROOT / path).exists(), (item['id'], path)
    for paths in architecture['evidence'].values():
        for path in paths:
            assert (ROOT / path).exists(), path
