"""Write the v0 reproduced results into manifests/local_cognition.v1.json.

Adds receipt-linked ``reproduced_results`` and a groot serving ``measurements`` row per arm.
It never touches ``source_reported_benchmarks``, ``promotion_state`` or ``role_defaults`` (PREREG rule 3).

  python benchmarks/tool_calling_portfolio/update_manifest.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z0int.cognition.manifest import parse_manifest  # noqa: E402

MANIFEST = ROOT / 'manifests/local_cognition.v1.json'
MACHINE = 'NVIDIA GeForce RTX 3080 Ti'
RUNTIME = 'llama.cpp-b11270-router'
ARM_TO_MODEL = {
    'functiongemma_270m': 'functiongemma_270m', 'hammer2.1_3b': 'hammer2.1_3b', 'hammer2.1_7b': 'hammer2.1_7b',
    'qwen3.5_4b': 'qwen3.5_4b', 'qwen3.5_9b': 'qwen3.5_9b', 'nemotron_orchestrator_8b': 'nemotron_orchestrator_8b',
    'qwen3_8b': 'qwen3_8b', 'qwen3_14b': 'qwen3_14b',
}
QUANT = {'functiongemma_270m': 'BF16'}
ARTIFACTS = {
    'functiongemma_270m': ('ggml-org/functiongemma-270m-it-GGUF', '2566ce14aedfc14fdd0de955ba67346425e67126',
                           'functiongemma-270m-it-bf16.gguf', '44090e534ddb3cfc683b696f2825b4056f413ad1e7963d69bd5196d5b3ae7ee6', 542847392),
    'hammer2.1_3b': ('mradermacher/Hammer2.1-3b-GGUF', '4b0e6fc41ca1179be0c8a0def23df7236ffa8981',
                     'Hammer2.1-3b.Q4_K_M.gguf', '320b6114e54cdee0b2ded57e7fc4c55e343819599c583459782750c72c0009ad', 1929442272),
    'hammer2.1_7b': ('mradermacher/Hammer2.1-7b-GGUF', '5adddc4c1b2e07eb3a299397d1d516ed98276673',
                     'Hammer2.1-7b.Q4_K_M.gguf', '22f425400b88ac8c7697e9d139601b34c1aa130cc2260f91367c643308a5ba61', 4681088000),
    'qwen3.5_4b': ('bartowski/Qwen_Qwen3.5-4B-GGUF', '4168f45a16a1290d65a4ec0fa312ae917a4c15d6',
                   'Qwen_Qwen3.5-4B-Q4_K_M.gguf', '13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983', 3013027808),
    'qwen3.5_9b': ('bartowski/Qwen_Qwen3.5-9B-GGUF', '182be2fd6c7bc44887d88a91cb03ff009cc9f549',
                   'Qwen_Qwen3.5-9B-Q4_K_M.gguf', 'd784ce9eda1a5a7b51e8f705a9e6310844bf4f173654d115823c775fdea56d43', 6169341984),
    'nemotron_orchestrator_8b': ('bartowski/nvidia_Orchestrator-8B-GGUF', 'b4bbbc08d2b475fe529428e6f358c932881aa0b5',
                                 'nvidia_Orchestrator-8B-Q4_K_M.gguf', '1cc7077e20b3339d1a46bc72e29959cdd4c7249ebbd73e6977e76f23625995c7', 5027783840),
    'qwen3_8b': ('Qwen/Qwen3-8B-GGUF', '7c41481', 'Qwen3-8B-Q4_K_M.gguf',
                 'd98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785', 5027783488),
    'qwen3_14b': ('Qwen/Qwen3-14B-GGUF', '530227a', 'Qwen3-14B-Q4_K_M.gguf',
                  '500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0', 9001752960),
}
BASELINES = {
    'qwen3_8b': ('Qwen/Qwen3-8B', '8B', 'Farm default local model (groot router id qwen3-8b-q4km).'),
    'qwen3_14b': ('Qwen/Qwen3-14B', '14B', 'Farm 14B (groot router id qwen3-14b-q4km); cannot co-reside with the 8B.'),
}


def frac(s):
    a, b = s.split('/')
    return int(a) / max(1, int(b)), int(b)


def main():
    res = json.loads((HERE / 'results_v0.json').read_text())
    items_sha = json.loads((HERE / 'items_public_v0.json').read_text())['items_sha256']
    prereg = subprocess.run(['git', 'log', '--format=%h', '-1', '--', 'benchmarks/tool_calling_portfolio/PREREG.md'],
                            cwd=ROOT, capture_output=True, text=True).stdout.strip()
    raw = json.loads(MANIFEST.read_text())
    raw['revision'] = '2026-09-30'
    for arm, mid in ARM_TO_MODEL.items():
        d = res['arms'].get(arm)
        if d is None:
            continue
        if mid not in raw['models']:
            hf, size, note = BASELINES[mid]
            raw['models'][mid] = {
                'model_id': mid, 'hf': hf, 'revision': None, 'role_tags': ['general_local_fallback'],
                'parameter_count': size, 'license': 'apache-2.0', 'gated_access': False, 'commercial_use': True,
                'supported_runtimes': ['llama.cpp', 'ollama', 'vllm'], 'available_quantizations': ['Q4_K_M'],
                'tool_call_template': 'qwen3-hermes', 'tool_parser': 'llama.cpp native (hermes)',
                'capabilities': {}, 'tested_context': 8192, 'tested_quantization': 'Q4_K_M',
                'measurements': [], 'source_reported_benchmarks': [], 'local_benchmark_receipt_ids': [],
                'promotion_state': 'candidate', 'deployment_constraints': [], 'notes': note + ' Added as a measured comparator.'}
        m = raw['models'][mid]
        rid = f'tool-calling-portfolio-v0:{arm}:{items_sha[:12]}'
        quant = QUANT.get(arm, 'Q4_K_M')
        common = {'receipt_id': rid, 'machine': MACHINE, 'runtime': RUNTIME, 'quantization': quant,
                  'adapter': '+'.join(f'{k}:{v}' for k, v in sorted(d['parse_paths'].items())),
                  'comparator': res['comparator'], 'measured_at': d['serving']['measured_at'],
                  'notes': f'prereg {prereg}; thinking off; temperature 0; benchmarks/tool_calling_portfolio/results_v0.json'}
        zf, zn = frac(d['z0_route']['form_exact'])
        rr = [
            {'suite': 'tool_calling_portfolio_v0', 'metric': 'exact_call_acc', 'value': d['exact_acc'], 'n': d['n']},
            {'suite': 'z0_route', 'metric': 'exact_call_acc', 'value': round(d['z0_route']['exact'] / d['z0_route']['n'], 3), 'n': d['z0_route']['n']},
            {'suite': 'z0_route', 'metric': 'form_exact_acc', 'value': round(zf, 3), 'n': zn},
            {'suite': 'bfcl_v4_subset', 'metric': 'exact_call_acc', 'value': round(d['bfcl']['exact'] / d['bfcl']['n'], 3), 'n': d['bfcl']['n']},
            {'suite': 'bfcl_v4_subset', 'metric': 'irrelevance_rejection', 'value': d['irrelevance_rejection'], 'n': 25},
            {'suite': 'state_packet_action_selector', 'metric': 'exact_call_acc',
             'value': round(d['action_selector']['exact'] / d['action_selector']['n'], 3), 'n': d['action_selector']['n']},
            {'suite': 'tool_calling_portfolio_v0', 'metric': 'args_validity', 'value': d['args_valid'],
             'n': int(d['args_valid_n'].split('/')[1])},
        ]
        m['reproduced_results'] = [r for r in m.get('reproduced_results', []) if r.get('receipt_id') != rid] + \
            [{**r, **common} for r in rr]
        if rid not in m.get('local_benchmark_receipt_ids', []):
            m.setdefault('local_benchmark_receipt_ids', []).append(rid)
        s = d['serving']
        meas = {'machine': MACHINE, 'runtime': RUNTIME, 'quantization': quant, 'context': 8192,
                'measured_vram_mib': s['bench_vram_loaded_mib'], 'vram_peak_mib': s['bench_vram_peak_sampled_mib'],
                'cold_start_ms': s['cold_start_ms'], 'decision_p50_ms': d['latency_ms']['client_p50'],
                'decision_p95_ms': d['latency_ms']['client_p95'], 'receipt_id': rid, 'measured_at': s['measured_at'],
                'notes': 'host groot; -c 8192 --parallel 2 (4096/slot); VRAM = bench router processes only (GPU shared); '
                         'decision latency = client wall mbp->groot over tailnet, tool-call prompts'}
        m['measurements'] = [x for x in m.get('measurements', []) if x.get('receipt_id') != rid] + [meas]
        repo, rev, f, sha, size = ARTIFACTS[arm]
        if mid in BASELINES or not m.get('local_artifact'):
            m['local_artifact'] = {'kind': 'gguf', 'hf_repo': repo, 'hf_revision': rev, 'file': f, 'sha256': sha, 'bytes': size}
    parse_manifest(raw)  # validates receipt linkage and licences before writing
    MANIFEST.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + '\n')
    print('updated', MANIFEST)


if __name__ == '__main__':
    main()
