"""Suite-wide isolation: no test may reach the developer's real AgentsView database (~/.agentsview)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_agentsview(tmp_path_factory, monkeypatch):
    monkeypatch.setenv('AGENTSVIEW_DATA_DIR', str(tmp_path_factory.mktemp('agentsview')))
