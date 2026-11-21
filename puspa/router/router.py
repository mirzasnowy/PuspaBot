"""Router model bertingkat (§2.4 revisi 7).

Dibangun DI ATAS factory generik Fase 0 (`demo/byok_provider.py`,
`build_byok_provider`) — bukan tulis ulang dari nol. Router ini
hanya menambah LAPISAN PEMILIHAN di atas factory itu: peran ->
(provider, model) termurah yang memenuhi syarat peran.

Peran:
- perencana + penyaji: model menengah termurah yang lolos 10 tugas
  acuan (`lolos_acuan=True` di katalog).
- pembaca + pencari: model termurah (apa pun keluarganya).
- pemeriksa: model dari keluarga BERBEDA dari pembuatnya bila router
  punya (mengurangi kesalahan yang berkorelasi, keputusan 14).
- vision: model vision termurah yang bisa membaca label/kemasan;
  kalau tidak tersedia, kembalikan None dan agen minta user menulis
  nama barang (gagal terlihat).

Tarif di katalog adalah KONFIGURASI (Rp per 1.000 token, dari harga
publik OpenRouter per Nov 2026, dibulatkan) — bukan angka yang
dijamin; kalibrasi ulang dari Buku Kerja di `kalibrasi_tarif`.
Keluarga model dipakai untuk aturan "berbeda keluarga".

Fallback dan kuota (pola 9Router, ditulis ulang sebagai kode Puspa):
`puspa/router/fallback.py` membangun rantai subscription -> cheap ->
free di atas katalog ini dan mengeksekusi satu request lewat rantai
itu, sementara `puspa/router/quota.py` melacak pemakaian dan hitung
mundur reset per provider. Dua fungsi di bawah ini adalah satu-satunya
wiring di modul ini: keduanya mendelegasikan ke modul tersebut dengan
lazy import supaya tidak ada circular import.

OAuth refresh dan terjemahan format (pola 9Router, ditulis ulang):
`puspa/router/oauth.py` merefresh token akses OAuth sebelum
kedaluwarsa untuk provider langganan yang dikenal factory BYOK
(`xai`, `github_copilot`, `openai_codex`), dan
`puspa/router/translate.py` menerjemahkan request/response antara
format OpenAI chat-completions dan format Anthropic Messages —
dua format yang ditangani jalur BYOK hari ini. Wiring di bawah
hanya mendelegasikan ke kedua modul itu; tidak ada provider live
yang dipanggil dari sini.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

# (provider, model, keluarga, rp_per_1k_input, rp_per_1k_output, vision, lolos_acuan)
CATALOG = (
    ("openrouter", "deepseek/deepseek-chat-v3.1", "deepseek", 9, 27, False, True),
    ("openrouter", "qwen/qwen3-32b", "qwen", 2, 6, False, True),
    ("openrouter", "google/gemma-3-4b-it", "gemma", 1, 3, True, False),
    ("openrouter", "meta-llama/llama-3.1-8b-instruct", "llama", 1, 2, False, False),
    ("openrouter", "z-ai/glm-4.6", "glm", 8, 24, True, True),
    ("openrouter", "openai/gpt-4o-mini", "openai", 10, 40, True, True),
    ("openrouter", "anthropic/claude-haiku-4.5", "anthropic", 16, 80, True, True),
)


@dataclass(frozen=True)
class ChoiceModel:
    provider: str
    model: str
    family: str
    rp_per_1k_input: int
    rp_per_1k_output: int
    vision: bool


def _choice(rows) -> ChoiceModel:
    provider, model, family, rp_in, rp_out, vision, _passed = rows
    return ChoiceModel(provider, model, family, rp_in, rp_out, vision)


def _cheapest(candidates) -> ChoiceModel:
    return min(
        (_choice(b) for b in candidates), key=lambda p: p.rp_per_1k_input + p.rp_per_1k_output
    )


def choose(role: str, *, creator_family: str | None = None) -> ChoiceModel | None:
    """Pilih (provider, model) termurah untuk satu peran.

    Peran: 'perencana' | 'penyaji' | 'pembaca' | 'pencari' |
    'pemeriksa' | 'vision'. Kembalikan None hanya untuk vision bila
    tidak ada model vision di katalog.
    """
    if role in ("perencana", "penyaji"):
        candidates = [b for b in CATALOG if b[6]]  # lolos 10 tugas acuan
        return _cheapest(candidates)
    if role in ("pembaca", "pencari"):
        return _cheapest(CATALOG)
    if role == "pemeriksa":
        candidates = (
            [b for b in CATALOG if b[2] != creator_family] if creator_family else list(CATALOG)
        )
        if not candidates:  # hanya satu keluarga di katalog: pakai termurah
            candidates = list(CATALOG)
        return _cheapest(candidates)
    if role == "vision":
        candidates = [b for b in CATALOG if b[5]]
        return _cheapest(candidates) if candidates else None
    raise ValueError(f"peran tidak dikenal: {role!r}")


def compute_cost(choice: ChoiceModel, input_tokens: int, output_tokens: int) -> int:
    """Biaya Rupiah (dibulatkan ke bawah) untuk satu panggilan."""
    return (input_tokens * choice.rp_per_1k_input + output_tokens * choice.rp_per_1k_output) // 1000


def build_provider(
    role: str,
    *,
    creator_family: str | None = None,
    user_id: str | None = None,
    dsn: str | None = None,
):
    """Bangun provider nanobot untuk peran lewat factory BYOK Fase 0.

    Kunci diambil dari vault per provider (`put_provider_key` Fase 0)
    atau argumen env; bila tidak ada kunci, factory melempar KeyError
    yang jujur (bukan kunci karangan). Vision yang tidak tersedia
    melempar ValueError supaya pemanggil meminta nama barang ke user.
    """
    choice = choose(role, creator_family=creator_family)
    if choice is None:
        raise ValueError("tidak ada model vision di router; minta user menulis nama barang")
    import sys
    from pathlib import Path

    demo_dir = Path(__file__).resolve().parent.parent.parent.parent / "demo"
    if str(demo_dir) not in sys.path:
        sys.path.insert(0, str(demo_dir))
    from demo.byok_provider import build_byok_provider

    return build_byok_provider(choice.provider, choice.model, user_id=user_id, dsn=dsn)


def default_chain(role: str, *, creator_family: str | None = None):
    """Rantai fallback subscription -> cheap -> free untuk satu peran.

    Delegasi ke `puspa.router.fallback.build_chain` (pola 9Router,
    ditulis ulang). Kembalikan list `ChainEntry`, urutan termahal dulu.
    """
    from .fallback import build_chain

    return build_chain(role, creator_family=creator_family)


def choose_with_quota(role: str, quota_store, *, creator_family: str | None = None):
    """Pilih model untuk satu peran dengan memperhatikan kuota terlacak.

    Delegasi ke `puspa.router.fallback.choose_with_quota`: tier pertama
    yang kuotanya belum habis. Kembalikan `ChoiceModel` atau None bila
    semua tier habis. `quota_store` adalah `puspa.router.quota.QuotaStore`.
    """
    from .fallback import choose_with_quota as _choose_with_quota

    entry = _choose_with_quota(role, quota_store, creator_family=creator_family)
    return entry.choice if entry is not None else None


# ---------------------------------------------------------------------------
# OAuth refresh + format translation (delegasi ke oauth.py / translate.py)
# ---------------------------------------------------------------------------

#: The one shared refresher for the router path. In-memory by design
#: (see `puspa/router/oauth.py`): tokens do not survive a restart.
_oauth_refresher = None


def oauth_refresher():
    """The router's shared `TokenRefresher` (created on first use)."""
    global _oauth_refresher
    if _oauth_refresher is None:
        from .oauth import TokenRefresher

        _oauth_refresher = TokenRefresher()
    return _oauth_refresher


def register_known_oauth_providers(refresher=None):
    """Register the three OAuth providers the BYOK factory already knows.

    All three are SIMULATED registrations: no live credential and no
    live call is made here. xAI carries the token endpoint and public
    client id read from nanobot's own source
    (`nanobot/nanobot/providers/xai_oauth.py`: issuer
    `https://auth.x.ai`, token endpoint
    `https://auth.x.ai/oauth2/token`); GitHub Copilot carries the
    public GitHub OAuth token endpoint but no client id; Codex
    carries neither, because its token endpoint was not verified
    from nanobot's source. A refresh against any of them raises a
    clear error naming what is missing until a real registration
    (client id, secret from env or vault, token URL) is supplied.
    Returns the list of provider names registered.
    """
    refresher = refresher if refresher is not None else oauth_refresher()
    refresher.register_simulated(
        "xai",
        token_url="https://auth.x.ai/oauth2/token",
        client_id="b1a00492-073a-47ea-816f-4c329264a828",
        scope=("openid", "profile", "email", "offline_access"),
        notes="public client id from nanobot source; interactive login "
        "via Puspa (`puspa provider login xai`); "
        "no client secret is used by this public client",
    )
    refresher.register_simulated(
        "github_copilot",
        token_url="https://github.com/login/oauth/access_token",
        client_id=None,
        notes="no client id wired yet; interactive login via "
        "Puspa (`puspa provider login github_copilot`)",
    )
    refresher.register_simulated(
        "openai_codex",
        token_url=None,
        client_id=None,
        notes="token endpoint not verified from nanobot source; "
        "interactive login via Puspa "
        "(`puspa provider login openai_codex`) is not wired yet",
    )
    return ["xai", "github_copilot", "openai_codex"]


def get_valid_oauth_token(provider: str, http_client=None) -> str:
    """Return a usable OAuth access token for ``provider``.

    Delegates to the shared `TokenRefresher`: refreshes proactively
    when the stored token expires inside the leeway, raises
    `TokenRefreshError` when no usable token exists. The caller
    sends the returned value as the Bearer token.

    E-18: a failed refresh is recorded in the observability
    metrics (per provider) before the error propagates.
    """
    from puspa.observability import metrics as observability_metrics

    from .oauth import TokenRefreshError

    try:
        return oauth_refresher().get_valid_token(provider, http_client)
    except TokenRefreshError:
        with contextlib.suppress(Exception):
            observability_metrics.get_metrics().record_credential_refresh_failure(provider)
        raise


def translate_chat_request(payload: dict, *, source: str, target: str):
    """Translate a chat request between OpenAI and Anthropic formats.

    Delegates to `puspa.router.translate.translate_request`.
    ``source`` / ``target`` are ``"openai"`` or ``"anthropic"``.
    """
    from .translate import translate_request

    return translate_request(payload, source=source, target=target)


def translate_chat_response(payload: dict, *, source: str, target: str):
    """Translate a chat response between OpenAI and Anthropic formats.

    Delegates to `puspa.router.translate.translate_response`.
    """
    from .translate import translate_response

    return translate_response(payload, source=source, target=target)
