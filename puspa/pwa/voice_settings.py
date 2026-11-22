"""Voice choice for the PWA Settings screen (V2-30).

A user picks an ASR provider (speech-to-text for the P-03
recorder) and a TTS provider (how replies are spoken), and the
choice is persisted in Postgres (table ``voice_settings``, one
row per user) so it survives a restart. It sits next to the
model choice in Settings, and follows the same rules as
:mod:`puspa.pwa.model_settings`: the row holds the choice only,
never a key. Every key lives only in the existing per-user
vault (``puspa.accounts.vault``), and is read at call time.

The providers:

- ASR: ``none`` (the stub transcriber answers uploads, as
  today) or ``openai-compatible`` (the user's own key against
  any endpoint speaking the OpenAI ``/audio/transcriptions``
  shape: OpenAI, Groq, DeepSeek, Together, or a local server).
- TTS: ``edge`` (the free default: Microsoft neural voices via
  :mod:`puspa.voice.edge_tts`, no key), ``browser`` (the PWA
  speaks with the built-in ``speechSynthesis`` voice and the
  server is never called), ``elevenlabs`` (BYOK), or
  ``openai-compatible`` (BYOK), or ``azure`` (Azure Speech tier
  F0: free forever, but needs an Azure account, so it is a
  choice, never the default).

A provider the user picked in Settings wins over the Edge
default: the default chain (Edge, then browser fallback) is
what a user who never opened Settings gets, and an explicit
BYOK choice is that user asking for their own provider instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from puspa.accounts.ports import _connect, resolve_dsn
from puspa.migrations import apply_migrations
from puspa.voice.edge_tts import DEFAULT_EDGE_VOICE

__all__ = [
    "ASR_PROVIDERS",
    "DEFAULT_ASR_MODEL",
    "DEFAULT_TTS_MODEL",
    "KNOWN_ASR_BASE_URLS",
    "KNOWN_TTS_BASE_URLS",
    "TTS_PROVIDERS",
    "VoiceSettings",
    "create_schema",
    "delete_voice_key",
    "get_settings",
    "has_voice_key",
    "resolve_asr",
    "resolve_tts",
    "save_settings",
    "save_voice_key",
    "validate_base_url",
]

#: ASR providers the Settings picker offers.
ASR_PROVIDERS = ("none", "openai-compatible")

#: TTS providers the Settings picker offers, in display order.
TTS_PROVIDERS = ("edge", "browser", "elevenlabs", "openai-compatible", "azure")

#: Default model asked of an OpenAI-compatible ASR endpoint.
DEFAULT_ASR_MODEL = "whisper-1"

#: Default model asked of an OpenAI-compatible TTS endpoint.
DEFAULT_TTS_MODEL = "tts-1"

#: Vault entry prefix for voice keys: ``voice:<kind>:<provider>``
#: (``voice:asr:openai``, ``voice:tts:elevenlabs``). Separate from
#: the model BYOK entries (``byok:<provider>``), so a voice key
#: can never be mistaken for a chat-model key.
VOICE_KEY_PREFIX = "voice:"

#: Known OpenAI-compatible base URLs for ASR providers, so a user
#: who picks a provider does not have to type its endpoint. A
#: user-supplied base URL on the settings wins over this map.
KNOWN_ASR_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "together": "https://api.together.xyz/v1",
}

#: Known OpenAI-compatible base URLs for TTS providers.
KNOWN_TTS_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "together": "https://api.together.xyz/v1",
}

#: Keys a caller must never try to store through the settings
#: path. Any payload carrying one is refused outright, so a key
#: can only ever enter the vault through :func:`save_voice_key`.
_FORBIDDEN_SETTINGS_KEYS = ("api_key", "apiKey", "key", "secret", "token", "password")


@dataclass(frozen=True)
class VoiceSettings:
    """One user's stored voice choice.

    ``asr_provider`` is one of ``ASR_PROVIDERS``; ``tts_provider``
    one of ``TTS_PROVIDERS``. ``asr_base_url`` / ``asr_model`` and
    ``tts_base_url`` / ``tts_model`` are the endpoint overrides;
    ``tts_voice`` is the voice id (Edge voice name, ElevenLabs
    voice id, or OpenAI-compatible voice name, depending on the
    provider); ``tts_region`` is the Azure region, used only
    when ``tts_provider`` is ``azure``. ``has_asr_key`` /
    ``has_tts_key`` report whether the matching vault entry exists,
    without exposing the key.
    """

    asr_provider: str = "none"
    asr_base_url: str | None = None
    asr_model: str | None = None
    tts_provider: str = "edge"
    tts_voice: str | None = None
    tts_model: str | None = None
    tts_base_url: str | None = None
    tts_region: str | None = None
    has_asr_key: bool = False
    has_tts_key: bool = False

    def to_public_dict(self) -> dict:
        """The settings as the Settings screen sees them.

        Contains no key material by construction: the row holds
        no key, and ``has_asr_key`` / ``has_tts_key`` are
        booleans only.
        """
        return {
            "asr_provider": self.asr_provider,
            "asr_base_url": self.asr_base_url,
            "asr_model": self.asr_model,
            "tts_provider": self.tts_provider,
            "tts_voice": self.tts_voice,
            "tts_model": self.tts_model,
            "tts_base_url": self.tts_base_url,
            "tts_region": self.tts_region,
            "has_asr_key": self.has_asr_key,
            "has_tts_key": self.has_tts_key,
        }


def validate_base_url(base_url: str) -> str:
    """Validate a voice base URL and return it stripped.

    Only http(s) URLs with a host are accepted, and userinfo is
    refused so credentials cannot hide in the URL. Raises
    ``ValueError`` naming the problem for anything else.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base URL must be a non-empty string")
    cleaned = base_url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"base URL {cleaned!r} must use http or https, not {parsed.scheme or '(no scheme)'!r}"
        )
    if not parsed.hostname:
        raise ValueError(f"base URL {cleaned!r} has no host")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain credentials (userinfo)")
    return cleaned


def create_schema(dsn=None) -> None:
    """Apply the ``voice_settings`` table (migration 0033_voice_settings)."""
    apply_migrations(resolve_dsn(dsn), target="0033")


def _voice_vault_name(kind: str, provider: str) -> str:
    return f"{VOICE_KEY_PREFIX}{kind}:{provider.strip().lower()}"


def _check_no_key(payload: dict) -> None:
    """Refuse any payload that tries to carry key material."""
    for forbidden in _FORBIDDEN_SETTINGS_KEYS:
        if forbidden in payload:
            raise ValueError(
                f"refusing to store {forbidden!r} outside the vault: "
                "a voice key is saved only through save_voice_key, "
                "never through the settings"
            )


def save_settings(user_id, settings: VoiceSettings | dict, dsn=None) -> VoiceSettings:
    """Persist a user's voice settings (choice only, never a key).

    Accepts a :class:`VoiceSettings` or a plain dict with the
    same fields. A dict carrying any key field (``api_key``,
    ``key``, ...) is refused with ``ValueError``. Keys go to
    the vault only. Returns the stored settings (with
    ``has_asr_key`` / ``has_tts_key`` filled in).
    """
    if isinstance(settings, dict):
        _check_no_key(settings)
        resolved = VoiceSettings(
            asr_provider=str(settings.get("asr_provider", "none")),
            asr_base_url=settings.get("asr_base_url"),
            asr_model=settings.get("asr_model"),
            tts_provider=str(settings.get("tts_provider", "edge")),
            tts_voice=settings.get("tts_voice"),
            tts_model=settings.get("tts_model"),
            tts_base_url=settings.get("tts_base_url"),
            tts_region=settings.get("tts_region"),
        )
    elif isinstance(settings, VoiceSettings):
        resolved = settings
    else:
        raise ValueError("settings must be a VoiceSettings or a dict")
    resolved = _normalize(resolved)
    create_schema(dsn)

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO voice_settings"
                " (user_id, asr_provider, asr_base_url, asr_model,"
                " tts_provider, tts_voice, tts_model, tts_base_url, tts_region)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (user_id) DO UPDATE SET"
                " asr_provider = EXCLUDED.asr_provider,"
                " asr_base_url = EXCLUDED.asr_base_url,"
                " asr_model = EXCLUDED.asr_model,"
                " tts_provider = EXCLUDED.tts_provider,"
                " tts_voice = EXCLUDED.tts_voice,"
                " tts_model = EXCLUDED.tts_model,"
                " tts_base_url = EXCLUDED.tts_base_url,"
                " tts_region = EXCLUDED.tts_region,"
                " updated_at = now()",
                (
                    user_id,
                    resolved.asr_provider,
                    resolved.asr_base_url,
                    resolved.asr_model,
                    resolved.tts_provider,
                    resolved.tts_voice,
                    resolved.tts_model,
                    resolved.tts_base_url,
                    resolved.tts_region,
                ),
            )
        conn.commit()
    return get_settings(user_id, dsn=dsn)


def _normalize(settings: VoiceSettings) -> VoiceSettings:
    """Validate one settings row and fill in per-provider defaults."""
    asr_provider = (
        settings.asr_provider.strip().lower() if isinstance(settings.asr_provider, str) else ""
    )
    if asr_provider not in ASR_PROVIDERS:
        raise ValueError(
            f"unknown ASR provider {settings.asr_provider!r} (known: {', '.join(ASR_PROVIDERS)})"
        )
    tts_provider = (
        settings.tts_provider.strip().lower() if isinstance(settings.tts_provider, str) else ""
    )
    if tts_provider not in TTS_PROVIDERS:
        raise ValueError(
            f"unknown TTS provider {settings.tts_provider!r} (known: {', '.join(TTS_PROVIDERS)})"
        )
    asr_base_url = settings.asr_base_url.strip() if isinstance(settings.asr_base_url, str) else None
    asr_base_url = asr_base_url or None
    asr_model = settings.asr_model.strip() if isinstance(settings.asr_model, str) else None
    asr_model = asr_model or None
    tts_base_url = settings.tts_base_url.strip() if isinstance(settings.tts_base_url, str) else None
    tts_base_url = tts_base_url or None
    tts_model = settings.tts_model.strip() if isinstance(settings.tts_model, str) else None
    tts_model = tts_model or None
    tts_voice = settings.tts_voice.strip() if isinstance(settings.tts_voice, str) else None
    tts_voice = tts_voice or None
    tts_region = settings.tts_region.strip() if isinstance(settings.tts_region, str) else None
    tts_region = tts_region or None
    if asr_base_url is not None:
        asr_base_url = validate_base_url(asr_base_url)
    if tts_base_url is not None:
        tts_base_url = validate_base_url(tts_base_url)
    if tts_provider == "edge" and tts_voice is None:
        tts_voice = DEFAULT_EDGE_VOICE
    if tts_provider == "azure" and tts_region is None:
        raise ValueError("Azure TTS needs a region (for example 'southeastasia')")
    return VoiceSettings(
        asr_provider=asr_provider,
        asr_base_url=asr_base_url,
        asr_model=asr_model,
        tts_provider=tts_provider,
        tts_voice=tts_voice,
        tts_model=tts_model,
        tts_base_url=tts_base_url,
        tts_region=tts_region,
    )


def get_settings(user_id, dsn=None) -> VoiceSettings:
    """Load a user's stored voice settings; the defaults when none is stored.

    Never raises for a missing row: a user who never opened the
    picker gets Edge TTS and the stub transcriber.
    """
    create_schema(dsn)

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT asr_provider, asr_base_url, asr_model,"
            " tts_provider, tts_voice, tts_model, tts_base_url, tts_region"
            " FROM voice_settings WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return VoiceSettings(tts_voice=DEFAULT_EDGE_VOICE)
    settings = VoiceSettings(
        asr_provider=row[0],
        asr_base_url=row[1],
        asr_model=row[2],
        tts_provider=row[3],
        tts_voice=row[4],
        tts_model=row[5],
        tts_base_url=row[6],
        tts_region=row[7],
    )
    return VoiceSettings(
        asr_provider=settings.asr_provider,
        asr_base_url=settings.asr_base_url,
        asr_model=settings.asr_model,
        tts_provider=settings.tts_provider,
        tts_voice=settings.tts_voice or DEFAULT_EDGE_VOICE
        if settings.tts_provider == "edge"
        else settings.tts_voice,
        tts_model=settings.tts_model,
        tts_base_url=settings.tts_base_url,
        tts_region=settings.tts_region,
        has_asr_key=has_voice_key(user_id, "asr", settings.asr_provider, dsn=dsn)
        if settings.asr_provider != "none"
        else False,
        has_tts_key=has_voice_key(user_id, "tts", settings.tts_provider, dsn=dsn)
        if settings.tts_provider in ("elevenlabs", "openai-compatible", "azure")
        else False,
    )


def save_voice_key(user_id, kind: str, provider: str, key: str, dsn=None) -> None:
    """Store a voice key in the existing per-user vault, and only there.

    ``kind`` is ``"asr"`` or ``"tts"``; the vault entry is
    ``voice:<kind>:<provider>`` (envelope-encrypted; only
    ciphertext reaches the database). An empty key is refused;
    the key value is never logged or returned.
    """
    from puspa.accounts.vault import put as vault_put

    if kind not in ("asr", "tts"):
        raise ValueError(f"voice key kind must be 'asr' or 'tts', not {kind!r}")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("voice provider must be a non-empty string")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("voice key must be a non-empty string")
    vault_put(user_id, _voice_vault_name(kind, provider), key, dsn=dsn)


def has_voice_key(user_id, kind: str, provider: str, dsn=None) -> bool:
    """Whether a voice key for ``kind``/``provider`` exists in the vault."""
    from puspa.accounts.vault import get as vault_get

    try:
        vault_get(user_id, _voice_vault_name(kind, provider), dsn=dsn)
    except (KeyError, Exception):
        return False
    return True


def delete_voice_key(user_id, kind: str, provider: str, dsn=None) -> None:
    """Remove a voice key from the vault. Idempotent."""
    from puspa.accounts.vault import delete as vault_delete

    vault_delete(user_id, _voice_vault_name(kind, provider), dsn=dsn)


def _speech_route_override(user_id, dsn=None) -> tuple[str | None, str | None]:
    """The stored speech route's endpoint + model, if one is stored.

    COW-08: a speech route sets the endpoint + model the
    OpenAI-compatible ASR/TTS engines call. Returns
    ``(base_url, model)`` (either may be None) when a row is
    stored, else ``(None, None)``: with no stored route the
    voice settings behave exactly as today. Fail-open: a
    routing table that cannot be read must never break a voice
    upload or a speak call, so any read error means "no
    override".
    """
    try:
        from puspa.pwa.capability_routing import KNOWN_BASE_URLS, get_route

        route = get_route(user_id, "speech", dsn=dsn)
    except Exception:
        return None, None
    if route.is_default or route.provider is None:
        return None, None
    return route.base_url or KNOWN_BASE_URLS.get(route.provider), route.model


def resolve_asr(user_id, settings: VoiceSettings | None = None, dsn=None, http_client=None):
    """Resolve a user's ASR choice to a transcriber instance.

    - ``none`` (or no key stored for the provider): the stub
      transcriber, exactly as the app behaves today.
    - ``openai-compatible``: :class:`OpenAICompatibleTranscriber`
      against the stored base URL (OpenAI's endpoint when none
      is stored) with the vault key, model default ``whisper-1``.
      The key lives under ``voice:asr:openai-compatible``.
      Raises ``KeyError`` when no vault key exists.
      COW-08: a stored speech route overrides the endpoint +
      model here (the key still comes from the voice vault).
    """
    from puspa.voice.asr import OpenAICompatibleTranscriber
    from puspa.voice.transcriber import StubTranscriber

    if settings is None:
        settings = get_settings(user_id, dsn=dsn)
    if settings.asr_provider == "none":
        return StubTranscriber()
    if settings.asr_provider == "openai-compatible":
        from puspa.accounts.vault import get as vault_get

        try:
            key = vault_get(user_id, _voice_vault_name("asr", "openai-compatible"), dsn=dsn)
        except Exception as exc:
            # Missing entry (KeyError) and an unusable vault
            # (VaultError when no master key is configured) both
            # mean the same thing to the caller: no usable key.
            raise KeyError(
                "no ASR key stored: save one in Settings first (it lives in the vault only)"
            ) from exc
        base_url = settings.asr_base_url or KNOWN_ASR_BASE_URLS["openai"]
        route_base_url, route_model = _speech_route_override(user_id, dsn=dsn)
        return OpenAICompatibleTranscriber(
            route_base_url or base_url,
            key,
            route_model or settings.asr_model or DEFAULT_ASR_MODEL,
            http_client=http_client,
        )
    raise ValueError(f"unknown ASR provider {settings.asr_provider!r}")


def resolve_tts(user_id, settings: VoiceSettings | None = None, dsn=None, http_client=None):
    """Resolve a user's TTS choice to a synthesis engine.

    Returns ``("edge", voice)`` for the free default (the caller
    speaks through :func:`puspa.voice.tts.synthesize_with_edge`),
    ``("browser", None)`` when the PWA must use its built-in
    voice (the server is never called), or a BYOK engine
    instance (ElevenLabs / OpenAI-compatible / Azure) built with
    the vault key. Raises ``KeyError`` when a BYOK provider has
    no vault key. COW-08: a stored speech route overrides the
    endpoint + model of the OpenAI-compatible engine (the key
    still comes from the voice vault).
    """
    from puspa.voice.tts import AzureTTS, ElevenLabsTTS, OpenAICompatibleTTS

    if settings is None:
        settings = get_settings(user_id, dsn=dsn)
    provider = settings.tts_provider
    if provider == "edge":
        return ("edge", settings.tts_voice or DEFAULT_EDGE_VOICE)
    if provider == "browser":
        return ("browser", None)
    from puspa.accounts.vault import get as vault_get

    try:
        key = vault_get(user_id, _voice_vault_name("tts", provider), dsn=dsn)
    except Exception as exc:
        # Missing entry (KeyError) and an unusable vault
        # (VaultError when no master key is configured) both mean
        # the same thing to the caller: no usable key.
        raise KeyError(
            f"no TTS key stored for provider {provider!r}: "
            "save one in Settings first (it lives in the vault only)"
        ) from exc
    if provider == "elevenlabs":
        voice = settings.tts_voice or "21m00Tcm4TlvDq8ikWAM"
        return ElevenLabsTTS(
            key, voice, settings.tts_model or "eleven_multilingual_v2", http_client=http_client
        )
    if provider == "openai-compatible":
        base_url = settings.tts_base_url or KNOWN_TTS_BASE_URLS["openai"]
        route_base_url, route_model = _speech_route_override(user_id, dsn=dsn)
        return OpenAICompatibleTTS(
            route_base_url or base_url,
            key,
            route_model or settings.tts_model or DEFAULT_TTS_MODEL,
            settings.tts_voice or "alloy",
            http_client=http_client,
        )
    if provider == "azure":
        region = (settings.tts_region or "").strip()
        if not region:
            raise ValueError(
                "Azure TTS needs a region: type it in Settings (for example 'southeastasia')"
            )
        return AzureTTS(
            key, region, settings.tts_voice or DEFAULT_EDGE_VOICE, http_client=http_client
        )
    raise ValueError(f"unknown TTS provider {provider!r}")
