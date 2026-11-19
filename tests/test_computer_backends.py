"""Conformance tests for the computer execution backends (G16).

The SAME test suite runs against every registered backend
(hermes-agent pattern): a backend passes by behaving like the
others, not by having its own bespoke tests. Backends that
cannot run on this host skip with a clear reason instead of
failing or faking — Docker containers cannot start on this VM
(the kernel rejects the ``setns`` syscall), so the Docker
variant skips here and its first live check is the CI sandbox
lane (.github/workflows/ci.yml), which builds the sandbox image
and runs the Docker variant on every PR.

These tests use no Puspa database tables themselves (temp dirs
and subprocesses only). Note: the shared ``tests/conftest.py``
autouse fixture still creates the schemas and truncates tables
in ``puspa_test`` for every test file in this directory,
including this one; that fixture cannot be opted out of from
here.
"""

import pytest

from puspa.computer import (
    BACKENDS,
    BackendError,
    BackendUnavailable,
    ComputerConfig,
    get_backend,
    list_backends,
)
from puspa.harness.sandbox_profiles import ProfileDenied

DOCKER_SKIP_REASON = (
    "docker backend unavailable on this host: containers cannot run here "
    "(kernel rejects setns); first live check is the CI sandbox lane"
)


@pytest.fixture(params=sorted(BACKENDS))
def backend(request, tmp_path):
    """One instance of each registered backend, per test."""
    name = request.param
    instance = get_backend(name, workdir=tmp_path / name)
    if not instance.check_available():
        pytest.skip(f"{name}: {DOCKER_SKIP_REASON}")
    yield instance
    instance.close()


# --- conformance: the same behaviour on every backend -----------------------


def test_run_python_prints_stdout(backend):
    result = backend.run_python('print("puspa-ok")\n')
    assert result.ok is True
    assert result.stdout.strip() == "puspa-ok"
    assert result.exit_code == 0
    assert result.timed_out is False


def test_run_python_failure_is_a_result_not_a_raise(backend):
    result = backend.run_python("import sys\nsys.exit(3)\n")
    assert result.ok is False
    assert result.exit_code == 3
    assert result.timed_out is False


def test_run_python_assertion_failure_is_a_result(backend):
    result = backend.run_python("assert 1 == 2, 'boom'\n")
    assert result.ok is False
    assert "AssertionError" in result.stderr


def test_run_python_timeout_is_a_result(backend):
    result = backend.run_python("import time\ntime.sleep(30)\n", timeout_s=1)
    assert result.ok is False
    assert result.timed_out is True
    assert result.exit_code is None


def test_run_python_rejects_non_str(backend):
    with pytest.raises(TypeError):
        backend.run_python(42)


def test_run_python_rejects_non_positive_timeout(backend):
    with pytest.raises(ValueError):
        backend.run_python("print(1)\n", timeout_s=0)


def test_run_command_echo(backend):
    result = backend.run_command(["echo", "hello"])
    assert result.ok is True
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_run_command_failure_is_a_result(backend):
    result = backend.run_command(["false"])
    assert result.ok is False
    assert result.exit_code != 0


def test_run_command_rejects_empty_argv(backend):
    with pytest.raises(ValueError):
        backend.run_command([])


def test_run_command_rejects_non_str_argv(backend):
    with pytest.raises(TypeError):
        backend.run_command(["echo", 1])


def test_sync_write_read_roundtrip(backend, tmp_path):
    # The docker backend has no persistent filesystem, so it
    # refuses sync; every other backend round-trips the file.
    if backend.name == "docker":
        pytest.skip("docker backend has no persistent filesystem")
    written = backend.sync.write("notes/hello.txt", "hello sync\n")
    assert written.exists()
    assert backend.sync.read("notes/hello.txt") == "hello sync\n"
    assert backend.sync.exists("notes/hello.txt") is True
    assert backend.sync.exists("notes/missing.txt") is False


def test_sync_write_outside_scope_is_refused(backend):
    # The docker backend refuses every sync write (no persistent
    # filesystem), so scope enforcement does not apply to it; the
    # refusal itself is covered by test_docker_backend_sync_refuses.
    if backend.name == "docker":
        pytest.skip("docker backend has no persistent filesystem")
    with pytest.raises(ProfileDenied):
        backend.sync.write("../escape.txt", "nope\n")


def test_sync_write_read_only_profile_is_refused(tmp_path):
    from puspa.computer import LocalBackend

    backend = LocalBackend(tmp_path / "ro", profile="read_only")
    with pytest.raises(ProfileDenied):
        backend.sync.write("x.txt", "nope\n")


def test_check_available_returns_bool(backend):
    assert backend.check_available() is True


def test_scope_root_is_the_workdir(backend, tmp_path):
    assert str(backend.scope.root).startswith(str(tmp_path))


# --- docker: honest unavailability on this host ------------------------------


def test_docker_backend_unavailable_when_daemon_dead(monkeypatch, tmp_path):
    """Docker down: a clear, honest error — it never fakes a run.

    The daemon is pointed at a dead endpoint, so this holds on any
    host (dev VM and CI alike) and does not depend on the host's own
    Docker state. On a host where Docker works, the live Docker
    variant of the conformance tests above is the coverage.
    """
    from puspa.computer import DockerBackend

    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:9")
    backend = DockerBackend(workdir=tmp_path / "docker")
    assert backend.check_available() is False
    with pytest.raises(BackendUnavailable) as excinfo:
        backend.run_python('print("x")\n')
    message = str(excinfo.value)
    assert "cannot run on this host" in message
    assert "Docker host" in message
    assert "No code was executed" in message


def test_docker_backend_sync_refuses(tmp_path):
    from puspa.computer import DockerBackend

    backend = DockerBackend(workdir=tmp_path / "docker")
    with pytest.raises(BackendError, match="no persistent filesystem"):
        backend.sync.write("x.txt", "nope\n")
    assert backend.sync.exists("x.txt") is False


def test_docker_backend_rejects_raw_env_and_host_cwd(tmp_path):
    from puspa.computer import DockerBackend

    backend = DockerBackend(workdir=tmp_path / "docker")
    with pytest.raises(BackendUnavailable):
        backend.run_command(["echo", "x"], env={"FOO": "bar"})
    with pytest.raises(BackendUnavailable):
        backend.run_command(["echo", "x"], cwd="/tmp")


# --- local: policy enforcement ------------------------------------------------


def test_local_backend_rejects_credential_env(tmp_path):
    """A credential-looking env key is refused, never passed on."""
    from puspa.computer import LocalBackend

    backend = LocalBackend(tmp_path / "local")
    for key in ("API_KEY", "github_token", "MY_SECRET", "DB_PASSWORD"):
        with pytest.raises(BackendError, match="credential-looking"):
            backend.run_command(["true"], env={key: "shh"})
    # A harmless key passes through fine.
    result = backend.run_command(["true"], env={"PUSPA_MODE": "test"})
    assert result.ok is True


def test_local_backend_cwd_outside_scope_is_refused(tmp_path):
    from puspa.computer import LocalBackend

    backend = LocalBackend(tmp_path / "local")
    with pytest.raises(ProfileDenied):
        backend.run_command(["true"], cwd="/etc")


def test_local_backend_is_always_available(tmp_path):
    from puspa.computer import LocalBackend

    assert LocalBackend(tmp_path / "local").check_available() is True


# --- registry + config -------------------------------------------------------


def test_registry_lists_both_starting_backends():
    names = list_backends()
    assert "local" in names
    assert "docker" in names


def test_get_backend_unknown_name_lists_registered():
    with pytest.raises(BackendError, match="unknown execution backend"):
        get_backend("no-such-backend")


def test_config_defaults_to_local():
    config = ComputerConfig()
    assert config.backend == "local"
    assert config.build_backend().name == "local"


def test_config_from_dict_and_env():
    config = ComputerConfig.from_dict({"backend": "docker", "profile": "read_only"})
    assert config.backend == "docker"
    assert config.profile == "read_only"
    assert ComputerConfig.from_env({"PUSPA_COMPUTER_BACKEND": "docker"}).backend == "docker"
    assert ComputerConfig.from_env({}).backend == "local"


def test_config_refuses_unknown_backend_and_keys():
    with pytest.raises(BackendError):
        ComputerConfig(backend="no-such-backend")
    with pytest.raises(ValueError, match="unknown ComputerConfig keys"):
        ComputerConfig.from_dict({"backend": "local", "surprise": 1})
    with pytest.raises(ValueError):
        ComputerConfig(broker_token_ttl_s=0)
