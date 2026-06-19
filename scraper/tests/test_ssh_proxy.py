"""Tests for the SSH-tunnel HTTP proxy (parlamonitor/ssh_proxy.py).

These exercise the proxy machinery (CONNECT + absolute-form forwarding, byte
relay, config wiring) without needing a real SSH server: a fake "transport"
opens plain TCP sockets to a local origin HTTP server, standing in for
paramiko's ``direct-tcpip`` channels.
"""

from __future__ import annotations

import http.server
import select
import socket
import threading

import pytest
import requests

from parlamonitor.config import RuntimeConfig
from parlamonitor.ssh_proxy import SSHProxy


# --- fakes standing in for paramiko transport/channel ----------------------

class _SockChannel:
    """A paramiko-Channel-like wrapper over a real socket."""

    def __init__(self, sock: socket.socket):
        self._s = sock
        self.closed = False
        self.eof_received = False

    def fileno(self) -> int:
        return self._s.fileno()

    def setblocking(self, flag: bool) -> None:
        self._s.setblocking(flag)

    def sendall(self, data: bytes) -> None:
        self._s.sendall(data)

    def recv(self, n: int) -> bytes:
        data = self._s.recv(n)
        if data == b"":
            self.eof_received = True
        return data

    def recv_ready(self) -> bool:
        r, _, _ = select.select([self._s], [], [], 0)
        return bool(r)

    def close(self) -> None:
        self.closed = True
        try:
            self._s.close()
        except OSError:
            pass


class _FakeTransport:
    """Opens a real socket to ``target`` for every ``open_channel`` call."""

    def __init__(self, target: tuple[str, int]):
        self.target = target

    def open_channel(self, kind, dest, src):  # noqa: ARG002 - signature parity
        s = socket.create_connection(self.target, timeout=5)
        return _SockChannel(s)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = f"hello {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence test noise
        pass


@pytest.fixture
def origin():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address  # (host, port)
    finally:
        srv.shutdown()


@pytest.fixture
def proxy(origin):
    p = SSHProxy(host="ssh.example", user="u", key_path="/dev/null")
    p._transport = _FakeTransport(origin)
    p._start_listener()
    try:
        yield p
    finally:
        p.close()


# --- config wiring ---------------------------------------------------------

def test_from_config_disabled_without_host_or_key():
    assert SSHProxy.from_config(RuntimeConfig()) is None
    assert SSHProxy.from_config(RuntimeConfig(ssh_host="h")) is None
    assert SSHProxy.from_config(RuntimeConfig(ssh_key="/k")) is None


def test_from_config_requires_user():
    cfg = RuntimeConfig(ssh_host="h", ssh_key="/k")  # no user
    with pytest.raises(Exception):
        SSHProxy.from_config(cfg)


def test_from_config_builds_instance():
    cfg = RuntimeConfig(ssh_host="h", ssh_key="/k", ssh_user="me", ssh_port=2222)
    p = SSHProxy.from_config(cfg)
    assert p is not None
    assert (p.host, p.user, p.key_path, p.port) == ("h", "me", "/k", 2222)


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("PARLAMONITOR_SSH_HOST", "bastion")
    monkeypatch.setenv("PARLAMONITOR_SSH_USER", "scraper")
    monkeypatch.setenv("PARLAMONITOR_SSH_KEY", "/keys/id")
    monkeypatch.setenv("PARLAMONITOR_SSH_PORT", "2200")
    cfg = RuntimeConfig.from_env()
    assert (cfg.ssh_host, cfg.ssh_user, cfg.ssh_key, cfg.ssh_port) == (
        "bastion", "scraper", "/keys/id", 2200)


# --- authority parsing ------------------------------------------------------

def test_parse_authority():
    assert SSHProxy._parse_authority("host:8443") == ("host", 8443)
    assert SSHProxy._parse_authority("www.parlament.hu") == (
        "www.parlament.hu", 443)


# --- end-to-end through the proxy ------------------------------------------

def test_absolute_form_http_request(proxy, origin):
    """Plain HTTP through the proxy (requests sends an absolute-form URL)."""
    proxies = proxy.requests_proxies()
    url = f"http://{origin[0]}:{origin[1]}/foo"
    r = requests.get(url, proxies=proxies, timeout=5)
    assert r.status_code == 200
    assert r.text == "hello /foo"


def test_connect_tunnel_relays_bytes(proxy, origin):
    """CONNECT establishes a raw tunnel; bytes relay both ways."""
    s = socket.create_connection(("127.0.0.1", proxy._local_port), timeout=5)
    s.sendall(f"CONNECT {origin[0]}:{origin[1]} HTTP/1.1\r\n\r\n".encode())
    reply = s.recv(4096)
    assert b"200" in reply.split(b"\r\n", 1)[0]
    # Now speak HTTP to the origin over the established tunnel.
    s.sendall(b"GET /bar HTTP/1.1\r\n"
              b"Host: origin\r\nConnection: close\r\n\r\n")
    got = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        got += chunk
    s.close()
    assert b"200" in got
    assert b"hello /bar" in got


def test_connect_to_dead_target_returns_502(proxy):
    """A channel-open failure surfaces as 502 to the client."""
    # Point the transport at a closed port so open_channel raises.
    proxy._transport = _FakeTransport(("127.0.0.1", 1))
    s = socket.create_connection(("127.0.0.1", proxy._local_port), timeout=5)
    s.sendall(b"CONNECT 127.0.0.1:1 HTTP/1.1\r\n\r\n")
    reply = s.recv(4096)
    s.close()
    assert b"502" in reply
