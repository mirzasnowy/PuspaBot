"""Tes pengukuran gerbang 11 + 12 (parsial, yang bisa diukur di Fase 2).

Gerbang 11 (agen + sub-agen): buat agen ke-2 dari PWA, pola 3 Pembaca
+ 1 Pemeriksa, Pemeriksa tangkap harga salah 3/3, handoff macet
dilaporkan < 2 menit, Puspa tidak bisa dihapus, token di luar
ditolak 3/3, handoff melingkar ditolak, Hentikan < 5 detik.
Gerbang 12 (tulang punggung + bukti): rantai hash yang diubah
terdeteksi 3/3, angka yatim ditahan 3/3, kaset 9/10 identik.
"""

import psycopg
import pytest

from puspa.evidence.renderer import sajikan
from puspa.evidence.verifier import periksa_suntikan
from puspa.evidence.evidence import catat_bukti
from puspa.runtime.handoff import tolak_lingkaran, HandoffError
from puspa.runtime.subagents import SubagentPool, SubagentError


def test_gerbang11_pemeriksa_tangkap_suntikan_3_dari_3():
    def baca_ulang(url):
        return {"nilai": 12500000, "nama": "Laptop X"}
    for nilai_salah in (9999999, 15000000, 100000):
        b = catat_bukti(tugas_id="t1", klaim="Laptop X", nilai=nilai_salah,
                        satuan="Rp", sumber_url="https://tokopedia.com/produk/laptop-x",
                        potongan_teks="x", id_pembaca="sub-1")
        assert periksa_suntikan(b, nilai_salah=nilai_salah, baca_ulang=baca_ulang) != "Terverifikasi"


def test_gerbang11_token_di_luar_ditolak_3_dari_3(token_payloads):
    kolam = SubagentPool()
    for tools in (["hapus_semua"], ["baca", "kirim_tanpa_izin"], ["bayar"]):
        bukan_subset = dict(token_payloads["sub"], tools=tools)
        with pytest.raises(SubagentError):
            kolam.spawn(agen_id="agen-1", tugas_id="t1", kelas_izin="Pelaksana",
                        token_agen=token_payloads["agen"], token_subagen=bukan_subset)


def test_gerbang11_handoff_melingkar_ditolak():
    with pytest.raises(HandoffError):
        tolak_lingkaran(["puspa", "agen-riset"], "agen-riset", "puspa")


def test_gerbang12_rantai_hash_diubah_terdeteksi_3_dari_3(ledger, make_user):
    u = make_user()
    for i in range(3):
        tugas_id = f"tugas-rantai-{i}"
        ledger.append_event(tugas_id=tugas_id, agen_id=u["agen_id"],
                            jenis="tugas_dibuat", isi={"tujuan": "uji"})
        ledger.append_event(tugas_id=tugas_id, agen_id=u["agen_id"],
                            jenis="tugas_selesai", isi={})
        ok_sebelum, _ = ledger.verify_chain(tugas_id)
        assert ok_sebelum is True
        # Ubah diam-diam satu hash (sebagai pemilik DB; nilai unik per tugas).
        with psycopg.connect(ledger.dsn) as conn:
            conn.execute(
                "UPDATE kejadian SET hash = %s WHERE tugas_id = %s AND jenis = 'tugas_dibuat'",
                (f"00diubah{i}", tugas_id,))
            conn.commit()
        ok_sesudah, _ = ledger.verify_chain(tugas_id)
        assert ok_sesudah is False


def test_gerbang12_angka_yatim_ditahan_3_dari_3():
    for teks in ("Harganya Rp9.999.000.", "Tagihan Rp132.500 jatuh tempo 20 Nov.",
                 "Selisihnya 15% lebih murah."):
        hasil = sajikan(teks, {})
        assert hasil["ditahan"] is True and hasil["dibuang"]
