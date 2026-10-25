import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "operations/model-routing/generate_catalog.py"
SPEC = importlib.util.spec_from_file_location("routing_catalog_generator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_unknown_numbers_remain_null_and_bool_is_not_numeric():
    assert MODULE.number(None) is None
    assert MODULE.number(True) is None
    assert MODULE.number(float("nan")) is None
    assert MODULE.metric()["value"] is None
    assert MODULE.quantile([], .95) is None


def test_opencode_paid_and_unproven_free_models_are_removed():
    catalog = MODULE.Catalog(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp())
    for name in ("paid", "looks-free", "proven"):
        row = catalog.row("opencode-zen", name)
        catalog.prices(row, {"input": 1 if name == "paid" else 0,
                             "output": 1 if name == "paid" else 0}, "synthetic", catalog.at)
    catalog.rows[("opencode-zen", "looks-free")].pop("free_status")
    result = catalog.finish()
    rows = [r for r in result["models"] if r["provider"] == "opencode-zen"]
    assert [r["model"] for r in rows] == ["proven"]
    assert rows[0]["free_status"]["source"] == "synthetic"
    assert rows[0]["free_status"]["observed_at"] == catalog.at


def test_quantiles_use_actual_samples_not_their_mean():
    assert MODULE.quantile([0, 10, 100], .5) == 10
    assert MODULE.quantile([0, 10, 100], .95) == pytest.approx(91)
