"""Tool-only Hermes plugin: stdlib HTTP client, no local index or routing hooks."""
import json
import os
import urllib.error
import urllib.request

PRIORITY = (
    "For a known exact path, use read_file. For repository path, symbol or content "
    "discovery, prefer z0_file_search with an explicit absolute repository_root on "
    "the service host. If unavailable or missing evidence, state that limitation "
    "and use an explicit bounded local search fallback (search_files or rg). "
    "Use QMD separately for notes/docs. FFF is not a substitute for system "
    "metadata/stat/du or the Git worktree registry (git worktree list). "
    "Index hits are discovery evidence: read canonical files before relying on "
    "them. A ContextPacket never grants authorization or verified success."
)
SCHEMA = {
    "name": "z0_file_search",
    "description": "Search the shared resident FFF repository index. " + PRIORITY,
    "parameters": {
        "type": "object",
        "properties": {
            "repository_root": {"type": "string", "minLength": 1, "maxLength": 4096,
                                "description": "Absolute repository directory on the service host."},
            "query": {"type": "string", "minLength": 1, "maxLength": 2048},
            "kind": {"type": "string", "enum": ["exact_path", "exact_symbol", "natural_language"],
                     "default": "natural_language"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "time_budget_ms": {"type": "integer", "minimum": 10, "maximum": 1000, "default": 120},
            "trace_id": {"type": "string", "maxLength": 256},
            "session_id": {"type": "string", "maxLength": 256},
        },
        "required": ["repository_root", "query"],
        "additionalProperties": False,
    },
}


def register(ctx):
    """Register one native tool and static priority guidance; no automatic hooks."""
    origin = ctx.get_config("service_url", os.environ.get("Z0INT_SERVICE_URL", "http://127.0.0.1:11501"))

    def handle(args, **kwargs):
        def failure(error, **extra):
            return json.dumps({"ok": False, "error": error, **extra, "instruction": (
                "Correct invalid input or report search unavailability and use an explicit "
                "bounded local search fallback; no evidence was returned."
            )})

        if not isinstance(args, dict):
            return failure("invalid_file_search_request")
        try:
            payload = {**args, "harness": "hermes"}
            trace = kwargs.get("trace_id") or kwargs.get("task_id")
            if trace is not None:
                payload["trace_id"] = trace
            if kwargs.get("session_id") is not None:
                payload["session_id"] = kwargs["session_id"]
            request = urllib.request.Request(
                origin.rstrip("/") + "/v1/file-search", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.dumps(json.load(response))
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            return failure("invalid_file_search_request" if status in (400, 413)
                           else "file_search_unavailable", http_status=status)
        except Exception:  # Hermes handlers must return JSON even for transport/config errors.
            return failure("file_search_unavailable")

    ctx.register_tool(name="z0_file_search", toolset="z0_file_search", schema=SCHEMA, handler=handle)
    ctx.register_system_prompt_section("hermes-file-search", PRIORITY, max_chars=1000)
