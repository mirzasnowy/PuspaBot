"""Uji kaset (§2.11 revisi 7).

- Setiap `tool_selesai` di Buku Kerja menyimpan keluaran persisnya.
  Dari situ satu tugas lengkap bisa diputar ulang: agen dijalankan
  lagi dengan tool diganti pemutar kaset (tanpa jaringan, tanpa beban
  ke marketplace/provider). Model tetap dipanggil hidup.
- Dipakai untuk: (a) regresi 10 tugas acuan; (b) uji suntik harga
  salah; (c) mengulang pengukuran gerbang 2; (d) membandingkan biaya
  antar versi.
- Kaset acuan: 10 kaset (satu per tugas acuan), dibuat dari data
  Nero di Fase 2 (`cassettes/` di repo build — data uji milik Nero).
- Privasi: kaset disimpan lokal, tidak dibagikan, ikut terhapus saat
  tugasnya dihapus (file dihapus + baris `kaset` dihapus + tombstone
  di Buku Kerja).
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path

CASSETTE_DIR_DEFAULT = Path(__file__).resolve().parent.parent.parent / "cassettes"

# 10 tugas acuan (tetap, §8 revisi 7).
TASK_REFERENCE = (
    "research_summarize_5_articles",
    "read_email_create_draft",
    "schedule_meeting_from_email",
    "compare_price_laptop",
    "check_bill_pln_prepare_payment",
    "search_file_drive_summarize",
    "remember_yesterday_preferences",
    "bill_reminder",
    "remind_bill_due_this_week",
    "prepare_payment_electricity_token_50k",
)


class CassetteError(Exception):
    """Kaset tidak valid / tidak ditemukan."""


def record_from_ledger(event: list[dict], task_id: str) -> dict:
    """Bangun kaset dari kejadian `tool_selesai` satu tugas.

    Kembalikan {"tugas_id": ..., "rekaman": [{"tool": ..., "keluaran": ...}]}.
    """
    recording = []
    for ev in event:
        if ev.get("type") != "tool_completed" or ev.get("task_id") != task_id:
            continue
        content = ev.get("content") or {}
        recording.append({"tool": content.get("tool", "unknown"), "output": content.get("output")})
    return {"task_id": task_id, "recording": recording}


def save_cassette(
    cassette: dict,
    name: str,
    *,
    cassette_dir: Path | None = None,
    dsn=None,
    user_id: str | None = None,
    task_id: str | None = None,
) -> Path:
    """Simpan kaset sebagai file JSON lokal. Kembalikan path berkas."""
    folder = Path(cassette_dir or CASSETTE_DIR_DEFAULT)
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{name}.json"
    file_path.write_text(json.dumps(cassette, indent=2, ensure_ascii=False), encoding="utf-8")
    if dsn is not None and user_id and task_id:
        import os

        import psycopg

        dsn = dsn or os.environ.get(
            "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
        )
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO cassettes (id, task_id, user_id, name, berkas)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (f"kaset-{uuid.uuid4().hex[:8]}", task_id, user_id, name, str(file_path)),
                )
            conn.commit()
    return file_path


def load_cassette(path: str | Path) -> dict:
    """Muat kaset dari file JSON."""
    path = Path(path)
    if not path.exists():
        raise CassetteError(f"kaset tidak ditemukan: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class CassettePlayer:
    """Pemutar kaset: menggantikan tool (tanpa jaringan).

    `putar(tool)` mengembalikan keluaran rekaman berikutnya untuk
    tool itu (urutan rekaman dipertahankan). Tool yang tidak ada di
    kaset -> `CassetteError` jujur (bukan keluaran karangan).
    """

    def __init__(self, cassette: dict) -> None:
        self.cassette = copy.deepcopy(cassette)
        self._position: dict[str, int] = {}
        self.play_count = 0

    def play(self, tool: str):
        recording = [r for r in self.cassette.get("recording", []) if r.get("tool") == tool]
        idx = self._position.get(tool, 0)
        if idx >= len(recording):
            raise CassetteError(f"kaset kehabisan rekaman untuk tool {tool!r}")
        self._position[tool] = idx + 1
        self.play_count += 1
        return copy.deepcopy(recording[idx]["output"])

    def remaining(self, tool: str) -> int:
        recording = [r for r in self.cassette.get("recording", []) if r.get("tool") == tool]
        return len(recording) - self._position.get(tool, 0)


def inject_price_wrong(cassette: dict, *, tool: str, value_new) -> dict:
    """Uji suntik: ubah satu angka di keluaran Pembaca dalam kaset.

    Kembalikan kaset BARU (asli tidak diubah). Pemeriksa harus
    menangkap perubahan ini saat putar ulang (gerbang 11).
    """
    new = copy.deepcopy(cassette)
    for recording in new.get("recording", []):
        if recording.get("tool") != tool:
            continue
        output = recording.get("output")
        if isinstance(output, dict) and "value" in output:
            recording["output"] = {**output, "value": value_new}
            return new
        if isinstance(output, str):
            updated_at = re.sub(r"Rp[\d.,]+", f"Rp{value_new:,}".replace(",", "."), output, count=1)
            if updated_at != output:
                recording["output"] = updated_at
                return new
    raise CassetteError(f"tidak ada angka yang bisa disuntik di tool {tool!r}")


def delete_cassette_task(task_id: str, *, cassette_dir: Path | None = None, dsn=None) -> dict:
    """Hapus semua kaset satu tugas (file + baris DB). Kembalikan
    {"dihapus": [nama, ...]}. Dipanggil saat tugas dihapus (tombstone
    Buku Kerja ditulis pemanggil lewat `tugas_dihentikan`)."""
    folder = Path(cassette_dir or CASSETTE_DIR_DEFAULT)
    deleted = []
    if dsn is not None:
        import os

        import psycopg

        dsn = dsn or os.environ.get(
            "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
        )
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name, berkas FROM cassettes WHERE task_id = %s", (task_id,))
                rows = cur.fetchall()
                cur.execute("DELETE FROM cassettes WHERE task_id = %s", (task_id,))
            conn.commit()
        for name, file_path in rows:
            p = Path(file_path)
            if p.exists():
                p.unlink()
            deleted.append(name)
        return {"deleted": deleted}
    for file_path in folder.glob("*.json"):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("task_id") == task_id:
            file_path.unlink()
            deleted.append(file_path.stem)
    return {"deleted": deleted}
