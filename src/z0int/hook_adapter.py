"""``python -m z0int.hook_adapter --harness <id> {prompt,stop,subagent-start,subagent-stop,session-start}``.

The command every harness shim runs; the work lives in the lean ``z0int.hook_entry``.
"""
from .hook_entry import main

if __name__ == '__main__':
    raise SystemExit(main())
