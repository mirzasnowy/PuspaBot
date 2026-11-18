"""Offline deploy validation (no Docker needed, no DB touched).

The shipped Dockerfile + docker-compose.yml must validate clean,
and synthetic bad files must fail with the right issue. These tests
do not build or run any container — Docker cannot run on this VM
(the kernel rejects setns); a live deploy check on a VPS is still
required (see docs/teams.md). They run under the repo conftest like
every other test file; they touch no database table.
"""

from pathlib import Path

from puspa.teams.deploy import (
    parse_dockerfile,
    validate_compose,
    validate_dockerfile,
)

BUILD_ROOT = Path(__file__).resolve().parent.parent


def test_shipped_dockerfile_validates_clean():
    issues = validate_dockerfile(BUILD_ROOT / "Dockerfile")
    assert issues == []


def test_shipped_compose_validates_clean():
    issues = validate_compose(BUILD_ROOT / "docker-compose.yml")
    assert issues == []


def test_parse_dockerfile_finds_both_stages():
    info = parse_dockerfile((BUILD_ROOT / "Dockerfile").read_text(encoding="utf-8"))
    assert info.errors == []
    assert [stage.name for stage in info.stages] == ["builder", "runtime"]
    assert "ENTRYPOINT" in info.instructions
    assert "HEALTHCHECK" in info.instructions


def test_single_stage_dockerfile_is_rejected(tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text("FROM python:3.12-slim\nRUN echo hi\n", encoding="utf-8")
    issues = validate_dockerfile(bad)
    assert any("multi-stage" in issue for issue in issues)


def test_dockerfile_without_s6_overlay_is_rejected(tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text(
        "FROM python:3.12-slim AS builder\nRUN echo hi\n"
        "FROM python:3.12-slim AS runtime\n"
        'ENTRYPOINT ["/init"]\n'
        "ENV PUSPA_DASHBOARD_HOST=127.0.0.1\n"
        "ENV PUSPA_UID=1000\nENV PUSPA_GID=1000\n"
        "HEALTHCHECK CMD curl -f http://127.0.0.1:8000/manifest.json\n",
        encoding="utf-8",
    )
    issues = validate_dockerfile(bad)
    assert any("s6-overlay" in issue for issue in issues)


def test_dockerfile_with_wrong_entrypoint_is_rejected(tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text(
        "FROM python:3.12-slim AS builder\n"
        "RUN echo s6-overlay\n"
        "FROM python:3.12-slim AS runtime\n"
        'ENTRYPOINT ["python", "-m", "uvicorn"]\n'
        "ENV PUSPA_DASHBOARD_HOST=127.0.0.1\n"
        "ENV PUSPA_UID=1000\nENV PUSPA_GID=1000\n"
        "HEALTHCHECK CMD curl -f http://127.0.0.1:8000/manifest.json\n",
        encoding="utf-8",
    )
    issues = validate_dockerfile(bad)
    assert any("/init" in issue for issue in issues)


def test_dockerfile_binding_0_0_0_0_is_rejected(tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text(
        "FROM python:3.12-slim AS builder\n"
        "RUN echo s6-overlay\n"
        "FROM python:3.12-slim AS runtime\n"
        'ENTRYPOINT ["/init"]\n'
        "ENV PUSPA_DASHBOARD_HOST=0.0.0.0\n"
        "ENV PUSPA_UID=1000\nENV PUSPA_GID=1000\n"
        "HEALTHCHECK CMD curl -f http://127.0.0.1:8000/manifest.json\n",
        encoding="utf-8",
    )
    issues = validate_dockerfile(bad)
    assert any("127.0.0.1" in issue for issue in issues)


def test_dockerfile_with_baked_api_key_is_rejected(tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text(
        "FROM python:3.12-slim AS builder\n"
        "RUN echo s6-overlay\n"
        "FROM python:3.12-slim AS runtime\n"
        'ENTRYPOINT ["/init"]\n'
        "ENV PUSPA_DASHBOARD_HOST=127.0.0.1\n"
        "ENV PUSPA_API_KEY=secret-key-here\n"
        "ENV PUSPA_UID=1000\nENV PUSPA_GID=1000\n"
        "HEALTHCHECK CMD curl -f http://127.0.0.1:8000/manifest.json\n",
        encoding="utf-8",
    )
    issues = validate_dockerfile(bad)
    assert any("API key" in issue for issue in issues)


def test_dockerfile_without_healthcheck_is_rejected(tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text(
        "FROM python:3.12-slim AS builder\n"
        "RUN echo s6-overlay\n"
        "FROM python:3.12-slim AS runtime\n"
        'ENTRYPOINT ["/init"]\n'
        "ENV PUSPA_DASHBOARD_HOST=127.0.0.1\n"
        "ENV PUSPA_UID=1000\nENV PUSPA_GID=1000\n",
        encoding="utf-8",
    )
    issues = validate_dockerfile(bad)
    assert any("HEALTHCHECK" in issue for issue in issues)


def test_parse_dockerfile_reports_structural_errors():
    info = parse_dockerfile("RUN echo hi\nFROM python:3.12-slim\nBOGUS x\n")
    assert any("before the first FROM" in error for error in info.errors)
    assert any("unknown Dockerfile instruction" in error for error in info.errors)
    assert parse_dockerfile("").errors == ["Dockerfile has no FROM stage"]


def test_compose_with_public_port_is_rejected(tmp_path):
    bad = tmp_path / "docker-compose.yml"
    bad.write_text(
        "services:\n"
        "  gateway:\n"
        "    image: puspa:local\n"
        "    ports:\n"
        '      - "0.0.0.0:8001:8001"\n'
        "    volumes:\n"
        "      - puspa-home:/home/puspa\n"
        "  dashboard:\n"
        "    image: puspa:local\n"
        "    ports:\n"
        '      - "127.0.0.1:8000:8000"\n'
        "    volumes:\n"
        "      - puspa-home:/home/puspa\n"
        "volumes:\n"
        "  puspa-home:\n",
        encoding="utf-8",
    )
    issues = validate_compose(bad)
    assert any("127.0.0.1" in issue for issue in issues)
    assert any("gateway" in issue for issue in issues)


def test_compose_with_api_key_set_is_rejected(tmp_path):
    bad = tmp_path / "docker-compose.yml"
    bad.write_text(
        "services:\n"
        "  gateway:\n"
        "    image: puspa:local\n"
        "    environment:\n"
        "      PUSPA_API_KEY: secret-key-here\n"
        "    ports:\n"
        '      - "127.0.0.1:8001:8001"\n'
        "    volumes:\n"
        "      - puspa-home:/home/puspa\n"
        "  dashboard:\n"
        "    image: puspa:local\n"
        "    ports:\n"
        '      - "127.0.0.1:8000:8000"\n'
        "    volumes:\n"
        "      - puspa-home:/home/puspa\n"
        "volumes:\n"
        "  puspa-home:\n",
        encoding="utf-8",
    )
    issues = validate_compose(bad)
    assert any("PUSPA_API_KEY" in issue for issue in issues)


def test_compose_missing_dashboard_is_rejected(tmp_path):
    bad = tmp_path / "docker-compose.yml"
    bad.write_text(
        "services:\n"
        "  gateway:\n"
        "    image: puspa:local\n"
        "    ports:\n"
        '      - "127.0.0.1:8001:8001"\n',
        encoding="utf-8",
    )
    issues = validate_compose(bad)
    assert any("dashboard" in issue for issue in issues)


def test_compose_invalid_yaml_is_rejected(tmp_path):
    bad = tmp_path / "docker-compose.yml"
    bad.write_text("services: [unclosed\n", encoding="utf-8")
    issues = validate_compose(bad)
    assert any("not valid YAML" in issue for issue in issues)


def test_missing_files_are_reported(tmp_path):
    assert validate_dockerfile(tmp_path / "Dockerfile") == [
        f"Dockerfile not found: {tmp_path / 'Dockerfile'}"
    ]
    assert validate_compose(tmp_path / "docker-compose.yml") == [
        f"docker-compose.yml not found: {tmp_path / 'docker-compose.yml'}"
    ]
