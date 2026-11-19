"""E-03: filtered egress proxy (plan 4.3).

The proxy itself runs live in-process (real sockets, real
CONNECT tunnelling to a local test server); the container-side
flag derivation is tested in test_sandbox_e03_runner.py, and
live container behaviour awaits a Docker host.
"""

from __future__ import annotations

import contextlib
import json
import socket
import struct
import threading

import pytest

from puspa.sandbox.egress import (
    DNSResolutionError,
    EgressError,
    EgressLedger,
    EgressLogEntry,
    EgressProxy,
    EgressProxyConfig,
    InMemoryEgressSink,
    PinnedDNSResolver,
    is_blocked_ip,
)
from puspa.sandbox.policy import NetworkEndpoint, NetworkPolicy

ALLOWED_HOST = "api.example.test"
ALLOWED_PORT = 8443
ALLOWED_HTTP_PORT = 80


def _policy(
    *, connect_port: int = ALLOWED_PORT, http_port: int = ALLOWED_HTTP_PORT
) -> NetworkPolicy:
    return NetworkPolicy(
        endpoints=(
            NetworkEndpoint(host=ALLOWED_HOST, port=connect_port),
            NetworkEndpoint(host=ALLOWED_HOST, port=http_port),
        )
    )


class FakeResolver:
    """A resolver with canned answers (and a call record)."""

    def __init__(self, mapping=None, *, error=None) -> None:
        self.mapping = mapping or {}
        self.error = error
        self.calls: list[str] = []

    def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        if self.error is not None:
            raise self.error
        if host not in self.mapping:
            raise DNSResolutionError(f"fake resolver knows nothing about {host!r}")
        return list(self.mapping[host])


class EchoServer:
    """A TCP server that echoes bytes back (the CONNECT target)."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.received: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        with contextlib.suppress(OSError):
            self.sock.close()
        self._thread.join(timeout=5)
        return False

    def _serve(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (OSError, TimeoutError):
                continue
            self.connections += 1
            threading.Thread(target=self._echo, args=(conn,), daemon=True).start()

    def _echo(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5)
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.received.append(chunk)
                    conn.sendall(chunk)
            except OSError:
                pass


class HttpServer:
    """A TCP server that answers one fixed HTTP response per request."""

    RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello"

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.requests: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        with contextlib.suppress(OSError):
            self.sock.close()
        self._thread.join(timeout=5)
        return False

    def _serve(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (OSError, TimeoutError):
                continue
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5)
            try:
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.requests.append(data.decode("utf-8", "replace"))
                conn.sendall(self.RESPONSE)
            except OSError:
                pass


def _read_response(sock: socket.socket) -> tuple[int, bytes]:
    """Read one HTTP response (head + Content-Length body)."""
    data = b""
    sock.settimeout(5)
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    length = 0
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    body = rest
    while len(body) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        body += chunk
    return status, body


@pytest.fixture()
def echo_server():
    with EchoServer() as server:
        yield server


@pytest.fixture()
def http_server():
    with HttpServer() as server:
        yield server


def _start_proxy(policy=None, resolver=None, sink=None, **kwargs):
    proxy = EgressProxy(
        policy if policy is not None else _policy(),
        resolver=resolver,
        sink=sink if sink is not None else InMemoryEgressSink(),
        allow_test_loopback=True,
        **kwargs,
    )
    proxy.start()
    return proxy


# --- blocked IP ranges -----------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",  # cloud metadata endpoint
        "169.254.1.1",  # link-local
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "0.0.0.0",
        "100.64.0.1",  # CGNAT
        "224.0.0.1",  # multicast
        "192.0.2.1",  # documentation
        "198.51.100.1",  # documentation
        "203.0.113.1",  # documentation
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "not-an-ip",
        "",
    ],
)
def test_blocked_ip_ranges(address):
    assert is_blocked_ip(address), f"{address!r} must be blocked"


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2001:4860:4860::8888"])
def test_global_ip_not_blocked(address):
    assert not is_blocked_ip(address)


# --- pinned DNS -------------------------------------------------------------------


class FakeDNSServer:
    """A UDP server that answers A queries with one canned IP."""

    def __init__(self, ip: str | None) -> None:
        self.ip = ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        with contextlib.suppress(OSError):
            self.sock.close()
        self._thread.join(timeout=5)
        return False

    def _serve(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                query, address = self.sock.recvfrom(4096)
            except (OSError, TimeoutError):
                continue
            try:
                self.sock.sendto(self._answer(query), address)
            except OSError:
                continue

    def _answer(self, query: bytes) -> bytes:
        txid = struct.unpack(">H", query[:2])[0]
        offset = 12
        while query[offset] != 0:
            offset += 1 + query[offset]
        offset += 1
        qtype = struct.unpack(">H", query[offset : offset + 2])[0]
        question = query[12 : offset + 4]
        has_answer = self.ip is not None and qtype == 1
        header = struct.pack(">HHHHHH", txid, 0x8180, 1, 1 if has_answer else 0, 0, 0)
        answer = b""
        if has_answer:
            assert self.ip is not None
            answer = struct.pack(">HHHIH", 0xC00C, 1, 1, 60, 4) + socket.inet_aton(self.ip)
        return header + question + answer


def _udp_send_works() -> bool:
    """True when this host allows UDP send (this VM blocks it)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.settimeout(0.5)
        probe.sendto(b"probe", ("127.0.0.1", 9))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def test_pinned_dns_resolves_through_the_fake_server():
    if not _udp_send_works():
        pytest.skip("this VM blocks UDP send (EPERM); live DNS test needs a host with UDP")
    with FakeDNSServer("93.184.216.34") as server:
        resolver = PinnedDNSResolver(
            servers=("127.0.0.1",),
            timeout_s=3,
            port=server.port,
            allow_test_loopback=True,
        )
        assert resolver.resolve("example.com") == ["93.184.216.34"]


def test_dns_wire_format_round_trip_without_sockets():
    """Query building + response parsing, with no UDP at all.

    The live fake-server test above is skipped where this VM
    blocks UDP; this test still proves the wire format: a
    query built by the client, answered by the fake server's
    response builder, parses back to the canned address.
    """
    from puspa.sandbox.egress import _dns_build_query, _dns_parse_answers

    server = FakeDNSServer("93.184.216.34")
    txid = 4242
    query = _dns_build_query("example.com", 1, txid)
    response = server._answer(query)
    assert _dns_parse_answers(response, txid, 1) == ["93.184.216.34"]
    # An empty answer section parses to no addresses.
    empty = FakeDNSServer(None)._answer(query)
    assert _dns_parse_answers(empty, txid, 1) == []


def test_pinned_dns_returns_literal_ips_without_traffic():
    resolver = PinnedDNSResolver(
        servers=("127.0.0.1",), timeout_s=0.2, port=9, allow_test_loopback=True
    )
    assert resolver.resolve("8.8.8.8") == ["8.8.8.8"]


def test_pinned_dns_fails_closed_when_no_server_answers():
    with FakeDNSServer(None) as server:
        resolver = PinnedDNSResolver(
            servers=("127.0.0.1",),
            timeout_s=3,
            port=server.port,
            allow_test_loopback=True,
        )
        with pytest.raises(DNSResolutionError):
            resolver.resolve("example.com")


def test_pinned_dns_rejects_private_server():
    with pytest.raises(EgressError, match="not a global address"):
        PinnedDNSResolver(servers=("10.0.0.53",))


# --- CONNECT tunnelling --------------------------------------------------------------


def test_connect_allowed_host_tunnels_bytes(echo_server):
    sink = InMemoryEgressSink()
    resolver = FakeResolver({ALLOWED_HOST: ["127.0.0.1"]})
    proxy = _start_proxy(
        policy=_policy(connect_port=echo_server.port), resolver=resolver, sink=sink
    )
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(
                f"CONNECT {ALLOWED_HOST}:{echo_server.port} HTTP/1.1\r\n"
                f"Host: {ALLOWED_HOST}:{echo_server.port}\r\n\r\n".encode()
            )
            status, _ = _read_response(client)
            assert status == 200
            client.sendall(b"PING")
            assert client.recv(4) == b"PING"
        finally:
            client.close()
    finally:
        proxy.stop()
    assert echo_server.connections == 1
    assert echo_server.received == [b"PING"]
    assert len(sink.entries) == 1
    entry = sink.entries[0]
    assert entry.allowed and entry.reason == "allowed"
    assert entry.method == "CONNECT" and entry.host == ALLOWED_HOST
    assert entry.port == echo_server.port and entry.resolved_ip == "127.0.0.1"


def test_connect_denied_host_is_refused_before_dns():
    sink = InMemoryEgressSink()
    resolver = FakeResolver({ALLOWED_HOST: ["127.0.0.1"]})
    proxy = _start_proxy(resolver=resolver, sink=sink)
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(b"CONNECT evil.example.com:443 HTTP/1.1\r\n\r\n")
            status, rest = _read_response(client)
            assert status == 403
            assert b"not_in_policy" in rest
        finally:
            client.close()
    finally:
        proxy.stop()
    assert resolver.calls == [], "a denied host must never be resolved"
    assert len(sink.entries) == 1
    entry = sink.entries[0]
    assert not entry.allowed and entry.reason == "not_in_policy"


def test_connect_allowed_host_with_blocked_resolution_is_refused():
    sink = InMemoryEgressSink()
    resolver = FakeResolver({ALLOWED_HOST: ["10.1.2.3"]})
    proxy = _start_proxy(resolver=resolver, sink=sink)
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(f"CONNECT {ALLOWED_HOST}:{ALLOWED_PORT} HTTP/1.1\r\n\r\n".encode())
            status, _ = _read_response(client)
            assert status == 403
        finally:
            client.close()
    finally:
        proxy.stop()
    assert len(sink.entries) == 1
    entry = sink.entries[0]
    assert not entry.allowed and entry.reason == "resolved_ip_blocked"
    assert entry.resolved_ip == "10.1.2.3"


def test_connect_allowed_host_with_metadata_resolution_is_refused():
    """DNS rebinding to the cloud metadata address is refused."""
    sink = InMemoryEgressSink()
    resolver = FakeResolver({ALLOWED_HOST: ["169.254.169.254"]})
    proxy = _start_proxy(resolver=resolver, sink=sink)
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(f"CONNECT {ALLOWED_HOST}:{ALLOWED_PORT} HTTP/1.1\r\n\r\n".encode())
            status, _ = _read_response(client)
            assert status == 403
        finally:
            client.close()
    finally:
        proxy.stop()
    assert sink.entries[0].reason == "resolved_ip_blocked"


def test_connect_dns_failure_is_refused():
    sink = InMemoryEgressSink()
    resolver = FakeResolver(error=DNSResolutionError("no answer"))
    proxy = _start_proxy(resolver=resolver, sink=sink)
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(f"CONNECT {ALLOWED_HOST}:{ALLOWED_PORT} HTTP/1.1\r\n\r\n".encode())
            status, _ = _read_response(client)
            assert status == 403
        finally:
            client.close()
    finally:
        proxy.stop()
    assert sink.entries[0].reason.startswith("dns_failed")


def test_connect_malformed_authority_is_refused():
    sink = InMemoryEgressSink()
    proxy = _start_proxy(sink=sink)
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(b"CONNECT not-a-hostport HTTP/1.1\r\n\r\n")
            status, _ = _read_response(client)
            assert status == 400
        finally:
            client.close()
    finally:
        proxy.stop()
    assert sink.entries[0].reason == "malformed_authority"


def test_loopback_resolution_is_blocked_without_the_test_escape(echo_server):
    sink = InMemoryEgressSink()
    resolver = FakeResolver({ALLOWED_HOST: ["127.0.0.1"]})
    proxy = EgressProxy(_policy(), resolver=resolver, sink=sink, allow_test_loopback=False)
    proxy.start()
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(f"CONNECT {ALLOWED_HOST}:{ALLOWED_PORT} HTTP/1.1\r\n\r\n".encode())
            status, _ = _read_response(client)
            assert status == 403
        finally:
            client.close()
    finally:
        proxy.stop()
    assert echo_server.connections == 0
    assert sink.entries[0].reason == "resolved_ip_blocked"


# --- absolute-form (http) forwarding ---------------------------------------------------


def test_absolute_form_get_is_forwarded(http_server):
    sink = InMemoryEgressSink()
    resolver = FakeResolver({ALLOWED_HOST: ["127.0.0.1"]})
    proxy = _start_proxy(policy=_policy(http_port=http_server.port), resolver=resolver, sink=sink)
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(
                f"GET http://{ALLOWED_HOST}:{http_server.port}/hello HTTP/1.1\r\n"
                f"Host: {ALLOWED_HOST}\r\n\r\n".encode()
            )
            status, body = _read_response(client)
            assert status == 200
            assert body == b"hello"
        finally:
            client.close()
    finally:
        proxy.stop()
    assert http_server.requests
    assert http_server.requests[0].startswith("GET /hello HTTP/1.1")
    assert len(sink.entries) == 1
    assert sink.entries[0].allowed and sink.entries[0].method == "GET"


def test_origin_form_request_is_refused():
    sink = InMemoryEgressSink()
    proxy = _start_proxy(sink=sink)
    try:
        client = socket.create_connection(("127.0.0.1", proxy.listen_port), timeout=5)
        try:
            client.sendall(b"GET /hello HTTP/1.1\r\nHost: example.com\r\n\r\n")
            status, _ = _read_response(client)
            assert status == 405
        finally:
            client.close()
    finally:
        proxy.stop()
    assert sink.entries[0].reason == "not_absolute_form"


# --- ledger ------------------------------------------------------------------------------


def test_egress_ledger_is_append_only_and_hash_chained(tmp_path):
    path = tmp_path / "egress.jsonl"
    ledger = EgressLedger(path)
    ledger.record(
        EgressLogEntry(
            ts=1000.0,
            session_id="s1",
            user_id="u1",
            method="CONNECT",
            host=ALLOWED_HOST,
            port=443,
            allowed=True,
            reason="allowed",
            resolved_ip="93.184.216.34",
        )
    )
    ledger.record(
        EgressLogEntry(
            ts=1001.0,
            session_id="s1",
            user_id="u1",
            method="CONNECT",
            host="evil.example.com",
            port=443,
            allowed=False,
            reason="not_in_policy",
        )
    )
    assert ledger.verify_chain()
    entries = ledger.entries()
    assert len(entries) == 2
    assert entries[0]["prev_hash"] == EgressLedger.GENESIS
    assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
    # Reopening continues the chain from the tail.
    reopened = EgressLedger(path)
    reopened.record(
        EgressLogEntry(
            ts=1002.0,
            session_id="s1",
            user_id="u1",
            method="GET",
            host=ALLOWED_HOST,
            port=80,
            allowed=True,
            reason="allowed",
        )
    )
    assert reopened.verify_chain()
    assert len(reopened.entries()) == 3


def test_egress_ledger_detects_tampering(tmp_path):
    path = tmp_path / "egress.jsonl"
    ledger = EgressLedger(path)
    ledger.record(
        EgressLogEntry(
            ts=1000.0,
            session_id="s1",
            user_id="u1",
            method="CONNECT",
            host=ALLOWED_HOST,
            port=443,
            allowed=True,
            reason="allowed",
        )
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["allowed"] = False
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert not EgressLedger(path).verify_chain()


def test_egress_ledger_refuses_a_corrupt_tail(tmp_path):
    path = tmp_path / "egress.jsonl"
    path.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(EgressError, match="corrupt"):
        EgressLedger(path)


# --- Docker wiring ------------------------------------------------------------------------


def _fake_docker_script(tmp_path, *, create_fails=False):
    if create_fails:
        create_body = "    sys.exit(1)\n"
    else:
        create_body = "    open(os.environ['FAKE_DOCKER_MARKER'], 'w').close()\n    sys.exit(0)\n"
    script = tmp_path / "docker"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "log = os.environ['FAKE_DOCKER_LOG']\n"
        "with open(log, 'a') as handle:\n"
        "    handle.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['network', 'inspect']:\n"
        "    sys.exit(0 if os.path.exists(os.environ['FAKE_DOCKER_MARKER']) else 1)\n"
        "if sys.argv[1:3] == ['network', 'create']:\n" + create_body + "sys.exit(0)\n"
    )
    script.chmod(0o755)
    return script


def test_ensure_internal_network_creates_when_missing(tmp_path, monkeypatch):
    script = _fake_docker_script(tmp_path)
    log = tmp_path / "log.txt"
    marker = tmp_path / "marker"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_MARKER", str(marker))
    proxy = EgressProxy(_policy())
    proxy.ensure_internal_network(docker_cmd=[str(script)])
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "network inspect puspa-egress"
    assert lines[1] == "network create --internal puspa-egress"
    assert marker.exists()


def test_ensure_internal_network_leaves_an_existing_network_alone(tmp_path, monkeypatch):
    script = _fake_docker_script(tmp_path)
    log = tmp_path / "log.txt"
    marker = tmp_path / "marker"
    marker.write_text("", encoding="utf-8")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_MARKER", str(marker))
    proxy = EgressProxy(_policy())
    proxy.ensure_internal_network(docker_cmd=[str(script)])
    assert log.read_text(encoding="utf-8").splitlines() == ["network inspect puspa-egress"]


def test_ensure_internal_network_failure_is_fail_closed(tmp_path, monkeypatch):
    script = _fake_docker_script(tmp_path, create_fails=True)
    log = tmp_path / "log.txt"
    marker = tmp_path / "marker"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_MARKER", str(marker))
    proxy = EgressProxy(_policy())
    with pytest.raises(EgressError, match="could not create internal Docker network"):
        proxy.ensure_internal_network(docker_cmd=[str(script)])


def test_container_network_flags_need_a_running_proxy():
    proxy = EgressProxy(_policy())
    with pytest.raises(EgressError, match="not running"):
        proxy.container_network_flags()


def test_container_network_flags_route_via_the_proxy():
    proxy = _start_proxy()
    try:
        flags = proxy.container_network_flags()
        port = proxy.listen_port
    finally:
        proxy.stop()
    assert flags[0:2] == ["--network", "puspa-egress"]
    assert "--dns" in flags and "127.0.0.1" in flags
    assert "--add-host" in flags
    assert "host.docker.internal:host-gateway" in flags
    url = f"http://host.docker.internal:{port}"
    assert f"http_proxy={url}" in flags
    assert f"https_proxy={url}" in flags
    assert f"HTTP_PROXY={url}" in flags
    assert f"HTTPS_PROXY={url}" in flags


def test_proxy_config_validation():
    with pytest.raises(EgressError, match="non-empty"):
        EgressProxyConfig(network_name="")
    with pytest.raises(EgressError, match="at least one pinned DNS server"):
        EgressProxyConfig(dns_servers=())
    assert EgressProxyConfig().network_name == "puspa-egress"


def test_proxy_start_stop_is_idempotent():
    proxy = EgressProxy(_policy())
    proxy.start()
    proxy.start()
    assert proxy.is_running and proxy.listen_port > 0
    proxy.stop()
    proxy.stop()
    assert not proxy.is_running
