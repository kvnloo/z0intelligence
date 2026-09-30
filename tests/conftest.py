"""Suite-wide opt-ins so the default run is hermetic.

- ``@pytest.mark.live`` tests call real model services and read host fixtures
  (e.g. /tmp/gate-cases2). They are skipped unless ``--run-live`` or
  ``Z0INT_RUN_LIVE=1`` is given.
- ``@pytest.mark.kerdoios`` tests exercise the real KERD quota projection in
  ``z0int.quota_budget``. They are skipped, with a reason, when the optional
  ``kerdoios`` dependency (``pip install '.[quota]'`` or ``Z0INT_KERDOIOS_ROOT``)
  is not importable. Without it ``quota_budget`` fails closed, which would make
  these assertions pass or fail for the wrong reason.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run tests marked live (need configured model services and host fixtures)")


def kerdoios_available() -> bool:
    """Mirror z0int.quota_budget's resolution: installed package, else Z0INT_KERDOIOS_ROOT."""
    root = os.environ.get("Z0INT_KERDOIOS_ROOT")
    added = bool(root and root not in sys.path)
    if added:
        sys.path.append(root)
    try:
        importlib.import_module("kerdoios.quota.model")
        importlib.import_module("kerdoios.quota.parse")
        return True
    except ImportError:
        return False
    finally:
        if added:
            sys.path.remove(root)


def pytest_collection_modifyitems(config, items):
    run_live = config.getoption("--run-live") or os.environ.get("Z0INT_RUN_LIVE") == "1"
    has_kerdoios = kerdoios_available()
    skip_live = pytest.mark.skip(reason="live: needs model services + host fixtures; use --run-live or Z0INT_RUN_LIVE=1")
    skip_kerd = pytest.mark.skip(reason="kerdoios not importable; install '.[quota]' or set Z0INT_KERDOIOS_ROOT")
    for item in items:
        if "live" in item.keywords and not run_live:
            item.add_marker(skip_live)
        if "kerdoios" in item.keywords and not has_kerdoios:
            item.add_marker(skip_kerd)
