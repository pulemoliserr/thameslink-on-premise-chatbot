"""On-premise network guard (IT & data protection: "no data leaves Thameslink").

Once installed, every outgoing network connection the app process makes is checked. Connections
to this machine (localhost) and to private network addresses (10.x, 172.16–31.x, 192.168.x, …)
are allowed, so a local Ollama server works. Anything else is refused ("block") or only recorded
("monitor"), and every attempt is counted so IT can see it in the app.

This is a safety net inside the app, not a replacement for a firewall: the server's firewall
should block outgoing traffic too.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time

from . import config

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_lock = threading.Lock()
STATE = {"mode": "off", "installed_at": None, "attempts": []}     # attempts: [{time, host, port, blocked}]


def is_internal(host: str) -> bool:
    if host in config.ALLOWED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return host in ("localhost",)
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _check(address) -> None:
    if not isinstance(address, tuple) or not address:           # Unix sockets etc.
        return
    host, port = str(address[0]), address[1] if len(address) > 1 else None
    if STATE["mode"] == "off" or is_internal(host):
        return
    blocked = STATE["mode"] == "block"
    with _lock:
        STATE["attempts"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "host": host, "port": port,
                                  "blocked": blocked})
        del STATE["attempts"][:-200]
    if blocked:
        raise ConnectionRefusedError(f"Blocked by the on-premise network guard: {host}:{port} is outside the network")


def _connect(self, address):
    _check(address)
    return _original_connect(self, address)


def _connect_ex(self, address):
    _check(address)
    return _original_connect_ex(self, address)


def install(mode: str | None = None) -> str:
    """Install the guard (idempotent). mode: block | monitor | off (default: config.NETWORK_GUARD)."""
    mode = (mode or config.NETWORK_GUARD).lower()
    if mode not in ("block", "monitor", "off"):
        mode = "block"
    STATE["mode"] = mode
    if mode != "off" and socket.socket.connect is not _connect:
        socket.socket.connect = _connect
        socket.socket.connect_ex = _connect_ex
        STATE["installed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return mode


def uninstall() -> None:
    socket.socket.connect = _original_connect
    socket.socket.connect_ex = _original_connect_ex
    STATE["mode"] = "off"


def external_attempts(since: float | None = None) -> list[dict]:
    return list(STATE["attempts"])


class strict:
    """Context manager: block external connections for the duration (used by IT's on-premise check)."""

    def __enter__(self):
        self.prev = STATE["mode"]
        self.start = len(STATE["attempts"])
        install("block")
        return self

    def __exit__(self, *exc):
        if self.prev == "off":
            uninstall()
        else:
            install(self.prev)
        return False

    @property
    def attempts(self) -> list[dict]:
        return STATE["attempts"][self.start:]
