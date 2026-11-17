"""Persona — gaya bicara dari contoh + koreksi (definisi 2.16, C124).

Pola yang diadaptasi (ditulis ulang, bukan disalin):
- elizaOS (MIT, survei §memori): character file — persona sebagai
  data (nama, sifat, gaya bicara); Puspa menyimpan gaya sebagai fakta
  profil, bukan file karakter.
- mem0 (Apache-2.0): memori persona per user; Puspa menyimpan koreksi
  sebagai fakta `dikonfirmasi_user` yang langsung berlaku.

Aturan:
- Gaya bicara disimpan sebagai fakta profil (`kunci='gaya_bicara'`).
- Koreksi user ("lebih santai", "jangan pakai gue") disimpan sebagai
  fakta `dikonfirmasi_user` (`kunci='koreksi_gaya'`) dan langsung
  berlaku di `get_persona`.
- Contoh pesan user (10-20, tanpa impor chat WhatsApp) disimpan di
  tabel `contoh_gaya`, maks 20 — contoh tertua dibuang.
- `render_reply` adalah transformasi deterministik (pengganti
  sementara render model — render model sungguhan Fase 2). Dipakai
  harness uji buta supaya persona bisa diuji tanpa memanggil model.
"""

from __future__ import annotations

import re

import psycopg
from psycopg.rows import dict_row

from ..memory import profile
from ..memory.schema import resolve_dsn

#: Tiga contoh gaya yang ditawarkan saat onboarding (C123).
STYLES = {
    "santai": {
        "name": "Santai",
        "deskripsi": "Bahasa sehari-hari, slang, singkat, hangat.",
        "example": [
            "Siap, gue cek dulu ya.",
            "Oke, udah ketemu yang paling murah.",
            "Santai, nanti gue ingetin sebelum jatuh tempo.",
        ],
    },
    "rapi": {
        "name": "Rapi",
        "deskripsi": "Bahasa baku yang sopan, kalimat lengkap.",
        "example": [
            "Baik, saya periksa terlebih dahulu.",
            "Saya sudah menemukan harga termurah untuk Anda.",
            "Saya akan mengingatkan Anda sebelum jatuh tempo.",
        ],
    },
    "campur": {
        "name": "Campur kode",
        "deskripsi": "Campur bahasa Indonesia dan Inggris, informal.",
        "example": [
            "Got it, aku cek dulu ya.",
            "Done — ini yang paling murah so far.",
            "Reminder set, nanti aku kabarin sebelum due date.",
        ],
    },
}

MAX_EXAMPLE = 20

#: Koreksi yang dikenali (frasa -> direktif). Frasa lain tetap disimpan
#: mentah sebagai fakta koreksi dan berlaku sebagai catatan gaya.
_KOREKSI_DIKENALI = (
    ("lebih santai", "santai"),
    ("lebih formal", "formal"),
    ("lebih baku", "formal"),
    ("lebih singkat", "singkat"),
    ("jangan pakai gue", "without_gue"),
    ("jangan pakai lo", "without_lo"),
    ("jangan pakai saya", "without_saya"),
)


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def choose_style(user_id, style, dsn=None):
    """Pilih 1 dari 3 contoh gaya; simpan sebagai fakta profil."""
    if style not in STYLES:
        raise ValueError(f"gaya tidak dikenal: {style!r} (pilih: {sorted(STYLES)})")
    return profile.add_fact(
        user_id, "gaya_bicara", style, "user_confirmed", dsn=dsn
    )


def add_style_samples(user_id, messages, dsn=None):
    """Simpan contoh pesan user sendiri (1-20 pesan); tertua dibuang.

    Kembalikan jumlah contoh yang tersimpan sesudah penambahan.
    """
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages harus list tak kosong")
    if len(messages) > MAX_EXAMPLE:
        raise ValueError(f"maks {MAX_EXAMPLE} pesan contoh sekali tempel")
    bersih = [m.strip() for m in messages if isinstance(m, str) and m.strip()]
    if not bersih:
        raise ValueError("tidak ada pesan contoh yang valid")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for text in bersih:
                cur.execute(
                    "INSERT INTO style_examples (user_id, text) VALUES (%s, %s)",
                    (user_id, text),
                )
            cur.execute(
                "SELECT COUNT(*) AS n FROM style_examples WHERE user_id = %s",
                (user_id,),
            )
            amount = cur.fetchone()["n"]
            if amount > MAX_EXAMPLE:
                cur.execute(
                    "DELETE FROM style_examples WHERE id IN ("
                    "SELECT id FROM style_examples WHERE user_id = %s "
                    "ORDER BY created_at ASC LIMIT %s)",
                    (user_id, amount - MAX_EXAMPLE),
                )
                amount = MAX_EXAMPLE
        conn.commit()
    return amount


def list_style_samples(user_id, dsn=None):
    """Contoh pesan user, urut tertua dulu."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT text FROM style_examples WHERE user_id = %s ORDER BY created_at ASC",
                (user_id,),
            )
            return [r["text"] for r in cur.fetchall()]


def apply_correction(user_id, teks_koreksi, dsn=None):
    """Simpan koreksi gaya user; langsung berlaku.

    Kembalikan dict fakta koreksi + `direktif` yang dikenali (atau None
    kalau frasa tidak dikenali — tetap disimpan mentah).
    """
    if not isinstance(teks_koreksi, str) or not teks_koreksi.strip():
        raise ValueError("teks koreksi harus string tak kosong")
    text = teks_koreksi.strip()
    direktif = None
    lowered = text.lower()
    for frasa, name in _KOREKSI_DIKENALI:
        if frasa in lowered:
            direktif = name
            break
    value = direktif or text
    fact = profile.add_fact(
        user_id, "koreksi_gaya", value, "user_confirmed", dsn=dsn
    )
    fact["direktif"] = direktif
    fact["teks_asli"] = text
    return fact


def get_persona(user_id, dsn=None):
    """Persona efektif user: gaya + koreksi + jumlah contoh.

    Urutan berlaku: gaya dasar dari fakta `gaya_bicara`, lalu semua
    fakta `koreksi_gaya` yang masih berlaku sebagai direktif.
    """
    profile = profile.get_profile(user_id, dsn=dsn)
    style = profile.get("gaya_bicara", "santai")
    koreksi = [
        f for f in profile.list_facts(user_id, key="koreksi_gaya", dsn=dsn)
        if f["status"] == "active"
    ]
    direktif = [f["value"] for f in koreksi]
    example = list_style_samples(user_id, dsn=dsn)
    return {
        "style": style,
        "deskripsi": STYLES.get(style, STYLES["santai"])["deskripsi"],
        "direktif": direktif,
        "amount_example": len(example),
        "example": example,
    }


def render_reply(persona, teks_dasar):
    """Render balasan dengan gaya persona (deterministik, tanpa model).

    Transformasi sementara sampai render model Fase 2:
    - direktif `tanpa_gue`: gue->aku, lo->kamu.
    - direktif `tanpa_lo`: lo->kamu.
    - direktif `formal`/`lebih formal`: gue->saya, lo->Anda.
    - direktif `singkat`: potong ke kalimat pertama.
    """
    text = teks_dasar
    direktif = persona.get("direktif", [])
    if "without_gue" in direktif:
        text = re.sub(r"\bgue\b", "aku", text, flags=re.IGNORECASE)
    if "without_lo" in direktif:
        text = re.sub(r"\blo\b", "kamu", text, flags=re.IGNORECASE)
    if "formal" in direktif:
        text = re.sub(r"\bgue\b", "saya", text, flags=re.IGNORECASE)
        text = re.sub(r"\blo\b", "Anda", text, flags=re.IGNORECASE)
    if "singkat" in direktif:
        pertama = text.split(".")[0]
        text = pertama.strip() + "." if pertama.strip() else text
    return text
