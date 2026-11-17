"""Tes metrik alur (§3.8) + panel Alur di PWA."""

from puspa.workflow.composer import kartu_langkah
from puspa.workflow.metrics import metrik_alur
from puspa.workflow.panel import hasil_jalan_html, kartu_langkah_html, panel_alur_html
from puspa.workflow.runner import aktifkan_alur, buat_alur, jalankan_alur, uji_kering
from puspa.workflow.templates import buat_dari_templat


def test_metrik_alur_awal_tertunda():
    m = metrik_alur()
    assert m["pengguna_dengan_alur_aktif"] == 0
    assert m["status_pengguna_aktif"] == "tertunda"
    assert "pengguna uji" in m["alasan_pengguna_aktif"]
    assert m["jalan_total"] == 0
    assert m["persen_jalan_tanpa_campur"] is None


def test_metrik_alur_menghitung_jalan_tanpa_campur(make_user, test_dsn,
                                                  izin_user_penuh):
    user_id, agen_id = make_user()
    alur = buat_dari_templat("rekap_tagihan_mingguan", user_id=user_id,
                             agen_id=agen_id, dsn=test_dsn)
    kering = uji_kering(alur["id"], user_id=user_id, dsn=test_dsn)
    aktifkan_alur(alur["id"], user_id=user_id, izin_user=izin_user_penuh,
                  hasil_kering=kering, dsn=test_dsn)
    jalankan_alur(alur["id"], user_id=user_id, dsn=test_dsn)
    m = metrik_alur(dsn=test_dsn)
    assert m["pengguna_dengan_alur_aktif"] == 1
    # 2 jalan tercatat: 1 uji kering + 1 jalan nyata — keduanya selesai.
    assert m["jalan_total"] == 2
    assert m["jalan_selesai_tanpa_campur"] == 2
    assert m["persen_jalan_tanpa_campur"] == 100.0
    assert m["status_pengguna_aktif"] == "tertunda"  # butuh 5 pengguna uji


def test_panel_kartu_langkah_escape_html():
    kartu = kartu_langkah({"blok": "kirim_notif",
                           "masukan": {"pesan": "<b>hai</b>"},
                           "judul": "Ingatkan <aku>"})
    html = kartu_langkah_html(kartu)
    assert "<b>hai</b>" not in html
    assert "&lt;b&gt;hai&lt;/b&gt;" not in html  # pesan tidak dirender di kartu
    assert "Ingatkan &lt;aku&gt;" in html
    assert "kirim_notif" in html


def test_panel_alur_dan_hasil_jalan():
    html = panel_alur_html(
        nama="Tagihan bulanan", pemicu={"jenis": "jadwal"},
        kartu=[kartu_langkah({"blok": "cek_tagihan",
                              "masukan": {"provider": "PLN",
                                          "nomor_pelanggan": ""},
                              "judul": "Cek PLN"})],
        manifes_teks="Konektor: tidak ada", pesan="Siap diaktifkan.")
    assert "Tagihan bulanan" in html and "cek_tagihan" in html
    assert "Siap diaktifkan." in html
    hasil = hasil_jalan_html({
        "status": "selesai",
        "langkah_hasil": [{"blok": "cek_tagihan", "sukses": True},
                          {"blok": "siapkan_bayar", "sukses": True,
                           "simulasi": True}],
        "biaya_rp": 0, "alasan": ""})
    assert "simulasi (uji kering)" in hasil
    assert "Rp0" in hasil
