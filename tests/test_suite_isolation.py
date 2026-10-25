"""No test reaches the developer's live AgentsView DB or z0 home (C7 added a memory probe to state_packet)."""
from __future__ import annotations

import os
from pathlib import Path

from z0int import agentsview_ro


def test_agentsview_data_dir_is_isolated_for_every_test(tmp_path_factory):
    assert 'AGENTSVIEW_DATA_DIR' in os.environ
    assert agentsview_ro.db_path().is_relative_to(tmp_path_factory.getbasetemp())
