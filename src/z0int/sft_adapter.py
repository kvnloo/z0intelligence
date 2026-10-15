"""Backend-neutral SFT experiment adapter for routing episodes (z0int#143 trainer interface).

The backend trains; it owns nothing else. Datasets come from the episode compiler, eval truth and promotion from
z0evals / Evolution Lab. A backend receives a read-only example file plus the dataset receipt and returns a plan or a
checkpoint receipt; it never sees held-out (dev/eval/recent) rows.

Backends:
  stub          writes the plan only (always available; the default)
  unsloth-lora  local LoRA definition for cheap iteration; trains only when the data gate passes, the library is
                importable and the caller passes ``allow_train=True``
  tinker        remote trainer; DISABLED by default. Private history must not leave the host without owner approval:
                it needs $Z0INT_HOME/config/sft_remote_approval.json naming this dataset's sha256 with
                ``owner_approved: true`` and ``allow_remote: true``.

Examples (``to_examples``): observed, independently labelled TRAIN episodes only. The target is the historical
action of a verified success (outcome-filtered imitation); failures are kept as counted negatives, synthetic rows are
excluded unless an ablation asks for them and then stay tagged ``synthetic``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .episodes import is_labelled

SCHEMA = 'z0int.sft_plan.v0'
EXAMPLE_SCHEMA = 'z0int.sft_example.v0'
# The data gate (mirrors the verified-loop sufficiency spirit): enough verified successes and failures per family.
MIN_POSITIVES, MIN_NEGATIVES, MIN_GROUPS = 200, 30, 10
ABLATIONS = ('base', 'sft', 'sft+state_packet', 'sft+retrieval', 'sft+verified_outcome')


def to_examples(train: Iterable[Mapping[str, Any]], *, include_synthetic: bool = False,
                with_state: bool = True) -> list[dict[str, Any]]:
    out = []
    for e in train:
        synthetic = e['provenance']['kind'] == 'synthetic'
        if synthetic and not include_synthetic:
            continue
        if not synthetic and not is_labelled(e):
            continue
        if not synthetic and e['outcome']['y_success'] != 1:
            continue
        ev = e['evidence']
        prompt = {'family': e['family'], 'candidates': e['candidates'].get('set'),
                  'legal': e['candidates'].get('legal')}
        if with_state:
            prompt['features'] = ev.get('features')
        out.append({'schema': EXAMPLE_SCHEMA, 'episode_id': e['episode_id'], 'family': e['family'],
                    'input': json.dumps(prompt, sort_keys=True, separators=(',', ':')),
                    'target': e['action']['chosen'], 'provenance': e['provenance']['kind'],
                    'group': e['lineage'].get('group')})
    return out


def sufficiency(train: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    fam: dict[str, dict[str, Any]] = {}
    for e in train:
        if not is_labelled(e):
            continue
        f = fam.setdefault(e['family'], {'positives': 0, 'negatives': 0, 'groups': set()})
        f['positives' if e['outcome']['y_success'] == 1 else 'negatives'] += 1
        f['groups'].add(e['lineage'].get('group'))
    report = {}
    for name, f in sorted(fam.items()):
        checks = {f'positives>={MIN_POSITIVES}': f['positives'] >= MIN_POSITIVES,
                  f'negatives>={MIN_NEGATIVES}': f['negatives'] >= MIN_NEGATIVES,
                  f'groups>={MIN_GROUPS}': len(f['groups']) >= MIN_GROUPS}
        report[name] = {'positives': f['positives'], 'negatives': f['negatives'], 'groups': len(f['groups']),
                        'checks': checks, 'decision': 'sufficient' if all(checks.values()) else 'INSUFFICIENT_DATA'}
    return {'families': report, 'any_sufficient': any(r['decision'] == 'sufficient' for r in report.values())}


@dataclass
class Backend:
    name: str
    remote: bool = False
    config: dict[str, Any] = field(default_factory=dict)

    def available(self) -> tuple[bool, str]:
        return True, 'ok'

    def train(self, examples_file: Path, out_dir: Path) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class StubBackend(Backend):
    name: str = 'stub'

    def train(self, examples_file: Path, out_dir: Path) -> dict[str, Any]:
        return {'status': 'planned_only', 'backend': self.name}


@dataclass
class UnslothLoRABackend(Backend):
    """Local LoRA definition. The defaults are a small instruct model on one consumer GPU."""
    name: str = 'unsloth-lora'
    config: dict[str, Any] = field(default_factory=lambda: {
        'base_model': 'unsloth/Qwen3-0.6B', 'max_seq_len': 2048, 'load_in_4bit': True,
        'lora': {'r': 16, 'alpha': 32, 'dropout': 0.0,
                 'target_modules': ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']},
        'train': {'epochs': 2, 'lr': 2e-4, 'batch_size': 8, 'grad_accum': 2, 'seed': 3407},
        'objective': 'completion-only loss on the target action token(s)'})

    def available(self) -> tuple[bool, str]:
        return (True, 'ok') if importlib.util.find_spec('unsloth') else (False, 'unsloth_not_installed')

    def train(self, examples_file: Path, out_dir: Path) -> dict[str, Any]:
        ok, why = self.available()
        if not ok:
            return {'status': 'unavailable', 'reason': why}
        raise NotImplementedError('LoRA training run is not wired in v0: the data gate has not passed on any host')


@dataclass
class TinkerBackend(Backend):
    name: str = 'tinker'
    remote: bool = True
    config: dict[str, Any] = field(default_factory=lambda: {'base_model': None, 'lora_rank': 16})

    def train(self, examples_file: Path, out_dir: Path) -> dict[str, Any]:
        raise PermissionError('remote training is disabled in v0')


BACKENDS = {'stub': StubBackend, 'unsloth-lora': UnslothLoRABackend, 'tinker': TinkerBackend}


def remote_approved(home: Path, dataset_sha256: str) -> tuple[bool, str]:
    p = home / 'config' / 'sft_remote_approval.json'
    try:
        a = json.loads(p.read_text())
    except (OSError, ValueError):
        return False, 'no_owner_approval_file'
    if a.get('owner_approved') is not True or a.get('allow_remote') is not True:
        return False, 'owner_approval_not_granted'
    if a.get('dataset_sha256') != dataset_sha256:
        return False, 'approval_names_another_dataset'
    return True, 'owner_approved'


def plan(backend_name: str, train: list[Mapping[str, Any]], *, home: Path, dataset_receipt: Mapping[str, Any],
         split_manifest: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if backend_name not in BACKENDS:
        raise ValueError(f'unknown backend {backend_name!r}; one of {sorted(BACKENDS)}')
    b = BACKENDS[backend_name]()
    examples = to_examples(train)
    body = ''.join(json.dumps(x, sort_keys=True) + '\n' for x in examples)
    ds_sha = hashlib.sha256(body.encode()).hexdigest()
    suff = sufficiency(train)
    avail, why = b.available()
    allowed, reason = True, 'ok'
    if b.remote:
        allowed, reason = remote_approved(home, ds_sha)
    if allowed and not suff['any_sufficient']:
        allowed, reason = False, 'INSUFFICIENT_DATA'
    if allowed and not avail:
        allowed, reason = False, why
    return {'schema': SCHEMA, 'backend': b.name, 'remote': b.remote, 'backend_config': b.config,
            'available': avail, 'train_allowed': allowed, 'reason': reason, 'sufficiency': suff,
            'examples': {'n': len(examples), 'sha256': ds_sha,
                         'by_family': {f: sum(1 for x in examples if x['family'] == f)
                                       for f in sorted({x['family'] for x in examples})}},
            'dataset': {'build_id': dataset_receipt.get('build_id'),
                        'episodes_sha256': dataset_receipt.get('episodes_sha256'),
                        'split_id': split_manifest.get('split_id'),
                        'locked_eval_window': split_manifest.get('locked_eval_window')},
            'ablations': list(ABLATIONS), 'owns': 'training only (no dataset, eval truth or promotion authority)',
            'promotion_path': 'offline -> replay-green -> shadow -> verified outcome -> eligible (z0evals#92)'}, body
