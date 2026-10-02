"""Launch the new one-call study only after its exact frozen protocol is reviewed.

Credentials must already be in the launch environment. No credential files,
model discovery requests, retries, fallback routes, or Task B imports occur here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed-protocol-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    protocol_path = HERE / 'PROTOCOL.json'
    if args.reviewed_protocol_sha256 != sha(protocol_path):
        parser.error('reviewed protocol hash does not match; no credential or provider action')
    protocol = json.loads(protocol_path.read_text())
    root = Path(protocol['implementation']['worktree'])
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if head != protocol['implementation']['commit']:
        parser.error('implementation revision changed')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip():
        parser.error('implementation worktree must be clean')
    for value in protocol['source_files']:
        if sha(Path(value['path'])) != value['sha256']:
            parser.error('frozen source changed: ' + value['path'])
    for repo, source in protocol['frozen_sources'].items():
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source['path'], text=True).strip()
        if actual != source['commit']:
            parser.error('frozen dependency changed: ' + repo)
    if (root.parent / 'physical-call-consumed.v1.json').exists():
        parser.error('one-call study bound already consumed; no automatic retry')
    if args.output.exists():
        parser.error('output must be create-only')
    if not os.environ.get('NOUS_API_KEY'):
        parser.error('NOUS_API_KEY must already be supplied by the reviewed root launch')
    args.output.mkdir(parents=True)
    (args.output / 'reviewed-protocol.json').write_bytes(protocol_path.read_bytes())
    env = dict(os.environ)  # Retain required proxy and TLS trust configuration.
    env['PYTHONPATH'] = str(root / 'src')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PATH'] = protocol['execution']['bun_bin_directory'] + os.pathsep + env.get('PATH', '')
    command = [protocol['execution']['python'], str(root / 'scripts/prove-aodl-nous-study.py'),
               '--output', str(args.output / 'raw'), '--omp-root', protocol['execution']['omp_root']]
    try:
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=180)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = exc.stdout or ''
        stderr = exc.stderr or ''
        if isinstance(stdout, bytes):
            stdout = stdout.decode('utf-8', 'replace')
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', 'replace')
        stderr += '\nStudy launcher timed out; preserve uncertain attempt and do not retry.\n'
    for label, content in [('stdout', stdout), ('stderr', stderr)]:
        (args.output / (label + '.log')).write_text(content.replace(env['NOUS_API_KEY'], '[REDACTED]'))
    receipt = {'study_id': protocol['study_id'], 'returncode': code,
               'raw': str(args.output / 'raw'), 'independent_check_still_required': True,
               'original_task_a_unchanged': True, 'task_b_import_allowed': False}
    (args.output / 'launch-result.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))
    return code


if __name__ == '__main__':
    sys.exit(main())
