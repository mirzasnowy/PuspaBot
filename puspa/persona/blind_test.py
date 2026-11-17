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

AMOUNT_ITEM = 20
AMOUNT_ASLI = 10
AMOUNT_PERSONA = 10
AMBANG_GERBANG = 0.60


def build_blind_test(user_id, base_replies, dsn=None, seed=7):
    """Bangun 20 item uji buta. Kembalikan dict uji (siap disimpan JSON).

    `base_replies`: 10 balasan dasar (string) yang dirender persona.
    Item asli diambil dari contoh gaya user (butuh >= 10 contoh).
    """
    if len(base_replies) < AMOUNT_PERSONA:
        raise ValueError(f"butuh minimal {AMOUNT_PERSONA} balasan dasar")
    persona = get_persona(user_id, dsn=dsn)
    asli = persona["example"]
    if len(asli) < AMOUNT_ASLI:
        raise ValueError(
            f"butuh minimal {AMOUNT_ASLI} contoh pesan user, baru ada {len(asli)}"
        )
    items = []
    for text in asli[:AMOUNT_ASLI]:
        items.append({"text": text, "source": "asli"})
    for text in base_replies[:AMOUNT_PERSONA]:
        items.append({"text": render_reply(persona, text), "source": "persona"})
    rng = random.Random(seed)
    rng.shuffle(items)
    for i, item in enumerate(items, start=1):
        item["number"] = i
    return {
        "user_id": user_id,
        "style": persona["style"],
        "directive": persona["directive"],
        "seed": seed,
        "items": items,
        "amount_juri": 0,
        "jawaban_juri": [],
        "result": None,
    }


def save_blind_test(test, path):
    """Simpan uji ke JSON."""
    Path(path).write_text(
        json.dumps(test, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return str(path)


def load_blind_test(path):
    """Muat uji dari JSON."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_sheet(test):
    """Lembar uji yang bisa dicetak/dikirim ke 5 teman juri."""
    lines = [
        "UJI BUTA PERSONA — Gerbang 3",
        f"Gaya persona: {test['style']}",
        "Tebak tiap balasan: ASLI (ditulis user) atau PERSONA (dibuat Puspa).",
        "",
    ]
    for item in test["items"]:
        lines.append(f"{item['number']}. {item['text']}")
        lines.append("   Tebakan: [ ] asli   [ ] persona")
    lines.append("")
    lines.append("Kirim tebakanmu (20 jawaban) ke penyelenggara uji.")
    return "\n".join(lines)


def score_blind_test(test, guesses):
    """Nilai jawaban SATU juri. `guesses`: list 20 nilai 'asli'/'persona'.

    Kembalikan dict {benar, total, akurasi, lolos} dan catat jawaban
    ke uji (di memori pemanggil; simpan ulang dengan save_blind_test).
    Akurasi = seberapa sering juri BENAR. Gerbang lolos kalau akurasi
    <= 60% (juri tidak bisa membedakan).
    """
    if len(guesses) != len(test["items"]):
        raise ValueError("jumlah tebakan harus sama dengan jumlah item")
    correct = 0
    for item, tebakan in zip(test["items"], guesses):
        if tebakan not in ("asli", "persona"):
            raise ValueError("tebakan harus 'asli' atau 'persona'")
        if tebakan == item["source"]:
            correct += 1
    akurasi = correct / len(test["items"])
    result = {
        "correct": correct,
        "total": len(test["items"]),
        "akurasi": akurasi,
        "passed": akurasi <= AMBANG_GERBANG,
    }
    test["jawaban_juri"].append({"tebakan": list(guesses), "result": result})
    test["amount_juri"] = len(test["jawaban_juri"])
    if test["amount_juri"] >= 5:
        rata = sum(
            j["result"]["akurasi"] for j in test["jawaban_juri"]
        ) / test["amount_juri"]
        test["result"] = {
            "rata_akurasi": rata,
            "passed": rata <= AMBANG_GERBANG,
            "amount_juri": test["amount_juri"],
        }
    return result
