"""Restore reviewed raw results from lossless, hash-bound transport parts."""
import argparse,gzip,hashlib,io,json,tarfile
from pathlib import Path

def unpack(repo,output=None):
    packed=repo/'results/thermocontext/2026-10-02-thrml-wave1-packed'
    manifest=json.loads((packed/'manifest.json').read_text());chunks=[]
    for part in manifest['parts']:
        data=(packed/part['name']).read_bytes()
        assert len(data)==part['bytes'] and hashlib.sha256(data).hexdigest()==part['sha256']
        chunks.append(data)
    archive=b''.join(chunks)
    assert hashlib.sha256(archive).hexdigest()==manifest['archive_sha256']
    destination=output if output is not None else repo/manifest['destination'];verified={}
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive)),mode='r:') as tar:
        members=tar.getmembers();assert {m.name for m in members}==set(manifest['files'])
        assert len(members)==len(manifest['files'])
        for member in members:
            assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            data=tar.extractfile(member).read();expected=manifest['files'][member.name]
            assert len(data)==expected['bytes'] and hashlib.sha256(data).hexdigest()==expected['sha256']
            verified[member.name]=data
    # All bytes are validated before any destination file is written.
    for name,data in verified.items():
        target=destination/name
        if target.exists():
            assert target.read_bytes()==data, f'Refusing to overwrite different evidence: {target}'
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(data)
    print(json.dumps({'verified_files':len(verified),'archive_sha256':manifest['archive_sha256'],'destination':str(destination)}))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path);a=ap.parse_args();unpack(Path(__file__).resolve().parents[2],a.output)
