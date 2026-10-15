import threading
import urllib.error
import urllib.request

from z0int.agentweb_bridge_capabilities import MAX_ACTIVE
from z0int.intelligence_service import (
    DRAINING,
    METRIC_LOCK,
    METRICS,
    Handler,
    SLOTS,
    Service,
)


def test_host_overload_is_503_before_handler_execution():
    DRAINING.clear()

    acquired = 0
    server = None
    thread = None
    try:
        for _ in range(MAX_ACTIVE):
            assert SLOTS.acquire(blocking=False)
            acquired += 1

        with METRIC_LOCK:
            before_rejected = METRICS["rejected_connections_total"]
            before_admitted = METRICS["admitted_connections_total"]
            before_finished = METRICS["dispatch_finished_total"]

        server = Service(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        url = f"http://127.0.0.1:{server.server_address[1]}/v1/plan"
        req = urllib.request.Request(
            url,
            data=b'{"ignored":true}',
            headers={"content-type": "application/json"},
            method="POST",
        )

        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert exc.headers["Retry-After"] == "1"
            assert exc.headers["X-Z0-Execution"] == "not_started"
            assert exc.read() == b""
        else:
            raise AssertionError("overloaded z0 service did not reject with 503")

        with METRIC_LOCK:
            assert METRICS["rejected_connections_total"] == before_rejected + 1
            assert METRICS["admitted_connections_total"] == before_admitted
            assert METRICS["dispatch_finished_total"] == before_finished
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)
        for _ in range(acquired):
            SLOTS.release()
