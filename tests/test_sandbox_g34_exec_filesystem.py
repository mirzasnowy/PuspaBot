"""G34: filesystem API + exec API (streaming, timeouts, exit codes).

OpenShell mechanisms under test (rewritten as Puspa code):
root-anchored file transfer (SFTP handler), bounded output
frames with a separate exit report (``sandbox_protocol.proto``
``Exec``), argv-only commands (no shell parsing), and
fail-closed Docker paths.
"""

import pytest

from puspa.computer import BackendUnavailable, DockerBackend, LocalBackend
from puspa.harness.sandbox_profiles import ProfileDenied
from puspa.sandbox import (
    ExecRequest,
    NetworkPolicy,
    QuotaExceeded,
    ResourceQuota,
    Sandbox,
    SandboxPolicy,
    exec_stream,
    filesystem_for_backend,
    run_exec,
)
from puspa.sandbox.filesystem import DockerSandboxFilesystem, LocalSandboxFilesystem

# --- filesystem: local (real, scoped) ---------------------------------------


def test_local_filesystem_round_trip(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    fs = filesystem_for_backend(backend)
    assert isinstance(fs, LocalSandboxFilesystem)
    assert fs.root == tmp_path.resolve()
    fs.write_text("notes/todo.txt", "buy milk\n")
    assert fs.read_text("notes/todo.txt") == "buy milk\n"
    fs.upload("data/blob.bin", b"\x00\x01\x02")
    assert fs.download("data/blob.bin") == b"\x00\x01\x02"
    entry = fs.stat("notes/todo.txt")
    assert entry.path == "notes/todo.txt"
    assert entry.size_bytes == 9
    assert entry.is_dir is False
    names = [item.path for item in fs.list_dir(".")]
    assert names == ["data", "notes"]
    children = [item.path for item in fs.list_dir("notes")]
    assert children == ["notes/todo.txt"]
    assert fs.delete("notes/todo.txt") is True
    assert fs.delete("notes/todo.txt") is False
    assert fs.list_dir("notes") == []


def test_local_filesystem_refuses_escape(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    fs = filesystem_for_backend(backend)
    with pytest.raises(ProfileDenied):
        fs.write_text("../escape.txt", "nope")
    with pytest.raises(ProfileDenied):
        fs.read_text("/etc/passwd")
    with pytest.raises(ProfileDenied):
        fs.list_dir("..")
    with pytest.raises(FileNotFoundError):
        fs.read_text("missing.txt")
    with pytest.raises(FileNotFoundError):
        fs.stat("missing.txt")
    with pytest.raises(ValueError, match="not empty"):
        fs.write_text("dir/f.txt", "x")
        fs.delete("dir")
    assert fs.delete("dir", recursive=True) is True
    with pytest.raises(ValueError, match="workdir itself"):
        fs.delete(".")


def test_local_filesystem_enforces_disk_quota(tmp_path):
    quota = ResourceQuota(disk_mb=16)
    backend = LocalBackend(workdir=tmp_path)
    fs = filesystem_for_backend(backend, quota)
    fs.upload("small.bin", b"x" * 1024)
    # 16 MiB quota, 1,000,000-byte per-file cap: sixteen 1 MB files
    # fit (16,000,000 bytes), the seventeenth would exceed the quota.
    for index in range(16):
        fs.upload(f"chunk-{index:02d}.bin", b"y" * 1_000_000)
    with pytest.raises(QuotaExceeded, match="disk quota exceeded"):
        fs.upload("chunk-16.bin", b"y" * 1_000_000)
    # Rewriting the same file with fewer bytes still fits.
    fs.upload("small.bin", b"x" * 10)
    assert fs.download("small.bin") == b"x" * 10


def test_local_filesystem_enforces_per_file_cap(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    fs = filesystem_for_backend(backend)
    with pytest.raises(ValueError, match="per-file cap"):
        fs.upload("huge.bin", b"x" * 1_000_001)
    with pytest.raises(ValueError, match="per-file cap"):
        fs.write_text("huge.txt", "x" * 1_000_001)


# --- filesystem: docker (uniform refusal) ------------------------------------


def test_docker_filesystem_refuses_everything(tmp_path):
    backend = DockerBackend(workdir=tmp_path)
    fs = filesystem_for_backend(backend)
    assert isinstance(fs, DockerSandboxFilesystem)
    with pytest.raises(BackendUnavailable, match="no persistent filesystem"):
        fs.write_text("a.txt", "x")
    with pytest.raises(BackendUnavailable, match="no persistent filesystem"):
        fs.read_text("a.txt")
    with pytest.raises(BackendUnavailable, match="no persistent filesystem"):
        fs.upload("a.bin", b"x")
    with pytest.raises(BackendUnavailable, match="no persistent filesystem"):
        fs.download("a.bin")
    with pytest.raises(BackendUnavailable, match="no persistent filesystem"):
        fs.list_dir(".")
    with pytest.raises(BackendUnavailable, match="no persistent filesystem"):
        fs.stat("a.txt")
    with pytest.raises(BackendUnavailable, match="no persistent filesystem"):
        fs.delete("a.txt")


def test_filesystem_for_unknown_backend_is_refused(tmp_path):
    from puspa.computer.backends import ExecutionBackend

    class MysteryBackend(ExecutionBackend):
        name = "mystery"

        @property
        def scope(self):
            return LocalBackend(workdir=tmp_path).scope

        @property
        def sync(self):
            return LocalBackend(workdir=tmp_path).sync

        def check_available(self):
            return True

        def run_command(self, argv, *, cwd=None, timeout_s=30, env=None):
            raise NotImplementedError

        def run_python(self, code, *, timeout_s=30):
            raise NotImplementedError

    from puspa.computer.errors import BackendError

    with pytest.raises(BackendError, match="no sandbox filesystem"):
        filesystem_for_backend(MysteryBackend())


# --- exec: one-shot ------------------------------------------------------------


def test_run_exec_argv_on_local_backend(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    result = run_exec(backend, ExecRequest(argv=("echo", "hello")))
    assert result.ok is True
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.duration_s >= 0


def test_run_exec_failure_is_a_result(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    result = run_exec(backend, ExecRequest(argv=("false",)))
    assert result.ok is False
    assert result.exit_code == 1
    result = run_exec(backend, ExecRequest(code="import sys\nsys.exit(3)\n"))
    assert result.ok is False
    assert result.exit_code == 3


def test_run_exec_timeout_is_a_result(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    result = run_exec(
        backend,
        ExecRequest(argv=("sleep", "30"), timeout_s=1),
    )
    assert result.ok is False
    assert result.timed_out is True
    assert result.exit_code is None


def test_run_exec_validates_request(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    with pytest.raises(ValueError, match="exactly one of argv / code"):
        run_exec(backend, ExecRequest())
    with pytest.raises(ValueError, match="exactly one of argv / code"):
        run_exec(backend, ExecRequest(argv=("echo",), code="print(1)"))
    with pytest.raises(ValueError, match="credential-looking env key"):
        run_exec(backend, ExecRequest(argv=("echo",), env={"MY_TOKEN": "x"}))
    with pytest.raises(ValueError, match="timeout_s"):
        ExecRequest(argv=("echo",), timeout_s=0)


def test_run_exec_docker_fails_closed(tmp_path):
    backend = DockerBackend(workdir=tmp_path)
    assert backend.check_available() is False
    with pytest.raises(BackendUnavailable):
        run_exec(backend, ExecRequest(argv=("echo", "hi")))


# --- exec: streaming ------------------------------------------------------------


def test_exec_stream_local_yields_chunks_then_exit(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    request = ExecRequest(code="print('one')\nprint('two')\n")
    chunks = list(exec_stream(backend, request))
    kinds = [chunk.kind for chunk in chunks]
    assert kinds[-1] == "exit"
    assert chunks[-1].exit_code == 0
    assert chunks[-1].timed_out is False
    stdout = "".join(chunk.data for chunk in chunks if chunk.kind == "stdout")
    assert stdout == "one\ntwo\n"


def test_exec_stream_local_stderr_and_exit_code(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    request = ExecRequest(
        code="import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(7)\n"
    )
    chunks = list(exec_stream(backend, request))
    assert chunks[-1].kind == "exit"
    assert chunks[-1].exit_code == 7
    assert "".join(c.data for c in chunks if c.kind == "stdout") == "out\n"
    assert "".join(c.data for c in chunks if c.kind == "stderr") == "err\n"


def test_exec_stream_local_timeout(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    request = ExecRequest(argv=("sleep", "30"), timeout_s=1)
    chunks = list(exec_stream(backend, request))
    assert chunks[-1].kind == "exit"
    assert chunks[-1].timed_out is True
    assert chunks[-1].exit_code is None


def test_exec_stream_local_cwd_stays_in_scope(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    request = ExecRequest(argv=("pwd",), cwd="..")
    with pytest.raises(ProfileDenied, match="outside the sandbox scope"):
        list(exec_stream(backend, request))


def test_exec_stream_docker_fails_closed(tmp_path):
    backend = DockerBackend(workdir=tmp_path)
    request = ExecRequest(argv=("echo", "hi"))
    with pytest.raises(BackendUnavailable):
        list(exec_stream(backend, request))


# --- runner wiring: quota + policy flags ---------------------------------------


def test_runner_flags_derive_from_quota_and_policy():
    quota = ResourceQuota(cpu_cores=1, memory_mb=512, disk_mb=512, max_pids=32)
    sandbox = Sandbox(quota=quota)
    assert sandbox.docker_flags() == [
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "32",
        "--user",
        "65534:65534",
        "--workdir",
        "/tmp",
    ]


def test_runner_flags_default_match_previous_hardening():
    sandbox = Sandbox()
    flags = sandbox.docker_flags()
    assert flags[:2] == ["--network", "none"]
    assert "--memory" in flags and "256m" in flags
    assert "--cpus" in flags and "0.5" in flags
    assert "--pids-limit" in flags and "64" in flags


def test_runner_with_allow_host_policy_refuses_before_docker(monkeypatch, tmp_path):
    """A policy needing the missing egress proxy is refused even
    when Docker is absent: admission order is policy first."""
    monkeypatch.setenv("PATH", str(tmp_path))
    policy = SandboxPolicy(network=NetworkPolicy.from_hosts(["api.openai.com"]))
    sandbox = Sandbox(policy=policy)
    with pytest.raises(Exception, match="egress proxy"):
        sandbox.run("print(1)")
    with pytest.raises(Exception, match="egress proxy"):
        sandbox.docker_flags()
