from z0int.receipt import measurement_state_counts_from_rows as f

def test_counts():
    rows = [{"measurement_state": "complete"}, {"measurement_state": "PARTIAL"}, {"measurement_state": "bogus"}, {}, {"measurement_state": "complete"}]
    assert f(rows) == {"complete": 2, "partial": 1, "unknown": 2}

def test_empty():
    assert f([]) == {}
