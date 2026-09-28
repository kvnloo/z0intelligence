"""Pinned multimodal decision adapter for Image JevBench-style tasks.

This module intentionally does not implement the text-only DecisionBackend
protocol. Images are a distinct input contract and must stay explicit.

The first baseline uses Qwen2.5-VL-3B-Instruct and converts arbitrary option
IDs to short canonical labels (A, B, ...). Probabilities are read from model
logits restricted to those labels rather than parsed from generated prose.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
DEFAULT_MODEL_REVISION = "66285546d2b821cf421d4f5eb2576359d3770cd3"
MAX_OPTIONS = 26


@dataclass(frozen=True)
class ImageDecisionOption:
    id: str
    description: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("option id must be nonempty")
        if not self.description.strip():
            raise ValueError("option description must be nonempty")


@dataclass(frozen=True)
class ImageDecisionResult:
    backend: str
    model: str
    revision: str
    choice: str
    probabilities: dict[str, float]
    latency_ms: float
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "z0int.image_decision.v1",
            **asdict(self),
        }


@dataclass
class _Loaded:
    model: Any
    processor: Any
    torch: Any


def canonical_labels(count: int) -> tuple[str, ...]:
    if not 2 <= count <= MAX_OPTIONS:
        raise ValueError(f"image decision requires 2..{MAX_OPTIONS} options")
    return tuple(chr(ord("A") + i) for i in range(count))


def normalize_logits(logits: Sequence[float]) -> list[float]:
    if not logits:
        raise ValueError("logits cannot be empty")
    values = [float(v) for v in logits]
    if any(not math.isfinite(v) for v in values):
        raise ValueError("logits must be finite")
    peak = max(values)
    weights = [math.exp(v - peak) for v in values]
    total = math.fsum(weights)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid logit normalization")
    return [v / total for v in weights]


def build_prompt(
    question: str,
    options: Sequence[ImageDecisionOption],
    labels: Sequence[str] | None = None,
) -> str:
    if not question.strip():
        raise ValueError("question must be nonempty")
    labels = tuple(labels or canonical_labels(len(options)))
    if len(labels) != len(options):
        raise ValueError("label/option length mismatch")
    rows = "\n".join(
        f"{label}. {option.description}" for label, option in zip(labels, options)
    )
    return (
        "Make the bounded decision from the supplied image.\n"
        "Use only the visible image evidence and the question below.\n"
        "Choose exactly one option. Do not explain the answer.\n\n"
        f"Question: {question}\n"
        f"Options:\n{rows}\n\n"
        f"Return exactly one label from: {', '.join(labels)}"
    )


class QwenImageDecisionBackend:
    """Local Qwen2.5-VL adapter with categorical option probabilities."""

    ID = "qwen25vl_3b_image_decision"

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        revision: str = DEFAULT_MODEL_REVISION,
        device: str | None = None,
        dtype: str = "bfloat16",
    ) -> None:
        self.model_id = model_id
        self.revision = revision
        self.device = device
        self.dtype = dtype
        self._loaded: _Loaded | None = None

    @classmethod
    def from_env(cls) -> "QwenImageDecisionBackend":
        return cls(
            model_id=os.environ.get("Z0INT_IMAGE_MODEL", DEFAULT_MODEL_ID),
            revision=os.environ.get("Z0INT_IMAGE_MODEL_REVISION", DEFAULT_MODEL_REVISION),
            device=os.environ.get("Z0INT_IMAGE_DEVICE") or None,
            dtype=os.environ.get("Z0INT_IMAGE_DTYPE", "bfloat16"),
        )

    def _ensure_loaded(self) -> _Loaded:
        if self._loaded is not None:
            return self._loaded

        try:
            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "image decision runtime requires torch + transformers; "
                "install the project and the image extra"
            ) from exc

        device = self.device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        dtype_map = {
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
            "float16": torch.float16,
            "fp16": torch.float16,
            "float32": torch.float32,
            "fp32": torch.float32,
        }
        if self.dtype not in dtype_map:
            raise ValueError(f"unsupported dtype: {self.dtype}")
        dtype = dtype_map[self.dtype]
        if device == "cpu" and dtype in (torch.float16, torch.bfloat16):
            dtype = torch.float32

        processor = AutoProcessor.from_pretrained(
            self.model_id,
            revision=self.revision,
            trust_remote_code=False,
        )
        kwargs: dict[str, Any] = {
            "revision": self.revision,
            "trust_remote_code": False,
            "torch_dtype": dtype,
        }
        if device.startswith("cuda"):
            kwargs["device_map"] = {"": device}
        model = AutoModelForMultimodalLM.from_pretrained(self.model_id, **kwargs)
        if not device.startswith("cuda"):
            model = model.to(device)
        model.eval()
        self._loaded = _Loaded(model=model, processor=processor, torch=torch)
        return self._loaded

    @staticmethod
    def _label_token_sequences(processor: Any, labels: Sequence[str]) -> list[list[int]]:
        tokenizer = getattr(processor, "tokenizer", processor)
        return [
            list(tokenizer.encode(label, add_special_tokens=False))
            for label in labels
        ]

    @staticmethod
    def _move_inputs(inputs: Any, device: Any) -> dict[str, Any]:
        if hasattr(inputs, "items"):
            return {
                key: (value.to(device) if hasattr(value, "to") else value)
                for key, value in inputs.items()
            }
        raise TypeError("processor output must be mapping-like")

    def _prepare_inputs(self, loaded: _Loaded, image_path: Path, prompt: str) -> dict[str, Any]:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError("install the image extra: pip install -e '.[image]'") from exc

        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        with Image.open(image_path) as opened:
            image = opened.convert("RGB").copy()

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        rendered = loaded.processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = loaded.processor(
            text=[rendered],
            images=[image],
            padding=True,
            return_tensors="pt",
        )
        device = next(loaded.model.parameters()).device
        return self._move_inputs(inputs, device)

    def _score_single_token_labels(
        self,
        loaded: _Loaded,
        inputs: dict[str, Any],
        token_ids: Sequence[int],
    ) -> list[float]:
        torch = loaded.torch
        with torch.inference_mode():
            output = loaded.model(**inputs)
            logits = output.logits[0, -1]
            return [float(logits[token_id].float().item()) for token_id in token_ids]

    def _score_sequence_labels(
        self,
        loaded: _Loaded,
        inputs: dict[str, Any],
        sequences: Sequence[Sequence[int]],
    ) -> list[float]:
        """Fallback exact continuation log-likelihood for multi-token labels."""
        torch = loaded.torch
        base_ids = inputs["input_ids"]
        base_mask = inputs.get("attention_mask")
        extra = {
            key: value
            for key, value in inputs.items()
            if key not in {"input_ids", "attention_mask"}
        }
        scores: list[float] = []
        with torch.inference_mode():
            for sequence in sequences:
                if not sequence:
                    raise ValueError("candidate label tokenized to empty sequence")
                continuation = torch.tensor(
                    [list(sequence)], dtype=base_ids.dtype, device=base_ids.device
                )
                full_ids = torch.cat((base_ids, continuation), dim=1)
                if base_mask is None:
                    full_mask = None
                else:
                    ones = torch.ones(
                        (base_mask.shape[0], len(sequence)),
                        dtype=base_mask.dtype,
                        device=base_mask.device,
                    )
                    full_mask = torch.cat((base_mask, ones), dim=1)
                output = loaded.model(
                    input_ids=full_ids,
                    attention_mask=full_mask,
                    **extra,
                )
                log_probs = torch.log_softmax(output.logits.float(), dim=-1)
                first_prediction_index = base_ids.shape[1] - 1
                score = 0.0
                for offset, token_id in enumerate(sequence):
                    score += float(
                        log_probs[0, first_prediction_index + offset, token_id].item()
                    )
                scores.append(score)
        return scores

    def decide(
        self,
        *,
        image: str | Path,
        question: str,
        options: Sequence[ImageDecisionOption],
    ) -> ImageDecisionResult:
        options = tuple(options)
        labels = canonical_labels(len(options))
        prompt = build_prompt(question, options, labels)
        loaded = self._ensure_loaded()

        started = time.perf_counter()
        inputs = self._prepare_inputs(loaded, Path(image).expanduser(), prompt)
        sequences = self._label_token_sequences(loaded.processor, labels)

        single_token = all(len(sequence) == 1 for sequence in sequences)
        unique_single = single_token and len({sequence[0] for sequence in sequences}) == len(sequences)
        if unique_single:
            raw_scores = self._score_single_token_labels(
                loaded, inputs, [sequence[0] for sequence in sequences]
            )
            readout = "restricted_next_token_logits"
        else:
            raw_scores = self._score_sequence_labels(loaded, inputs, sequences)
            readout = "restricted_continuation_log_likelihood"

        probabilities_list = normalize_logits(raw_scores)
        probabilities = {
            option.id: probability
            for option, probability in zip(options, probabilities_list)
        }
        best = max(range(len(options)), key=probabilities_list.__getitem__)
        latency_ms = (time.perf_counter() - started) * 1000.0

        return ImageDecisionResult(
            backend=self.ID,
            model=self.model_id,
            revision=self.revision,
            choice=options[best].id,
            probabilities=probabilities,
            latency_ms=latency_ms,
            diagnostics={
                "labels": {
                    label: option.id for label, option in zip(labels, options)
                },
                "label_token_ids": sequences,
                "readout": readout,
                "raw_option_scores": {
                    option.id: score for option, score in zip(options, raw_scores)
                },
                "temperature": 1.0,
                "generated_tokens": 0,
                "image_path": str(Path(image).expanduser()),
            },
        )
