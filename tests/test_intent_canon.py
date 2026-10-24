"""Consume emitted documents with the pinned AODL authority on MBP."""
import json
from pathlib import Path
import subprocess


def test_emitted_contract_uses_canonical_aodl():
    from aodl_contract import CANON_VERSION, canonicalize, validate_document_messages
    assert CANON_VERSION == "aodl-canon-1"
    checker = (Path(__file__).resolve().parents[1] / "omp-extensions/z0-relay-guard/checker.js").as_uri()
    script = """
const m = await import(process.argv[1]);
const d = m.createDocument('canon-session');
const s = m.ingestUser(d,{id:'user-entry',timestamp:'2026-10-24T12:00:00Z',message:{role:'user',content:'Check /tmp/canon-target.'}});
m.updateIntent(d,{op:'add',taskId:'canon.task',entryId:'user-entry',sentenceId:s.sentences[0].id,target:'/tmp/canon-target',acceptance:{type:'file_contains',path:'/tmp/canon-target',target:'/tmp/canon-target',pattern:'ready'}});
console.log(JSON.stringify(d));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script, checker], capture_output=True, text=True, check=True)
    doc = json.loads(run.stdout)
    assert validate_document_messages(doc) == []
    assert canonicalize(doc)
