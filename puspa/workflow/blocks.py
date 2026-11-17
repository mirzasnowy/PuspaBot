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
BLOCK_ACTION_LOGOUT = ("create_event", "prepare_payment")

# Domain yang boleh dibaca tiap blok baca (allowlist, §2.6). Tidak ada
# panggilan HTTP bebas di alur (§2.12): blok di luar daftar ini tidak
# boleh memanggil HTTP sama sekali.
DOMAIN_BLOCK = {
    "compare_prices": ("tokopedia.com", "shopee.co.id", "blibli.com"),
    "watch_prices": ("tokopedia.com", "shopee.co.id", "blibli.com"),
    "check_bill": ("situs_provider_bill",),
}

# Satu definisi per blok. `masukan`/`keluaran`: nama field -> tipe
# sederhana ("teks", "angka", "tanggal", "pilihan", "daftar", "uang",
# "ya_tidak"). `izin`: konektor, scope, domain, batas_belanja (Rp).
BLOK = {
    "check_bill": {
        "masukan": {"provider": "choice", "number_pelanggan": "text"},
        "output": {"amount": "uang", "due_date": "tanggal",
                     "status": "choice", "evidence_id": "text", "vonis": "choice"},
        "permission_class": "Pembaca",
        "permission": {"connectors": [], "scope": [], "domain": ["situs_provider_bill"],
                 "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Cek jumlah + jatuh tempo + status tagihan satu provider.",
    },
    "compare_prices": {
        "masukan": {"name_product": "text"},
        "output": {"termurah": "uang", "price": "items", "selisih": "uang",
                     "vonis": "choice"},
        "permission_class": "Pembaca",
        "permission": {"connectors": [], "scope": [],
                 "domain": ["tokopedia.com", "shopee.co.id", "blibli.com"],
                 "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Bandingkan harga barang baru di Tokopedia, Shopee, Blibli.",
    },
    "watch_prices": {
        "masukan": {"product_id": "text", "price_threshold": "uang"},
        "output": {"price_kini": "uang", "di_bawah_ambang": "ya_not",
                     "evidence_id": "text"},
        "permission_class": "Pembaca",
        "permission": {"connectors": [], "scope": [],
                 "domain": ["tokopedia.com", "shopee.co.id", "blibli.com"],
                 "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Baca harga barang di watchlist; benar bila di bawah ambang.",
    },
    "search_email": {
        "masukan": {"kata_key": "text"},
        "output": {"email": "items"},
        "permission_class": "Pembaca",
        "permission": {"connectors": ["gmail"], "scope": ["gmail.readonly"],
                 "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Cari email berdasarkan kata kunci (hanya metadata + ringkasan).",
    },
    "read_email": {
        "masukan": {"email_id": "text"},
        "output": {"pengirim": "text", "subjek": "text", "content": "text"},
        "permission_class": "Pembaca",
        "permission": {"connectors": ["gmail"], "scope": ["gmail.readonly"],
                 "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Baca satu email lewat Pembaca terkarantina (§2.6).",
    },
    "create_draft": {
        "masukan": {"penerima": "text", "subjek": "text", "content": "text"},
        "output": {"draft_id": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": ["gmail"], "scope": ["gmail.compose"],
                 "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Buat draf balasan email. Draf BUKAN kirim; kirim tetap butuh persetujuan.",
    },
    "create_event": {
        "masukan": {"title": "text", "created_at": "tanggal", "peserta": "items"},
        "output": {"event_id": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": ["calendar"], "scope": ["calendar.events"],
                 "domain": [], "batas_belanja": 0},
        "action_logout": True,
        "cost_rp": 0,
        "deskripsi": "Buat event Calendar. Aksi keluar: masuk antrean persetujuan.",
    },
    "prepare_payment": {
        "masukan": {"provider": "choice", "amount": "uang", "purpose": "text"},
        "output": {"draft_pay_id": "text", "content_hash": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [],
                 "batas_belanja": 0},
        "action_logout": True,
        "cost_rp": 0,
        "deskripsi": "Siapkan bayar mode aman: DRAF saja, user menyetujui di bank/e-wallet sendiri.",
    },
    "send_notification": {
        "masukan": {"message": "text"},
        "output": {"terkirim": "ya_not"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Kirim pengingat dalam aplikasi (Web Push). Bukan kirim email/SMS.",
    },
    "ask_user": {
        "masukan": {"pertanyaan": "text"},
        "output": {"jawaban": "text"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Berhenti dan bertanya ke user; jalan alur lanjut setelah dijawab.",
    },
    "wait_for_approval": {
        "masukan": {"action": "text"},
        "output": {"disetujui": "ya_not"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Tunggu persetujuan user atas satu aksi yang antre.",
    },
    "condition": {
        "masukan": {"ungkapan": "text"},
        "output": {"correct": "ya_not"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Percabangan: jalankan langkah `maka` bila benar, `kalau_not` bila salah.",
    },
    "for_each": {
        "masukan": {"items": "items"},
        "output": {"amount_item": "number"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Ulangi langkah dalam untuk tiap item (maks 20 item).",
    },
    "save_memory": {
        "masukan": {"fact": "text", "source": "choice"},
        "output": {"tersimpan": "ya_not"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Simpan fakta ke profil/episodik (asal: user_stated).",
    },
    "save_to_module_table": {
        "masukan": {"modules": "text", "table": "text", "rows": "items"},
        "output": {"tersimpan": "number"},
        "permission_class": "Pelaksana",
        "permission": {"connectors": [], "scope": [], "domain": [], "batas_belanja": 0},
        "action_logout": False,
        "cost_rp": 0,
        "deskripsi": "Simpan baris ke tabel milik modul (modul itu sendiri).",
    },
}

# Batas MVP (§2.12).
MAKS_STEP = 10
MAKS_CABANG = 2
MAKS_ITEM_FOR_EACH = 20
MAKS_WORKFLOW_ACTIVE = 5
CEILING_COST_JALAN_RP = 5000


class BlockError(Exception):
    """Blok tidak dikenal, masukan tidak lolos skema, atau batas dilanggar."""


def items_block() -> list[str]:
    """Nama 15 blok pustaka, terurut."""
    return sorted(BLOK)


def definisi_block(name: str) -> dict:
    """Definisi satu blok; `BlockError` bila nama tidak ada di pustaka."""
    if name not in BLOK:
        raise BlockError(f"blok {name!r} tidak ada di pustaka (15 blok MVP)")
    return BLOK[name]


def periksa_masukan(name: str, masukan: dict) -> list[str]:
    """Periksa masukan langkah terhadap skema blok. Kembalikan daftar
    field yang hilang (kosong = lolos). Tipe nilai tidak dicek di sini
    (nilai datang dari data alur, dicek lagi saat jalan)."""
    definisi = definisi_block(name)
    if not isinstance(masukan, dict):
        return ["masukan harus objek"]
    return [f for f in definisi["masukan"] if f not in masukan]


def periksa_step(step: dict) -> list[str]:
    """Periksa satu langkah alur: blok dikenal + masukan lengkap +
    percabangan berstruktur benar. Kembalikan daftar masalah."""
    masalah: list[str] = []
    if not isinstance(step, dict):
        return ["langkah harus objek"]
    name = step.get("block")
    if not isinstance(name, str) or name not in BLOK:
        return [f"blok {name!r} tidak ada di pustaka"]
    for f in periksa_masukan(name, step.get("masukan", {})):
        masalah.append(f"langkah {name}: masukan kurang {f!r}")
    if name == "condition":
        for cabang in ("maka", "kalau_not"):
            content = step.get(cabang, [])
            if not isinstance(content, list):
                masalah.append(f"langkah kondisi: {cabang!r} harus daftar langkah")
            else:
                for sub in content:
                    masalah += periksa_step(sub)
    if name == "for_each":
        content = step.get("step_dalam", [])
        if not isinstance(content, list):
            masalah.append("langkah for_each: 'step_dalam' harus daftar langkah")
        else:
            for sub in content:
                masalah += periksa_step(sub)
    return masalah


def kedalaman_cabang(step: list) -> int:
    """Tingkat percabangan terdalam (kondisi di dalam kondisi, dst.)."""
    dalam = 0
    for l in step:
        if not isinstance(l, dict):
            continue
        if l.get("block") == "condition":
            cabang = max(
                [kedalaman_cabang(l.get("maka", [])),
                 kedalaman_cabang(l.get("kalau_not", []))] or [0])
            dalam = max(dalam, 1 + cabang)
        elif l.get("block") == "for_each":
            dalam = max(dalam, kedalaman_cabang(l.get("step_dalam", [])))
    return dalam


def periksa_workflow(step: list) -> list[str]:
    """Periksa seluruh daftar langkah alur terhadap batas MVP.
    Kembalikan daftar masalah (kosong = lolos)."""
    masalah: list[str] = []
    if not isinstance(step, list) or not step:
        return ["alur harus punya minimal 1 langkah"]
    if len(step) > MAKS_STEP:
        masalah.append(
            f"alur punya {len(step)} langkah, maks {MAKS_STEP} di MVP")
    for l in step:
        masalah += periksa_step(l)
    cabang = kedalaman_cabang(step)
    if cabang > MAKS_CABANG:
        masalah.append(
            f"percabangan {cabang} tingkat, maks {MAKS_CABANG} di MVP")
    return masalah
