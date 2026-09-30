"""Suite-wide opt-ins so the default run is hermetic.

``@pytest.mark.live`` tests call real model services and read host fixtures
(e.g. /tmp/gate-cases2). They are skipped unless ``--run-live`` or
``Z0INT_RUN_LIVE=1`` is given.
"""
from __future__ import annotations

import os

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run tests marked live (need configured model services and host fixtures)")


def pytest_collection_modifyitems(config, items):
    run_live = config.getoption("--run-live") or os.environ.get("Z0INT_RUN_LIVE") == "1"
    skip_live = pytest.mark.skip(reason="live: needs model services + host fixtures; use --run-live or Z0INT_RUN_LIVE=1")
    for item in items:
        if "live" in item.keywords and not run_live:
            item.add_marker(skip_live)
