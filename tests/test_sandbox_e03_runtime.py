"""E-03: stronger-isolation runtimes + spawn measurement (plan 4.3, gate 9).

Config (runc/runsc/kata), availability validation, and the
measurement harness that times a spawn against the 3-second
gate-9 target. Real spawns are measured on a Docker host;
here the harness runs against fake spawns.
"""

from __future__ import annotations

import pytest

from puspa.sandbox.runtime import (
    DEFAULT_SPAWN_TARGET_S,
    IsolationRuntime,
    IsolationRuntimeError,
    RuntimeConfig,
    docker_spawn_probe,
    evaluate_runtimes,
    list_docker_runtimes,
    measure_spawn,
)


class FakeClock:
    """A clock the test advances by hand."""

    def __init__(self, readings):
        self._readings = iter(readings)

    def __call__(self):
        return next(self._readings)


# --- config -----------------------------------------------------------------------


def test_runtime_names_parse_and_reject():
    assert IsolationRuntime.from_name("runc") is IsolationRuntime.RUNC
    assert IsolationRuntime.from_name("runsc") is IsolationRuntime.GVisor
    assert IsolationRuntime.from_name("kata") is IsolationRuntime.KATA
    assert IsolationRuntime.from_name("  RUNSC ") is IsolationRuntime.GVisor
    with pytest.raises(IsolationRuntimeError, match="unknown isolation runtime"):
        IsolationRuntime.from_name("firecracker")


def test_runtime_config_flags():
    assert RuntimeConfig().docker_flags() == []
    assert RuntimeConfig.from_name("runsc").docker_flags() == ["--runtime", "runsc"]
    assert RuntimeConfig.from_name("kata").docker_flags() == ["--runtime", "kata"]
    assert RuntimeConfig().spawn_target_s == DEFAULT_SPAWN_TARGET_S == 3.0
    assert RuntimeConfig().to_dict() == {"runtime": "runc", "spawn_target_s": 3.0}


def test_runtime_config_rejects_bad_target():
    with pytest.raises(IsolationRuntimeError, match="spawn_target_s must be > 0"):
        RuntimeConfig(spawn_target_s=0)


def test_runtime_validate_against_daemon_offer():
    RuntimeConfig.from_name("runsc").validate(frozenset({"runc", "runsc"}))
    with pytest.raises(IsolationRuntimeError, match="not offered by this Docker daemon"):
        RuntimeConfig.from_name("runsc").validate(frozenset({"runc"}))
    with pytest.raises(IsolationRuntimeError, match="not offered"):
        RuntimeConfig.from_name("kata").validate(frozenset())
    # Unknown availability skips the check (the daemon decides later).
    RuntimeConfig.from_name("kata").validate(None)
    # runc never needs the check.
    RuntimeConfig().validate(frozenset())


# --- measurement ---------------------------------------------------------------------


def test_measure_spawn_fast_meets_the_target():
    clock = FakeClock([10.0, 12.4])
    measurement = measure_spawn(lambda: None, runtime="runsc", clock=clock)
    assert measurement.spawn_s == pytest.approx(2.4)
    assert measurement.meets_target
    assert measurement.error is None


def test_measure_spawn_slow_misses_the_target():
    clock = FakeClock([10.0, 13.5])
    measurement = measure_spawn(lambda: None, runtime="kata", clock=clock)
    assert measurement.spawn_s == pytest.approx(3.5)
    assert not measurement.meets_target


def test_measure_spawn_failure_never_meets_the_target():
    def _fail():
        raise OSError("daemon refused the runtime")

    clock = FakeClock([10.0, 10.1])
    measurement = measure_spawn(_fail, runtime="runsc", clock=clock)
    assert not measurement.meets_target
    assert measurement.error == "daemon refused the runtime"


def test_evaluate_runtimes_reports_fastest_and_all_meet():
    # The fake clock decides the spawn times: runc 1s, runsc 2s, kata 4s.
    clock = FakeClock([0.0, 1.0, 0.0, 2.0, 0.0, 4.0])
    report = evaluate_runtimes(
        [RuntimeConfig.from_name(name) for name in ("runc", "runsc", "kata")],
        lambda config: lambda: None,
        clock=clock,
    )
    assert report["fastest"] == "runc"
    assert not report["all_meet_target"], "kata at 4s misses the 3s target"
    by_runtime = {item["runtime"]: item for item in report["measurements"]}
    assert by_runtime["runc"]["meets_target"]
    assert by_runtime["runsc"]["meets_target"]
    assert not by_runtime["kata"]["meets_target"]


def test_evaluate_runtimes_with_no_success_has_no_fastest():
    def _failing(config):
        def _spawn():
            raise OSError("no daemon")

        return _spawn

    report = evaluate_runtimes(
        [RuntimeConfig.from_name("runsc")],
        _failing,
        clock=FakeClock([0.0, 0.5]),
    )
    assert report["fastest"] is None
    assert not report["all_meet_target"]


# --- Docker probes (fake docker binary) ------------------------------------------------


def _fake_docker(tmp_path, *, info_output='{"runc": {}, "runsc": {}}', exit_code=0):
    script = tmp_path / "docker"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "info" ]; then\n'
        f"  printf '%s\\n' '{info_output}'\n"
        "  exit 0\n"
        "fi\n"
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    return [str(script)]


def test_list_docker_runtimes_reads_docker_info(tmp_path):
    assert list_docker_runtimes(_fake_docker(tmp_path)) == {"runc", "runsc"}


def test_list_docker_runtimes_empty_when_docker_fails(tmp_path):
    script = _fake_docker(tmp_path, info_output="not json")
    assert list_docker_runtimes(script) == frozenset()
    assert list_docker_runtimes([str(tmp_path / "no-such-docker")]) == frozenset()


def test_docker_spawn_probe_times_a_real_run(tmp_path):
    elapsed = docker_spawn_probe("puspa-sandbox:3.12-local", docker_cmd=_fake_docker(tmp_path))
    assert elapsed >= 0


def test_docker_spawn_probe_failure_is_not_a_measurement(tmp_path):
    with pytest.raises(IsolationRuntimeError, match="spawn probe container exited"):
        docker_spawn_probe(
            "puspa-sandbox:3.12-local",
            docker_cmd=_fake_docker(tmp_path, exit_code=1),
        )
    with pytest.raises(IsolationRuntimeError, match="spawn probe failed"):
        docker_spawn_probe(
            "puspa-sandbox:3.12-local",
            docker_cmd=[str(tmp_path / "no-such-docker")],
        )
