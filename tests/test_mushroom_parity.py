"""Parity: the mushroom backend must agree with Evolution Lab's own inference path.

z0int must not depend on the lab, so the forward pass is re-derived in
`z0int.backends.mushroom`. Re-deriving is how lookalikes drift, so this pins the two
implementations against each other on the frozen P0 splits when the lab is importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

LAB = Path("/home/kvn/tmp/evolution-lab")
P0 = LAB / "data" / "p0"

pytestmark = pytest.mark.skipif(
    not (P0 / "confirm.npz").is_file(), reason="frozen P0 splits not present"
)


def _lab_predict(frames: np.ndarray):
    sys.path.insert(0, str(LAB))
    from evolution_lab.models import _apply_delayed_cue_protocol, _kc_codes, _pn_features

    class Ep:
        __slots__ = ("frames",)

        def __init__(self, f):
            self.frames = f

    b = np.load(P0 / "recovery_student.npz", allow_pickle=True)
    k = int(np.asarray(b["k_winners"]).reshape(-1)[0])
    eps = [Ep(frames[i]) for i in range(frames.shape[0])]
    x = _pn_features(eps)
    h = _kc_codes(x, b["W_pn_kc"], k)
    raw = (h @ b["W_kc_mbon"]).argmax(axis=1)
    return _apply_delayed_cue_protocol(eps, raw)


def _backend_predict(frames: np.ndarray):
    from z0int.backends.mushroom import MushroomBackend, _delayed_cue_protocol, _kc_codes, _pn_features

    b = MushroomBackend()
    w_pn_kc, w_kc_mbon, k, _ = b._load()
    x = _pn_features(frames)
    h = _kc_codes(x, w_pn_kc, k)
    raw = (h @ w_kc_mbon).argmax(axis=1)
    return _delayed_cue_protocol(frames, raw)


@pytest.mark.parametrize("split", ["confirm", "ood"])
def test_mushroom_matches_evolution_lab(split: str) -> None:
    frames = np.load(P0 / f"{split}.npz", allow_pickle=True)["frames"]
    assert np.array_equal(_backend_predict(frames), _lab_predict(frames)), (
        f"mushroom backend diverged from the lab path on {split}"
    )


def test_mushroom_refuses_bounded_choice() -> None:
    """It is a recovery specialist; it must say so rather than guess."""
    from z0int.backends.base import request_from_mapping
    from z0int.backends.mushroom import MushroomBackend

    req = request_from_mapping(
        {
            # two options so base validation passes and the mushroom's own guard is
            # what actually rejects the request
            "state": {"task": "one_obvious_tool", "legal_actions": ["fs.read", "fs.write"]},
            "questions": [
                {
                    "id": "decision",
                    "type": "choice",
                    "instructions": "pick one",
                    "options": [
                        {"id": "fs.read", "description": "read"},
                        {"id": "fs.write", "description": "write"},
                    ],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="recovery specialist"):
        MushroomBackend().evaluate(req)
