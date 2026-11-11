"""Evidence checks use synthetic rows, never the held-out source file."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import verify_evidence as verify


def row():
    return {'id': 'one', 'correct': True, 'gold_index': 0,
            'confidence': .8, 'probabilities': [.8, .2], 'latency_ms': 1.0}


def test_recompute_metrics_from_rows():
    expected = {'n': 1, 'accuracy': 1.0, 'probability_coverage': 1.0,
                'ece_10_equal_width': .2, 'brier_multiclass': .08}
    verify.check_rows([row()], expected)


def test_corrupt_accuracy_fails():
    with pytest.raises(ValueError, match='accuracy'):
        verify.check_rows([row()], {'n': 1, 'accuracy': 0.0})


def test_duplicate_rows_fail():
    with pytest.raises(ValueError, match='Duplicate'):
        verify.check_rows([row(), row()], {'n': 2})


def test_invalid_probability_fails():
    bad = copy.deepcopy(row())
    bad['probabilities'] = [.8, .8]
    with pytest.raises(ValueError, match='probability'):
        verify.check_rows([bad], {'n': 1})


def test_receipt_binds_summary_bytes(tmp_path):
    summary = tmp_path / 'summary.json'
    summary.write_text('{}\n')
    reads = [{'event': 'test_read_started', 'split_sha256': 'split'},
             {'event': 'test_read_completed', 'summary_sha256': verify.digest(summary), 'rows': 3764}]
    verify.check_receipt(reads, summary, 'split', 3764)
    summary.write_text('{"changed": true}\n')
    with pytest.raises(ValueError, match='summary'):
        verify.check_receipt(reads, summary, 'split', 3764)


def test_duplicate_read_event_fails(tmp_path):
    with pytest.raises(ValueError, match='exactly one'):
        verify.check_receipt([{'event': 'test_read_started'}] * 2, tmp_path / 'unused', 'split', 3764)
