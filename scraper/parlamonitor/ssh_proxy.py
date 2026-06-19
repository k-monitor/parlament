"""Tunnel parlament.hu traffic through an SSH host (paramiko).

Some networks can only reach ``parlament.hu`` from a specific egress IP. This
module lets the scraper route every HTTP request through an SSH server the
operator controls, without installing a system-wide SOCKS daemon.

It works by opening one persistent SSH connection (key-based auth) and serving
a tiny **HTTP CONNECT proxy** on a local ephemeral port. Each request that
``requests`` sends to that proxy is forwarded over the SSH transport via a
``direct-tcpip`` channel, so the connection to parlament.hu originates from the
SSH host. An HTTP proxy (rather than SOCKS) is used deliberately: ``requests``
speaks it natively, so the only extra runtime dependency is ``paramiko``.

Usage::

    proxy = SSHProxy.from_config(config)   # None if SSH is not configured
    if proxy:
        proxy.start()                      # connects + starts listener thread
        session.proxies = proxy.requests_proxies()
        ...
        proxy.close()                      # tears down channels + SSH session

:class:`SSHProxy` is also a context manager.
"""

from __future__ import annotations

import logging
import select
import socket
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# How long to wait for the SSH `direct-tcpip` channel relay select() loop.
_RELAY_TIMEOUT = 1.0
_BUF = 32768


class SSHProxyError(RuntimeError):
    """Raised when the SSH tunnel cannot be established."""


@dataclass
class SSHProxy:
    """A local HTTP proxy whose outbound connections exit via an SSH host."""

    host: str
    user: str
    key_path: str
    port: int = 22
    key_passphrase: str | None = None
    known_hosts: str | None = None          # path; None -> auto-add unknown keys
    connect_timeout: float = 30.0

    # populated by start()
    _client: object | None = None           # paramiko.SSHClient
    _transport: object | None = None
    _server: socket.socket | None = None
    _local_port: int = 0
    _thread: threading.Thread | None = None
    _closing: threading.Event | None = None

    @classmethod
    def from_config(cls, config) -> "SSHProxy | None":
        """Build from a :class:`RuntimeConfig`, or ``None`` if SSH is off.

        SSH tunnelling is considered configured once a host **and** a key path
        are present; the user defaults to the current login name upstream.
        """
        if not (config.ssh_host and config.ssh_key):
            return None
        if not config.ssh_user:
            raise SSHProxyError("ssh_host/ssh_key set but ssh_user is missing")
        return cls(
            host=config.ssh_host,
            user=config.ssh_user,
            key_path=config.ssh_key,
            port=config.ssh_port,
            key_passphrase=config.ssh_key_passphrase,
            known_hosts=config.ssh_known_hosts,
        )

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Open the SSH connection and start the local proxy listener."""
        try:
            import paramiko
        except ImportError as e:  # pragma: no cover - dependency hint
            raise SSHProxyError(
                "SSH tunnelling needs the 'paramiko' package "
                "(pip install paramiko)") from e

        pkey = self._load_key(paramiko)

        # Try a normal connection first. If it fails and the key is RSA, the
        # server may be an old one (e.g. OpenSSH < 7.2) that only accepts the
        # legacy ``ssh-rsa`` (RSA-SHA1) signature. We re-enable that signature
        # and force it on the retry — the equivalent of
        # ``ssh -o PubkeyAcceptedKeyTypes=ssh-rsa``.
        transport, err = self._connect(paramiko, pkey, force_ssh_rsa=False)
        if transport is None and isinstance(pkey, paramiko.RSAKey):
            if self._enable_legacy_ssh_rsa(paramiko):
                logger.info("SSH connect failed (%s); retrying with legacy "
                            "ssh-rsa (RSA-SHA1)", err)
                transport, err = self._connect(
                    paramiko, pkey, force_ssh_rsa=True)
            else:
                logger.warning("SSH connect failed and SHA-1 RSA signing is "
                               "unavailable; cannot fall back to ssh-rsa")
        if transport is None:
            raise SSHProxyError(
                f"SSH connection to {self.user}@{self.host}:{self.port} "
                f"failed (key {self.key_path}): {err}") from err

        transport.set_keepalive(30)
        # The Transport owns the socket; close() closes it via _client.
        self._client = transport
        self._transport = transport
        self._start_listener()
        logger.info("SSH proxy up: 127.0.0.1:%d -> %s@%s:%d",
                    self._local_port, self.user, self.host, self.port)

    def _load_key(self, paramiko):
        """Load the private key, trying each key type until one parses.

        ``SSHClient.connect`` can auto-detect, but loading explicitly gives a
        clear error (and lets us know the key type for the ssh-rsa fallback).
        """
        # RSA first: it is the common case for the legacy servers this targets.
        # Use getattr so key types absent in a given paramiko version (e.g.
        # DSSKey, removed as DSA was deprecated) are simply skipped.
        candidates = [getattr(paramiko, n, None) for n in
                      ("RSAKey", "Ed25519Key", "ECDSAKey", "DSSKey")]
        candidates = [c for c in candidates if c is not None]
        errors = []
        for cls in candidates:
            try:
                return cls.from_private_key_file(
                    self.key_path, password=self.key_passphrase)
            except paramiko.PasswordRequiredException as e:
                raise SSHProxyError(
                    f"SSH key {self.key_path} is encrypted; set "
                    f"PARLAMONITOR_SSH_KEY_PASSPHRASE") from e
            except (paramiko.SSHException, OSError) as e:
                errors.append(f"{cls.__name__}: {e}")
        raise SSHProxyError(
            f"Could not load SSH key {self.key_path}: " + "; ".join(errors))

    @staticmethod
    def _enable_legacy_ssh_rsa(paramiko) -> bool:
        """Restore SHA-1 RSA signing if this paramiko build dropped it.

        paramiko >= 4 removed ``ssh-rsa`` from ``RSAKey.HASHES``, so it can no
        longer produce the SHA-1 signature that pre-7.2 OpenSSH servers
        require — attempting it raises ``KeyError: 'ssh-rsa'``. cryptography
        still implements SHA-1, so we add the entry back. Returns ``True`` once
        ssh-rsa signing is available.
        """
        hashes_map = paramiko.RSAKey.HASHES
        if "ssh-rsa" in hashes_map:
            return True
        try:
            from cryptography.hazmat.primitives import hashes
        except ImportError:  # pragma: no cover - cryptography is a paramiko dep
            return False
        hashes_map["ssh-rsa"] = hashes.SHA1
        hashes_map.setdefault("ssh-rsa-cert-v01@openssh.com", hashes.SHA1)
        return True

    def _connect(self, paramiko, pkey, *, force_ssh_rsa: bool):
        """Open one authenticated SSH transport.

        Returns ``(transport, None)`` on success, or ``(None, exc)`` on an
        SSH-level failure (auth / key-algorithm negotiation) that the caller
        may retry with ``force_ssh_rsa``. Transport-level failures (DNS,
        refused, host-key mismatch) raise :class:`SSHProxyError` immediately —
        retrying with a different signature algorithm wouldn't help.

        Uses paramiko's low-level ``Transport`` rather than ``SSHClient`` so we
        can control the public-key signature algorithm list directly, which is
        the only way to re-enable the legacy ``ssh-rsa`` signature.
        """
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout)
        except OSError as e:
            raise SSHProxyError(
                f"SSH connection to {self.user}@{self.host}:{self.port} "
                f"failed: {e}") from e

        transport = paramiko.Transport(sock)
        transport.use_compression(True)
        if force_ssh_rsa:
            # Enable the legacy ssh-rsa (SHA-1) public-key signature for auth.
            transport._preferred_pubkeys = ("ssh-rsa",)

        try:
            transport.start_client(timeout=self.connect_timeout)
        except paramiko.SSHException as e:
            transport.close()
            return None, e
        except Exception as e:
            transport.close()
            raise SSHProxyError(
                f"SSH connection to {self.user}@{self.host}:{self.port} "
                f"failed: {e}") from e

        try:
            self._verify_host_key(paramiko, transport)
        except Exception:
            transport.close()
            raise

        try:
            transport.auth_publickey(self.user, pkey)
        except paramiko.SSHException as e:
            transport.close()
            return None, e

        if not transport.is_authenticated():
            transport.close()
            return None, paramiko.AuthenticationException(
                "authentication failed")
        return transport, None

    def _verify_host_key(self, paramiko, transport) -> None:
        """Reject unknown host keys against ``known_hosts``; else trust-on-use.

        With no ``known_hosts`` configured this is a no-op — the equivalent of
        ``-o StrictHostKeyChecking=no``.
        """
        if not self.known_hosts:
            return
        server_key = transport.get_remote_server_key()
        hostkeys = paramiko.HostKeys(self.known_hosts)
        entry = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
        if not hostkeys.check(entry, server_key):
            raise SSHProxyError(
                f"host key verification failed for {entry} "
                f"(not in {self.known_hosts})")

    def _start_listener(self) -> None:
        """Bind the local proxy socket and spawn the accept thread.

        Assumes ``self._transport`` is already set (by :meth:`start`, or
        injected directly in tests).
        """
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(64)
        server.settimeout(1.0)

        self._server = server
        self._local_port = server.getsockname()[1]
        self._closing = threading.Event()
        self._thread = threading.Thread(
            target=self._accept_loop, name="ssh-proxy", daemon=True)
        self._thread.start()

    def requests_proxies(self) -> dict[str, str]:
        """The ``proxies`` mapping pointing ``requests`` at this proxy."""
        if not self._local_port:
            raise SSHProxyError("SSH proxy not started")
        url = f"http://127.0.0.1:{self._local_port}"
        return {"http": url, "https": url}

    def close(self) -> None:
        if self._closing is not None:
            self._closing.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - best-effort teardown
                pass
            self._client = None
        self._transport = None

    def __enter__(self) -> "SSHProxy":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- proxy internals ---------------------------------------------------

    def _accept_loop(self) -> None:
        assert self._server is not None and self._closing is not None
        while not self._closing.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            head = self._read_head(conn)
            if head is None:
                return
            request_line, rest = head
            method, target, _ = request_line.split(" ", 2)
            if method.upper() == "CONNECT":
                dest_host, dest_port = self._parse_authority(target)
                channel = self._open_channel(dest_host, dest_port)
                if channel is None:
                    conn.sendall(
                        b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                # Absolute-form request (plain HTTP through the proxy).
                parts = urlsplit(target)
                dest_host = parts.hostname or ""
                dest_port = parts.port or 80
                channel = self._open_channel(dest_host, dest_port)
                if channel is None:
                    conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                version = request_line.split(" ", 2)[2]
                origin_line = f"{method} {path} {version}\r\n"
                channel.sendall(origin_line.encode("latin-1") + rest)
            self._relay(conn, channel)
        except Exception as e:  # pragma: no cover - per-connection isolation
            logger.debug("proxy connection error: %s", e)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _read_head(conn: socket.socket) -> tuple[str, bytes] | None:
        """Read up to the end of the HTTP request head (``\\r\\n\\r\\n``).

        Returns the request line and any bytes already read past the head
        (e.g. a request body), or ``None`` if the client hung up early.
        """
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(_BUF)
            if not chunk:
                return None
            buf += chunk
            if len(buf) > 65536:  # guard against a runaway/garbage client
                return None
        line, _, after = buf.partition(b"\r\n")
        return line.decode("latin-1"), after

    @staticmethod
    def _parse_authority(authority: str) -> tuple[str, int]:
        """Split ``host:port`` from a CONNECT target (port defaults to 443)."""
        if ":" in authority:
            host, _, port = authority.rpartition(":")
            return host, int(port)
        return authority, 443

    def _open_channel(self, dest_host: str, dest_port: int):
        if self._transport is None:
            return None
        try:
            return self._transport.open_channel(
                "direct-tcpip", (dest_host, dest_port), ("127.0.0.1", 0))
        except Exception as e:
            logger.warning("SSH channel to %s:%d failed: %s",
                           dest_host, dest_port, e)
            return None

    def _relay(self, conn: socket.socket, channel) -> None:
        """Pump bytes between the local client socket and the SSH channel."""
        conn.setblocking(False)
        channel.setblocking(False)
        closing = self._closing
        while closing is None or not closing.is_set():
            try:
                readable, _, _ = select.select(
                    [conn, channel], [], [], _RELAY_TIMEOUT)
            except (OSError, ValueError):
                break
            if conn in readable:
                try:
                    data = conn.recv(_BUF)
                except (BlockingIOError, InterruptedError):
                    data = None  # spurious wakeup; nothing to forward
                except OSError:
                    break
                if data:
                    channel.sendall(data)
                elif data == b"":
                    break  # recv returned empty -> client closed
            if channel in readable:
                try:
                    data = channel.recv(_BUF)
                except (BlockingIOError, InterruptedError):
                    data = None  # spurious wakeup; nothing to forward
                except OSError:
                    break
                if data:
                    conn.sendall(data)
                elif data == b"":
                    break  # channel at EOF -> remote closed
        try:
            channel.close()
        except Exception:  # pragma: no cover
            pass
