"""Pustaka blok alur (§2.12 revisi 7) — 15 blok tetap di MVP.

Tiap blok punya: skema masukan, skema keluaran, kelas izin, manifes
izin sendiri, dan penanda aksi keluar. Blok yang memicu aksi keluar
tetap lewat gerbang tunggal (§2.6) — runner tidak pernah mengeksekusi
aksi keluar langsung; ia meminta persetujuan dulu (lihat approvals.py).

Blok baru berisi kode TETAP fase 6. Di MVP hanya 15 blok ini;
kebutuhan blok baru dicatat sebagai Permintaan Blok (lihat composer.py).
"""

from __future__ import annotations

# Kelas izin sub-agen (§2.2): Pembaca membaca konten tak tepercaya
# tanpa alat tulis; Pelaksana memakai alat/konektor dengan masukan
# ber-skema; Pemeriksa membaca ulang sumber secara independen.
KLASS_PERMISSION = ("Pembaca", "Pelaksana", "Pemeriksa")

# Blok yang memicu aksi keluar (§2.6): kirim, buat event, siapkan
# bayar. Ketiganya WAJIB lewat antrean persetujuan di runner.
OUTBOUND_ACTION_BLOCKS = ("create_event", "prepare_payment")

# Domain yang boleh dibaca tiap blok baca (allowlist, §2.6). Tidak ada
# panggilan HTTP bebas di alur (§2.12): blok di luar daftar ini tidak
# boleh memanggil HTTP sama sekali.
BLOCK_DOMAINS = {
    "compare_prices": ("tokopedia.com", "shopee.co.id", "blibli.com"),
    "watch_prices": ("tokopedia.com", "shopee.co.id", "blibli.com"),
    "check_bill": ("site_provider_bill",),
}

# Satu definisi per blok. `masukan`/`keluaran`: nama field -> tipe
# sederhana ("teks", "angka", "tanggal", "pilihan", "daftar", "uang",
# "ya_tidak"). `izin`: konektor, scope, domain, batas_belanja (Rp).
BLOCKS = {
    "check_bill": {
        "inputs": {"provider": "choice", "customer_number": "text"},
        "output": {"amount": "money", "due_date": "date",
                     "status": "choice", "evidence_id": "text", "verdict": "choice"},
        "permission_class": "Pembaca",
        "permission": {"connectors": [], "scope": [], "domain": ["site_provider_bill"],
                 "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Cek jumlah + jatuh tempo + status tagihan satu provider.",
    },
    "compare_prices": {
        "inputs": {"name_product": "text"},
        "output": {"cheapest": "money", "price": "items", "spread": "money",
                     "verdict": "choice"},
        "permission_class": "Pembaca",
        "permission": {"connectors": [], "scope": [],
                 "domain": ["tokopedia.com", "shopee.co.id", "blibli.com"],
                 "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Bandingkan harga barang baru di Tokopedia, Shopee, Blibli.",
    },
    "watch_prices": {
        "inputs": {"product_id": "text", "price_threshold": "money"},
        "output": {"current_price": "money", "below_threshold": "boolean",
                     "evidence_id": "text"},
        "permission_class": "Pembaca",
        "permission": {"connectors": [], "scope": [],
                 "domain": ["tokopedia.com", "shopee.co.id", "blibli.com"],
                 "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Baca harga barang di watchlist; benar bila di bawah ambang.",
    },
    "search_email": {
        "inputs": {"keywords": "text"},
        "output": {"email": "items"},
        "permission_class": "Pembaca",
        "permission": {"connectors": ["gmail"], "scope": ["gmail.readonly"],
                 "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Cari email berdasarkan kata kunci (hanya metadata + ringkasan).",
    },
    "read_email": {
        "inputs": {"email_id": "text"},
        "output": {"sender": "text", "subject": "text", "content": "text"},
        "permission_class": "Pembaca",
        "permission": {"connectors": ["gmail"], "scope": ["gmail.readonly"],
                 "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Baca satu email lewat Pembaca terkarantina (§2.6).",
    },
    "create_draft": {
        "inputs": {"recipient": "text", "subject": "text", "content": "text"},
        "output": {"draft_id": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": ["gmail"], "scope": ["gmail.compose"],
                 "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Buat draf balasan email. Draf BUKAN kirim; kirim tetap butuh persetujuan.",
    },
    "create_event": {
        "inputs": {"title": "text", "created_at": "date", "attendees": "items"},
        "output": {"event_id": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": ["calendar"], "scope": ["calendar.events"],
                 "domain": [], "spending_limit": 0},
        "outbound_action": True,
        "cost_rp": 0,
        "description": "Buat event Calendar. Aksi keluar: masuk antrean persetujuan.",
    },
    "prepare_payment": {
        "inputs": {"provider": "choice", "amount": "money", "purpose": "text"},
        "output": {"payment_draft_id": "text", "content_hash": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [],
                 "spending_limit": 0},
        "outbound_action": True,
        "cost_rp": 0,
        "description": "Siapkan bayar mode aman: DRAF saja, user menyetujui di bank/e-wallet sendiri.",
    },
    "send_notification": {
        "inputs": {"message": "text"},
        "output": {"sent": "boolean"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Kirim pengingat dalam aplikasi (Web Push). Bukan kirim email/SMS.",
    },
    "ask_user": {
        "inputs": {"question": "text"},
        "output": {"answer": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Berhenti dan bertanya ke user; jalan alur lanjut setelah dijawab.",
    },
    "wait_for_approval": {
        "inputs": {"action": "text"},
        "output": {"approved": "boolean"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Tunggu persetujuan user atas satu aksi yang antre.",
    },
    "condition": {
        "inputs": {"expression": "text"},
        "output": {"correct": "boolean"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Percabangan: jalankan langkah `maka` bila benar, `kalau_not` bila salah.",
    },
    "for_each": {
        "inputs": {"items": "items"},
        "output": {"item_count": "number"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Ulangi langkah dalam untuk tiap item (maks 20 item).",
    },
    "save_memory": {
        "inputs": {"fact": "text", "source": "choice"},
        "output": {"saved": "boolean"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Simpan fakta ke profil/episodik (asal: user_stated).",
    },
    "save_to_module_table": {
        "inputs": {"modules": "text", "table": "text", "rows": "items"},
        "output": {"saved": "number"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "spending_limit": 0},
        "outbound_action": False,
        "cost_rp": 0,
        "description": "Simpan baris ke tabel milik modul (modul itu sendiri).",
    },
}

# Batas MVP (§2.12).
MAX_STEPS = 10
MAX_BRANCHES = 2
MAX_ITEMS_PER_FOR_EACH = 20
MAX_ACTIVE_WORKFLOWS = 5
CEILING_COST_PER_RUN_RP = 5000


class BlockError(Exception):
    """Blok tidak dikenal, masukan tidak lolos skema, atau batas dilanggar."""


def list_blocks() -> list[str]:
    """Nama 15 blok pustaka, terurut."""
    return sorted(BLOCKS)


def block_definition(name: str) -> dict:
    """Definisi satu blok; `BlockError` bila nama tidak ada di pustaka."""
    if name not in BLOCKS:
        raise BlockError(f"blok {name!r} tidak ada di pustaka (15 blok MVP)")
    return BLOCKS[name]


def validate_inputs(name: str, inputs: dict) -> list[str]:
    """Periksa masukan langkah terhadap skema blok. Kembalikan daftar
    field yang hilang (kosong = lolos). Tipe nilai tidak dicek di sini
    (nilai datang dari data alur, dicek lagi saat jalan)."""
    definition = block_definition(name)
    if not isinstance(inputs, dict):
        return ["masukan harus objek"]
    return [f for f in definition["inputs"] if f not in inputs]


def validate_step(step: dict) -> list[str]:
    """Periksa satu langkah alur: blok dikenal + masukan lengkap +
    percabangan berstruktur benar. Kembalikan daftar masalah."""
    problems: list[str] = []
    if not isinstance(step, dict):
        return ["langkah harus objek"]
    name = step.get("block")
    if not isinstance(name, str) or name not in BLOCKS:
        return [f"blok {name!r} tidak ada di pustaka"]
    for f in validate_inputs(name, step.get("inputs", {})):
        problems.append(f"langkah {name}: masukan kurang {f!r}")
    if name == "condition":
        for branch in ("then", "else"):
            content = step.get(branch, [])
            if not isinstance(content, list):
                problems.append(f"langkah kondisi: {branch!r} harus daftar langkah")
            else:
                for sub in content:
                    problems += validate_step(sub)
    if name == "for_each":
        content = step.get("nested_steps", [])
        if not isinstance(content, list):
            problems.append("langkah for_each: 'step_dalam' harus daftar langkah")
        else:
            for sub in content:
                problems += validate_step(sub)
    return problems


def branch_depth(step: list) -> int:
    """Tingkat percabangan terdalam (kondisi di dalam kondisi, dst.)."""
    depth = 0
    for step_item in step:
        if not isinstance(step_item, dict):
            continue
        if step_item.get("block") == "condition":
            branch = max(
                [branch_depth(step_item.get("then", [])),
                 branch_depth(step_item.get("else", []))] or [0])
            depth = max(depth, 1 + branch)
        elif step_item.get("block") == "for_each":
            depth = max(depth, branch_depth(step_item.get("nested_steps", [])))
    return depth


def validate_workflow(step: list) -> list[str]:
    """Periksa seluruh daftar langkah alur terhadap batas MVP.
    Kembalikan daftar masalah (kosong = lolos)."""
    problems: list[str] = []
    if not isinstance(step, list) or not step:
        return ["alur harus punya minimal 1 langkah"]
    if len(step) > MAX_STEPS:
        problems.append(
            f"alur punya {len(step)} langkah, maks {MAX_STEPS} di MVP")
    for step_item in step:
        problems += validate_step(step_item)
    branch = branch_depth(step)
    if branch > MAX_BRANCHES:
        problems.append(
            f"percabangan {branch} tingkat, maks {MAX_BRANCHES} di MVP")
    return problems
