"""Sandbox policy objects (G34, OpenShell pattern, rewritten).

OpenShell admits a sandbox only after its policy is validated
(``Attach`` + ``Confirm`` in ``proto/sandbox_protocol.proto``:
unsupported requirements fail *before* ``StartAgent``), and
every network connection then passes a policy check before it
leaves the sandbox (deny-by-default, named rules with explicit
``host:port`` endpoints, middleware that fails closed by
default). Its filesystem policy is a pair of allow lists
(read-only / read-write directories, workdir read-write by
default) and its process policy names the user/group the
workload runs as.

Puspa takes the *shape* of those objects, rewritten as plain
Python dataclasses over the policy Puspa already has:

- :class:`NetworkPolicy` — deny-by-default plus explicit
  endpoints. Hosts are matched the same way the existing
  :class:`puspa.sandbox.allowlist.Allowlist` matches them
  (exact host or subdomain), and every host must already be on
  the session allowlist: a policy can only narrow the
  allowlist, never widen it.
- :class:`FilesystemPolicy` — read-only / read-write directory
  lists plus ``include_workdir`` (OpenShell's
  ``FilesystemPolicy`` fields, same names).
- :class:`SandboxPolicy` — the bundle validated at session
  creation, before any backend is built or any code runs.
  A policy that fails validation refuses the session; nothing
  is executed, and no backend is started.

Honest limit: the network policy is enforced at admission
(session creation) and, for the Docker backend, by the
``--network none`` run flag when the policy allows no host.
A policy that *does* allow hosts needs OpenShell's filtered
egress (an HTTP CONNECT proxy with DNS mediation); Puspa has
no such proxy yet, so the runner refuses to run with a
non-empty policy instead of silently running with the network
on. See :mod:`puspa.sandbox.runner` and
``docs/sandbox-deepening.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .allowlist import Allowlist

__all__ = [
    "FilesystemPolicy",
    "NetworkEndpoint",
    "NetworkPolicy",
    "PolicyError",
    "SandboxPolicy",
]

#: Ports a network policy may admit when the caller does not name
#: ports explicitly: plain HTTP and TLS only.
DEFAULT_POLICY_PORTS: tuple[int, ...] = (80, 443)

#: The user/group the workload runs as inside the container,
#: matching the hardened runner flags (non-root 65534/nobody).
DEFAULT_RUN_AS_USER = "65534"
DEFAULT_RUN_AS_GROUP = "65534"


class PolicyError(ValueError):
    """A sandbox policy is invalid or refuses an operation.

    Raised at validation time (session creation), never after
    execution has started.
    """


def _normalise_host(host: str) -> str:
    cleaned = (host or "").strip().lower().rstrip(".")
    if not cleaned:
        raise PolicyError("network endpoint host must be a non-empty string")
    if "://" in cleaned or "/" in cleaned or ":" in cleaned:
        raise PolicyError(
            f"network endpoint host {host!r} must be a bare hostname, "
            "not a URL (use the host part only)"
        )
    return cleaned


def _host_matches(host: str, domain: str) -> bool:
    """True when ``host`` is ``domain`` or a subdomain of it."""
    return host == domain or host.endswith("." + domain)


@dataclass(frozen=True)
class NetworkEndpoint:
    """One admitted ``host:port`` pair (OpenShell ``NetworkEndpoint``).

    ``host`` is a bare hostname (no scheme, no path, no port).
    ``port`` is a single TCP port; a policy that needs several
    ports lists several endpoints.
    """

    host: str
    port: int = 443

    def __post_init__(self) -> None:
        host = _normalise_host(self.host)
        object.__setattr__(self, "host", host)
        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise PolicyError(f"network endpoint port must be 1-65535, got {self.port!r}")


@dataclass(frozen=True)
class NetworkPolicy:
    """Deny-by-default network policy for one sandbox session.

    Empty means *no* network at all (the runner passes
    ``--network none``). A non-empty policy names the exact
    endpoints the session may reach; anything else is denied.
    There is no wildcard host and no wildcard port.

    ``allowed_hosts`` is the host view of ``endpoints`` (kept
    for callers that only need hosts, e.g. the allowlist check
    at session creation).
    """

    endpoints: tuple[NetworkEndpoint, ...] = ()

    def __post_init__(self) -> None:
        for endpoint in self.endpoints:
            if not isinstance(endpoint, NetworkEndpoint):
                raise PolicyError("network endpoints must be NetworkEndpoint values")

    @classmethod
    def deny_all(cls) -> NetworkPolicy:
        """The default: no network endpoint is admitted."""
        return cls(())

    @classmethod
    def from_hosts(
        cls,
        hosts: list[str],
        *,
        ports: tuple[int, ...] = DEFAULT_POLICY_PORTS,
        allowlist: Allowlist | None = None,
    ) -> NetworkPolicy:
        """Build a policy from hosts, each on every given port.

        Every host must be on ``allowlist`` (the session
        allowlist, defaulting to
        :data:`puspa.sandbox.allowlist.DEFAULT_ALLOWLIST`):
        a policy narrows the allowlist, it never widens it.
        A host outside the allowlist raises :class:`PolicyError`
        at build time, before any session exists.
        """
        checked = allowlist if allowlist is not None else Allowlist()
        endpoints: list[NetworkEndpoint] = []
        for host in hosts:
            cleaned = _normalise_host(host)
            if not checked.is_allowed(cleaned):
                raise PolicyError(
                    f"network policy host {cleaned!r} is not on the session "
                    "allowlist; a policy can only narrow the allowlist"
                )
            for port in ports:
                endpoints.append(NetworkEndpoint(host=cleaned, port=port))
        return cls(tuple(endpoints))

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Hosts this policy admits (any port)."""
        return frozenset(endpoint.host for endpoint in self.endpoints)

    @property
    def is_empty(self) -> bool:
        """True when the policy admits no endpoint (no network)."""
        return not self.endpoints

    def allows(self, host: str, port: int) -> bool:
        """True only when ``host:port`` is an admitted endpoint.

        Host matching covers subdomains of an admitted host
        (``api.tokopedia.com`` matches ``tokopedia.com``), the
        same rule the allowlist uses; the port must match
        exactly.
        """
        cleaned = (host or "").strip().lower().rstrip(".")
        if not cleaned:
            return False
        return any(
            _host_matches(cleaned, endpoint.host) and endpoint.port == port
            for endpoint in self.endpoints
        )

    def allows_host(self, host: str) -> bool:
        """True when any admitted endpoint covers ``host``."""
        cleaned = (host or "").strip().lower().rstrip(".")
        if not cleaned:
            return False
        return any(_host_matches(cleaned, endpoint.host) for endpoint in self.endpoints)

    def docker_network_mode(self) -> str:
        """Docker run mode for this policy: ``"none"`` or ``"proxy"``.

        ``"none"`` (empty policy) is enforced today with
        ``--network none``. ``"proxy"`` means a filtered egress
        proxy is required; Puspa has no such proxy yet, so the
        runner refuses to run in that mode rather than opening
        the network. The mode name is returned (not silently
        downgraded) so the refusal is diagnosable.
        """
        return "proxy" if self.endpoints else "none"


@dataclass(frozen=True)
class FilesystemPolicy:
    """Filesystem allow lists for one sandbox session.

    Mirrors OpenShell's ``FilesystemPolicy`` fields: the workdir
    is read-write when ``include_workdir`` is True (the
    default), ``read_only`` lists extra directories the session
    may read, ``read_write`` lists extra directories it may
    write. Paths are sandbox-relative (``/workspace/...`` style
    absolute paths are rejected: the sandbox has one workdir,
    and everything else is addressed relative to it).
    """

    include_workdir: bool = True
    read_only: tuple[str, ...] = ()
    read_write: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, paths in (("read_only", self.read_only), ("read_write", self.read_write)):
            for path in paths:
                if not isinstance(path, str) or not path.strip():
                    raise PolicyError(f"filesystem policy {name} entries must be non-empty strings")
                if path.startswith("/"):
                    raise PolicyError(
                        f"filesystem policy {name} entry {path!r} must be "
                        "sandbox-relative, not absolute"
                    )
                if ".." in path.split("/"):
                    raise PolicyError(
                        f"filesystem policy {name} entry {path!r} must not contain '..' segments"
                    )

    def allows_write(self, path: str) -> bool:
        """True when ``path`` (sandbox-relative) may be written."""
        cleaned = (path or "").strip().lstrip("/")
        if not cleaned:
            return self.include_workdir
        if self.include_workdir:
            return True
        return any(
            cleaned == entry or cleaned.startswith(entry.rstrip("/") + "/")
            for entry in self.read_write
        )

    def allows_read(self, path: str) -> bool:
        """True when ``path`` (sandbox-relative) may be read."""
        cleaned = (path or "").strip().lstrip("/")
        if not cleaned:
            return True
        if self.include_workdir:
            return True
        entries = tuple(self.read_only) + tuple(self.read_write)
        return any(
            cleaned == entry or cleaned.startswith(entry.rstrip("/") + "/") for entry in entries
        )


@dataclass(frozen=True)
class SandboxPolicy:
    """The full policy bundle for one sandbox session.

    ``version`` is the policy schema version (OpenShell carries
    one on ``SandboxPolicy`` so a stored policy stays
    interpretable after the schema changes). ``run_as_user`` /
    ``run_as_group`` name the workload identity inside the
    container (non-root by default, matching the runner).
    """

    version: int = 1
    filesystem: FilesystemPolicy = field(default_factory=FilesystemPolicy)
    network: NetworkPolicy = field(default_factory=NetworkPolicy.deny_all)
    run_as_user: str = DEFAULT_RUN_AS_USER
    run_as_group: str = DEFAULT_RUN_AS_GROUP

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or self.version < 1:
            raise PolicyError(f"policy version must be a positive int, got {self.version!r}")
        if not isinstance(self.filesystem, FilesystemPolicy):
            raise PolicyError("filesystem policy must be a FilesystemPolicy")
        if not isinstance(self.network, NetworkPolicy):
            raise PolicyError("network policy must be a NetworkPolicy")
        if not self.run_as_user or not self.run_as_group:
            raise PolicyError("run_as_user and run_as_group must be non-empty strings")
        if self.run_as_user in ("0", "root") or self.run_as_group in ("0", "root"):
            raise PolicyError("a sandbox workload must never run as root")

    def validate(self) -> None:
        """Re-check the whole bundle (all checks already run at build).

        Kept as an explicit admission step so session creation
        reads as OpenShell's ``Attach`` + ``Confirm``: validate
        first, build the backend only after validation passes.
        """
        self.__post_init__()

    def docker_user_flag(self) -> str:
        """``--user`` value for ``docker run`` (``uid:gid``)."""
        return f"{self.run_as_user}:{self.run_as_group}"
