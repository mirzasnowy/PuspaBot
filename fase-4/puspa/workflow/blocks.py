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
KELAS_IZIN = ("Pembaca", "Pelaksana", "Pemeriksa")

# Blok yang memicu aksi keluar (§2.6): kirim, buat event, siapkan
# bayar. Ketiganya WAJIB lewat antrean persetujuan di runner.
BLOK_AKSI_KELUAR = ("buat_event", "siapkan_bayar")

# Domain yang boleh dibaca tiap blok baca (allowlist, §2.6). Tidak ada
# panggilan HTTP bebas di alur (§2.12): blok di luar daftar ini tidak
# boleh memanggil HTTP sama sekali.
DOMAIN_BLOK = {
    "bandingkan_harga": ("tokopedia.com", "shopee.co.id", "blibli.com"),
    "pantau_harga": ("tokopedia.com", "shopee.co.id", "blibli.com"),
    "cek_tagihan": ("situs_provider_tagihan",),
}

# Satu definisi per blok. `masukan`/`keluaran`: nama field -> tipe
# sederhana ("teks", "angka", "tanggal", "pilihan", "daftar", "uang",
# "ya_tidak"). `izin`: konektor, scope, domain, batas_belanja (Rp).
BLOK = {
    "cek_tagihan": {
        "masukan": {"provider": "pilihan", "nomor_pelanggan": "teks"},
        "keluaran": {"jumlah": "uang", "jatuh_tempo": "tanggal",
                     "status": "pilihan", "bukti_id": "teks", "vonis": "pilihan"},
        "kelas_izin": "Pembaca",
        "izin": {"konektor": [], "scope": [], "domain": ["situs_provider_tagihan"],
                 "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Cek jumlah + jatuh tempo + status tagihan satu provider.",
    },
    "bandingkan_harga": {
        "masukan": {"nama_barang": "teks"},
        "keluaran": {"termurah": "uang", "harga": "daftar", "selisih": "uang",
                     "vonis": "pilihan"},
        "kelas_izin": "Pembaca",
        "izin": {"konektor": [], "scope": [],
                 "domain": ["tokopedia.com", "shopee.co.id", "blibli.com"],
                 "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Bandingkan harga barang baru di Tokopedia, Shopee, Blibli.",
    },
    "pantau_harga": {
        "masukan": {"produk_id": "teks", "ambang_harga": "uang"},
        "keluaran": {"harga_kini": "uang", "di_bawah_ambang": "ya_tidak",
                     "bukti_id": "teks"},
        "kelas_izin": "Pembaca",
        "izin": {"konektor": [], "scope": [],
                 "domain": ["tokopedia.com", "shopee.co.id", "blibli.com"],
                 "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Baca harga barang di watchlist; benar bila di bawah ambang.",
    },
    "cari_email": {
        "masukan": {"kata_kunci": "teks"},
        "keluaran": {"email": "daftar"},
        "kelas_izin": "Pembaca",
        "izin": {"konektor": ["gmail"], "scope": ["gmail.readonly"],
                 "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Cari email berdasarkan kata kunci (hanya metadata + ringkasan).",
    },
    "baca_email": {
        "masukan": {"email_id": "teks"},
        "keluaran": {"pengirim": "teks", "subjek": "teks", "isi": "teks"},
        "kelas_izin": "Pembaca",
        "izin": {"konektor": ["gmail"], "scope": ["gmail.readonly"],
                 "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Baca satu email lewat Pembaca terkarantina (§2.6).",
    },
    "buat_draf": {
        "masukan": {"penerima": "teks", "subjek": "teks", "isi": "teks"},
        "keluaran": {"draf_id": "teks"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": ["gmail"], "scope": ["gmail.compose"],
                 "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Buat draf balasan email. Draf BUKAN kirim; kirim tetap butuh persetujuan.",
    },
    "buat_event": {
        "masukan": {"judul": "teks", "waktu": "tanggal", "peserta": "daftar"},
        "keluaran": {"event_id": "teks"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": ["calendar"], "scope": ["calendar.events"],
                 "domain": [], "batas_belanja": 0},
        "aksi_keluar": True,
        "biaya_rp": 0,
        "deskripsi": "Buat event Calendar. Aksi keluar: masuk antrean persetujuan.",
    },
    "siapkan_bayar": {
        "masukan": {"provider": "pilihan", "jumlah": "uang", "tujuan": "teks"},
        "keluaran": {"draf_bayar_id": "teks", "isi_hash": "teks"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [],
                 "batas_belanja": 0},
        "aksi_keluar": True,
        "biaya_rp": 0,
        "deskripsi": "Siapkan bayar mode aman: DRAF saja, user menyetujui di bank/e-wallet sendiri.",
    },
    "kirim_notif": {
        "masukan": {"pesan": "teks"},
        "keluaran": {"terkirim": "ya_tidak"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Kirim pengingat dalam aplikasi (Web Push). Bukan kirim email/SMS.",
    },
    "tanya_user": {
        "masukan": {"pertanyaan": "teks"},
        "keluaran": {"jawaban": "teks"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Berhenti dan bertanya ke user; jalan alur lanjut setelah dijawab.",
    },
    "tunggu_persetujuan": {
        "masukan": {"aksi": "teks"},
        "keluaran": {"disetujui": "ya_tidak"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Tunggu persetujuan user atas satu aksi yang antre.",
    },
    "kondisi": {
        "masukan": {"ungkapan": "teks"},
        "keluaran": {"benar": "ya_tidak"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Percabangan: jalankan langkah `maka` bila benar, `kalau_tidak` bila salah.",
    },
    "untuk_tiap": {
        "masukan": {"daftar": "daftar"},
        "keluaran": {"jumlah_item": "angka"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Ulangi langkah dalam untuk tiap item (maks 20 item).",
    },
    "simpan_memori": {
        "masukan": {"fakta": "teks", "asal": "pilihan"},
        "keluaran": {"tersimpan": "ya_tidak"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Simpan fakta ke profil/episodik (asal: diucapkan_user).",
    },
    "simpan_ke_tabel_modul": {
        "masukan": {"modul": "teks", "tabel": "teks", "baris": "daftar"},
        "keluaran": {"tersimpan": "angka"},
        "kelas_izin": "Pelaksana",
        "izin": {"konektor": [], "scope": [], "domain": [], "batas_belanja": 0},
        "aksi_keluar": False,
        "biaya_rp": 0,
        "deskripsi": "Simpan baris ke tabel milik modul (modul itu sendiri).",
    },
}

# Batas MVP (§2.12).
MAKS_LANGKAH = 10
MAKS_CABANG = 2
MAKS_ITEM_UNTUK_TIAP = 20
MAKS_ALUR_AKTIF = 5
PLAFON_BIAYA_JALAN_RP = 5000


class BlockError(Exception):
    """Blok tidak dikenal, masukan tidak lolos skema, atau batas dilanggar."""


def daftar_blok() -> list[str]:
    """Nama 15 blok pustaka, terurut."""
    return sorted(BLOK)


def definisi_blok(nama: str) -> dict:
    """Definisi satu blok; `BlockError` bila nama tidak ada di pustaka."""
    if nama not in BLOK:
        raise BlockError(f"blok {nama!r} tidak ada di pustaka (15 blok MVP)")
    return BLOK[nama]


def periksa_masukan(nama: str, masukan: dict) -> list[str]:
    """Periksa masukan langkah terhadap skema blok. Kembalikan daftar
    field yang hilang (kosong = lolos). Tipe nilai tidak dicek di sini
    (nilai datang dari data alur, dicek lagi saat jalan)."""
    definisi = definisi_blok(nama)
    if not isinstance(masukan, dict):
        return ["masukan harus objek"]
    return [f for f in definisi["masukan"] if f not in masukan]


def periksa_langkah(langkah: dict) -> list[str]:
    """Periksa satu langkah alur: blok dikenal + masukan lengkap +
    percabangan berstruktur benar. Kembalikan daftar masalah."""
    masalah: list[str] = []
    if not isinstance(langkah, dict):
        return ["langkah harus objek"]
    nama = langkah.get("blok")
    if not isinstance(nama, str) or nama not in BLOK:
        return [f"blok {nama!r} tidak ada di pustaka"]
    for f in periksa_masukan(nama, langkah.get("masukan", {})):
        masalah.append(f"langkah {nama}: masukan kurang {f!r}")
    if nama == "kondisi":
        for cabang in ("maka", "kalau_tidak"):
            isi = langkah.get(cabang, [])
            if not isinstance(isi, list):
                masalah.append(f"langkah kondisi: {cabang!r} harus daftar langkah")
            else:
                for sub in isi:
                    masalah += periksa_langkah(sub)
    if nama == "untuk_tiap":
        isi = langkah.get("langkah_dalam", [])
        if not isinstance(isi, list):
            masalah.append("langkah untuk_tiap: 'langkah_dalam' harus daftar langkah")
        else:
            for sub in isi:
                masalah += periksa_langkah(sub)
    return masalah


def kedalaman_cabang(langkah: list) -> int:
    """Tingkat percabangan terdalam (kondisi di dalam kondisi, dst.)."""
    dalam = 0
    for l in langkah:
        if not isinstance(l, dict):
            continue
        if l.get("blok") == "kondisi":
            cabang = max(
                [kedalaman_cabang(l.get("maka", [])),
                 kedalaman_cabang(l.get("kalau_tidak", []))] or [0])
            dalam = max(dalam, 1 + cabang)
        elif l.get("blok") == "untuk_tiap":
            dalam = max(dalam, kedalaman_cabang(l.get("langkah_dalam", [])))
    return dalam


def periksa_alur(langkah: list) -> list[str]:
    """Periksa seluruh daftar langkah alur terhadap batas MVP.
    Kembalikan daftar masalah (kosong = lolos)."""
    masalah: list[str] = []
    if not isinstance(langkah, list) or not langkah:
        return ["alur harus punya minimal 1 langkah"]
    if len(langkah) > MAKS_LANGKAH:
        masalah.append(
            f"alur punya {len(langkah)} langkah, maks {MAKS_LANGKAH} di MVP")
    for l in langkah:
        masalah += periksa_langkah(l)
    cabang = kedalaman_cabang(langkah)
    if cabang > MAKS_CABANG:
        masalah.append(
            f"percabangan {cabang} tingkat, maks {MAKS_CABANG} di MVP")
    return masalah
