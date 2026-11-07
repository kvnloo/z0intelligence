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
