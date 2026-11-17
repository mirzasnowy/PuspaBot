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

KOMPONEN = ("kartu", "tabel", "daftar", "form", "angka", "grafik_garis",
            "tombol_aksi")


def _esc(teks) -> str:
    return html.escape(str(teks), quote=True)


def _rupiah(nilai) -> str:
    try:
        return f"Rp{int(nilai):,}".replace(",", ".")
    except (TypeError, ValueError):
        return _esc(nilai)


def render_grafik_garis(judul: str, nilai: list) -> str:
    """Satu grafik garis (SVG) dari daftar angka. Tanpa JavaScript."""
    if not nilai:
        return f'<div class="grafik"><h4>{_esc(judul)}</h4><p>Belum ada data.</p></div>'
    angka = [float(v) for v in nilai]
    atas, bawah = max(angka), min(angka)
    rentang = (atas - bawah) or 1.0
    lebar, tinggi = 300, 120
    titik = []
    for i, v in enumerate(angka):
        x = 10 + (i * (lebar - 20) / max(len(angka) - 1, 1))
        y = 10 + (1 - (v - bawah) / rentang) * (tinggi - 20)
        titik.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(titik)
    return (
        f'<div class="grafik"><h4>{_esc(judul)}</h4>'
        f'<svg width="{lebar}" height="{tinggi}" viewBox="0 0 {lebar} {tinggi}" '
        f'role="img" aria-label="grafik garis {_esc(judul)}">'
        f'<polyline points="{polyline}" fill="none" stroke="#2563eb" '
        f'stroke-width="2"/></svg>'
        f'<p>{_esc(bawah)} – {_esc(atas)}</p></div>'
    )


def render_tampilan(tampilan: dict, data: dict) -> str:
    """Render satu tampilan. `data`: {nama_tabel: [baris, ...]} — data
    contoh (pratinjau) atau data nyata modul."""
    jenis = tampilan.get("jenis")
    judul = tampilan.get("judul", "")
    if jenis not in KOMPONEN:
        raise ValueError(f"komponen {jenis!r} tidak diizinkan")
    if jenis == "kartu":
        teks = tampilan.get("teks", "")
        return (f'<div class="kartu"><h4>{_esc(judul)}</h4>'
                f'<p>{_esc(teks)}</p></div>')
    if jenis == "angka":
        tabel = tampilan.get("tabel", "")
        kolom = tampilan.get("kolom", "")
        baris = data.get(tabel, [])
        total = sum(float(b.get(kolom, 0) or 0) for b in baris
                    if isinstance(b.get(kolom), (int, float)))
        label = tampilan.get("format", "")
        nilai = _rupiah(total) if label == "Rupiah" else f"{total:,.0f}".replace(",", ".")
        return (f'<div class="angka"><h4>{_esc(judul)}</h4>'
                f'<p class="nilai">{nilai}</p></div>')
    if jenis == "tabel":
        tabel = tampilan.get("tabel", "")
        kolom = tampilan.get("kolom", []) or []
        baris = data.get(tabel, [])
        kepala = "".join(f"<th>{_esc(k)}</th>" for k in kolom)
        isi = ""
        for b in baris:
            sel = "".join(f"<td>{_esc(b.get(k, ''))}</td>" for k in kolom)
            isi += f"<tr>{sel}</tr>"
        return (f'<div class="tabel"><h4>{_esc(judul)}</h4>'
                f'<table><thead><tr>{kepala}</tr></thead>'
                f'<tbody>{isi}</tbody></table></div>')
    if jenis == "daftar":
        tabel = tampilan.get("tabel", "")
        kolom = tampilan.get("kolom", "")
        baris = data.get(tabel, [])
        item = "".join(f"<li>{_esc(b.get(kolom, ''))}</li>" for b in baris)
        return f'<div class="daftar"><h4>{_esc(judul)}</h4><ul>{item}</ul></div>'
    if jenis == "form":
        kolom = tampilan.get("kolom", []) or []
        field = ""
        for k in kolom:
            field += (f'<label>{_esc(k)} '
                      f'<input name="{_esc(k)}" type="text"></label>')
        return (f'<div class="form"><h4>{_esc(judul)}</h4>'
                f'<form>{field}<button type="submit">Simpan</button></form></div>')
    if jenis == "grafik_garis":
        tabel = tampilan.get("tabel", "")
        kolom = tampilan.get("kolom", "")
        baris = data.get(tabel, [])
        nilai = [b.get(kolom) for b in baris
                 if isinstance(b.get(kolom), (int, float))]
        return render_grafik_garis(judul, nilai)
    # tombol_aksi
    return (f'<div class="tombol-aksi"><button data-aksi="{_esc(tampilan.get("aksi", ""))}" '
            f'data-alur="{_esc(tampilan.get("alur", ""))}">{_esc(judul)}</button></div>')


def render_modul(manifes: dict, data: dict | None = None) -> str:
    """Render seluruh modul: tiap tampilan dengan data contoh
    (manifes['contoh_data']) bila `data` tidak diberikan. Dipakai
    pratinjau di panel Modul (§2.13)."""
    data = data if data is not None else (manifes.get("contoh_data") or {})
    bagian = [f'<section class="panel-modul"><h3>{_esc(manifes.get("nama", ""))}</h3>']
    for t in manifes.get("tampilan", []) or []:
        bagian.append(render_tampilan(t, data))
    bagian.append("</section>")
    return "\n".join(bagian)
