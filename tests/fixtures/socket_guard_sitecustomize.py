"""Test socket guard (sitecustomize for child processes): the capture children need no socket at all, so every
connect / connect_ex (any family: AF_INET/AF_INET6 on any address and port, loopback included, and AF_UNIX) is
refused and logged to $Z0INT_SOCKET_GUARD_LOG. The test asserts the log stays empty."""
import os
import socket

_LOG = os.environ.get('Z0INT_SOCKET_GUARD_LOG')


def _record(sock, address):
    if _LOG:
        with open(_LOG, 'a') as fh:
            fh.write(f'{os.getpid()} {sock.family!r} {address!r}\n')


def connect(self, address):
    _record(self, address)
    raise ConnectionRefusedError(111, 'socket guard: blocked connect')


def connect_ex(self, address):
    _record(self, address)
    return 111


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
