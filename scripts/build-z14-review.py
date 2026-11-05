#!/usr/bin/env python3
"""Build a review-only Z14 packet from explicit local source ranges, without inference."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(spec_path, out):
    spec = json.loads(spec_path.read_text())
    sources = []
    for item in spec['sources']:
        data = Path(item['path']).read_bytes()
        excerpt = ''.join(data.decode().splitlines(keepends=True)[item['start_line'] - 1:item['end_line']])
        assert excerpt.strip(), item['id']
        sources.append({**item, 'file_sha256': digest(data), 'excerpt': excerpt,
                        'excerpt_sha256': digest(excerpt.encode())})
    catalog = {s['id']: s for s in sources}
    for quote in spec['quotes']:
        assert quote['text'] in catalog[quote['source']]['excerpt'], quote
    for mapping in spec['mappings']:
        assert mapping['sources'] and all(s in catalog for s in mapping['sources'])
    out.mkdir(parents=True, exist_ok=True)
    packet = {**spec, 'sources': sources, 'approval': 'pending', 'acceptance_receipt': 'missing',
              'execution_enabled': False}
    def save(name, obj):
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    save('review-packet.json', packet)
    # This is a bounded excerpt review, not ingestion of the whole conversation.
    quote = next(q for q in spec['quotes'] if q['id'] == 'card-ask')
    prepare = {'op': 'prepare', 'session_id': 'ayo-Z14-review', 'entry_id': 'G0800-card-excerpt',
               'timestamp': '2026-11-04T20:58:52.125000+00:00', 'text': quote['text']}
    def compile_doc(request):
        return json.loads(subprocess.check_output(['node', str(REPO / 'src/z0int/intent_compile.mjs')],
                                                   input=json.dumps(request).encode()))
    doc = compile_doc(prepare)
    sentences = doc['plan']['bindings']['sources'][prepare['entry_id']]['sentences']
    assert len(sentences) == 1, 'Use one exact source sentence for this bounded review task'
    sentence_id = sentences[0]['id']
    records = [{'source': 'z14-frozen-local', 'id': a['id'], 'evidence_kind': a['kind'],
                'text': a['claim']} for a in spec['assumptions']]
    assumptions = [{'claim': a['claim'], 'citations': [
        {'source': r['source'], 'id': r['id'], 'evidence_kind': r['evidence_kind']}]} for a, r in zip(spec['assumptions'], records)]
    target = str(out / 'REVIEW-CARD.md')
    task = {'entryId': prepare['entry_id'], 'sentenceId': sentence_id, 'target': target,
            'interpretation': 'Present a source-linked Z12/Z13 assumptions card only. No execution, qualification, or approval is implied.',
            'executor': 'self', 'assumptions': [], 'conflicts': [],
            'verifier': {'type': 'human_review', 'target': target},
            'preconditions': [{'claim': a['claim'], 'verifier': {'type': 'human_review', 'target': a['observation_target']}}
                              for a in spec['assumptions']]}
    draft = {'classification': 'new_ask', 'sentences': [{'entryId': prepare['entry_id'], 'sentenceId': sentence_id,
             'classification': 'add', 'reason': 'Exact excerpt explicitly requests an assumptions card.'}],
             'tasks': [task], 'amendments': [], 'assumptions': assumptions, 'conflicts': [], 'delegation_plan': []}
    save('intent-draft.json', draft)
    save('memory-catalog.json', records)
    contract = compile_doc({'doc': doc, 'draft': draft, 'records': records, 'model_id': 'none:human-authored-review',
                            'contract_id': 'Z14-review-only'})
    save('intent-contract.json', contract)
    # The native schema does not render facts. Unknown assumptions must be visible in gates.
    board = {'version': 1, 'title': 'Z14 review only | Z12/Z13 outcomes NOT proven',
             'intent': {'revision': contract['revision'], 'sourceHash': contract['provenance']['sourceHash'],
                        'sentences': [{'text': q['text'], 'role': q['role']} for q in spec['quotes']]},
             'contract': {'approval': 'pending', 'contractHash': contract['plan']['bindings']['approval']['contractHash'],
                          'preconditions': [{'label': f"{a['kind'].upper()} {a['id']}: {a['claim']} Observe: {a['observe']}", 'state': 'unknown'}
                                            for a in spec['assumptions']],
                          'acceptance': {'receipt': 'missing', 'clauses': spec['acceptance']},
                          'facts': [{'text': a['claim'], 'label': 'claimed'} for a in spec['assumptions']]},
             'work': {'slices': [{'id': m['id'], 'title': m['concept'] + ': ' + m['status'],
                                 'where': m['native_mapping'], 'state': m['state']} for m in spec['mappings']]},
             # Required but not selected/displayed. These zeros are empty placeholders, not telemetry.
             'intel': {'decisions': {'rows': 0, 'last': '2026-11-05', 'execution': [], 'providers': [], 'status': []},
                       'value': {'estimatedAvoided': 0, 'pairedRows': 0, 'reportedSaved': 0, 'verifiedTasks': 0},
                       'context': {'episodes': 0, 'imported': 0, 'predictions': 0, 'judged': 0, 'hit': 0, 'top1': []},
                       'health': {'ok': False, 'backends': []}}}
    save('intent-board.json', board)
    (out / 'review.openui').write_text('root = IntentBoard("z0", [said, gates, work, proof])\nsaid = DeclaredIntent()\ngates = ContractGates()\nwork = WorkSlices()\nproof = ProofReceipts()\n')
    lines = ['# Z14 assumptions and acceptance card', '',
             'Review only. Approval is pending. No acceptance receipt, activation, deployment, model call, or task execution is claimed.', '',
             'This card covers the Z12/Z13 concepts, not completion of those work items. Source observations and owner intent do not prove capability improvement.', '',
             '## Owner words', '']
    for q in spec['quotes']:
        lines += [f"> {q['text']}", f"Source [{q['source']}](#sources), exact substring. Role: {q['role']}.", '']
    lines += ['G1012 asks for results. It does not approve a plan or attest completion. See source G1012.', '',
              '## Concept to implementation and evaluation', '']
    for m in spec['mappings']:
        lines += [f"### {m['id']} {m['concept']}", f"- Product/project: {m['product']}",
                  f"- Repository and files: {m['implementation']}", f"- Existing evidence: {m['status']}",
                  f"- Evaluation and acceptance: {m['evaluation']}", f"- Memory relation: {m['memory']}",
                  f"- Sources: {', '.join(m['sources'])}.", '']
    lines += ['## Unresolved assumptions', '']
    for a in spec['assumptions']:
        lines += [f"- {a['id']} [{a['kind'].upper()}]: {a['claim']}",
                  f"  Observation prerequisite, not eventual acceptance: {a['observe']}",
                  f"  Typed verifier: `human_review`, target `{a['observation_target']}`. Not executed. Sources: {', '.join(a['sources'])}."]
    lines += ['', '## Acceptance for this review artifact', ''] + [f'- {c}' for c in spec['acceptance']]
    lines += ['', 'The native renderer has no link field. It displays source keys and path strings. Full source links are below.',
              'The native facts enum has no unknown value. Unknown assumptions retain UNKNOWN in visible gate labels and unknown in the AODL catalog.',
              'Required intel fields contain empty placeholders. No telemetry sections are selected or claimed.', '', '## Sources', '']
    for s in sources:
        lines += [f"- {s['id']}: [{s['path']}:{s['start_line']}-{s['end_line']}](file://{s['path']}#L{s['start_line']}). File SHA256 `{s['file_sha256']}`."]
    (out / 'REVIEW-CARD.md').write_text('\n'.join(lines) + '\n')
    print(f'Built review-only packet, {len(sources)} frozen source ranges, {len(spec["mappings"])} mappings, {len(spec["assumptions"])} unresolved assumptions in {out}')


if __name__ == '__main__':
    build(Path(sys.argv[1]), Path(sys.argv[2]).resolve())
