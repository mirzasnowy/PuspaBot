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
"""

from __future__ import annotations

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
    return min((_choice(b) for b in candidates),
               key=lambda p: p.rp_per_1k_input + p.rp_per_1k_output)


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
        candidates = [b for b in CATALOG if b[2] != creator_family] if creator_family else list(CATALOG)
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


def build_provider(role: str, *, creator_family: str | None = None,
                   user_id: str | None = None, dsn: str | None = None):
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
    from demo.byok_provider import build_byok_provider  # noqa: E402

    return build_byok_provider(choice.provider, choice.model,
                               user_id=user_id, dsn=dsn)


def default_chain(role: str, *, creator_family: str | None = None):
    """Rantai fallback subscription -> cheap -> free untuk satu peran.

    Delegasi ke `puspa.router.fallback.build_chain` (pola 9Router,
    ditulis ulang). Kembalikan list `ChainEntry`, urutan termahal dulu.
    """
    from .fallback import build_chain

    return build_chain(role, creator_family=creator_family)


def choose_with_quota(role: str, quota_store, *,
                      creator_family: str | None = None):
    """Pilih model untuk satu peran dengan memperhatikan kuota terlacak.

    Delegasi ke `puspa.router.fallback.choose_with_quota`: tier pertama
    yang kuotanya belum habis. Kembalikan `ChoiceModel` atau None bila
    semua tier habis. `quota_store` adalah `puspa.router.quota.QuotaStore`.
    """
    from .fallback import choose_with_quota as _choose_with_quota

    entry = _choose_with_quota(role, quota_store,
                               creator_family=creator_family)
    return entry.choice if entry is not None else None
