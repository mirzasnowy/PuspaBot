"""Tes penyusun alur lewat chat (§2.12) + manifes izin turunan."""

from puspa.workflow.composer import kartu_langkah, susun_draf
from puspa.workflow.manifest import format_manifes, hitung_manifes, subset_izin


def test_susun_contoh_definisi(make_user, test_dsn):
    user_id, _ = make_user()
    draf = susun_draf(
        "tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, siapkan bayar",
        user_id=user_id, dsn=test_dsn)
    assert draf["pemicu"] == {"jenis": "jadwal", "mode": "tanggal",
                              "tanggal": 25, "jam_mulai": "07:00"}
    blok = [l["blok"] for l in draf["langkah"]]
    assert blok == ["cek_tagihan", "cek_tagihan", "kirim_notif", "siapkan_bayar"]
    assert draf["kartu"][0]["blok"] == "cek_tagihan"
    assert draf["permintaan_blok"] == []


def test_susun_rekap_semua_tagihan(make_user, test_dsn):
    user_id, _ = make_user()
    draf = susun_draf("ingatkan semua tagihan yang jatuh tempo minggu ini",
                      user_id=user_id, dsn=test_dsn)
    assert draf["pemicu"]["jenis"] in ("jadwal", "jatuh_tempo")
    blok = [l["blok"] for l in draf["langkah"]]
    assert blok.count("cek_tagihan") == 4
    assert blok[-1] == "kirim_notif"


def test_susun_blok_belum_ada_dicatat_permintaan_blok(make_user, test_dsn):
    user_id, _ = make_user()
    draf = susun_draf("tiap tanggal 25 cek PLN, lalu kirim email ke ibu",
                      user_id=user_id, dsn=test_dsn)
    assert "kirim_email" in draf["permintaan_blok"]
    assert "belum ada" in draf["pesan"]
    # Tercatat di tabel permintaan_blok Fase 3 (bahan peta jalan).
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM permintaan_blok"
                        " WHERE user_id = %s AND blok = 'kirim_email'",
                        (user_id,))
            assert cur.fetchone()[0] == 1


def test_susun_tidak_paham_memberi_pesan_jujur(make_user, test_dsn):
    user_id, _ = make_user()
    draf = susun_draf("asdf qwer zxcv", user_id=user_id, dsn=test_dsn)
    assert draf["langkah"] == []
    assert "belum paham" in draf["pesan"]


def test_kartu_langkah_tandai_aksi_keluar():
    kartu = kartu_langkah({"blok": "siapkan_bayar",
                           "masukan": {"provider": "PLN", "jumlah": 0, "tujuan": ""},
                           "judul": "Siapkan bayar"})
    assert kartu["aksi_keluar"] is True
    kartu2 = kartu_langkah({"blok": "cek_tagihan",
                            "masukan": {"provider": "PLN", "nomor_pelanggan": ""}})
    assert kartu2["aksi_keluar"] is False


def test_manifes_turunan_gabungan_blok():
    langkah = [
        {"blok": "cek_tagihan", "masukan": {"provider": "PLN", "nomor_pelanggan": ""}},
        {"blok": "baca_email", "masukan": {"email_id": "x"}},
        {"blok": "siapkan_bayar", "masukan": {"provider": "PLN", "jumlah": 132500,
                                              "tujuan": "PLN"}},
    ]
    manifes = hitung_manifes(langkah)
    assert manifes["konektor"] == ["gmail"]
    assert manifes["scope"] == ["gmail.readonly"]
    assert "situs_provider_tagihan" in manifes["domain"]
    assert manifes["batas_belanja"] == 132500


def test_manifes_turunan_masuk_kondisi_dan_untuk_tiap():
    langkah = [
        {"blok": "kondisi", "masukan": {"ungkapan": "a == b"},
         "maka": [{"blok": "buat_event",
                   "masukan": {"judul": "x", "waktu": "2026-11-20", "peserta": []}}],
         "kalau_tidak": []},
    ]
    manifes = hitung_manifes(langkah)
    assert manifes["konektor"] == ["calendar"]
    assert manifes["scope"] == ["calendar.events"]


def test_subset_izin_kurang_konektor_ditolak():
    manifes = {"konektor": ["gmail"], "scope": ["gmail.readonly"],
               "domain": [], "batas_belanja": 0, "blok": ["baca_email"]}
    lolos, kurang = subset_izin(manifes, {"konektor": [], "scope": [],
                                          "domain": [], "batas_belanja": 0})
    assert lolos is False
    assert kurang and "gmail" in kurang[0]


def test_subset_izin_batas_belanja_ditolak():
    manifes = {"konektor": [], "scope": [], "domain": [],
               "batas_belanja": 500000, "blok": ["siapkan_bayar"]}
    lolos, kurang = subset_izin(manifes, {"konektor": [], "scope": [],
                                          "domain": [], "batas_belanja": 0})
    assert lolos is False
    assert any("batas belanja" in k for k in kurang)


def test_format_manifes_bisa_dibaca_user():
    teks = format_manifes({"konektor": ["gmail"], "scope": ["gmail.readonly"],
                           "domain": [], "batas_belanja": 0,
                           "blok": ["baca_email"]})
    assert "gmail" in teks and "Rp0" in teks
