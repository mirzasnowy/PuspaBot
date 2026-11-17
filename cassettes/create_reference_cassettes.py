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
        {"tool": "search_artikel", "output": {"type": "artikel", "amount": 5,
         "title": ["AI agents 2026", "RAG di produksi", "Evaluasi LLM",
                   "Router model murah", "Sandbox aman"]}},
        {"tool": "read_artikel", "output": {"type": "summaries", "value": 5,
         "text": "Lima artikel dirangkum."}},
    ],
    "read_email_create_draft": [
        {"tool": "gmail_search", "output": {"type": "email", "amount": 1,
         "id": ["m-uji-1"]}},
        {"tool": "gmail_read", "output": {"type": "email", "value": 1,
         "text": "[DATA: Halo, tolong kirim laporan minggu ini.]"}},
        {"tool": "gmail_create_draft", "output": {"type": "draft", "value": 1,
         "id": "draf-uji-1"}},
    ],
    "schedule_meeting_from_email": [
        {"tool": "gmail_read", "output": {"type": "email", "value": 1,
         "text": "[DATA: Bisakah kita rapat Rabu jam 10?] "}},
        {"tool": "calendar_create_event", "output": {"type": "events", "value": 1,
         "id": "ev-uji-1"}},
    ],
    "compare_price_laptop": [
        {"tool": "read_price", "output": {"type": "price", "value": 12500000,
         "store": "Tokopedia", "product": "Laptop X 14 inci 16GB"}},
        {"tool": "read_price", "output": {"type": "price", "value": 11999000,
         "store": "Shopee", "product": "Laptop X 14 inci 16GB"}},
        {"tool": "read_price", "output": {"type": "price", "value": 12499000,
         "store": "Blibli", "product": "Laptop X 14 inci 16GB"}},
    ],
    "check_bill_pln_prepare_payment": [
        {"tool": "read_bill", "output": {"type": "bill", "value": 132500,
         "penyedia": "PLN", "due_date": "20 Nov 2026"}},
        {"tool": "prepare_payment", "output": {"type": "pay", "value": 132500,
         "status": "pending_approval"}},
    ],
    "search_file_drive_summarize": [
        {"tool": "drive_search", "output": {"type": "file", "amount": 1,
         "name": ["nota-uji.pdf"]}},
        {"tool": "drive_read", "output": {"type": "summaries", "value": 1,
         "text": "[DATA: Nota uji, total Rp132.500.]"}},
    ],
    "ingat_preferensi_kemarin": [
        {"tool": "read_memory", "output": {"type": "fact", "value": 1,
         "text": "User suka jawaban singkat, bahasa santai."}},
    ],
    "bill_reminder": [
        {"tool": "create_trigger", "output": {"type": "triggers", "value": 1,
         "schedule": "harian 07:00"}},
    ],
    "remind_bill_due_this_week": [
        {"tool": "read_bill", "output": {"type": "bill", "value": 132500,
         "penyedia": "PLN", "due_date": "20 Nov 2026"}},
        {"tool": "read_bill", "output": {"type": "bill", "value": 85000,
         "penyedia": "PDAM", "due_date": "22 Nov 2026"}},
    ],
    "prepare_payment_electricity_token_50k": [
        {"tool": "prepare_payment", "output": {"type": "pay", "value": 50000,
         "status": "pending_approval"}},
    ],
}


def main() -> None:
    folder = Path(__file__).resolve().parent
    for name, recording in KASET.items():
        data = {"task_id": f"tugas-acuan-{name}", "name": name, "recording": recording}
        path = folder / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"ditulis {path} ({len(recording)} rekaman)")


if __name__ == "__main__":
    main()
