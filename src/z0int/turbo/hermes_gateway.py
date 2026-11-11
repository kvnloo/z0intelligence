"""Installation-bound native Hermes gateway client (executed with runpy).

Closing a CLI viewer is NOT cancellation. Interrupt the exact owned session and
wait for its admission receipt; never change a shared profile's run budget.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import signal
import time
import uuid


def emit(value):
    print(json.dumps(value), flush=True)


async def execute(args):
    from hermes_cli.gateway_client import connect_gateway
    from hermes_cli.gateway_chat_view import GatewayChatView
    from hermes_cli.stream_json import StreamJsonEmitter

    stop = Path(args.stop)
    deadline = time.monotonic() + args.seconds
    interrupted = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, interrupted.set)
    async with connect_gateway() as client:
        description = await client.rpc("runtime.describe")
        parameters = description.get("session_create", {}).get("parameters", [])
        policy: dict[str, object] = dict(
            provider=args.provider,
            model=args.model,
            cwd=args.cwd,
            safe_mode=True,
            # Tool-using implementation and independent verification need room
            # to diagnose a failed browser check; the task deadline still owns
            # wall time and cancellation. Never relabel an exhausted turn green.
            max_turns=48,
        )
        if not set(policy) <= set(parameters):
            raise RuntimeError("native_gateway_contract_unsupported")
        if "run_budget" in parameters:
            policy["run_budget"] = max(0.1, deadline - time.monotonic())
        if stop.exists() or time.monotonic() >= deadline:
            return 130
        snapshot = await client.rpc(
            "session.create", request_id=uuid.uuid4().hex, source="oneshot", **policy
        )
        sid = snapshot["stored_session_id"]
        actual = (snapshot.get("info") or {}).get("model")
        if not isinstance(actual, str) or actual != args.model:
            raise RuntimeError("native_model_identity_mismatch")
        emitter = StreamJsonEmitter(model=actual, session_id=sid)
        view = GatewayChatView(client, snapshot, quiet=True, emitter=emitter)
        run = asyncio.create_task(
            view.run(Path(args.query_file).read_text(), oneshot=True)
        )
        reason = None
        try:
            while not run.done():
                if (
                    stop.exists()
                    or interrupted.is_set()
                    or time.monotonic() >= deadline
                ):
                    reason = (
                        "cancelled"
                        if stop.exists() or interrupted.is_set()
                        else "deadline"
                    )
                    # Wait for the submission acknowledgement before interrupting: otherwise
                    # a prompt could be admitted AFTER the interrupt and escape the bound.
                    if view.finite_admission is not None:
                        await view.command("/stop")
                        break
                await asyncio.sleep(0.1)
            if reason and not run.done():
                await asyncio.wait_for(asyncio.shield(run), timeout=20)
            code = await run
            admission = view.finite_admission
            receipt = await client.rpc(
                "prompt.receipt",
                session_id=view.session_id,
                admission_id=admission,
                include_result=True,
            )
            # Preserve missing usage as null; StreamJsonEmitter historically fills zeros.
            result = receipt.get("result") or {}
            emit(
                {
                    "type": "native_receipt",
                    "session_id": view.session_id,
                    "admission_id": admission,
                    "receipt": receipt,
                    "input_tokens": result.get("input_tokens"),
                    "output_tokens": result.get("output_tokens"),
                }
            )
            if reason:
                outcome = (view.completions.get(admission) or {}).get("outcome")
                confirmed = outcome in {"cancelled", "completed", "failed"}
                emit(
                    {
                        "type": "native_stop",
                        "reason": reason,
                        "terminal_confirmed": confirmed,
                        "outcome": outcome,
                    }
                )
                return 130 if confirmed else 1
            return code
        finally:
            if not run.done():
                # Failure/detach is not success. Best effort fenced stop, never global stop.
                with suppress(Exception):
                    await asyncio.wait_for(view.command("/stop"), timeout=5)
                run.cancel()
                with suppress(asyncio.CancelledError):
                    await run


def main():
    parser = argparse.ArgumentParser()
    for name in ("provider", "model", "cwd", "query-file", "stop"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    args = parser.parse_args()
    try:
        return asyncio.run(execute(args))
    except Exception as exc:
        emit({"type": "native_error", "error": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
