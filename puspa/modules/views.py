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

COMPONENTS = ("card", "table", "items", "form", "number", "line_chart",
            "action_button")


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _rupiah(value) -> str:
    try:
        return f"Rp{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return _esc(value)


def render_line_chart(title: str, value: list) -> str:
    """Satu grafik garis (SVG) dari daftar angka. Tanpa JavaScript."""
    if not value:
        return f'<div class="grafik"><h4>{_esc(title)}</h4><p>Belum ada data.</p></div>'
    number = [float(v) for v in value]
    top, bottom = max(number), min(number)
    span = (top - bottom) or 1.0
    width, height = 300, 120
    points = []
    for i, v in enumerate(number):
        x = 10 + (i * (width - 20) / max(len(number) - 1, 1))
        y = 10 + (1 - (v - bottom) / span) * (height - 20)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    return (
        f'<div class="grafik"><h4>{_esc(title)}</h4>'
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="grafik garis {_esc(title)}">'
        f'<polyline points="{polyline}" fill="none" stroke="#2563eb" '
        f'stroke-width="2"/></svg>'
        f'<p>{_esc(bottom)} – {_esc(top)}</p></div>'
    )


def render_view(view: dict, data: dict) -> str:
    """Render satu tampilan. `data`: {nama_tabel: [baris, ...]} — data
    contoh (pratinjau) atau data nyata modul."""
    event_type = view.get("type")
    title = view.get("title", "")
    if event_type not in COMPONENTS:
        raise ValueError(f"komponen {event_type!r} tidak diizinkan")
    if event_type == "card":
        text = view.get("text", "")
        return (f'<div class="kartu"><h4>{_esc(title)}</h4>'
                f'<p>{_esc(text)}</p></div>')
    if event_type == "number":
        table = view.get("table", "")
        columns = view.get("columns", "")
        rows = data.get(table, [])
        total = sum(float(row.get(columns, 0) or 0) for row in rows
                    if isinstance(row.get(columns), (int, float)))
        label = view.get("format", "")
        value = _rupiah(total) if label == "Rupiah" else f"{total:,.0f}".replace(",", ".")
        return (f'<div class="angka"><h4>{_esc(title)}</h4>'
                f'<p class="nilai">{value}</p></div>')
    if event_type == "table":
        table = view.get("table", "")
        columns = view.get("columns", []) or []
        rows = data.get(table, [])
        header = "".join(f"<th>{_esc(k)}</th>" for k in columns)
        content = ""
        for row in rows:
            cells = "".join(f"<td>{_esc(row.get(k, ''))}</td>" for k in columns)
            content += f"<tr>{cells}</tr>"
        return (f'<div class="tabel"><h4>{_esc(title)}</h4>'
                f'<table><thead><tr>{header}</tr></thead>'
                f'<tbody>{content}</tbody></table></div>')
    if event_type == "items":
        table = view.get("table", "")
        columns = view.get("columns", "")
        rows = data.get(table, [])
        item = "".join(f"<li>{_esc(row.get(columns, ''))}</li>" for row in rows)
        return f'<div class="daftar"><h4>{_esc(title)}</h4><ul>{item}</ul></div>'
    if event_type == "form":
        columns = view.get("columns", []) or []
        field = ""
        for k in columns:
            field += (f'<label>{_esc(k)} '
                      f'<input name="{_esc(k)}" type="text"></label>')
        return (f'<div class="form"><h4>{_esc(title)}</h4>'
                f'<form>{field}<button type="submit">Simpan</button></form></div>')
    if event_type == "line_chart":
        table = view.get("table", "")
        columns = view.get("columns", "")
        rows = data.get(table, [])
        value = [row.get(columns) for row in rows
                 if isinstance(row.get(columns), (int, float))]
        return render_line_chart(title, value)
    # tombol_aksi
    return (f'<div class="tombol-aksi"><button data-aksi="{_esc(view.get("action", ""))}" '
            f'data-alur="{_esc(view.get("workflows", ""))}">{_esc(title)}</button></div>')


def render_module(manifest: dict, data: dict | None = None) -> str:
    """Render seluruh modul: tiap tampilan dengan data contoh
    (manifes['contoh_data']) bila `data` tidak diberikan. Dipakai
    pratinjau di panel Modul (§2.13)."""
    data = data if data is not None else (manifest.get("example_data") or {})
    parts = [f'<section class="panel-modul"><h3>{_esc(manifest.get("name", ""))}</h3>']
    for t in manifest.get("views", []) or []:
        parts.append(render_view(t, data))
    parts.append("</section>")
    return "\n".join(parts)
