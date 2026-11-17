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

KASET_DIR_DEFAULT = Path(__file__).resolve().parent.parent.parent / "cassettes"

# 10 tugas acuan (tetap, §8 revisi 7).
TUGAS_ACUAN = (
    "riset_rangkum_5_artikel",
    "baca_email_buat_draf",
    "jadwalkan_meeting_dari_email",
    "bandingkan_harga_laptop",
    "cek_tagihan_pln_siapkan_bayar",
    "cari_file_drive_ringkas",
    "ingat_preferensi_kemarin",
    "pengingat_tagihan",
    "ingatkan_tagihan_jatuh_tempo_minggu_ini",
    "siapkan_bayar_token_listrik_50rb",
)


class CassetteError(Exception):
    """Kaset tidak valid / tidak ditemukan."""


def rekam_dari_ledger(kejadian: list[dict], tugas_id: str) -> dict:
    """Bangun kaset dari kejadian `tool_selesai` satu tugas.

    Kembalikan {"tugas_id": ..., "rekaman": [{"tool": ..., "keluaran": ...}]}.
    """
    rekaman = []
    for ev in kejadian:
        if ev.get("jenis") != "tool_selesai" or ev.get("tugas_id") != tugas_id:
            continue
        isi = ev.get("isi") or {}
        rekaman.append({"tool": isi.get("tool", "tidak_dikenal"),
                        "keluaran": isi.get("keluaran")})
    return {"tugas_id": tugas_id, "rekaman": rekaman}


def simpan_kaset(kaset: dict, nama: str, *, kaset_dir: Path | None = None,
                 dsn=None, user_id: str | None = None,
                 tugas_id: str | None = None) -> Path:
    """Simpan kaset sebagai file JSON lokal. Kembalikan path berkas."""
    folder = Path(kaset_dir or KASET_DIR_DEFAULT)
    folder.mkdir(parents=True, exist_ok=True)
    berkas = folder / f"{nama}.json"
    berkas.write_text(json.dumps(kaset, indent=2, ensure_ascii=False), encoding="utf-8")
    if dsn is not None and user_id and tugas_id:
        import os

        import psycopg

        dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kaset (id, tugas_id, user_id, nama, berkas)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (f"kaset-{uuid.uuid4().hex[:8]}", tugas_id, user_id, nama, str(berkas)),
                )
            conn.commit()
    return berkas


def muat_kaset(path: str | Path) -> dict:
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

    def __init__(self, kaset: dict) -> None:
        self.kaset = copy.deepcopy(kaset)
        self._posisi: dict[str, int] = {}
        self.putaran = 0

    def putar(self, tool: str):
        rekaman = [r for r in self.kaset.get("rekaman", []) if r.get("tool") == tool]
        idx = self._posisi.get(tool, 0)
        if idx >= len(rekaman):
            raise CassetteError(f"kaset kehabisan rekaman untuk tool {tool!r}")
        self._posisi[tool] = idx + 1
        self.putaran += 1
        return copy.deepcopy(rekaman[idx]["keluaran"])

    def sisa(self, tool: str) -> int:
        rekaman = [r for r in self.kaset.get("rekaman", []) if r.get("tool") == tool]
        return len(rekaman) - self._posisi.get(tool, 0)


def suntik_harga_salah(kaset: dict, *, tool: str, nilai_baru) -> dict:
    """Uji suntik: ubah satu angka di keluaran Pembaca dalam kaset.

    Kembalikan kaset BARU (asli tidak diubah). Pemeriksa harus
    menangkap perubahan ini saat putar ulang (gerbang 11).
    """
    baru = copy.deepcopy(kaset)
    for rekaman in baru.get("rekaman", []):
        if rekaman.get("tool") != tool:
            continue
        keluaran = rekaman.get("keluaran")
        if isinstance(keluaran, dict) and "nilai" in keluaran:
            rekaman["keluaran"] = {**keluaran, "nilai": nilai_baru}
            return baru
        if isinstance(keluaran, str):
            diubah = re.sub(r"Rp[\d.,]+", f"Rp{nilai_baru:,}".replace(",", "."), keluaran, count=1)
            if diubah != keluaran:
                rekaman["keluaran"] = diubah
                return baru
    raise CassetteError(f"tidak ada angka yang bisa disuntik di tool {tool!r}")


def hapus_kaset_tugas(tugas_id: str, *, kaset_dir: Path | None = None,
                      dsn=None) -> dict:
    """Hapus semua kaset satu tugas (file + baris DB). Kembalikan
    {"dihapus": [nama, ...]}. Dipanggil saat tugas dihapus (tombstone
    Buku Kerja ditulis pemanggil lewat `tugas_dihentikan`)."""
    folder = Path(kaset_dir or KASET_DIR_DEFAULT)
    dihapus = []
    if dsn is not None:
        import os

        import psycopg

        dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT nama, berkas FROM kaset WHERE tugas_id = %s", (tugas_id,))
                baris = cur.fetchall()
                cur.execute("DELETE FROM kaset WHERE tugas_id = %s", (tugas_id,))
            conn.commit()
        for nama, berkas in baris:
            p = Path(berkas)
            if p.exists():
                p.unlink()
            dihapus.append(nama)
        return {"dihapus": dihapus}
    for berkas in folder.glob("*.json"):
        try:
            data = json.loads(berkas.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("tugas_id") == tugas_id:
            berkas.unlink()
            dihapus.append(berkas.stem)
    return {"dihapus": dihapus}
