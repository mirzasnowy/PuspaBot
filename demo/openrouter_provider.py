"""Provider OpenRouter (BYOK) untuk demo Puspa — wrapper tipis.

Sejak generalisasi BYOK, implementasi tinggal di ``demo/byok_provider.py``
(:func:`build_byok_provider`); modul ini dipertahankan demi kompatibilitas
agar kode lama yang mengimpor ``build_openrouter_provider`` /
``get_openrouter_surrogate`` tetap jalan. Demo utama kini memakai factory generik
langsung (``demo/byok_demo.py`` -> ``build_byok_provider("openrouter", ...)``).

Kunci API tidak pernah ditulis di file ini, tidak pernah di-hardcode, dan
tidak pernah dicetak/dilog. Setiap panggilan mengambil surrogate segar
(``hsurr:...``) dari ``dynamic_credential_entry("custom.openrouter")``;
runtime menukar surrogate itu dengan kunci asli hanya pada request ke host
terdaftar ``openrouter.ai``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_CONNECTION = "custom.openrouter"
_DYNAMIC_CREDENTIALS_DIR = "/opt/muse/skills/custom-connector-setup/bin"


def _ensure_nanobot_on_path() -> None:
    """Pastikan paket ``nanobot`` (clone lokal di ``fase-0/nanobot``) bisa diimpor."""
    try:
        import nanobot  # noqa: F401
    except ImportError:
        candidate = Path(__file__).resolve().parent.parent / "nanobot"
        if (candidate / "nanobot" / "__init__.py").exists():
            sys.path.insert(0, str(candidate))
        import nanobot  # noqa: F401  (raise ImportError asli bila tetap gagal)


def get_openrouter_surrogate() -> str:
    """Ambil surrogate segar (``hsurr:...``) untuk koneksi OpenRouter.

    Surrogate diambil fresh dari ``dynamic_credential_entry`` setiap kali
    dipanggil. Nilai yang dikembalikan TIDAK BOLEH dicetak, dilog, atau
    ditulis ke file oleh pemanggil.
    """
    if _DYNAMIC_CREDENTIALS_DIR not in sys.path:
        sys.path.insert(0, _DYNAMIC_CREDENTIALS_DIR)
    from dynamic_credentials import dynamic_credential_entry

    entry = dynamic_credential_entry(_CONNECTION)
    surrogate = str(entry["surrogate"]).strip()
    if not surrogate.startswith("hsurr:"):
        raise RuntimeError("OpenRouter surrogate tidak valid (bukan hsurr:)")
    return surrogate


def build_openrouter_provider(model: str) -> Any:
    """Bangun provider nanobot (``LLMProvider``) untuk satu model OpenRouter.

    Wrapper tipis di atas :func:`demo.byok_provider.build_byok_provider`
    (``provider="openrouter"``). Argumen dan perilaku sama seperti dulu:
    ``OpenAICompatProvider`` dengan spec registry ``openrouter``,
    ``api_base`` OpenRouter, dan surrogate segar sebagai ``api_key``.
    """
    _ensure_nanobot_on_path()
    # Import lokal: byok_provider memakai get_openrouter_surrogate dari modul ini,
    # jadi impor di tingkat modul akan circular.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from byok_provider import build_byok_provider

    return build_byok_provider("openrouter", model)
