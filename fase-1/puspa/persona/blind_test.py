"""Harness uji buta gerbang 3 (definisi 8, gerbang 3; C124; I3).

Target gerbang 3: 5 teman tidak bisa membedakan balasan persona dari
balasan asli di atas 60% — 20 balasan, separuh asli separuh persona.

Harness ini MEMBANGUN uji + MENILAI hasilnya. UJI MANUSIANYA
menunggu Nero (5 teman belum direkrut, belum ada yang menjawab) —
file ini TIDAK mengarang hasil manusia. Yang bisa dijalankan sekarang:
bangun 20 item, simpan sebagai JSON, cetak lembar uji, lalu nilai
jawaban juri begitu ada.

Isi uji:
- 10 item ASLI: pesan contoh user sendiri (dari `contoh_gaya`).
- 10 item PERSONA: balasan dasar yang dirender lewat persona
  (`render_reply` — deterministik; render model sungguhan Fase 2).
- Urutan diacak deterministik (seed) supaya uji bisa diulang.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .persona import get_persona, render_reply

JUMLAH_ITEM = 20
JUMLAH_ASLI = 10
JUMLAH_PERSONA = 10
AMBANG_GERBANG = 0.60


def build_blind_test(user_id, base_replies, dsn=None, seed=7):
    """Bangun 20 item uji buta. Kembalikan dict uji (siap disimpan JSON).

    `base_replies`: 10 balasan dasar (string) yang dirender persona.
    Item asli diambil dari contoh gaya user (butuh >= 10 contoh).
    """
    if len(base_replies) < JUMLAH_PERSONA:
        raise ValueError(f"butuh minimal {JUMLAH_PERSONA} balasan dasar")
    persona = get_persona(user_id, dsn=dsn)
    asli = persona["contoh"]
    if len(asli) < JUMLAH_ASLI:
        raise ValueError(
            f"butuh minimal {JUMLAH_ASLI} contoh pesan user, baru ada {len(asli)}"
        )
    items = []
    for teks in asli[:JUMLAH_ASLI]:
        items.append({"teks": teks, "asal": "asli"})
    for teks in base_replies[:JUMLAH_PERSONA]:
        items.append({"teks": render_reply(persona, teks), "asal": "persona"})
    rng = random.Random(seed)
    rng.shuffle(items)
    for i, item in enumerate(items, start=1):
        item["nomor"] = i
    return {
        "user_id": user_id,
        "gaya": persona["gaya"],
        "direktif": persona["direktif"],
        "seed": seed,
        "items": items,
        "jumlah_juri": 0,
        "jawaban_juri": [],
        "hasil": None,
    }


def save_blind_test(uji, path):
    """Simpan uji ke JSON."""
    Path(path).write_text(
        json.dumps(uji, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return str(path)


def load_blind_test(path):
    """Muat uji dari JSON."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_sheet(uji):
    """Lembar uji yang bisa dicetak/dikirim ke 5 teman juri."""
    lines = [
        "UJI BUTA PERSONA — Gerbang 3",
        f"Gaya persona: {uji['gaya']}",
        "Tebak tiap balasan: ASLI (ditulis user) atau PERSONA (dibuat Puspa).",
        "",
    ]
    for item in uji["items"]:
        lines.append(f"{item['nomor']}. {item['teks']}")
        lines.append("   Tebakan: [ ] asli   [ ] persona")
    lines.append("")
    lines.append("Kirim tebakanmu (20 jawaban) ke penyelenggara uji.")
    return "\n".join(lines)


def score_blind_test(uji, guesses):
    """Nilai jawaban SATU juri. `guesses`: list 20 nilai 'asli'/'persona'.

    Kembalikan dict {benar, total, akurasi, lolos} dan catat jawaban
    ke uji (di memori pemanggil; simpan ulang dengan save_blind_test).
    Akurasi = seberapa sering juri BENAR. Gerbang lolos kalau akurasi
    <= 60% (juri tidak bisa membedakan).
    """
    if len(guesses) != len(uji["items"]):
        raise ValueError("jumlah tebakan harus sama dengan jumlah item")
    benar = 0
    for item, tebakan in zip(uji["items"], guesses):
        if tebakan not in ("asli", "persona"):
            raise ValueError("tebakan harus 'asli' atau 'persona'")
        if tebakan == item["asal"]:
            benar += 1
    akurasi = benar / len(uji["items"])
    hasil = {
        "benar": benar,
        "total": len(uji["items"]),
        "akurasi": akurasi,
        "lolos": akurasi <= AMBANG_GERBANG,
    }
    uji["jawaban_juri"].append({"tebakan": list(guesses), "hasil": hasil})
    uji["jumlah_juri"] = len(uji["jawaban_juri"])
    if uji["jumlah_juri"] >= 5:
        rata = sum(
            j["hasil"]["akurasi"] for j in uji["jawaban_juri"]
        ) / uji["jumlah_juri"]
        uji["hasil"] = {
            "rata_akurasi": rata,
            "lolos": rata <= AMBANG_GERBANG,
            "jumlah_juri": uji["jumlah_juri"],
        }
    return hasil
