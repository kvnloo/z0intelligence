#!/usr/bin/env python3
"""A stand-in for `claude -p` over stream-json, for the optchat tests. No network.

FAKE_LOG       append one JSON line per process: argv and the environment
FAKE_REQUESTS  API requests each send wants to make (default 1); a send that
               wants more than --max-turns stops there with error_max_turns
FAKE_SLEEP     seconds to wait before answering
FAKE_TEXT      the reply (default: a 200-byte line)
"""
import json
import os
import sys
import time

argv = sys.argv[1:]
limit = int(argv[argv.index("--max-turns") + 1]) if "--max-turns" in argv else 10 ** 6
if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "a") as fh:
        fh.write(json.dumps({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd()}) + "\n")
want = int(os.environ.get("FAKE_REQUESTS", "1"))
text = os.environ.get("FAKE_TEXT", "line " + "s" * 200)
sent = 0
for raw in sys.stdin:
    if not raw.strip():
        continue
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    made = min(want, limit)
    for n in range(made):
        sent += 1
        last = n == want - 1
        block = {"type": "text", "text": text} if last else {"type": "tool_use", "name": "mcp__chat__zoom", "input": {"id": 0, "n": 1}}
        print(json.dumps({"type": "assistant", "message": {"id": f"msg_{os.getpid()}_{sent}", "model": "fake-haiku",
                                                         "usage": {"input_tokens": 1}, "content": [block]}}), flush=True)
    cut = want > limit
    print(json.dumps({"type": "result", "subtype": "error_max_turns" if cut else "success", "is_error": cut,
                      "result": None if cut else text, "num_turns": made,
                      "usage": {"input_tokens": made, "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0, "output_tokens": made}}), flush=True)
