"""Tests for voice settings in PWA Settings (V2-30).

Persistence: the voice choice lives in Postgres (table
``voice_settings``), every key lives only in the existing
per-user vault (``voice:<kind>:<provider>``, envelope-encrypted).
Resolution: ASR/TTS choices resolve to the real engines with
the vault key, and fail loudly when the key is missing.

No test touches the network or a real key: the only key values
used are obviously fake and are only ever sent to a mock
transport, never to a real host.
"""

from puspa.db import connect as _db_connect
import pytest

from puspa.pwa.voice_settings import (
    VoiceSettings,
    create_schema,
    delete_voice_key,
    get_settings,
    has_voice_key,
    resolve_asr,
    resolve_tts,
    save_settings,
    save_voice_key,
    validate_base_url,
)

FAKE_VOICE_KEY = "test-voice-key-not-real"


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture(autouse=True)
def _voice_settings_table(test_dsn):
    create_schema(test_dsn)
    yield
    with _db_connect(test_dsn) as conn:
        conn.execute("TRUNCATE voice_settings")
        conn.commit()


def test_default_settings_are_edge_tts_and_no_asr(user, test_dsn):
    settings = get_settings(user["user_id"], dsn=test_dsn)
    assert settings.asr_provider == "none"
    assert settings.tts_provider == "edge"
    assert settings.tts_voice == "id-ID-GadisNeural"
    assert settings.has_asr_key is False
    assert settings.has_tts_key is False


def test_save_and_read_round_trip(user, test_dsn):
    saved = save_settings(
        user["user_id"],
        {
            "asr_provider": "openai-compatible",
            "asr_base_url": "https://api.openai.com/v1",
            "asr_model": "whisper-1",
            "tts_provider": "elevenlabs",
            "tts_voice": "voice-abc",
        },
        dsn=test_dsn,
    )
    assert saved.asr_provider == "openai-compatible"
    assert saved.tts_provider == "elevenlabs"
    assert saved.tts_voice == "voice-abc"
    again = get_settings(user["user_id"], dsn=test_dsn)
    assert again.asr_base_url == "https://api.openai.com/v1"
    assert again.tts_voice == "voice-abc"


def test_save_refuses_key_in_settings_body(user, test_dsn):
    with pytest.raises(ValueError, match="outside the vault"):
        save_settings(
            user["user_id"], {"tts_provider": "edge", "api_key": FAKE_VOICE_KEY}, dsn=test_dsn
        )
    assert get_settings(user["user_id"], dsn=test_dsn).tts_provider == "edge"


def test_save_rejects_unknown_providers_and_bad_urls(user, test_dsn):
    with pytest.raises(ValueError, match="unknown ASR provider"):
        save_settings(user["user_id"], {"asr_provider": "whisper-local"}, dsn=test_dsn)
    with pytest.raises(ValueError, match="unknown TTS provider"):
        save_settings(user["user_id"], {"tts_provider": "google"}, dsn=test_dsn)
    with pytest.raises(ValueError, match="must use http or https"):
        save_settings(
            user["user_id"],
            {"asr_provider": "openai-compatible", "asr_base_url": "ftp://x/v1"},
            dsn=test_dsn,
        )
    with pytest.raises(ValueError, match="must not contain credentials"):
        save_settings(
            user["user_id"],
            {"asr_provider": "openai-compatible", "asr_base_url": "https://user:pass@x/v1"},
            dsn=test_dsn,
        )


def test_azure_needs_a_region(user, test_dsn):
    with pytest.raises(ValueError, match="needs a region"):
        save_settings(
            user["user_id"],
            {"tts_provider": "azure", "tts_voice": "id-ID-GadisNeural"},
            dsn=test_dsn,
        )
    saved = save_settings(
        user["user_id"],
        {"tts_provider": "azure", "tts_voice": "id-ID-GadisNeural", "tts_region": "southeastasia"},
        dsn=test_dsn,
    )
    assert saved.tts_region == "southeastasia"


def test_voice_key_round_trip_lives_in_vault_only(user, test_dsn, vault_master_key):
    save_settings(
        user["user_id"],
        {
            "asr_provider": "openai-compatible",
            "tts_provider": "elevenlabs",
            "tts_voice": "voice-abc",
        },
        dsn=test_dsn,
    )
    save_voice_key(user["user_id"], "asr", "openai-compatible", FAKE_VOICE_KEY, dsn=test_dsn)
    save_voice_key(user["user_id"], "tts", "elevenlabs", FAKE_VOICE_KEY, dsn=test_dsn)
    settings = get_settings(user["user_id"], dsn=test_dsn)
    assert settings.has_asr_key is True
    assert settings.has_tts_key is True
    public = settings.to_public_dict()
    assert FAKE_VOICE_KEY not in str(public)
    # The row itself holds no key column at all.
    with _db_connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM voice_settings WHERE user_id = %s", (user["user_id"],))
        row = cur.fetchone()
    assert FAKE_VOICE_KEY not in str(row)
    delete_voice_key(user["user_id"], "asr", "openai-compatible", dsn=test_dsn)
    assert has_voice_key(user["user_id"], "asr", "openai-compatible", dsn=test_dsn) is False


def test_save_voice_key_refuses_empty_key(user, test_dsn, vault_master_key):
    with pytest.raises(ValueError, match="non-empty string"):
        save_voice_key(user["user_id"], "asr", "openai-compatible", "  ", dsn=test_dsn)
    with pytest.raises(ValueError, match="must be 'asr' or 'tts'"):
        save_voice_key(user["user_id"], "chat", "openai", FAKE_VOICE_KEY, dsn=test_dsn)


def test_resolve_asr_without_key_falls_back_to_stub(user, test_dsn):
    save_settings(user["user_id"], {"asr_provider": "none"}, dsn=test_dsn)
    assert resolve_asr(user["user_id"], dsn=test_dsn).name == "stub"


def test_resolve_asr_with_key_builds_the_real_engine(user, test_dsn, vault_master_key):
    save_settings(
        user["user_id"],
        {"asr_provider": "openai-compatible", "asr_base_url": "https://api.groq.com/openai/v1"},
        dsn=test_dsn,
    )
    save_voice_key(user["user_id"], "asr", "openai-compatible", FAKE_VOICE_KEY, dsn=test_dsn)
    engine = resolve_asr(user["user_id"], dsn=test_dsn)
    assert engine.name == "openai-compatible"
    assert engine.base_url == "https://api.groq.com/openai/v1"
    assert engine.model == "whisper-1"


def test_resolve_asr_without_key_fails_loudly(user, test_dsn, vault_master_key):
    save_settings(user["user_id"], {"asr_provider": "openai-compatible"}, dsn=test_dsn)
    with pytest.raises(KeyError, match="no ASR key stored"):
        resolve_asr(user["user_id"], dsn=test_dsn)


def test_resolve_tts_edge_default_and_browser(user, test_dsn):
    kind, voice = resolve_tts(user["user_id"], dsn=test_dsn)
    assert kind == "edge"
    assert voice == "id-ID-GadisNeural"
    save_settings(user["user_id"], {"tts_provider": "browser"}, dsn=test_dsn)
    assert resolve_tts(user["user_id"], dsn=test_dsn) == ("browser", None)


def test_resolve_tts_elevenlabs_with_key(user, test_dsn, vault_master_key):
    save_settings(
        user["user_id"], {"tts_provider": "elevenlabs", "tts_voice": "voice-abc"}, dsn=test_dsn
    )
    save_voice_key(user["user_id"], "tts", "elevenlabs", FAKE_VOICE_KEY, dsn=test_dsn)
    engine = resolve_tts(user["user_id"], dsn=test_dsn)
    assert engine.name == "elevenlabs"
    assert engine.voice_id == "voice-abc"


def test_resolve_tts_without_key_fails_loudly(user, test_dsn, vault_master_key):
    save_settings(
        user["user_id"], {"tts_provider": "elevenlabs", "tts_voice": "voice-abc"}, dsn=test_dsn
    )
    with pytest.raises(KeyError, match="no TTS key stored"):
        resolve_tts(user["user_id"], dsn=test_dsn)


def test_validate_base_url():
    assert validate_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    with pytest.raises(ValueError, match="must use http or https"):
        validate_base_url("ftp://x/v1")
    with pytest.raises(ValueError, match="must not contain credentials"):
        validate_base_url("https://user:pass@x/v1")


def test_voice_settings_dataclass_defaults():
    assert VoiceSettings().tts_provider == "edge"
    assert VoiceSettings().asr_provider == "none"
    assert "api_key" not in VoiceSettings().to_public_dict()
