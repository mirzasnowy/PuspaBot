"""Buat 10 kaset acuan Fase 2 (satu per tugas acuan, §2.11).

Data uji milik Nero (angka contoh yang masuk akal, BUKAN harga live):
tiap kaset berisi rekaman `tool_selesai` dengan keluaran persis.
Jalankan: `python cassettes/buat_kaset_acuan.py` dari build/fase-2/.
Kaset disimpan lokal, tidak dibagikan.
"""

import json
from pathlib import Path

KASET = {
    "riset_rangkum_5_artikel": [
        {"tool": "cari_artikel", "keluaran": {"jenis": "artikel", "jumlah": 5,
         "judul": ["AI agents 2026", "RAG di produksi", "Evaluasi LLM",
                   "Router model murah", "Sandbox aman"]}},
        {"tool": "baca_artikel", "keluaran": {"jenis": "ringkasan", "nilai": 5,
         "teks": "Lima artikel dirangkum."}},
    ],
    "baca_email_buat_draf": [
        {"tool": "gmail_cari", "keluaran": {"jenis": "email", "jumlah": 1,
         "id": ["m-uji-1"]}},
        {"tool": "gmail_baca", "keluaran": {"jenis": "email", "nilai": 1,
         "teks": "[DATA: Halo, tolong kirim laporan minggu ini.]"}},
        {"tool": "gmail_buat_draf", "keluaran": {"jenis": "draf", "nilai": 1,
         "id": "draf-uji-1"}},
    ],
    "jadwalkan_meeting_dari_email": [
        {"tool": "gmail_baca", "keluaran": {"jenis": "email", "nilai": 1,
         "teks": "[DATA: Bisakah kita rapat Rabu jam 10?] "}},
        {"tool": "calendar_buat_event", "keluaran": {"jenis": "event", "nilai": 1,
         "id": "ev-uji-1"}},
    ],
    "bandingkan_harga_laptop": [
        {"tool": "baca_harga", "keluaran": {"jenis": "harga", "nilai": 12500000,
         "toko": "Tokopedia", "barang": "Laptop X 14 inci 16GB"}},
        {"tool": "baca_harga", "keluaran": {"jenis": "harga", "nilai": 11999000,
         "toko": "Shopee", "barang": "Laptop X 14 inci 16GB"}},
        {"tool": "baca_harga", "keluaran": {"jenis": "harga", "nilai": 12499000,
         "toko": "Blibli", "barang": "Laptop X 14 inci 16GB"}},
    ],
    "cek_tagihan_pln_siapkan_bayar": [
        {"tool": "baca_tagihan", "keluaran": {"jenis": "tagihan", "nilai": 132500,
         "penyedia": "PLN", "jatuh_tempo": "20 Nov 2026"}},
        {"tool": "siapkan_bayar", "keluaran": {"jenis": "bayar", "nilai": 132500,
         "status": "menunggu_persetujuan"}},
    ],
    "cari_file_drive_ringkas": [
        {"tool": "drive_cari", "keluaran": {"jenis": "file", "jumlah": 1,
         "nama": ["nota-uji.pdf"]}},
        {"tool": "drive_baca", "keluaran": {"jenis": "ringkasan", "nilai": 1,
         "teks": "[DATA: Nota uji, total Rp132.500.]"}},
    ],
    "ingat_preferensi_kemarin": [
        {"tool": "baca_memori", "keluaran": {"jenis": "fakta", "nilai": 1,
         "teks": "User suka jawaban singkat, bahasa santai."}},
    ],
    "pengingat_tagihan": [
        {"tool": "buat_pemicu", "keluaran": {"jenis": "pemicu", "nilai": 1,
         "jadwal": "harian 07:00"}},
    ],
    "ingatkan_tagihan_jatuh_tempo_minggu_ini": [
        {"tool": "baca_tagihan", "keluaran": {"jenis": "tagihan", "nilai": 132500,
         "penyedia": "PLN", "jatuh_tempo": "20 Nov 2026"}},
        {"tool": "baca_tagihan", "keluaran": {"jenis": "tagihan", "nilai": 85000,
         "penyedia": "PDAM", "jatuh_tempo": "22 Nov 2026"}},
    ],
    "siapkan_bayar_token_listrik_50rb": [
        {"tool": "siapkan_bayar", "keluaran": {"jenis": "bayar", "nilai": 50000,
         "status": "menunggu_persetujuan"}},
    ],
}


def main() -> None:
    folder = Path(__file__).resolve().parent
    for nama, rekaman in KASET.items():
        data = {"tugas_id": f"tugas-acuan-{nama}", "nama": nama, "rekaman": rekaman}
        path = folder / f"{nama}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"ditulis {path} ({len(rekaman)} rekaman)")


if __name__ == "__main__":
    main()
