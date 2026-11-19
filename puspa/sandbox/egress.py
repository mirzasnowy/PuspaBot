"""Filtered egress proxy for the sandbox (E-03, plan 4.3).

Plan 4.3 requires the network allowlist to be enforced through
an egress proxy *Puspa controls*, not just a firewall: pinned
DNS, blocked internal ranges and cloud metadata endpoints, and
every outbound request logged to the ledger. Before E-03 the
runner refused any non-empty network policy outright, because
no such proxy existed (see ``docs/sandbox-deepening.md``,
honest limit 2). This module is that proxy:

- :class:`EgressProxy` — a small HTTP proxy (standard library
  only) that speaks ``CONNECT`` (for ``https://`` targets) and
  absolute-form requests (for ``http://`` targets). Every
  request is decided against the session's
  :class:`~puspa.sandbox.policy.NetworkPolicy` (deny-by-default,
  explicit ``host:port`` endpoints): a request outside the
  policy is refused with 403 and logged, never forwarded.
- Pinned DNS: the proxy resolves admitted hostnames itself with
  :class:`PinnedDNSResolver`, a minimal UDP DNS client that
  talks only to the configured resolver addresses (default
  ``1.1.1.1`` and ``8.8.8.8``). The container never resolves
  names: in proxy mode the runner passes ``--dns 127.0.0.1``
  (the container's own resolver is dead) and
  ``--add-host host.docker.internal:host-gateway`` (the proxy
  itself is reached by a static ``/etc/hosts`` entry), so the
  only DNS that happens is the proxy's, against pinned servers.
- Blocked ranges: after resolving, :func:`is_blocked_ip`
  refuses any address that is not globally routable
  (RFC 1918, loopback, link-local — including the cloud
  metadata address ``169.254.169.254`` — CGNAT, multicast,
  reserved, documentation ranges). A hostname that resolves
  only to blocked addresses is refused, so a DNS rebinding
  trick cannot smuggle the agent onto the internal network.
  An unparseable address is treated as blocked (fail-closed).
- Ledger: every request — allowed or denied — is recorded once
  in an :class:`EgressLedger`, an append-only JSON-lines file
  where each entry carries the previous entry's hash, so the
  record is tamper-evident. That file *is* the egress ledger:
  the Postgres :class:`puspa.ledger.ledger.Ledger` has a closed
  set of 31 event types with no network event, so bridging
  these entries into Postgres needs a new event type (an
  operator/schema decision for a follow-up, not something to
  smuggle in under an unrelated type here).
- Network: the proxy is meant to sit on a Docker ``--internal``
  network (see :meth:`EgressProxy.ensure_internal_network`),
  which has no external route at all — the container cannot
  bypass the proxy even if it tried, because the network itself
  goes nowhere except the host gateway where the proxy listens.

The proxy never fetches on the agent's behalf beyond the
single requested connection: it opens one TCP connection to
the resolved address and relays bytes. It holds no credentials
and injects none (credential injection at the proxy is
OpenShell's job, not Puspa's; Puspa credentials travel through
:mod:`puspa.computer.broker`).

Honest limit: no live container has run through this proxy on
this VM (Docker cannot run here). The proxy itself is tested
live in-process (real sockets, real CONNECT tunnelling to a
local test server); the container-side wiring (``--network``,
``--dns``, ``--add-host``) is flag derivation tested here and
awaits a Docker host (E-02's VPS) for live verification.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import random
import shutil
import socket
import struct
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .policy import NetworkPolicy

__all__ = [
    "DEFAULT_DNS_SERVERS",
    "DEFAULT_NETWORK_NAME",
    "DNSResolutionError",
    "EgressError",
    "EgressLedger",
    "EgressLogEntry",
    "EgressLogSink",
    "EgressProxy",
    "InMemoryEgressSink",
    "PinnedDNSResolver",
    "is_blocked_ip",
]

#: Pinned DNS resolvers (Cloudflare, Google). The proxy talks to
#: these addresses only; the container's own resolver is dead in
#: proxy mode, so no other resolver can be used.
DEFAULT_DNS_SERVERS: tuple[str, ...] = ("1.1.1.1", "8.8.8.8")

#: Docker network the proxy-mode container runs on (created with
#: ``--internal``: no external route, so the proxy is the only
#: way out).
DEFAULT_NETWORK_NAME = "puspa-egress"

#: The proxy's hostname inside the container (static /etc/hosts
#: entry pointing at the host gateway; no DNS lookup needed).
PROXY_HOST_IN_CONTAINER = "host.docker.internal"

#: Dead resolver given to the container: its own DNS never
#: answers, so all name resolution happens in the proxy.
CONTAINER_DEAD_DNS = "127.0.0.1"

_CONNECT_TIMEOUT_S = 10.0
_RELAY_TIMEOUT_S = 30.0
_DNS_TIMEOUT_S = 5.0
_MAX_DNS_PACKET = 4096


class EgressError(ValueError):
    """Egress proxy misconfiguration or setup failure.

    Raised before any container is built (missing network,
    proxy not running when its flags are needed).
    """


class DNSResolutionError(Exception):
    """Pinned DNS resolution failed for a hostname."""


def is_blocked_ip(value: str) -> bool:
    """True when ``value`` must never be dialled from a sandbox.

    Blocked means "not globally routable": private (RFC 1918),
    loopback, link-local (including the cloud metadata address
    ``169.254.169.254``), CGNAT, multicast, reserved, and
    documentation ranges. An unparseable value is blocked too
    (fail-closed: never dial what cannot be classified).
    """
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return True
    # `is_global` is True for multicast in CPython's ipaddress,
    # so multicast is excluded explicitly.
    return not address.is_global or address.is_multicast


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- pinned DNS (minimal UDP client, standard library only) ------------------


def _dns_query_name(host: str) -> bytes:
    labels = host.rstrip(".").split(".")
    out = bytearray()
    for label in labels:
        encoded = label.encode("ascii")
        if not encoded or len(encoded) > 63:
            raise DNSResolutionError(f"invalid DNS label in host {host!r}")
        out.append(len(encoded))
        out += encoded
    out.append(0)
    return bytes(out)


def _dns_build_query(host: str, qtype: int, txid: int) -> bytes:
    header = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    question = _dns_query_name(host) + struct.pack(">HH", qtype, 1)
    return header + question


def _dns_read_name(packet: bytes, offset: int) -> tuple[str, int]:
    """Read a (possibly compressed) DNS name at ``offset``.

    Returns the name and the offset just past the name field
    (following a compression pointer does not advance past it).
    """
    labels: list[str] = []
    next_offset = offset
    jumped = False
    while True:
        if offset >= len(packet):
            raise DNSResolutionError("truncated DNS packet while reading a name")
        length = packet[offset]
        if length == 0:
            if not jumped:
                next_offset = offset + 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise DNSResolutionError("truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            if not jumped:
                next_offset = offset + 2
            offset = pointer
            jumped = True
            continue
        if length & 0xC0:
            raise DNSResolutionError("invalid DNS label length")
        end = offset + 1 + length
        if end > len(packet):
            raise DNSResolutionError("truncated DNS label")
        labels.append(packet[offset + 1 : end].decode("ascii", "replace"))
        offset = end
    return ".".join(labels), next_offset


def _dns_parse_answers(packet: bytes, txid: int, qtype: int) -> list[str]:
    """Extract A/AAAA addresses from one DNS response packet."""
    if len(packet) < 12:
        raise DNSResolutionError("DNS response too short")
    resp_txid, flags, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", packet[:12])
    if resp_txid != txid:
        raise DNSResolutionError("DNS response transaction id mismatch")
    if flags & 0x000F != 0:
        raise DNSResolutionError(f"DNS server returned error code {flags & 0x000F}")
    offset = 12
    for _ in range(qdcount):
        _, offset = _dns_read_name(packet, offset)
        if offset + 4 > len(packet):
            raise DNSResolutionError("truncated DNS question section")
        offset += 4
    addresses: list[str] = []
    for _ in range(ancount):
        _, offset = _dns_read_name(packet, offset)
        if offset + 10 > len(packet):
            raise DNSResolutionError("truncated DNS answer section")
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", packet[offset : offset + 10])
        offset += 10
        if offset + rdlength > len(packet):
            raise DNSResolutionError("truncated DNS record data")
        rdata = packet[offset : offset + rdlength]
        offset += rdlength
        if rtype == qtype and rtype == 1 and len(rdata) == 4:
            addresses.append(str(ipaddress.IPv4Address(rdata)))
        elif rtype == qtype and rtype == 28 and len(rdata) == 16:
            addresses.append(str(ipaddress.IPv6Address(rdata)))
    return addresses


class PinnedDNSResolver:
    """Resolve hostnames using only the pinned DNS servers.

    A and AAAA are asked in turn (A first); servers are tried
    in order until one answers. A literal IP address is
    returned as-is (no DNS traffic for it). Any failure raises
    :class:`DNSResolutionError` — the caller refuses the request
    rather than falling back to the system resolver.
    ``allow_test_loopback`` is a test-only escape (a fake DNS
    server on 127.0.0.1); it must never be set on a production
    resolver.
    """

    def __init__(
        self,
        servers: tuple[str, ...] = DEFAULT_DNS_SERVERS,
        *,
        timeout_s: float = _DNS_TIMEOUT_S,
        port: int = 53,
        allow_test_loopback: bool = False,
    ) -> None:
        if not servers:
            raise EgressError("pinned DNS needs at least one server address")
        for server in servers:
            if is_blocked_ip(server) and not (
                allow_test_loopback and server in ("127.0.0.1", "::1")
            ):
                raise EgressError(
                    f"pinned DNS server {server!r} is not a global address; "
                    "a resolver must be a public IP"
                )
        if timeout_s <= 0:
            raise EgressError("DNS timeout_s must be > 0")
        if not 1 <= port <= 65535:
            raise EgressError(f"DNS port must be 1-65535, got {port!r}")
        self.servers = tuple(servers)
        self.timeout_s = timeout_s
        self.port = port

    def _ask(self, host: str, qtype: int, server: str) -> list[str]:
        txid = random.randint(0, 65535)
        query = _dns_build_query(host, qtype, txid)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout_s)
            sock.sendto(query, (server, self.port))
            packet, _ = sock.recvfrom(_MAX_DNS_PACKET)
        return _dns_parse_answers(packet, txid, qtype)

    def resolve(self, host: str) -> list[str]:
        """IP addresses for ``host`` (A then AAAA, de-duplicated)."""
        cleaned = (host or "").strip().rstrip(".")
        if not cleaned:
            raise DNSResolutionError("cannot resolve an empty hostname")
        try:
            ipaddress.ip_address(cleaned)
        except ValueError:
            pass
        else:
            return [cleaned]
        errors: list[str] = []
        for qtype in (1, 28):
            for server in self.servers:
                try:
                    found = self._ask(cleaned, qtype, server)
                except (OSError, DNSResolutionError, struct.error) as exc:
                    errors.append(f"{server}: {exc}")
                    continue
                if found:
                    return list(dict.fromkeys(found))
        raise DNSResolutionError(
            f"pinned DNS could not resolve {cleaned!r}: {'; '.join(errors) or 'no answer'}"
        )


# --- egress log (append-only, hash-chained) -----------------------------------


@dataclass(frozen=True)
class EgressLogEntry:
    """One proxied request: the decision and what it was based on.

    ``allowed`` False means the request never left the proxy.
    ``resolved_ip`` is the address the proxy dialled (or would
    have dialled) — recorded so a rebinding attempt stays
    visible even when it was refused.
    """

    ts: float
    session_id: str
    user_id: str
    method: str
    host: str
    port: int
    allowed: bool
    reason: str
    resolved_ip: str | None = None

    def to_dict(self) -> dict:
        """Plain-dict form (JSON-serialisable, key order irrelevant)."""
        return {
            "ts": self.ts,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "method": self.method,
            "host": self.host,
            "port": self.port,
            "allowed": self.allowed,
            "reason": self.reason,
            "resolved_ip": self.resolved_ip,
        }


class EgressLogSink:
    """Where egress decisions are recorded (one entry per request)."""

    def record(self, entry: EgressLogEntry) -> None:
        """Record ``entry``. Must not raise for a logging failure
        in a way that opens the request; sinks that cannot record
        raise, and the proxy treats a logging failure as a
        refusal (see :class:`EgressProxy`)."""
        raise NotImplementedError


class InMemoryEgressSink(EgressLogSink):
    """A sink that keeps entries in memory (tests and embedding)."""

    def __init__(self) -> None:
        self.entries: list[EgressLogEntry] = []

    def record(self, entry: EgressLogEntry) -> None:
        self.entries.append(entry)


class EgressLedger(EgressLogSink):
    """The egress ledger: append-only JSON lines, hash-chained.

    Each line is one entry plus ``prev_hash`` (the previous
    line's ``entry_hash``, or ``"GENESIS"``) and ``entry_hash``
    (SHA-256 of the canonical JSON of the entry plus
    ``prev_hash``). Tampering with a line breaks every later
    hash; :meth:`verify_chain` checks the whole file. A file
    whose tail is corrupt is refused at open (fail-closed:
    never append to a broken chain silently).
    """

    GENESIS = "GENESIS"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._prev_hash = self._load_tail_hash()

    def _load_tail_hash(self) -> str:
        if not self.path.exists():
            return self.GENESIS
        try:
            lines = [
                line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
        except OSError as exc:
            raise EgressError(f"egress ledger is not readable: {self.path} ({exc})") from exc
        if not lines:
            return self.GENESIS
        try:
            tail = json.loads(lines[-1])
            entry_hash = tail["entry_hash"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise EgressError(
                f"egress ledger tail is corrupt, refusing to append: {self.path} ({exc})"
            ) from exc
        if not isinstance(entry_hash, str) or not entry_hash:
            raise EgressError(f"egress ledger tail has no entry_hash: {self.path}")
        return entry_hash

    def record(self, entry: EgressLogEntry) -> None:
        """Append ``entry`` to the ledger (thread-safe)."""
        payload = dict(entry.to_dict())
        payload["prev_hash"] = self._prev_hash
        payload["entry_hash"] = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        line = _canonical_json(payload) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self._prev_hash = payload["entry_hash"]

    def entries(self) -> list[dict]:
        """All ledger lines, parsed (oldest first)."""
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def verify_chain(self) -> bool:
        """True when every entry's hash links to its predecessor."""
        previous = self.GENESIS
        for record in self.entries():
            expected_prev = record.get("prev_hash")
            if expected_prev != previous:
                return False
            payload = {key: value for key, value in record.items() if key != "entry_hash"}
            if hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest() != record.get(
                "entry_hash"
            ):
                return False
            previous = record["entry_hash"]
        return True


# --- the proxy -----------------------------------------------------------------


@dataclass
class EgressProxyConfig:
    """How the proxy is reached from inside a container.

    ``network_name`` is the Docker ``--internal`` network the
    container runs on; ``proxy_host`` is the hostname the
    container uses for the proxy (a static ``/etc/hosts`` entry,
    never DNS). ``dns_servers`` are the pinned resolvers.
    """

    network_name: str = DEFAULT_NETWORK_NAME
    proxy_host: str = PROXY_HOST_IN_CONTAINER
    dns_servers: tuple[str, ...] = DEFAULT_DNS_SERVERS

    def __post_init__(self) -> None:
        if not self.network_name or not self.proxy_host:
            raise EgressError("egress proxy network_name and proxy_host must be non-empty")
        if not self.dns_servers:
            raise EgressError("egress proxy needs at least one pinned DNS server")


class _ProxyHandler(BaseHTTPRequestHandler):
    """One proxied request. All decisions go through the proxy.

    Only two shapes are served: ``CONNECT host:port`` and
    absolute-form requests (``GET http://host:port/path``).
    Anything else is refused (405) and logged: a proxy that
    silently served origin-form requests would be an open relay
    for whatever the container sent it.
    """

    protocol_version = "HTTP/1.1"
    _hop_by_hop = frozenset(
        {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
        }
    )

    # -- plumbing ------------------------------------------------------------

    @property
    def proxy(self) -> EgressProxy:
        """The owning proxy (set on the server by :meth:`EgressProxy.start`)."""
        return self.server.proxy  # type: ignore[attr-defined]

    def log_message(self, *args: object) -> None:
        """Silence the default stderr log (the sink is the log)."""

    def _log(
        self,
        *,
        method: str,
        host: str,
        port: int,
        allowed: bool,
        reason: str,
        resolved_ip: str | None = None,
    ) -> None:
        import time

        entry = EgressLogEntry(
            ts=time.time(),
            session_id=self.proxy.session_id,
            user_id=self.proxy.user_id,
            method=method,
            host=host,
            port=port,
            allowed=allowed,
            reason=reason,
            resolved_ip=resolved_ip,
        )
        self.proxy.sink.record(entry)

    def _deny(
        self,
        *,
        method: str,
        host: str,
        port: int,
        reason: str,
        code: int,
        resolved_ip: str | None = None,
    ) -> None:
        self._log(
            method=method,
            host=host,
            port=port,
            allowed=False,
            reason=reason,
            resolved_ip=resolved_ip,
        )
        body = f"egress denied: {reason}\n".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _decide(self, host: str, port: int) -> tuple[str | None, str | None]:
        """Policy + DNS + blocked-range decision.

        Returns ``(ip, error)``: the address to dial, or an
        error reason when the request must be refused (``ip``
        is then the resolved address, if one was known, for the
        log). A policy miss is refused before any DNS happens,
        so denied hosts are never even resolved.
        """
        if not self.proxy.policy.allows(host, port):
            return None, "not_in_policy"
        try:
            resolved = self.proxy.resolver.resolve(host)
        except DNSResolutionError as exc:
            return None, f"dns_failed: {exc}"
        for candidate in resolved:
            if self.proxy.allow_test_loopback and candidate in (
                "127.0.0.1",
                "::1",
            ):
                return candidate, None
            if not is_blocked_ip(candidate):
                return candidate, None
        return resolved[0] if resolved else None, "resolved_ip_blocked"

    def _relay(self, left: socket.socket, right: socket.socket) -> None:
        """Pump bytes both ways until one side closes (or errors)."""

        def _pump(source: socket.socket, target: socket.socket) -> None:
            try:
                while True:
                    chunk = source.recv(65536)
                    if not chunk:
                        break
                    target.sendall(chunk)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    target.shutdown(socket.SHUT_WR)

        worker = threading.Thread(target=_pump, args=(left, right), daemon=True)
        worker.start()
        _pump(right, left)
        worker.join(timeout=5)

    # -- CONNECT (https) -------------------------------------------------------

    def do_CONNECT(self) -> None:
        host, port = _split_authority(self.path)
        if host is None or port is None:
            self._deny(
                method="CONNECT",
                host=self.path,
                port=0,
                reason="malformed_authority",
                code=400,
            )
            return
        ip, error = self._decide(host, port)
        if error is not None:
            self._deny(
                method="CONNECT",
                host=host,
                port=port,
                reason=error,
                code=403,
                resolved_ip=ip,
            )
            return
        assert ip is not None
        try:
            upstream = socket.create_connection((ip, port), timeout=_CONNECT_TIMEOUT_S)
        except OSError as exc:
            self._deny(
                method="CONNECT",
                host=host,
                port=port,
                reason=f"connect_failed: {exc}",
                code=502,
                resolved_ip=ip,
            )
            return
        self._log(
            method="CONNECT",
            host=host,
            port=port,
            allowed=True,
            reason="allowed",
            resolved_ip=ip,
        )
        try:
            self.send_response(200, "Connection Established")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.connection.settimeout(_RELAY_TIMEOUT_S)
            upstream.settimeout(_RELAY_TIMEOUT_S)
            self._relay(self.connection, upstream)
        finally:
            upstream.close()
        self.close_connection = True

    # -- absolute-form (http) ----------------------------------------------------

    def _handle_absolute_form(self, method: str) -> None:
        parsed = urlparse(self.path)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            self._deny(
                method=method,
                host=self.path,
                port=0,
                reason="not_absolute_form",
                code=405,
            )
            return
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ip, error = self._decide(host, port)
        if error is not None:
            self._deny(
                method=method,
                host=host,
                port=port,
                reason=error,
                code=403,
                resolved_ip=ip,
            )
            return
        assert ip is not None
        try:
            upstream = socket.create_connection((ip, port), timeout=_CONNECT_TIMEOUT_S)
        except OSError as exc:
            self._deny(
                method=method,
                host=host,
                port=port,
                reason=f"connect_failed: {exc}",
                code=502,
                resolved_ip=ip,
            )
            return
        self._log(
            method=method,
            host=host,
            port=port,
            allowed=True,
            reason="allowed",
            resolved_ip=ip,
        )
        try:
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            request_lines = [f"{method} {target} HTTP/1.1", f"Host: {host}"]
            for name, value in self.headers.items():
                if name.lower() in self._hop_by_hop or name.lower() == "host":
                    continue
                request_lines.append(f"{name}: {value}")
            request_lines.append("Connection: close")
            request_lines.append("")
            request_lines.append("")
            upstream.sendall("\r\n".join(request_lines).encode("utf-8", "surrogateescape"))
            length = self.headers.get("Content-Length")
            if length is not None:
                remaining = int(length)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    upstream.sendall(chunk)
                    remaining -= len(chunk)
            upstream.settimeout(_RELAY_TIMEOUT_S)
            self.connection.settimeout(_RELAY_TIMEOUT_S)
            self._relay(self.connection, upstream)
        finally:
            upstream.close()
        self.close_connection = True

    def do_GET(self) -> None:
        """Absolute-form GET (``GET http://host:port/path``)."""
        self._handle_absolute_form("GET")

    def do_POST(self) -> None:
        """Absolute-form POST."""
        self._handle_absolute_form("POST")

    def do_PUT(self) -> None:
        """Absolute-form PUT."""
        self._handle_absolute_form("PUT")

    def do_DELETE(self) -> None:
        """Absolute-form DELETE."""
        self._handle_absolute_form("DELETE")

    def do_PATCH(self) -> None:
        """Absolute-form PATCH."""
        self._handle_absolute_form("PATCH")

    def do_HEAD(self) -> None:
        """Absolute-form HEAD."""
        self._handle_absolute_form("HEAD")

    def do_OPTIONS(self) -> None:
        """Absolute-form OPTIONS."""
        self._handle_absolute_form("OPTIONS")


def _split_authority(authority: str) -> tuple[str | None, int | None]:
    """Split a CONNECT authority (``host:port``) into its parts."""
    value = (authority or "").strip()
    if not value:
        return None, None
    if value.startswith("["):
        close = value.find("]")
        if close < 0 or not value[close + 1 :].startswith(":"):
            return None, None
        host, port_text = value[1:close], value[close + 2 :]
    elif value.count(":") == 1:
        host, port_text = value.rsplit(":", 1)
    else:
        return None, None
    try:
        port = int(port_text)
    except ValueError:
        return None, None
    if not host or not 1 <= port <= 65535:
        return None, None
    return host.lower().rstrip("."), port


class EgressProxy:
    """The filtered egress proxy: policy check + pinned DNS + log.

    ``policy`` is the session's
    :class:`~puspa.sandbox.policy.NetworkPolicy` (deny-by-default).
    ``resolver`` defaults to a :class:`PinnedDNSResolver` over
    ``config.dns_servers``; tests inject a fake resolver.
    ``sink`` defaults to an in-memory sink; pass an
    :class:`EgressLedger` for the durable hash-chained record.
    ``allow_test_loopback`` is a test-only escape (the local
    test server listens on 127.0.0.1, which the proxy otherwise
    refuses like any loopback address); it must never be set on
    a production proxy.
    """

    def __init__(
        self,
        policy: NetworkPolicy,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        config: EgressProxyConfig | None = None,
        resolver: PinnedDNSResolver | None = None,
        sink: EgressLogSink | None = None,
        session_id: str = "",
        user_id: str = "",
        allow_test_loopback: bool = False,
    ) -> None:
        if not isinstance(policy, NetworkPolicy):
            raise EgressError("egress proxy needs a NetworkPolicy")
        self.policy = policy
        self.host = host
        self.port = port
        self.config = config if config is not None else EgressProxyConfig()
        self.resolver = (
            resolver if resolver is not None else PinnedDNSResolver(self.config.dns_servers)
        )
        self.sink = sink if sink is not None else InMemoryEgressSink()
        self.session_id = session_id
        self.user_id = user_id
        self.allow_test_loopback = allow_test_loopback
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True while the proxy is listening."""
        return self._server is not None

    @property
    def listen_port(self) -> int:
        """The port the proxy listens on (0 before :meth:`start`)."""
        if self._server is None:
            return 0
        return int(self._server.server_address[1])

    @property
    def proxy_url(self) -> str:
        """The proxy URL on this host (for operator tooling)."""
        return f"http://{self.host}:{self.listen_port}"

    @property
    def container_proxy_url(self) -> str:
        """The proxy URL as the container sees it (static host)."""
        return f"http://{self.config.proxy_host}:{self.listen_port}"

    def start(self) -> None:
        """Bind and serve in a background thread (idempotent)."""
        if self._server is not None:
            return
        server = ThreadingHTTPServer((self.host, self.port), _ProxyHandler)
        server.daemon_threads = True
        server.proxy = self  # type: ignore[attr-defined]
        self._server = server
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Stop serving and release the port (idempotent)."""
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    # -- Docker wiring ----------------------------------------------------------

    def container_network_flags(self) -> list[str]:
        """``docker run`` flags that route the container via this proxy.

        Raises :class:`EgressError` when the proxy is not
        running: flags without a live proxy would point the
        container at a dead listener, and a container with a
        dead proxy must fail at build time, not at first request.
        """
        if not self.is_running:
            raise EgressError(
                "egress proxy is not running; start it before building proxy-mode container flags"
            )
        url = self.container_proxy_url
        return [
            "--network",
            self.config.network_name,
            "--dns",
            CONTAINER_DEAD_DNS,
            "--add-host",
            f"{self.config.proxy_host}:host-gateway",
            "--env",
            f"http_proxy={url}",
            "--env",
            f"https_proxy={url}",
            "--env",
            f"HTTP_PROXY={url}",
            "--env",
            f"HTTPS_PROXY={url}",
        ]

    def ensure_internal_network(self, *, docker_cmd: list[str] | None = None) -> None:
        """Create the ``--internal`` Docker network if it is missing.

        Idempotent: an existing network is left alone. Raises
        :class:`EgressError` when Docker is absent or the
        network cannot be created — proxy mode then refuses to
        run, rather than running on a network with a route out.
        """
        cmd = docker_cmd if docker_cmd is not None else self._docker_cmd()
        if cmd is None:
            raise EgressError(
                "docker binary not found; cannot create the internal "
                f"egress network {self.config.network_name!r}"
            )
        name = self.config.network_name
        try:
            inspect = subprocess.run(
                [*cmd, "network", "inspect", name],
                check=False,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EgressError(f"could not inspect Docker network {name!r}: {exc}") from exc
        if inspect.returncode == 0:
            return
        try:
            created = subprocess.run(
                [*cmd, "network", "create", "--internal", name],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EgressError(f"could not create Docker network {name!r}: {exc}") from exc
        if created.returncode != 0:
            raise EgressError(
                f"could not create internal Docker network {name!r}: "
                f"{created.stderr.strip() or created.stdout.strip()}"
            )

    @staticmethod
    def _docker_cmd() -> list[str] | None:
        path = shutil.which("docker")
        return [path] if path is not None else None
