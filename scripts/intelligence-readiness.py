#!/usr/bin/env python3
"""Publish existing Kind EndpointSlice readiness from the user service lifecycle."""
import json
import os
import subprocess
import sys
import time
import urllib.request

ready=sys.argv[1]=='ready'
if ready:
    deadline=time.monotonic()+25
    while True:
        try:
            for host in ['127.0.0.1',os.environ.get('Z0INT_KIND_GATEWAY','172.19.0.1')]:
                with urllib.request.urlopen(f'http://{host}:11501/readyz',timeout=2) as response:
                    if not json.load(response)['ok']:raise RuntimeError('not ready')
            break
        except Exception:
            if time.monotonic()>deadline:raise SystemExit('Host intelligence did not become ready')
            time.sleep(.2)
patch={'endpoints':[{'addresses':[os.environ.get('Z0INT_KIND_GATEWAY','172.19.0.1')],'conditions':{'ready':ready}}]}
try:
    result=subprocess.run(['kubectl','--context','kind-hermes-lab','--request-timeout=5s','-n','hermes-lab','patch','endpointslice','z0intelligence-host','--type=merge','-p',json.dumps(patch)],capture_output=True,text=True,timeout=8)
    published=result.returncode==0
except (OSError, subprocess.TimeoutExpired):
    published=False
print(json.dumps({'host_ready':ready,'kind_readiness_published':published}))
# A stopped Kind cluster must not take away the host fast path.
