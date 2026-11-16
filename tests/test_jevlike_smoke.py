"""Smoke tests for the ported jevlike trainable scorers."""

import pytest

torch = pytest.importorskip("torch")  # ML stack is the optional `ml` extra
from torch.nn import functional as F  # noqa: E402

from openjev_phase1.jevlike.data import ByteCollator, synthetic_example
from openjev_phase1.jevlike.model import TinyScorer


def test_tiny_scorer_learns_and_normalises():
    torch.manual_seed(5)
    examples = [synthetic_example(1000 + index) for index in range(32)]
    batch = ByteCollator(128, 24)(examples)
    model = TinyScorer(width=32, rank=32, context_tokens=128)
    optimiser = torch.optim.Adam(model.parameters(), lr=0.01)
    initial = float(F.cross_entropy(model(batch), batch["labels"]).detach())
    for _ in range(30):
        loss = F.cross_entropy(model(batch), batch["labels"])
        optimiser.zero_grad(set_to_none=True)
        loss.backward()
        optimiser.step()
    logits = model(batch)
    final = float(F.cross_entropy(logits, batch["labels"]).detach())
    assert final < initial * 0.6
    assert torch.allclose(logits.softmax(-1).sum(-1), torch.ones(len(examples)), atol=1e-6)
