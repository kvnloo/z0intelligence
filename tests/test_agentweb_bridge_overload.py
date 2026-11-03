import socket
import threading
import time
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


def test_host_overload_503_survives_split_headers_and_body(monkeypatch):
    # Hosted flake (run 41549647735): urllib may send headers and body in two
    # writes. The overload path must read the request before closing, or the
    # unread bytes turn the close into a reset and the client never sees 503.
    DRAINING.clear()
    handled = []
    original_setup = Handler.setup

    def recording_setup(self):
        handled.append(self.client_address)
        original_setup(self)

    monkeypatch.setattr(Handler, "setup", recording_setup)

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

        body = b'{"ignored":true}'
        head = (
            b"POST /v1/plan HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n"
        )
        with socket.create_connection(server.server_address, timeout=3) as client:
            client.sendall(head)
            time.sleep(0.2)
            client.sendall(body)
            response = b""
            while chunk := client.recv(65536):
                response += chunk

        status, _, rest = response.partition(b"\r\n")
        headers, _, payload = rest.partition(b"\r\n\r\n")
        header_lines = headers.split(b"\r\n")
        assert status == b"HTTP/1.1 503 Service Unavailable"
        assert b"Retry-After: 1" in header_lines
        assert b"X-Z0-Execution: not_started" in header_lines
        assert b"Content-Length: 0" in header_lines
        assert payload == b""
        assert handled == []

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


def test_idle_rejected_clients_do_not_stall_later_admissions(monkeypatch):
    # Review of #301: the overload drain ran on the accept loop, so each
    # rejected client that sent a partial header and went silent blocked
    # accept() for the full drain deadline. Three such clients delayed the
    # next admissible request by ~3 s even after a slot had freed.
    import z0int.intelligence_service as svc

    DRAINING.clear()
    rejected = threading.Semaphore(0)
    first_rejection = []
    original_metric = svc.metric

    def recording_metric(name, amount=1):
        original_metric(name, amount)
        if name == "rejected_connections_total":
            if not first_rejection:
                first_rejection.append(time.monotonic())
            rejected.release()

    monkeypatch.setattr(svc, "metric", recording_metric)

    acquired = 0
    server = None
    thread = None
    idle = []
    try:
        for _ in range(MAX_ACTIVE):
            assert SLOTS.acquire(blocking=False)
            acquired += 1

        with METRIC_LOCK:
            before_rejected = METRICS["rejected_connections_total"]
            before_admitted = METRICS["admitted_connections_total"]

        server = Service(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        for _ in range(3):
            client = socket.create_connection(server.server_address, timeout=10)
            idle.append(client)
            client.sendall(b"POST /v1/plan HTTP/1.1\r\nHost: 127.0")

        # All three must be rejected before the slot frees, or one of them
        # would be admitted instead of the request under test.
        for _ in range(3):
            assert rejected.acquire(timeout=10)
        SLOTS.release()
        acquired -= 1

        url = f"http://127.0.0.1:{server.server_address[1]}/healthz"
        with urllib.request.urlopen(url, timeout=10) as response:
            assert response.status == 200
            assert response.read() == b'{"ok": true}'
        elapsed = time.monotonic() - first_rejection[0]
        assert elapsed < 1.5, f"admission stalled {elapsed:.2f}s behind idle rejected clients"

        # The idle clients still get the exact overload reply once their
        # bounded drain gives up.
        for client in idle:
            response = b""
            while chunk := client.recv(65536):
                response += chunk
            status, _, rest = response.partition(b"\r\n")
            headers, _, payload = rest.partition(b"\r\n\r\n")
            header_lines = headers.split(b"\r\n")
            assert status == b"HTTP/1.1 503 Service Unavailable"
            assert b"Retry-After: 1" in header_lines
            assert b"X-Z0-Execution: not_started" in header_lines
            assert b"Content-Length: 0" in header_lines
            assert payload == b""

        with METRIC_LOCK:
            assert METRICS["rejected_connections_total"] == before_rejected + 3
            assert METRICS["admitted_connections_total"] == before_admitted + 1
    finally:
        for client in idle:
            client.close()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)
        for _ in range(acquired):
            SLOTS.release()


def test_exhausted_reject_drains_reply_503_without_blocking(monkeypatch):
    import z0int.intelligence_service as svc

    draining = threading.Event()
    draining.set()
    monkeypatch.setattr(svc, "DRAINING", draining)
    exhausted = threading.BoundedSemaphore(1)
    assert exhausted.acquire(blocking=False)
    monkeypatch.setattr(svc, "REJECT_DRAINS", exhausted)

    class IdleClient:
        timeout = None
        sent = b""

        def settimeout(self, timeout):
            self.timeout = timeout

        def recv(self, size):
            if self.timeout == 0.0:
                raise BlockingIOError
            raise AssertionError("blocking drain on the accept loop")

        def sendall(self, data):
            self.sent += data

    server = Service.__new__(Service)
    closed = []
    server.shutdown_request = closed.append
    request = IdleClient()
    with METRIC_LOCK:
        before_rejected = METRICS["rejected_connections_total"]
    server.process_request(request, ("127.0.0.1", 1234))

    assert closed == [request]
    assert request.sent.startswith(b"HTTP/1.1 503 Service Unavailable\r\n")
    assert b"\r\nX-Z0-Execution: not_started\r\n" in request.sent
    with METRIC_LOCK:
        assert METRICS["rejected_connections_total"] == before_rejected + 1
