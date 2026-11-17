"""Render tampilan modul (§2.13 revisi 7).

Komponen tetap yang dirender PWA: kartu, tabel, daftar, form, angka
ringkas, grafik garis dari tabel modul, tombol aksi. Render di sini
menghasilkan HTML fragment TANPA JavaScript — data dimasukkan sebagai
teks yang di-escape (tidak ada HTML dari bot atau dari data user yang
lolos tanpa escape).

Grafik garis: SVG polyline dari satu kolom angka tabel modul.
"""

from __future__ import annotations

import html

KOMPONEN = ("card", "table", "items", "form", "number", "grafik_garis",
            "tombol_action")


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _rupiah(value) -> str:
    try:
        return f"Rp{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return _esc(value)


def render_grafik_garis(title: str, value: list) -> str:
    """Satu grafik garis (SVG) dari daftar angka. Tanpa JavaScript."""
    if not value:
        return f'<div class="grafik"><h4>{_esc(title)}</h4><p>Belum ada data.</p></div>'
    number = [float(v) for v in value]
    atas, bawah = max(number), min(number)
    rentang = (atas - bawah) or 1.0
    lebar, high = 300, 120
    titik = []
    for i, v in enumerate(number):
        x = 10 + (i * (lebar - 20) / max(len(number) - 1, 1))
        y = 10 + (1 - (v - bawah) / rentang) * (high - 20)
        titik.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(titik)
    return (
        f'<div class="grafik"><h4>{_esc(title)}</h4>'
        f'<svg width="{lebar}" height="{high}" viewBox="0 0 {lebar} {high}" '
        f'role="img" aria-label="grafik garis {_esc(title)}">'
        f'<polyline points="{polyline}" fill="none" stroke="#2563eb" '
        f'stroke-width="2"/></svg>'
        f'<p>{_esc(bawah)} – {_esc(atas)}</p></div>'
    )


def render_tampilan(tampilan: dict, data: dict) -> str:
    """Render satu tampilan. `data`: {nama_tabel: [baris, ...]} — data
    contoh (pratinjau) atau data nyata modul."""
    event_type = tampilan.get("type")
    title = tampilan.get("title", "")
    if event_type not in KOMPONEN:
        raise ValueError(f"komponen {event_type!r} tidak diizinkan")
    if event_type == "card":
        text = tampilan.get("text", "")
        return (f'<div class="kartu"><h4>{_esc(title)}</h4>'
                f'<p>{_esc(text)}</p></div>')
    if event_type == "number":
        table = tampilan.get("table", "")
        kolom = tampilan.get("kolom", "")
        rows = data.get(table, [])
        total = sum(float(b.get(kolom, 0) or 0) for b in rows
                    if isinstance(b.get(kolom), (int, float)))
        label = tampilan.get("format", "")
        value = _rupiah(total) if label == "Rupiah" else f"{total:,.0f}".replace(",", ".")
        return (f'<div class="angka"><h4>{_esc(title)}</h4>'
                f'<p class="nilai">{value}</p></div>')
    if event_type == "table":
        table = tampilan.get("table", "")
        kolom = tampilan.get("kolom", []) or []
        rows = data.get(table, [])
        kepala = "".join(f"<th>{_esc(k)}</th>" for k in kolom)
        content = ""
        for b in rows:
            sel = "".join(f"<td>{_esc(b.get(k, ''))}</td>" for k in kolom)
            content += f"<tr>{sel}</tr>"
        return (f'<div class="tabel"><h4>{_esc(title)}</h4>'
                f'<table><thead><tr>{kepala}</tr></thead>'
                f'<tbody>{content}</tbody></table></div>')
    if event_type == "items":
        table = tampilan.get("table", "")
        kolom = tampilan.get("kolom", "")
        rows = data.get(table, [])
        item = "".join(f"<li>{_esc(b.get(kolom, ''))}</li>" for b in rows)
        return f'<div class="daftar"><h4>{_esc(title)}</h4><ul>{item}</ul></div>'
    if event_type == "form":
        kolom = tampilan.get("kolom", []) or []
        field = ""
        for k in kolom:
            field += (f'<label>{_esc(k)} '
                      f'<input name="{_esc(k)}" type="text"></label>')
        return (f'<div class="form"><h4>{_esc(title)}</h4>'
                f'<form>{field}<button type="submit">Simpan</button></form></div>')
    if event_type == "grafik_garis":
        table = tampilan.get("table", "")
        kolom = tampilan.get("kolom", "")
        rows = data.get(table, [])
        value = [b.get(kolom) for b in rows
                 if isinstance(b.get(kolom), (int, float))]
        return render_grafik_garis(title, value)
    # tombol_aksi
    return (f'<div class="tombol-aksi"><button data-aksi="{_esc(tampilan.get("action", ""))}" '
            f'data-alur="{_esc(tampilan.get("workflows", ""))}">{_esc(title)}</button></div>')


def render_module(manifest: dict, data: dict | None = None) -> str:
    """Render seluruh modul: tiap tampilan dengan data contoh
    (manifes['contoh_data']) bila `data` tidak diberikan. Dipakai
    pratinjau di panel Modul (§2.13)."""
    data = data if data is not None else (manifest.get("example_data") or {})
    bagian = [f'<section class="panel-modul"><h3>{_esc(manifest.get("name", ""))}</h3>']
    for t in manifest.get("tampilan", []) or []:
        bagian.append(render_tampilan(t, data))
    bagian.append("</section>")
    return "\n".join(bagian)
