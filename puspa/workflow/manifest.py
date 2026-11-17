"""Manifes izin turunan alur (§2.12 revisi 7).

Dari blok yang dipakai, KODE (bukan model) menghitung izin minimal
alur: konektor, scope, domain, batas belanja. User melihat dan
menyetujui manifes itu saat mengaktifkan. Token alur adalah himpunan
bagian dari izin user (§2.6) — pemeriksaan himpunan bagian dilakukan
di sini, oleh kode.
"""

from __future__ import annotations

from puspa.workflow.blocks import BLOK, BlockError


def hitung_manifest(step: list) -> dict:
    """Hitung izin minimal dari semua blok di langkah (termasuk blok di
    dalam kondisi / untuk_tiap). Kembalikan manifes:
    {konektor, scope, domain, batas_belanja, blok} — semuanya terurut.
    """
    connector: set[str] = set()
    scope: set[str] = set()
    domain: set[str] = set()
    batas_belanja = 0
    block: list[str] = []

    def _kunjungi(items):
        nonlocal batas_belanja
        for l in items:
            if not isinstance(l, dict):
                continue
            name = l.get("block")
            if name not in BLOK:
                raise BlockError(f"blok {name!r} tidak ada di pustaka")
            block.append(name)
            permission = BLOK[name]["permission"]
            connector.update(permission["connectors"])
            scope.update(permission["scope"])
            domain.update(permission["domain"])
            masukan = l.get("masukan", {}) or {}
            if name == "prepare_payment":
                amount = masukan.get("amount")
                if isinstance(amount, (int, float)) and amount > 0:
                    batas_belanja = max(batas_belanja, int(amount))
            if name == "condition":
                _kunjungi(l.get("maka", []))
                _kunjungi(l.get("kalau_not", []))
            elif name == "for_each":
                _kunjungi(l.get("step_dalam", []))

    _kunjungi(step)
    return {
        "connectors": sorted(connector),
        "scope": sorted(scope),
        "domain": sorted(domain),
        "batas_belanja": batas_belanja,
        "block": sorted(set(block)),
    }


def subset_permission(manifest: dict, permission_user: dict) -> tuple[bool, list[str]]:
    """Apakah manifes alur himpunan bagian dari izin user?
    `izin_user`: {konektor, scope, domain, batas_belanja}. Kembalikan
    (lolos, daftar kekurangan) — kekurangan kosong bila lolos."""
    less: list[str] = []
    for k in ("connectors", "scope", "domain"):
        punya = set(permission_user.get(k, []) or [])
        for item in manifest.get(k, []):
            if item not in punya:
                less.append(f"{k} {item!r} tidak diberikan user")
    if int(manifest.get("batas_belanja", 0)) > int(permission_user.get("batas_belanja", 0)):
        less.append(
            f"batas belanja Rp{manifest['batas_belanja']:,} melebihi "
            f"batas user Rp{permission_user.get('batas_belanja', 0):,}".replace(",", "."))
    return (not less, less)


def format_manifest(manifest: dict) -> str:
    """Tampilan manifes untuk user saat mengaktifkan (bahasa biasa)."""
    rows = ["Izin yang diminta alur ini:"]
    rows.append(f"- Konektor: {', '.join(manifest['connectors']) or 'tidak ada'}")
    rows.append(f"- Scope: {', '.join(manifest['scope']) or 'tidak ada'}")
    rows.append(f"- Domain dibaca: {', '.join(manifest['domain']) or 'tidak ada'}")
    rows.append(f"- Batas belanja: Rp{manifest['batas_belanja']:,}".replace(",", "."))
    rows.append(f"- Blok dipakai: {', '.join(manifest['block'])}")
    return "\n".join(rows)
