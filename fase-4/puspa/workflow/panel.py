"""Panel Alur/Modul di PWA (§2.1 layar Modul, §2.12-2.13 revisi 7).

Render HTML fragment (tanpa JavaScript) untuk:
- kartu langkah alur (kanvas penyusun — adaptasi pola builder visual
  Langflow ke kartu langkah di panel Alur, BUKAN klon UI Langflow);
- hasil jalan alur / uji kering;
- pratinjau modul (lihat puspa/modules/views.py).

Fragment ini dipasang ke template layar Modul Fase 2 oleh integrasi
PWA (di luar paket Fase 4 — paket Fase 2 tidak diubah di fase ini,
lihat STATUS_FASE4.md). Fungsi di sini murni render dari data.
"""

from __future__ import annotations

import html


def _esc(teks) -> str:
    return html.escape(str(teks), quote=True)


def kartu_langkah_html(kartu: dict) -> str:
    """Satu kartu langkah: judul, blok, kelas izin, lencana aksi keluar."""
    lencana = ""
    if kartu.get("aksi_keluar"):
        lencana = ' <span class="lencana-aksi">butuh persetujuan</span>'
    return (
        f'<div class="kartu-langkah" data-blok="{_esc(kartu.get("blok", ""))}">'
        f'<h4>{_esc(kartu.get("judul", ""))}</h4>'
        f'<p>{_esc(kartu.get("deskripsi", ""))}</p>'
        f'<p class="meta">Blok: <code>{_esc(kartu.get("blok", ""))}</code>'
        f' · Kelas: {_esc(kartu.get("kelas_izin", ""))}{lencana}</p>'
        f'</div>'
    )


def panel_alur_html(*, nama: str, pemicu: dict, kartu: list[dict],
                    manifes_teks: str = "", pesan: str = "") -> str:
    """Panel draf alur: nama + pemicu + kartu langkah + manifes izin."""
    kartu_html = "\n".join(kartu_langkah_html(k) for k in kartu)
    pemicu_teks = _esc(pemicu.get("jenis", "manual"))
    pesan_html = f'<p class="pesan">{_esc(pesan)}</p>' if pesan else ""
    manifes_html = (f'<pre class="manifes">{_esc(manifes_teks)}</pre>'
                    if manifes_teks else "")
    return (
        f'<section class="panel-alur"><h3>{_esc(nama)}</h3>'
        f'<p>Pemicu: {pemicu_teks}</p>{pesan_html}'
        f'<div class="daftar-langkah">{kartu_html}</div>'
        f'{manifes_html}</section>'
    )


def hasil_jalan_html(hasil: dict) -> str:
    """Hasil jalan / uji kering: tiap langkah + status + biaya + alasan."""
    baris = []
    for l in hasil.get("langkah_hasil", []):
        status = "berhasil" if l.get("sukses") else (
            "menunggu" if l.get("sukses") is None else "gagal")
        if l.get("simulasi"):
            status = "simulasi (uji kering)"
        baris.append(f'<li>{_esc(l.get("blok", "?"))} — {status}</li>')
    alasan = hasil.get("alasan") or ""
    return (
        f'<section class="hasil-jalan"><p>Status: {_esc(hasil.get("status", ""))}</p>'
        f'<ul>{"".join(baris)}</ul>'
        f'<p>Biaya: Rp{int(hasil.get("biaya_rp", 0)):,}</p>'.replace(",", ".")
        + (f'<p>Alasan: {_esc(alasan)}</p>' if alasan else "")
        + '</section>'
    )
