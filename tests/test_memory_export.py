"""Tests for the portable memory export (``puspa/memory/memory_export.py``).

Unit tests (no database): encryption round trip, wrong passphrase,
tampered archives, and scope validation. Integration tests (live
``puspa_test_w2`` database): a full export from seeded facts,
episodes, procedures, summaries, MEMORY.md, causal hypotheses and
a world model with a known unknown; a vault secret never appears
anywhere in the decrypted archive; the export is recorded as a
ledger event carrying counts and the archive hash only; and the
read-back check passes.
"""

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory.memory_export import (
    ARCHIVE_FORMAT,
    ARCHIVE_VERSION,
    EXPORT_EVENT_TYPE,
    ExportScope,
    build_archive_bytes,
    build_memory_archive,
    decrypt_archive_bytes,
    derive_key,
    encrypt_archive_bytes,
    export_memory,
    memory_export_task_id,
    verify_archive,
    verify_archive_bytes,
)

PASSPHRASE = "correct horse battery staple"


def _sections():
    return {
        "MEMORY.md": b"# MEMORY.md\n\n- **hometown**: Karawang\n",
        "events.jsonl": b'{"task_id": "t1", "hash": "abc"}\n',
    }


def _manifest_base():
    return {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "puspa_version": "0.1.0",
        "user_id": "user-1",
        "exported_at": datetime.now(UTC).isoformat(),
        "scope": ExportScope().describe(),
        "embedding": {"model_id": "hash", "dimension": 384},
        "counts": {"memory": 1, "events": 1},
        "ledger_tips": {"t1": "abc"},
        "excluded": [],
    }


def _tamper(zip_bytes, name, replacement):
    """Rebuild a ZIP with one file replaced (hashes now stale)."""
    source = zipfile.ZipFile(io.BytesIO(zip_bytes))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = replacement if item.filename == name else source.read(item.filename)
            target.writestr(item, data)
    return buffer.getvalue()


# -- unit: encryption ----------------------------------------------------


def test_encrypt_decrypt_round_trip():
    zip_bytes, _ = build_archive_bytes(_sections(), _manifest_base())
    sealed = encrypt_archive_bytes(zip_bytes, PASSPHRASE)
    assert sealed != zip_bytes
    assert decrypt_archive_bytes(sealed, PASSPHRASE) == zip_bytes


def test_decrypt_wrong_passphrase_fails():
    zip_bytes, _ = build_archive_bytes(_sections(), _manifest_base())
    sealed = encrypt_archive_bytes(zip_bytes, PASSPHRASE)
    with pytest.raises(ValueError, match="wrong passphrase"):
        decrypt_archive_bytes(sealed, "wrong passphrase")


def test_decrypt_corrupted_ciphertext_fails():
    zip_bytes, _ = build_archive_bytes(_sections(), _manifest_base())
    sealed = encrypt_archive_bytes(zip_bytes, PASSPHRASE)
    corrupted = sealed[:-5] + b"xxxxx"
    with pytest.raises(ValueError):
        decrypt_archive_bytes(corrupted, PASSPHRASE)


def test_decrypt_encrypted_archive_without_passphrase_fails():
    zip_bytes, _ = build_archive_bytes(_sections(), _manifest_base())
    sealed = encrypt_archive_bytes(zip_bytes, PASSPHRASE)
    with pytest.raises(ValueError, match="passphrase is required"):
        decrypt_archive_bytes(sealed)


def test_decrypt_plain_zip_passes_through():
    zip_bytes, _ = build_archive_bytes(_sections(), _manifest_base())
    assert decrypt_archive_bytes(zip_bytes) == zip_bytes


def test_derive_key_rejects_empty_passphrase_and_bad_salt():
    with pytest.raises(ValueError):
        derive_key("", b"0" * 16)
    with pytest.raises(ValueError):
        derive_key(PASSPHRASE, b"short")


def test_derive_key_is_stable_and_salt_specific():
    assert derive_key(PASSPHRASE, b"0" * 16) == derive_key(PASSPHRASE, b"0" * 16)
    assert derive_key(PASSPHRASE, b"0" * 16) != derive_key(PASSPHRASE, b"1" * 16)


# -- unit: archive build and verify --------------------------------------


def test_build_and_verify_round_trip():
    zip_bytes, manifest = build_archive_bytes(_sections(), _manifest_base())
    assert manifest["files"]["MEMORY.md"]["sha256"]
    assert manifest["files"]["events.jsonl"]["bytes"] == len(_sections()["events.jsonl"])
    assert verify_archive_bytes(zip_bytes) == manifest


def test_verify_rejects_tampered_file_content():
    zip_bytes, _ = build_archive_bytes(_sections(), _manifest_base())
    tampered = _tamper(zip_bytes, "MEMORY.md", b"# MEMORY.md\n\n- **hometown**: Jakarta\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_archive_bytes(tampered)


def test_verify_rejects_tampered_count():
    _zip_bytes, manifest = build_archive_bytes(_sections(), _manifest_base())
    manifest["counts"]["events"] = 99
    rebuilt, _ = build_archive_bytes(_sections(), manifest)
    with pytest.raises(ValueError, match="count mismatch"):
        verify_archive_bytes(rebuilt)


def test_verify_rejects_missing_manifest():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("MEMORY.md", b"# MEMORY.md\n")
    with pytest.raises(ValueError, match="no manifest"):
        verify_archive_bytes(buffer.getvalue())


def test_verify_rejects_unknown_format_and_version():
    base = _manifest_base()
    base["format"] = "something-else"
    zip_bytes, _ = build_archive_bytes(_sections(), base)
    with pytest.raises(ValueError, match="unknown archive format"):
        verify_archive_bytes(zip_bytes)
    base = _manifest_base()
    base["version"] = 99
    zip_bytes, _ = build_archive_bytes(_sections(), base)
    with pytest.raises(ValueError, match="unsupported archive version"):
        verify_archive_bytes(zip_bytes)


def test_verify_rejects_non_zip_bytes():
    with pytest.raises(ValueError, match="not a valid ZIP"):
        verify_archive_bytes(b"this is not a zip file")


def test_build_archive_bytes_needs_a_section():
    with pytest.raises(ValueError, match="at least one section"):
        build_archive_bytes({}, _manifest_base())


# -- unit: scope -----------------------------------------------------------


def test_scope_rejects_unknown_layer():
    with pytest.raises(ValueError, match="unknown export layers"):
        ExportScope(layers=("memory", "nope"))


def test_scope_rejects_naive_datetimes_and_reversed_range():
    with pytest.raises(ValueError, match="timezone-aware"):
        ExportScope(date_from=datetime.now())
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="must not be before"):
        ExportScope(date_from=now, date_to=now - timedelta(days=1))


def test_scope_describe_and_includes():
    scope = ExportScope(layers=("memory", "events"), person="ana")
    assert scope.includes("memory")
    assert not scope.includes("graph")
    described = scope.describe()
    assert described["layers"] == ["memory", "events"]
    assert described["person"] == "ana"
    assert ExportScope().describe()["layers"] == "full"


# -- integration: full export ----------------------------------------------


def _seed_full_user(make_user, test_dsn):
    """A user with one row in every memory layer."""
    from puspa.ledger import Ledger
    from puspa.memory import durable, episodic, procedural, profile
    from puspa.world.causal import CausalHypothesis, CausalLedger
    from puspa.world.model import WorldModel
    from puspa.world.store import save_causal_hypotheses, save_world_state

    user = make_user()
    user_id = user["user_id"]
    agent_id = user["agent_id"]
    project_task = str(uuid.uuid4())
    other_task = str(uuid.uuid4())
    profile.add_fact(user_id, "person:ana", "Ana, sister", "user_stated", dsn=test_dsn)
    profile.add_fact(user_id, "person:budi", "Budi, friend", "user_stated", dsn=test_dsn)
    profile.add_fact(user_id, "preference:language", "Indonesian", "user_stated", dsn=test_dsn)
    profile.add_fact(
        user_id, "standing_goal", "save Rp1 billion by 25", "user_stated", dsn=test_dsn
    )
    episodic.record_episode(
        user_id,
        "Ana asked about the cheapest laptop on Tokopedia",
        "chat",
        agent_id=agent_id,
        task_id=project_task,
        dsn=test_dsn,
    )
    episodic.record_episode(
        user_id,
        "Budi paid the PLN bill for March",
        "task_completed",
        agent_id=agent_id,
        task_id=other_task,
        dsn=test_dsn,
    )
    procedural.record_successful_task(
        user_id, "check_pln", ["open the bill site", "read the amount"], dsn=test_dsn
    )
    durable.rebuild_memory(user_id, dsn=test_dsn)
    Ledger(test_dsn).append_event(
        task_id=project_task,
        agent_id=agent_id,
        event_type="task_completed",
        content={"outcome": "checked the PLN bill"},
    )
    ledger = CausalLedger.from_dict(
        {
            "hypotheses": [
                CausalHypothesis(
                    id=str(uuid.uuid4()),
                    cause="rain",
                    effect="traffic",
                    condition="",
                    strength=0.7,
                    evidence=[{"kind": "observation", "id": "obs-1"}],
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                    supporting=2,
                    contradicting=1,
                ).to_dict()
            ]
        }
    )
    save_causal_hypotheses(user_id, agent_id, ledger, dsn=test_dsn)
    model = WorldModel()
    model.day = 15
    action = {"type": "check_price", "product_id": "kopi-abc", "store": "Shopee"}
    model.update({"action": action, "observed": {"price": 5100000}, "task": "grounding", "day": 15})
    model.predict(action)
    model.update({"action": action, "observed": {"price": 9999999}, "day": 15})
    assert len(model.ignorance_map()) == 1
    save_world_state(user_id, agent_id, model, dsn=test_dsn)
    user["project_task"] = project_task
    return user


def _read_archive(path, passphrase=None):
    """Decrypted archive files as {name: text}."""
    data = verify_archive(path, passphrase)
    raw = decrypt_archive_bytes(path.read_bytes(), passphrase)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}, data


def test_full_export_round_trip_and_contents(make_user, test_dsn, tmp_path):
    user = _seed_full_user(make_user, test_dsn)
    user_id = user["user_id"]
    path = tmp_path / "memory.puspa"
    result = export_memory(user_id, path, PASSPHRASE, dsn=test_dsn)
    assert result.path == path
    assert result.encrypted is True
    assert result.archive_sha256
    assert result.counts["people"] == 2
    assert result.counts["values"] == 2
    assert result.counts["skills"] == 1
    assert result.counts["causal"] == 1
    assert result.counts["ignorance"] == 1
    assert result.counts["events"] >= 1
    assert result.counts["graph"] >= 1

    files, manifest = _read_archive(path, PASSPHRASE)
    assert set(files) == {
        "MEMORY.md",
        "events.jsonl",
        "graph.jsonl",
        "people.jsonl",
        "values.jsonl",
        "skills/procedures.jsonl",
        "causal.jsonl",
        "ignorance.jsonl",
        "manifest.json",
    }
    assert manifest["format"] == ARCHIVE_FORMAT
    assert manifest["version"] == ARCHIVE_VERSION
    assert manifest["embedding"]["model_id"] == "hash"
    assert manifest["embedding"]["dimension"] == 384
    assert manifest["ledger_tips"], "manifest must record the ledger hash-chain tips"
    assert "MEMORY.md" in files["MEMORY.md"]
    people = [json.loads(line) for line in files["people.jsonl"].splitlines()]
    assert {row["key"] for row in people} == {"person:ana", "person:budi"}
    values = [json.loads(line) for line in files["values.jsonl"].splitlines()]
    assert {row["key"] for row in values} == {"preference:language", "standing_goal"}
    skills = [json.loads(line) for line in files["skills/procedures.jsonl"].splitlines()]
    assert skills[0]["service"] == "check_pln"
    causal = [json.loads(line) for line in files["causal.jsonl"].splitlines()]
    assert causal[0]["cause"] == "rain" and causal[0]["effect"] == "traffic"
    ignorance = [json.loads(line) for line in files["ignorance.jsonl"].splitlines()]
    assert ignorance[0]["key"] == "price:kopi-abc@Shopee"
    events = [json.loads(line) for line in files["events.jsonl"].splitlines()]
    assert any(event.get("task_id") == user["project_task"] for event in events)
    assert all("hash" in event and "previous_hash" in event for event in events)
    graph = [json.loads(line) for line in files["graph.jsonl"].splitlines()]
    assert any(row["type"] == "node" for row in graph)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result.archive_sha256


def test_export_without_passphrase_when_encrypted_fails(make_user, test_dsn, tmp_path):
    user = make_user()
    with pytest.raises(ValueError, match="passphrase is required"):
        export_memory(user["user_id"], tmp_path / "memory.puspa", dsn=test_dsn)


def test_export_unknown_user_fails(make_user, test_dsn, tmp_path):
    with pytest.raises(ValueError, match="unknown user"):
        export_memory(str(uuid.uuid4()), tmp_path / "memory.puspa", PASSPHRASE, dsn=test_dsn)


def test_unencrypted_export_verifies_without_passphrase(make_user, test_dsn, tmp_path):
    user = _seed_full_user(make_user, test_dsn)
    path = tmp_path / "memory.zip"
    result = export_memory(user["user_id"], path, None, dsn=test_dsn, encrypted=False)
    assert result.encrypted is False
    assert verify_archive(path)["encrypted"] is False


def test_vault_secret_never_appears_in_archive(make_user, test_dsn, tmp_path, vault_master_key):
    from puspa.accounts import vault

    user = _seed_full_user(make_user, test_dsn)
    user_id = user["user_id"]
    secret = "sk-live-9f27d41c-secret-value"
    vault.put(user_id, "api-key", secret, dsn=test_dsn)
    path = tmp_path / "memory.puspa"
    export_memory(user_id, path, PASSPHRASE, dsn=test_dsn)
    files, _ = _read_archive(path, PASSPHRASE)
    blob = "\n".join(files.values())
    assert secret not in blob
    assert "api-key" not in blob
    assert "sk-live" not in blob


def test_secret_pasted_into_memory_is_redacted(make_user, test_dsn, tmp_path):
    from puspa.memory import durable, profile

    user = make_user()
    user_id = user["user_id"]
    profile.add_fact(
        user_id,
        "preference:note",
        "my token is api_key=AKIA-SECRET-12345 do not share",
        "user_stated",
        dsn=test_dsn,
    )
    durable.rebuild_memory(user_id, dsn=test_dsn)
    path = tmp_path / "memory.puspa"
    export_memory(user_id, path, PASSPHRASE, dsn=test_dsn)
    files, _ = _read_archive(path, PASSPHRASE)
    blob = "\n".join(files.values())
    assert "AKIA-SECRET-12345" not in blob
    assert "redacted" in blob


def test_export_records_ledger_event_without_memory_content(make_user, test_dsn, tmp_path):
    from puspa.ledger import Ledger

    user = _seed_full_user(make_user, test_dsn)
    user_id = user["user_id"]
    path = tmp_path / "memory.puspa"
    result = export_memory(user_id, path, PASSPHRASE, dsn=test_dsn)
    events = Ledger(test_dsn).events(memory_export_task_id(user_id), event_type=EXPORT_EVENT_TYPE)
    assert len(events) == 1
    content = events[0]["content"]
    assert content["kind"] == "memory_export"
    assert content["format"] == ARCHIVE_FORMAT
    assert content["counts"] == result.counts
    assert content["archive_sha256"] == result.archive_sha256
    assert content["encrypted"] is True
    assert "Karawang" not in json.dumps(content)
    assert "Ana" not in json.dumps(content)


def test_scope_layers_exports_only_those_sections(make_user, test_dsn, tmp_path):
    user = _seed_full_user(make_user, test_dsn)
    path = tmp_path / "memory.puspa"
    scope = ExportScope(layers=("memory", "people"))
    export_memory(user["user_id"], path, PASSPHRASE, scope, dsn=test_dsn)
    files, manifest = _read_archive(path, PASSPHRASE)
    assert set(files) == {"MEMORY.md", "people.jsonl", "manifest.json"}
    assert manifest["scope"]["layers"] == ["memory", "people"]


def test_scope_person_keeps_one_person(make_user, test_dsn, tmp_path):
    user = _seed_full_user(make_user, test_dsn)
    path = tmp_path / "memory.puspa"
    scope = ExportScope(person="ana")
    export_memory(user["user_id"], path, PASSPHRASE, scope, dsn=test_dsn)
    files, _ = _read_archive(path, PASSPHRASE)
    people = [json.loads(line) for line in files["people.jsonl"].splitlines()]
    assert [row["key"] for row in people] == ["person:ana"]


def test_scope_project_keeps_one_task(make_user, test_dsn, tmp_path):
    user = _seed_full_user(make_user, test_dsn)
    path = tmp_path / "memory.puspa"
    scope = ExportScope(project=user["project_task"])
    export_memory(user["user_id"], path, PASSPHRASE, scope, dsn=test_dsn)
    files, _ = _read_archive(path, PASSPHRASE)
    events = [json.loads(line) for line in files["events.jsonl"].splitlines()]
    assert events and all(event.get("task_id") == user["project_task"] for event in events)
    graph = [json.loads(line) for line in files["graph.jsonl"].splitlines()]
    assert graph, "the project episode must produce graph lines"
    assert not any(row.get("name") == "Budi" for row in graph)


def test_scope_date_range_excludes_old_events(make_user, test_dsn, tmp_path):
    user = _seed_full_user(make_user, test_dsn)
    path = tmp_path / "memory.puspa"
    scope = ExportScope(date_from=datetime.now(UTC) + timedelta(days=1))
    export_memory(user["user_id"], path, PASSPHRASE, scope, dsn=test_dsn)
    files, manifest = _read_archive(path, PASSPHRASE)
    assert manifest["counts"]["events"] == 0
    assert manifest["counts"]["graph"] == 0
    assert files["events.jsonl"] == ""
    assert files["graph.jsonl"] == ""


def test_build_memory_archive_sections_are_stable(make_user, test_dsn):
    user = _seed_full_user(make_user, test_dsn)
    first = build_memory_archive(user["user_id"], dsn=test_dsn)
    second = build_memory_archive(user["user_id"], dsn=test_dsn)
    assert first.counts == second.counts
    assert first.manifest["files"] == second.manifest["files"]


# -- integration: CLI ------------------------------------------------------


def test_cli_memory_export_writes_archive(make_user, test_dsn, tmp_path, monkeypatch):
    from puspa.cli import main

    user = _seed_full_user(make_user, test_dsn)
    path = tmp_path / "memory.puspa"
    monkeypatch.setenv("PUSPA_TEST_DSN", test_dsn)
    monkeypatch.setenv("PUSPA_EXPORT_PASSPHRASE", PASSPHRASE)
    assert main(["memory", "export", "--user", user["user_id"], "--out", str(path)]) == 0
    assert verify_archive(path, PASSPHRASE)["format"] == ARCHIVE_FORMAT


def test_cli_memory_export_without_passphrase_fails(make_user, test_dsn, tmp_path, monkeypatch):
    import puspa.cli
    from puspa.cli import main

    user = make_user()
    monkeypatch.setenv("PUSPA_TEST_DSN", test_dsn)
    monkeypatch.delenv("PUSPA_EXPORT_PASSPHRASE", raising=False)
    monkeypatch.setattr(puspa.cli.getpass, "getpass", lambda prompt="": "")
    assert (
        main(
            [
                "memory",
                "export",
                "--user",
                user["user_id"],
                "--out",
                str(tmp_path / "memory.puspa"),
            ]
        )
        == 1
    )


def test_cli_memory_export_no_encrypt_needs_no_passphrase(
    make_user, test_dsn, tmp_path, monkeypatch
):
    from puspa.cli import main

    user = make_user()
    path = tmp_path / "memory.zip"
    monkeypatch.setenv("PUSPA_TEST_DSN", test_dsn)
    monkeypatch.delenv("PUSPA_EXPORT_PASSPHRASE", raising=False)
    assert (
        main(
            [
                "memory",
                "export",
                "--user",
                user["user_id"],
                "--out",
                str(path),
                "--no-encrypt",
            ]
        )
        == 0
    )
    assert verify_archive(path)["encrypted"] is False


def test_cli_unknown_command_still_prints_help(capsys):
    from puspa.cli import main

    assert main(["frobnicate"]) == 0
    # Default CLI language is Indonesian (COW-18); --lang en flips it.
    assert "perintah tidak dikenal: 'frobnicate'" in capsys.readouterr().out
    assert main(["--lang", "en", "frobnicate"]) == 0
    assert "unknown command: 'frobnicate'" in capsys.readouterr().out


# -- integration: PWA route --------------------------------------------------


def test_pwa_archive_route_returns_encrypted_file(make_user, test_dsn):
    from fastapi.testclient import TestClient

    from puspa.pwa.app import create_app

    user = _seed_full_user(make_user, test_dsn)
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    assert "/api/memory/export/archive" in {route.path for route in app.routes}
    with TestClient(app) as client:
        response = client.post("/api/memory/export/archive", json={"passphrase": PASSPHRASE})
    assert response.status_code == 200
    assert "puspa-memory-archive.bin" in response.headers["content-disposition"]
    assert verify_archive_bytes(response.content, PASSPHRASE)["format"] == ARCHIVE_FORMAT


def test_pwa_archive_route_requires_login_and_passphrase(make_user, test_dsn):
    from fastapi.testclient import TestClient

    from puspa.pwa.app import create_app

    user = make_user()
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.post("/api/memory/export/archive", json={}).status_code == 401
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    with TestClient(app) as client:
        assert client.post("/api/memory/export/archive", json={}).status_code == 400
        assert (
            client.post(
                "/api/memory/export/archive",
                json={"passphrase": PASSPHRASE, "layers": ["nope"]},
            ).status_code
            == 400
        )
