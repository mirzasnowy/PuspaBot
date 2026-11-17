"""Manifes izin turunan alur (§2.12 revisi 7).

Dari blok yang dipakai, KODE (bukan model) menghitung izin minimal
alur: konektor, scope, domain, batas belanja. User melihat dan
menyetujui manifes itu saat mengaktifkan. Token alur adalah himpunan
bagian dari izin user (§2.6) — pemeriksaan himpunan bagian dilakukan
di sini, oleh kode.
"""

from __future__ import annotations

from puspa.workflow.blocks import BLOK, BlockError


def hitung_manifes(langkah: list) -> dict:
    """Hitung izin minimal dari semua blok di langkah (termasuk blok di
    dalam kondisi / untuk_tiap). Kembalikan manifes:
    {konektor, scope, domain, batas_belanja, blok} — semuanya terurut.
    """
    konektor: set[str] = set()
    scope: set[str] = set()
    domain: set[str] = set()
    batas_belanja = 0
    blok: list[str] = []

    def _kunjungi(daftar):
        nonlocal batas_belanja
        for l in daftar:
            if not isinstance(l, dict):
                continue
            nama = l.get("blok")
            if nama not in BLOK:
                raise BlockError(f"blok {nama!r} tidak ada di pustaka")
            blok.append(nama)
            izin = BLOK[nama]["izin"]
            konektor.update(izin["konektor"])
            scope.update(izin["scope"])
            domain.update(izin["domain"])
            masukan = l.get("masukan", {}) or {}
            if nama == "siapkan_bayar":
                jumlah = masukan.get("jumlah")
                if isinstance(jumlah, (int, float)) and jumlah > 0:
                    batas_belanja = max(batas_belanja, int(jumlah))
            if nama == "kondisi":
                _kunjungi(l.get("maka", []))
                _kunjungi(l.get("kalau_tidak", []))
            elif nama == "untuk_tiap":
                _kunjungi(l.get("langkah_dalam", []))

    _kunjungi(langkah)
    return {
        "konektor": sorted(konektor),
        "scope": sorted(scope),
        "domain": sorted(domain),
        "batas_belanja": batas_belanja,
        "blok": sorted(set(blok)),
    }


def subset_izin(manifes: dict, izin_user: dict) -> tuple[bool, list[str]]:
    """Apakah manifes alur himpunan bagian dari izin user?
    `izin_user`: {konektor, scope, domain, batas_belanja}. Kembalikan
    (lolos, daftar kekurangan) — kekurangan kosong bila lolos."""
    kurang: list[str] = []
    for k in ("konektor", "scope", "domain"):
        punya = set(izin_user.get(k, []) or [])
        for item in manifes.get(k, []):
            if item not in punya:
                kurang.append(f"{k} {item!r} tidak diberikan user")
    if int(manifes.get("batas_belanja", 0)) > int(izin_user.get("batas_belanja", 0)):
        kurang.append(
            f"batas belanja Rp{manifes['batas_belanja']:,} melebihi "
            f"batas user Rp{izin_user.get('batas_belanja', 0):,}".replace(",", "."))
    return (not kurang, kurang)


def format_manifes(manifes: dict) -> str:
    """Tampilan manifes untuk user saat mengaktifkan (bahasa biasa)."""
    baris = ["Izin yang diminta alur ini:"]
    baris.append(f"- Konektor: {', '.join(manifes['konektor']) or 'tidak ada'}")
    baris.append(f"- Scope: {', '.join(manifes['scope']) or 'tidak ada'}")
    baris.append(f"- Domain dibaca: {', '.join(manifes['domain']) or 'tidak ada'}")
    baris.append(f"- Batas belanja: Rp{manifes['batas_belanja']:,}".replace(",", "."))
    baris.append(f"- Blok dipakai: {', '.join(manifes['blok'])}")
    return "\n".join(baris)
