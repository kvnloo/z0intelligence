"""Minimal NanoJev inference runtime port for z0int.

Derived from the MIT-licensed NanoJev project:
https://github.com/TianyuCodings/NanoJev

Port only the local inference architecture/encoding needed to load compatible
checkpoint bundles. Research environments, games, web assets, and training
artifacts intentionally remain outside z0int.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import sys
import math


REQUIRED_CHECKPOINT_ENTRIES = (
    "best.safetensors",
    "config.json",
    "tokenizer",
    "backbone_config",
)


def validate_checkpoint_dir(path: Path) -> list[str]:
    missing: list[str] = []
    for name in REQUIRED_CHECKPOINT_ENTRIES:
        p = path / name
        if name in ("tokenizer", "backbone_config"):
            if not p.is_dir():
                missing.append(name + "/")
        elif not p.is_file():
            missing.append(name)
    return missing


def canonical_state_text(state: str | dict[str, Any] | list[Any]) -> str:
    if isinstance(state, str):
        return state
    return json.dumps(state, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _encode(tokenizer, text: str) -> list[int]:
    return tokenizer.encode(text, add_special_tokens=False)


@dataclass(frozen=True)
class RuntimeExample:
    question_id: str
    type: str
    candidate_ids: tuple[str, ...]
    leaf_tokens: tuple[tuple[int, ...], ...]
    #: True when the state did not fit ``max_length`` and had its head dropped.
    #: Callers must surface this: a decision taken on a truncated state was not
    #: taken on the state the caller passed in.
    truncated: bool = False


def build_examples(request, tokenizer, max_length: int) -> list[RuntimeExample]:
    """Compile the z0int DecisionRequest into NanoJev candidate paths.

    Question transport ids are returned for output joining but never inserted
    into semantic model text.
    """
    state_text = canonical_state_text(request.state)
    eos = tokenizer.eos_token_id
    if not isinstance(eos, int) or eos < 0:
        raise ValueError("NanoJev tokenizer requires eos_token_id")

    result: list[RuntimeExample] = []
    for q in request.questions:
        if q.type == "boolean":
            candidate_ids = ("false", "true")
            candidate_texts = ("The proposition is true.",)
        elif q.type == "choice":
            candidate_ids = tuple(x.id for x in q.options)
            candidate_texts = tuple(f"{x.id}: {x.description}" for x in q.options)
        else:
            candidate_ids = tuple(str(i) for i in range(len(q.levels)))
            candidate_texts = tuple(q.levels)

        prefix_a = f"State:\n{state_text}\n"
        prefix_b = f"Question type: {q.type}\nQuestion:\n{q.instructions}\n"
        if q.type == "boolean":
            if q.false_criterion is not None:
                prefix_b += f"False criterion: {q.false_criterion}\n"
            if q.true_criterion is not None:
                prefix_b += f"True criterion: {q.true_criterion}\n"

        prefix = _encode(tokenizer, prefix_a) + _encode(tokenizer, prefix_b)
        candidate_ids_tokens = tuple(
            _encode(tokenizer, f"Candidate:\n{text}\nDecision:") + [eos]
            for text in candidate_texts
        )
        leaves = tuple(tuple(prefix + cand) for cand in candidate_ids_tokens)
        longest = max(map(len, leaves))
        did_truncate = False
        if longest > max_length:
            # Previously this raised, so a single long row aborted the whole run.
            # On the real abstentionbench corpus 16/150 rows overflow a
            # checkpoint-fixed 512-token budget (median row needs 296 incl. the
            # 50-token question block), so the raise blocked a benchmark that
            # 89% of rows fit.
            #
            # Trim the FRONT of the state and keep the TAIL: for this benchmark
            # the assistant response under judgement is at the end of `state`,
            # and the question block is appended after it. Dropping the head
            # keeps the part the verdict depends on.
            #
            # `max_length` comes from the checkpoint's own config.json and is the
            # budget the model was trained with, so it is not raised here.
            head = _encode(tokenizer, "State:\n")
            state_tokens = _encode(tokenizer, state_text)
            tail = _encode(tokenizer, "\n") + _encode(tokenizer, prefix_b)
            budget = max_length - len(head) - len(tail) - max(map(len, candidate_ids_tokens))
            state_tokens = state_tokens[-budget:] if budget > 0 else []
            leaves = tuple(tuple(head + state_tokens + tail + cand) for cand in candidate_ids_tokens)
            print(
                f"  [nanojev] truncated state for question {q.id}: "
                f"{longest} -> {max(map(len, leaves))} tokens (budget {max_length}, kept tail)",
                file=sys.stderr,
            )
            if max(map(len, leaves)) > max_length:
                raise ValueError(
                    f"question {q.id}: cannot fit max_length={max_length} even with an empty state"
                )
            did_truncate = True
        result.append(
            RuntimeExample(
                question_id=q.id,
                type=q.type,
                candidate_ids=candidate_ids,
                leaf_tokens=leaves,
                truncated=did_truncate,
            )
        )
    return result


def _decision_model_class():
    import torch
    from torch import nn
    import torch.nn.functional as F

    class DecisionModel(nn.Module):
        def __init__(self, backbone, set_head: str):
            super().__init__()
            self.backbone = backbone
            hidden = backbone.config.hidden_size
            self.norm = nn.LayerNorm(hidden)
            self.scalar = nn.Linear(hidden, 1)
            self.set_head = set_head
            if set_head == "attention":
                self.set_project = nn.Linear(hidden + 1, 128)
                self.set_attention = nn.MultiheadAttention(
                    128, 4, dropout=0.0, batch_first=True
                )
                self.set_output = nn.Linear(128, 1)

        def forward(self, examples: list[RuntimeExample], pad_token: int):
            paths = [ids for ex in examples for ids in ex.leaf_tokens]
            device = self.scalar.weight.device
            lengths = torch.tensor([len(ids) for ids in paths], device=device)
            width = int(lengths.max())
            tokens = torch.full(
                (len(paths), width), pad_token, dtype=torch.long, device=device
            )
            for i, ids in enumerate(paths):
                tokens[i, : len(ids)] = torch.tensor(ids, device=device)
            attention = torch.arange(width, device=device)[None, :] < lengths[:, None]
            hidden = self.backbone(
                input_ids=tokens, attention_mask=attention, use_cache=False
            ).last_hidden_state
            leaves = hidden[
                torch.arange(len(paths), device=device), lengths - 1
            ]

            kmax = max(len(ex.candidate_ids) for ex in examples)
            h = leaves.new_zeros((len(examples), kmax, leaves.shape[-1]))
            valid = torch.zeros(
                (len(examples), kmax), dtype=torch.bool, device=device
            )
            offset = 0
            for i, ex in enumerate(examples):
                n = len(ex.leaf_tokens)
                h[i, :n] = leaves[offset : offset + n]
                valid[i, : len(ex.candidate_ids)] = True
                offset += n

            h = self.norm(h)
            z = self.scalar(h).squeeze(-1).float()

            choice = torch.tensor(
                [i for i, ex in enumerate(examples) if ex.type == "choice"],
                device=device,
            )
            if self.set_head == "attention" and len(choice):
                log_k = (
                    valid[choice]
                    .sum(-1)
                    .float()
                    .log()[:, None, None]
                    .expand(-1, kmax, 1)
                )
                u = self.set_project(
                    torch.cat([h[choice], log_k.to(h.dtype)], dim=-1)
                )
                mixed, _ = self.set_attention(
                    u,
                    u,
                    u,
                    key_padding_mask=~valid[choice],
                    need_weights=False,
                )
                delta = self.set_output(torch.tanh(u + mixed)).squeeze(-1).float()
                z = z.index_add(0, choice, delta)

            out = []
            for i, ex in enumerate(examples):
                if ex.type == "boolean":
                    out.append(
                        F.pad(
                            torch.stack([z[i, 0] * 0, z[i, 0]]),
                            (0, kmax - 2),
                        )
                    )
                else:
                    out.append(z[i])
            return torch.stack(out).masked_fill(~valid, -1e9), valid

    return DecisionModel


class NanoJevEngine:
    def __init__(
        self,
        checkpoint_dir: Path,
        *,
        device_name: str = "cuda:0",
        precision: str = "bf16",
    ):
        if precision not in {"bf16", "fp32"}:
            raise ValueError("precision must be bf16 or fp32")
        checkpoint_dir = checkpoint_dir.expanduser().resolve()
        missing = validate_checkpoint_dir(checkpoint_dir)
        if missing:
            raise ValueError(f"incomplete NanoJev checkpoint: missing {missing}")

        import torch
        from safetensors.torch import load_file
        from transformers import AutoConfig, AutoModel, AutoTokenizer

        device = torch.device(device_name)
        if device.type == "cuda":
            if not torch.cuda.is_available():
                raise ValueError("NanoJev on cuda requires a visible CUDA device")
            if precision == "bf16" and not torch.cuda.is_bf16_supported():
                raise ValueError("requested BF16 but device does not support it")
        elif device.type == "cpu":
            # CPU runs FP32 compute: bf16 autocast without AVX512-BF16/AMX is a
            # large slowdown, and FP32 is the reference numerics anyway.
            precision = "fp32"
        else:
            raise ValueError(f"NanoJev supports cuda or cpu, not {device.type!r}")

        config = json.loads((checkpoint_dir / "config.json").read_text())
        set_head = config.get("set_head")
        if set_head not in {"none", "attention"}:
            raise ValueError("NanoJev config requires set_head=none|attention")

        tokenizer = AutoTokenizer.from_pretrained(
            checkpoint_dir / "tokenizer",
            local_files_only=True,
            trust_remote_code=False,
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token

        body_config = AutoConfig.from_pretrained(
            checkpoint_dir / "backbone_config",
            local_files_only=True,
            trust_remote_code=False,
        )
        body_config.use_cache = False
        backbone = AutoModel.from_config(
            body_config,
            attn_implementation="sdpa",
            trust_remote_code=False,
        ).float()

        DecisionModel = _decision_model_class()
        model = DecisionModel(backbone, set_head)
        weights = load_file(str(checkpoint_dir / "best.safetensors"), device="cpu")
        model.load_state_dict(weights, strict=True)
        del weights

        model.to(device=device, dtype=torch.float32)
        model.eval()

        self.checkpoint_dir = checkpoint_dir
        self.config = config
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
        self.precision = precision
        self.max_length = int(config.get("max_length") or 512)
        self._torch = torch

    def predict(self, request):
        torch = self._torch
        examples = build_examples(request, self.tokenizer, self.max_length)
        if self.device.type == "cuda":
            started = torch.cuda.Event(enable_timing=True)
            ended = torch.cuda.Event(enable_timing=True)
            started.record()
            with torch.inference_mode(), torch.autocast(
                "cuda",
                dtype=torch.bfloat16,
                enabled=self.precision == "bf16",
            ):
                logits, _ = self.model(examples, self.tokenizer.pad_token_id)
            ended.record()
            torch.cuda.synchronize(self.device)
            forward_ms = float(started.elapsed_time(ended))
        else:
            import time as _time

            t0 = _time.perf_counter()
            with torch.inference_mode():
                logits, _ = self.model(examples, self.tokenizer.pad_token_id)
            forward_ms = (_time.perf_counter() - t0) * 1000.0

        answers = []
        for ex, row in zip(examples, logits):
            k = len(ex.candidate_ids)
            probs = row[:k].float().softmax(-1).cpu().tolist()
            answers.append((ex, probs))
        return {
            "answers": answers,
            "forward_ms": forward_ms,
            "candidate_paths": sum(len(ex.leaf_tokens) for ex in examples),
            "questions": len(examples),
            "autoregressive_decode_steps": 0,
            "network_model_calls": 0,
            "prefix_sharing": False,
            # A truncated state means the model judged a state the caller did not
            # pass in. That must travel with the result, not only to stderr.
            "truncated_questions": [ex.question_id for ex in examples if ex.truncated],
        }
