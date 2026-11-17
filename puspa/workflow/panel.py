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


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def card_step_html(card: dict) -> str:
    """Satu kartu langkah: judul, blok, kelas izin, lencana aksi keluar."""
    lencana = ""
    if card.get("action_logout"):
        lencana = ' <span class="lencana-aksi">butuh persetujuan</span>'
    return (
        f'<div class="kartu-langkah" data-blok="{_esc(card.get("block", ""))}">'
        f'<h4>{_esc(card.get("title", ""))}</h4>'
        f'<p>{_esc(card.get("deskripsi", ""))}</p>'
        f'<p class="meta">Blok: <code>{_esc(card.get("block", ""))}</code>'
        f' · Kelas: {_esc(card.get("permission_class", ""))}{lencana}</p>'
        f'</div>'
    )


def panel_workflow_html(*, name: str, trigger: dict, card: list[dict],
                    manifest_teks: str = "", message: str = "") -> str:
    """Panel draf alur: nama + pemicu + kartu langkah + manifes izin."""
    card_html = "\n".join(card_step_html(k) for k in card)
    trigger_teks = _esc(trigger.get("type", "manual"))
    message_html = f'<p class="pesan">{_esc(message)}</p>' if message else ""
    manifest_html = (f'<pre class="manifes">{_esc(manifest_teks)}</pre>'
                    if manifest_teks else "")
    return (
        f'<section class="panel-alur"><h3>{_esc(name)}</h3>'
        f'<p>Pemicu: {trigger_teks}</p>{message_html}'
        f'<div class="daftar-langkah">{card_html}</div>'
        f'{manifest_html}</section>'
    )


def result_jalan_html(result: dict) -> str:
    """Hasil jalan / uji kering: tiap langkah + status + biaya + alasan."""
    rows = []
    for l in result.get("step_results", []):
        status = "successful" if l.get("success") else (
            "pending" if l.get("success") is None else "failed")
        if l.get("simulasi"):
            status = "simulasi (uji kering)"
        rows.append(f'<li>{_esc(l.get("block", "?"))} — {status}</li>')
    reason = result.get("reason") or ""
    return (
        f'<section class="hasil-jalan"><p>Status: {_esc(result.get("status", ""))}</p>'
        f'<ul>{"".join(rows)}</ul>'
        f'<p>Biaya: Rp{int(result.get("cost_rp", 0)):,}</p>'.replace(",", ".")
        + (f'<p>Alasan: {_esc(reason)}</p>' if reason else "")
        + '</section>'
    )
