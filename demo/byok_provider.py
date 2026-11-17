"""Factory BYOK generik Puspa: satu pintu masuk untuk semua provider LLM.

Satu fungsi utama: :func:`build_byok_provider`. Dibangun DI ATAS factory /
registry nanobot (bukan client HTTP buatan sendiri): provider OpenAI-compatible
lewat ``OpenAICompatProvider`` + spec registry nanobot, Anthropic lewat
``AnthropicProvider`` native, Azure lewat ``AzureOpenAIProvider`` native,
Bedrock lewat ``BedrockProvider`` native, dan provider OAuth (Copilot, Codex,
xAI Grok) lewat class native nanobot masing-masing.

Pemakaian (dari ``build/fase-0`` dengan venv aktif)::

    from demo.byok_provider import build_byok_provider

    provider = build_byok_provider("openrouter", "qwen/qwen3.7-flash")
    provider = build_byok_provider("openai", "gpt-4o-mini", key="sk-...")
    provider = build_byok_provider(None, "anthropic/claude-sonnet-4-6", key="sk-ant-...")
    provider = build_byok_provider("openai_compatible", "llama-3.1-8b",
    ...                            base_url="https://api.together.xyz/v1", key="...")

Kunci API TIDAK PERNAH di-hardcode, dicetak, atau dilog di sini. Untuk
OpenRouter, kunci diambil sebagai surrogate segar (``hsurr:...``) dari
``dynamic_credential_entry("custom.openrouter")`` lewat ``get_openrouter_surrogate()``
di ``demo/openrouter_provider.py`` (dipakai ulang, bukan ditulis ulang);
runtime menukar surrogate itu dengan kunci asli hanya pada request ke host
terdaftar ``openrouter.ai``. Untuk provider lain yang belum ada kunci
terdaftar, kunci disuplai eksplisit lewat argumen ``key`` (demo/mock: nilai
mock yang JELAS-JELAS palsu, mis. ``"mock-key-untuk-uji-protokol"``) atau
lewat vault per provider — lihat :func:`put_provider_key` /
:func:`get_provider_key` dan ``demo/MATRIKS_BYOK.md``. JANGAN minta kunci
di chat dan JANGAN mengarang kunci.

Menambah provider baru (satu baris config)
--------------------------------------------
Provider OpenAI-compatible baru cukup SATU baris::

    register_provider("nama-baru", "https://api.vendor.example/v1")

``register_provider`` membuat spec dinamis (``create_dynamic_spec`` nanobot)
dan mendaftarkannya di :data:`CUSTOM_PROVIDERS`; factory langsung bisa
memakainya tanpa mengubah kode lain. Contoh yang sudah terdaftar di bawah:
``together`` (https://api.together.xyz/v1) — dibuktikan lewat mock, bukan
live. Provider yang SUDAH ada di registry nanobot (groq, deepseek, mistral,
dll.) tidak perlu didaftarkan: factory menemukannya lewat ``find_by_name``.

Per provider (ringkas; matriks lengkap di ``demo/MATRIKS_BYOK.md``)
--------------------------------------------------------------------
- openrouter: OpenAI-compatible, base https://openrouter.ai/api/v1,
  auth ``Authorization: Bearer <surrogate hsurr:...>``, pesan format
  OpenAI chat-completions (``{"role", "content"}`` + ``tools``).
- openai: OpenAI-compatible, base https://api.openai.com/v1 (default SDK),
  auth ``Authorization: Bearer <OPENAI_API_KEY>``, format chat-completions
  (model gpt-5+/o-series tertentu dialihkan nanobot ke Responses API).
- anthropic: NATIVE Anthropic Messages API, base https://api.anthropic.com
  (SDK menambah ``/v1``), auth ``x-api-key: <key>`` + header
  ``anthropic-version``, pesan format Messages API (``system`` terpisah,
  content blocks, ``tool_use``/``tool_result``).
- gemini: OpenAI-compatible, base
  https://generativelanguage.googleapis.com/v1beta/openai/, auth
  ``Authorization: Bearer <GEMINI_API_KEY>``, format chat-completions.
  (Tidak ada provider Gemini native di nanobot.)
- azure_openai: NATIVE Responses API, base = endpoint Azure pengguna
  (``https://<resource>.openai.azure.com``; WAJIB diisi lewat ``base_url``,
  model = nama deployment), auth ``Authorization: Bearer <key>`` (atau
  DefaultAzureCredential bila key kosong), format Responses API.
- bedrock: NATIVE Bedrock Converse API (boto3 ``bedrock-runtime``), auth
  BUKAN api key biasa: kredensial AWS (env ``AWS_ACCESS_KEY_ID`` /
  ``AWS_SECRET_ACCESS_KEY`` / profile, atau ``AWS_BEARER_TOKEN_BEDROCK``),
  format Converse (``modelId``, ``messages``, ``toolConfig``).
- xai: OAuth (``XAIGrokProvider`` native, langganan xAI; BUKAN api key).
  API key xAI biasa (https://api.x.ai/v1) tersedia lewat
  ``openai_compatible`` dengan ``base_url="https://api.x.ai/v1"``.
- github_copilot: OAuth GitHub (``nanobot provider login github-copilot``).
- openai_codex: OAuth Codex (``nanobot provider login openai-codex``).
- ollama / lokal: OpenAI-compatible, base http://localhost:11434/v1,
  tanpa kunci (kunci dummy ``"ollama"``), format chat-completions.
- openai_compatible (generik): OpenAI-compatible, ``base_url`` bebas
  WAJIB diisi, auth ``Authorization: Bearer <key>``, format
  chat-completions. Inilah jalur untuk provider APA PUN (DeepSeek, Groq,
  Together, Mistral, dll. — yang sudah di registry nanobot bisa juga
  dipanggil langsung dengan namanya).

Catatan lingkungan: semua HTTP keluar di host ini harus memakai
``NO_PROXY=localhost,127.0.0.1`` (entri IPv6 dalam kurung di NO_PROXY bawaan
merusak httpx).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap sys.path (seperti demo/byok_demo.py / tests/conftest.py)
# ---------------------------------------------------------------------------

_FASE0 = Path(__file__).resolve().parent.parent
_DEMO_DIR = Path(__file__).resolve().parent
_NANOBOT_LOCAL = _FASE0 / "nanobot"
if str(_NANOBOT_LOCAL) not in sys.path and (_NANOBOT_LOCAL / "nanobot" / "__init__.py").exists():
    sys.path.insert(0, str(_NANOBOT_LOCAL))
if str(_FASE0) not in sys.path:
    sys.path.insert(0, str(_FASE0))
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

#: Kunci mock yang JELAS-JELAS palsu — hanya untuk demo/mock, bukan kunci nyata.
MOCK_KEY = "mock-key-untuk-uji-protokol"

#: Nama provider yang factory ini kenali (nama kanonis, lihat registry nanobot).
SUPPORTED_PROVIDERS = (
    "openrouter",
    "openai",
    "anthropic",
    "gemini",
    "azure_openai",
    "bedrock",
    "xai",
    "github_copilot",
    "openai_codex",
    "ollama",
    "openai_compatible",
    # Semua nama di registry nanobot juga diterima apa adanya
    # (groq, deepseek, mistral, together*, huggingface, siliconflow, ...).
    # "*" di atas: "together" BUKAN di registry nanobot — didaftarkan di bawah
    # lewat register_provider sebagai bukti satu-baris-config.
)

#: Provider OAuth (tanpa api key; butuh login OAuth nanobot, lihat matriks).
OAUTH_PROVIDERS = ("xai", "github_copilot", "openai_codex")

#: Provider yang konstruksinya boleh tanpa kunci (lokal / OAuth / Bedrock
#: yang kredensialnya datang dari env AWS / profile).
KEY_OPTIONAL_PROVIDERS = ("ollama", "bedrock", *OAUTH_PROVIDERS)

#: Prefix model -> provider (dipakai bila argumen provider None).
#: Hanya prefix yang tidak ambigu. "google/gemma-..." TIDAK dipetakan otomatis:
#: di OpenRouter itu model gateway, bukan Gemini native.
MODEL_PREFIX_PROVIDERS: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "deepseek": "deepseek",
    "groq": "groq",
    "mistral": "mistral",
    "openrouter": "openrouter",
    "azure": "azure_openai",
    "azure_openai": "azure_openai",
    "bedrock": "bedrock",
    "xai": "xai",
    "grok": "xai",
    "github_copilot": "github_copilot",
    "copilot": "github_copilot",
    "openai_codex": "openai_codex",
    "codex": "openai_codex",
    "ollama": "ollama",
    "together": "together",
}

#: Model tanpa prefix -> provider (awalan nama model yang khas).
MODEL_NAME_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "gemini"),
    ("grok-", "xai"),
    ("deepseek-", "deepseek"),
)


def resolve_provider_name(provider: str | None, model: str) -> str:
    """Tentukan nama provider kanonis dari nama eksplisit atau prefix model.

    Aturan: nama eksplisit menang; bila ``provider`` None, lihat segmen
    pertama model (``"anthropic/claude-..."`` -> ``"anthropic"``) lalu
    awalan nama model (``"gpt-..."`` -> ``"openai"``). Gagal -> ValueError
    yang menyebut provider yang didukung (bukan tebakan diam-diam).
    """
    if provider:
        return _canonical(provider)
    model = (model or "").strip()
    if "/" in model:
        prefix = model.split("/", 1)[0].strip().lower().replace("-", "_")
        if prefix in MODEL_PREFIX_PROVIDERS:
            return MODEL_PREFIX_PROVIDERS[prefix]
        # Prefix "openai/..." dsb. yang sudah dinormalisasi di atas.
        if prefix in ("openai", "anthropic", "gemini"):
            return prefix
    bare = model.rsplit("/", 1)[-1].strip().lower()
    for prefix, provider_name in MODEL_NAME_PROVIDERS:
        if bare.startswith(prefix):
            return provider_name
    raise ValueError(
        f"provider tidak bisa ditebak dari model {model!r}; "
        f"sebutkan provider eksplisit. Didukung: {', '.join(SUPPORTED_PROVIDERS)} "
        "(+ semua name di registry nanobot, mis. groq/deepseek/mistral)."
    )


def _canonical(name: str) -> str:
    """Normalisasi nama provider: kecil, '-' -> '_', alias umum."""
    name = (name or "").strip().lower().replace("-", "_")
    alias = {
        "openai_compat": "openai_compatible",
        "custom": "openai_compatible",
        "generic": "openai_compatible",
        "grok": "xai",
        "xai_grok": "xai",
        "copilot": "github_copilot",
        "codex": "openai_codex",
        "local": "ollama",
        "lm_studio": "ollama",  # sama-sama lokal OpenAI-compatible; base_url diganti pemanggil
    }
    return alias.get(name, name)


# ---------------------------------------------------------------------------
# Registrasi provider generik — SATU BARIS CONFIG per provider baru
# ---------------------------------------------------------------------------

#: name -> base_url default untuk provider yang didaftarkan lewat config
#: (bukan dari registry nanobot). Satu entri = satu provider baru.
CUSTOM_PROVIDERS: dict[str, str] = {}


def register_provider(name: str, base_url: str) -> str:
    """Daftarkan provider OpenAI-compatible baru dalam SATU BARIS config.

    Contoh: ``register_provider("together", "https://api.together.xyz/v1")``.
    Mengembalikan nama kanonis. Setelah ini,
    ``build_byok_provider(name, model, key=...)`` langsung jalan lewat
    ``OpenAICompatProvider`` + spec dinamis nanobot (``create_dynamic_spec``),
    tanpa mengubah kode factory. Auth: ``Authorization: Bearer <key>``,
    format pesan: OpenAI chat-completions.
    """
    canonical_name = _canonical(name)
    base = base_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError(f"base_url provider {name!r} harus http(s): {base_url!r}")
    CUSTOM_PROVIDERS[canonical_name] = base
    return canonical_name


# Bukti satu-baris-config: Together AI TIDAK ada di registry nanobot
# (cek: find_by_name("together") is None), tapi cukup baris ini untuk masuk.
register_provider("together", "https://api.together.xyz/v1")


# ---------------------------------------------------------------------------
# Kunci: surrogate OpenRouter (dipakai ulang) + vault per provider
# ---------------------------------------------------------------------------

def get_openrouter_surrogate() -> str:
    """Surrogate segar OpenRouter — dipakai ulang dari openrouter_provider.

    Import dilakukan di dalam fungsi supaya modul ini tidak circular dengan
    ``openrouter_provider`` (modul itu kini wrapper tipis di atas factory ini).
    Nilai yang dikembalikan TIDAK BOLEH dicetak, dilog, atau ditulis ke file.
    """
    from openrouter_provider import get_openrouter_surrogate as _openrouter_surrogate

    return _openrouter_surrogate()


def vault_key_name(provider: str) -> str:
    """Nama entri vault untuk kunci satu provider: ``byok:<provider>``."""
    return f"byok:{_canonical(provider)}"


def put_provider_key(user_id, provider: str, key: str, dsn: str | None = None) -> None:
    """Simpan kunci provider ke vault terenkripsi Puspa (per-user, HKDF).

    Memakai ``puspa.accounts.vault`` yang sudah ada (envelope encryption:
    master key env -> HKDF(user_id) -> Fernet; ciphertext saja di DB).
    Cara Nero menambah kunci nanti: lewat Secure Vault (bukan chat) —
    lihat ``demo/MATRIKS_BYOK.md``. Fungsi ini HANYA dipanggil dari kode
    produk / skrip setup yang sudah memegang kunci dari Secure Vault.
    """
    from puspa.accounts.vault import put as vault_put

    if not key or not str(key).strip():
        raise ValueError(f"kunci provider {provider!r} kosong; tidak disimpan")
    vault_put(user_id, vault_key_name(provider), str(key), dsn=dsn)


def get_provider_key(user_id, provider: str, dsn: str | None = None) -> str:
    """Ambil kunci provider dari vault. Raise KeyError bila belum ada."""
    from puspa.accounts.vault import get as vault_get

    return vault_get(user_id, vault_key_name(provider), dsn=dsn)


def resolve_key(provider: str, key: str | None, *, user_id=None, dsn: str | None = None) -> str | None:
    """Tentukan kunci untuk satu provider, urutan: argumen -> vault -> env.

    - OpenRouter: surrogate segar (bukan dari vault/env).
    - OAuth / lokal / Bedrock: boleh None (kunci bukan dari sini).
    - Lainnya: argumen ``key`` dulu; lalu vault ``byok:<provider>`` bila
      ``user_id`` diberi; lalu env ``<PROVIDER>_API_KEY`` dari registry
      nanobot (mis. ``OPENAI_API_KEY``). Bila tetap tidak ada, raise
      ``KeyError`` yang menyebut persis apa yang dibutuhkan (bukan kunci
      karangan).
    """
    provider_name = _canonical(provider)
    if provider_name == "openrouter":
        return key or get_openrouter_surrogate()
    if provider_name in KEY_OPTIONAL_PROVIDERS:
        return key  # boleh None
    if key:
        return key
    if user_id is not None:
        try:
            return get_provider_key(user_id, provider_name, dsn=dsn)
        except KeyError:
            pass
    env_key = _env_key_for(provider_name)
    if env_key:
        value = os.environ.get(env_key)
        if value:
            return value
    raise KeyError(
        f"kunci provider {provider_name!r} belum ada: isi argumen key=..., simpan di "
        f"vault sebagai {vault_key_name(provider_name)!r}, atau set env {env_key or '(tidak ada env key)'}; "
        "jangan mengarang kunci dan jangan minta kunci di chat — "
        "tambah lewat Secure Vault (lihat demo/MATRIKS_BYOK.md)."
    )


def _env_key_for(provider: str) -> str | None:
    """Nama env var kunci dari registry nanobot ('' -> None)."""
    try:
        from nanobot.providers.registry import find_by_name

        spec = find_by_name(provider)
        return spec.env_key or None if spec else None
    except Exception:
        return None


def _spec_for(provider: str):
    """Spec registry nanobot untuk provider, atau spec dinamis (custom)."""
    from nanobot.providers.registry import create_dynamic_spec, find_by_name

    if provider in CUSTOM_PROVIDERS:
        return create_dynamic_spec(provider)
    spec = find_by_name(provider)
    if spec is not None:
        return spec
    if provider == "openai_compatible":
        return find_by_name("custom")
    return None


# ---------------------------------------------------------------------------
# Factory utama
# ---------------------------------------------------------------------------

def build_byok_provider(provider: str | None, model: str, key: str | None = None,
                        base_url: str | None = None, **kwargs: Any):
    """Bangun provider nanobot (``LLMProvider``) untuk provider + model apa pun.

    Args:
        provider: nama provider (``"openrouter"``, ``"openai"``, ``"anthropic"``,
            ``"gemini"``, ``"azure_openai"``, ``"bedrock"``, ``"xai"``,
            ``"github_copilot"``, ``"openai_codex"``, ``"ollama"``,
            ``"openai_compatible"``, atau nama registry nanobot lain seperti
            ``"groq"``/``"deepseek"``/``"mistral"``/``"together"``). Boleh None
            bila model membawa prefix yang tidak ambigu (lihat
            :func:`resolve_provider_name`).
        model: id model (apa adanya; prefix ``provider/`` dipertahankan
            kecuali spec nanobot yang memintanya dipotong).
        key: kunci API. Untuk OpenRouter boleh None (surrogate segar).
            Untuk provider lain yang butuh kunci dan tidak diberi: dicari
            di vault (bila ``user_id`` diberi) lalu env; bila tetap tidak
            ada -> ``KeyError`` jujur (bukan kunci karangan).
        base_url: override base URL / endpoint. WAJIB untuk
            ``openai_compatible`` (bebas) dan ``azure_openai`` (endpoint
            Azure ``https://<resource>.openai.azure.com``).
        kwargs: ``user_id``/``dsn`` (pencarian vault), ``region``/``profile``
            (Bedrock), ``client`` (Bedrock: client boto3/mock yang di-inject
            — dipakai mock harness supaya Bedrock terbukti tanpa AWS).

    Returns:
        ``LLMProvider`` nanobot yang siap dipakai ``AgentLoop``.

    Raises:
        ValueError: provider tak dikenal / base_url wajib tapi tidak diisi.
        KeyError: kunci provider belum ada (lihat :func:`resolve_key`).
        RuntimeError: dependensi provider tidak terpasang (mis. boto3
            untuk Bedrock tanpa client inject).
    """
    if not model or not str(model).strip():
        raise ValueError("model tidak boleh kosong")
    provider_name = resolve_provider_name(provider, model)
    user_id = kwargs.get("user_id")
    dsn = kwargs.get("dsn")

    # --- provider OAuth: konstruksi tanpa kunci, gagal di login saat chat ---
    if provider_name == "github_copilot":
        from nanobot.providers.github_copilot_provider import GitHubCopilotProvider

        return GitHubCopilotProvider(default_model=model, provider_name="github_copilot")
    if provider_name == "openai_codex":
        from nanobot.providers.openai_codex_provider import OpenAICodexProvider

        return OpenAICodexProvider(default_model=model, provider_name="openai_codex")
    if provider_name == "xai":
        from nanobot.providers.xai_grok_provider import XAIGrokProvider

        return XAIGrokProvider(default_model=model, provider_name="xai_grok")

    # --- provider native non-OAuth ---
    if provider_name == "anthropic":
        from nanobot.providers.anthropic_provider import AnthropicProvider

        key = resolve_key(provider_name, key, user_id=user_id, dsn=dsn)
        return AnthropicProvider(
            api_key=key,
            api_base=base_url,
            default_model=model,
            spec=_spec_for(provider_name),
            provider_name="anthropic",
        )
    if provider_name == "azure_openai":
        from nanobot.providers.azure_openai_provider import AzureOpenAIProvider

        endpoint = (base_url or "").strip()
        if not endpoint:
            raise ValueError(
                "azure_openai butuh base_url = endpoint Azure, mis. "
                "'https://<resource>.openai.azure.com'; model = nama deployment."
            )
        key = resolve_key(provider_name, key, user_id=user_id, dsn=dsn)
        return AzureOpenAIProvider(
            api_key=key, api_base=endpoint, default_model=model,
            provider_name="azure_openai",
        )
    if provider_name == "bedrock":
        from nanobot.providers.bedrock_provider import BedrockProvider

        return BedrockProvider(
            api_key=key,
            api_base=base_url,
            default_model=model,
            region=kwargs.get("region"),
            profile=kwargs.get("profile"),
            client=kwargs.get("client"),
            provider_name="bedrock",
        )

    # --- OpenAI-compatible (jalur umum: registry nanobot / custom) ---
    key = resolve_key(provider_name, key, user_id=user_id, dsn=dsn)
    spec = _spec_for(provider_name)
    if spec is None and provider_name not in ("openai_compatible",):
        raise ValueError(
            f"provider {provider_name!r} tidak dikenal (tidak ada di registry nanobot "
            "dan bukan provider custom terdaftar). Daftarkan dulu lewat "
            "register_provider(name, base_url), atau pakai 'openai_compatible' "
            "dengan base_url bebas. Didukung: "
            + ", ".join(SUPPORTED_PROVIDERS)
        )
    base = (base_url or "").strip() or None
    if base is None:
        if provider_name == "openai_compatible":
            raise ValueError(
                "openai_compatible butuh base_url bebas, mis. "
                "'https://api.together.xyz/v1' atau 'https://api.x.ai/v1'."
            )
        if provider_name in CUSTOM_PROVIDERS:
            base = CUSTOM_PROVIDERS[provider_name]
        elif spec is not None and spec.default_api_base:
            base = spec.default_api_base
    if base is None:
        if provider_name == "openai":
            base = "https://api.openai.com/v1"  # eksplisit (sama dengan default SDK)
        elif provider_name in ("ollama",):
            base = "http://localhost:11434/v1"
        else:
            raise ValueError(
                f"provider {provider_name!r} butuh base_url (tidak ada default di registry); "
                "isi base_url=... atau pakai provider yang punya default."
            )
    if provider_name == "openai_compatible" and not base_url:
        raise ValueError(
            "openai_compatible butuh base_url bebas, mis. "
            "'https://api.together.xyz/v1' atau 'https://api.x.ai/v1'."
        )
    if provider_name == "ollama" and not key:
        key = "ollama"  # server lokal tidak memeriksa kunci

    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    return OpenAICompatProvider(
        api_key=key,
        api_base=base,
        default_model=model,
        spec=spec,
        provider_name=provider_name,
    )
