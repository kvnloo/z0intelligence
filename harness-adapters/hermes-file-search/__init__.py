"""Tool-only Hermes plugin: stdlib HTTP client, no local index or routing hooks."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

# The service admits Git toplevels only, rejects the shared workspace roots outright
# (a tested policy) and refuses any toplevel over its file-count ceiling. Naming that
# contract here removes the mis-rooted call, which otherwise costs one wasted turn
# before the agent falls back to search_files anyway.
REJECTED_ROOTS = (
    "/mnt/zer0models, /mnt/zer0models/workspace/zer0, /workspace, /home/kvn, ~/.hermes"
)
PRIORITY = (
    "For a known exact path, use read_file. z0_file_search needs repository_root = a Git "
    "toplevel on the service host (groot), e.g. "
    "/mnt/zer0models/workspace/zer0/oss/<repo> or .../products/<repo>; a subdirectory of "
    f"a repository is rejected too. Always rejected: {REJECTED_ROOTS} and any filesystem "
    "root, plus any toplevel over 200,000 files (large repos carrying build caches). For "
    "those trees fall back to search_files with a bounded path: it skips unreadable "
    "directories and still returns results. Use QMD separately for notes/docs. FFF is not "
    "a substitute for system metadata/stat/du or the Git worktree registry "
    "(git worktree list). Index hits are discovery evidence: read canonical files before "
    "relying on them. A ContextPacket never grants authorization or verified success."
)
FAILURE_INSTRUCTION = (
    "Correct invalid input or report search unavailability and use an explicit bounded "
    "local search fallback; no evidence was returned. A non-existent root means "
    "repository_root is not a directory on the service host (groot): confirm the path "
    "with ls before retrying. A rejected root means it is not an admitted Git toplevel: "
    f"{REJECTED_ROOTS} and filesystem roots are always rejected, as are subdirectories of "
    "a repository and any toplevel over 200,000 files. Retry only with an admitted "
    "toplevel that exists and is under that ceiling; otherwise use search_files with a "
    "bounded path."
)
SCHEMA = {
    "name": "z0_file_search",
    "description": "Search the shared resident FFF repository index. " + PRIORITY,
    "parameters": {
        "type": "object",
        "properties": {
            "repository_root": {"type": "string", "minLength": 1, "maxLength": 4096,
                                "description": "Absolute Git toplevel directory on the service host (groot); "
                                               "the shared workspace roots and filesystem roots are rejected."},
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
SECTION_MAX_CHARS = 1000
_LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")


def _same_host(origin: str) -> bool:
    """True when the configured service runs on this machine, so a local stat is valid."""
    host = (urllib.parse.urlsplit(origin).hostname or "").strip("[]")
    return host in _LOOPBACK


def register(ctx):
    """Register one native tool and static priority guidance; no automatic hooks."""
    origin = ctx.get_config("service_url", os.environ.get("Z0INT_SERVICE_URL", "http://127.0.0.1:11501"))
    local_service = _same_host(origin)

    def handle(args, **kwargs):
        def failure(error, **extra):
            return json.dumps({"ok": False, "error": error, **extra,
                               "instruction": FAILURE_INSTRUCTION})

        if not isinstance(args, dict):
            return failure("invalid_file_search_request")
        # A root that is not an existing directory can only fail: the service
        # resolves it strictly and answers 400. Say so here instead of paying for
        # the round trip, and only when the service shares this filesystem.
        root = args.get("repository_root")
        if local_service and isinstance(root, str) and root and not os.path.isdir(root):
            return failure("repository_root_not_found_on_service_host", repository_root=root)
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
            try:  # Carry the service's own reason; its 400 body is otherwise opaque.
                body = exc.read()
                service_error = json.loads(body).get("error")
            except Exception:
                service_error = None
            exc.close()
            return failure("invalid_file_search_request" if status in (400, 413)
                           else "file_search_unavailable", http_status=status,
                           service_error=service_error)
        except Exception:  # Hermes handlers must return JSON even for transport/config errors.
            return failure("file_search_unavailable")

    ctx.register_tool(name="z0_file_search", toolset="z0_file_search", schema=SCHEMA, handler=handle)
    ctx.register_system_prompt_section("hermes-file-search", PRIORITY,
                                       max_chars=SECTION_MAX_CHARS)
