"""C1 e2e socket guard: refuse and log any connect to port 11501 or to a non-loopback address."""
import ipaddress
import os
import socket

_LOG = os.environ.get('Z0INT_SOCKET_GUARD_LOG')
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex


def _blocked(sock, address):
    if sock.family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple):
        return False
    host, port = address[0], address[1]
    if port == 11501:
        return True
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host not in ('localhost',)


def _record(address):
    if _LOG:
        with open(_LOG, 'a') as fh:
            fh.write(f'{os.getpid()} {address!r}\n')


def connect(self, address):
    if _blocked(self, address):
        _record(address)
        raise ConnectionRefusedError(111, 'socket guard: blocked connect')
    return _orig_connect(self, address)


def connect_ex(self, address):
    if _blocked(self, address):
        _record(address)
        return 111
    return _orig_connect_ex(self, address)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
