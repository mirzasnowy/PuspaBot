"""Tes anggaran + siklus tugas 5 tahap (§2.4)."""

import pytest

from puspa.runtime.budget import (AnggaranHabis, BudgetTracker,
                                  kalibrasi_dari_ledger)
from puspa.runtime.tasks import (Langkah, Task, TaskRunner, buat_rencana,
                                 TaskError)


def _langkah(nama="cari", jenis_keluar="hasil"):
    return Langkah(nama=nama, masukan={"jenis": "cari", "q": "x"},
                   keluaran_diharapkan={"jenis": jenis_keluar},
                   syarat_selesai="hasil ditemukan")


def test_anggaran_langkah_dan_rupiah():
    b = BudgetTracker(rupiah_max=2000, langkah_max=25)
    b.catat_langkah(25)
    with pytest.raises(AnggaranHabis):
        b.catat_langkah(1)
    b2 = BudgetTracker()
    b2.catat_biaya(1999)
    with pytest.raises(AnggaranHabis):
        b2.catat_biaya(2)


def test_anggaran_subagen_menyempit_ke_bawah():
    b = BudgetTracker(rupiah_max=2000, langkah_max=25)
    b.catat_langkah(20)
    b.catat_biaya(1500)
    sub = b.anggaran_subagen()
    assert sub["langkah_max"] == 5
    assert sub["rupiah_max"] == 500


def test_kalibrasi_dari_ledger_tanpa_data_jujur():
    hasil = kalibrasi_dari_ledger([])
    assert hasil["jumlah_tugas"] == 0 and hasil["rata_rata_rp"] is None


def test_kalibrasi_dari_ledger_dengan_data():
    kejadian = [
        {"tugas_id": "t1", "isi": {"biaya_rp": 500}},
        {"tugas_id": "t1", "isi": {"biaya_rp": 300}},
        {"tugas_id": "t2", "isi": {"biaya_rp": 1200}},
    ]
    hasil = kalibrasi_dari_ledger(kejadian)
    assert hasil["jumlah_tugas"] == 2
    assert hasil["rata_rata_rp"] == 1000
    assert hasil["maks_rp"] == 1200


def test_siklus_lima_tahap_selesai():
    runner = TaskRunner(pelaksana=lambda nama, masukan: {"jenis": "hasil", "nilai": 42})
    task = runner.jalankan(Task(tugas_id="tugas-1", user_id="u1", agen_id="puspa",
                                tujuan="riset dan rangkum lima artikel",
                                rencana=[_langkah("cari"), _langkah("rangkum")]))
    assert task.status == "selesai" and task.tahap == "sajikan"
    jenis = [e["jenis"] for e in runner.kejadian_memori()]
    for wajib in ("tugas_dibuat", "rencana_dibuat", "handoff_diajukan",
                  "handoff_diserahkan", "pemeriksaan_selesai", "tugas_selesai"):
        assert wajib in jenis


def test_tujuan_ambigu_tanya_satu_pertanyaan():
    runner = TaskRunner()
    task = runner.jalankan(Task(tugas_id="tugas-2", user_id="u1", agen_id="puspa",
                                tujuan="cek harga"))
    assert task.status == "menunggu_user"
    assert task.pertanyaan  # maks 1 pertanyaan


def test_rencana_aksi_keluar_berhenti_di_persetujuan():
    lg = _langkah("kirim")
    lg.aksi_keluar = True
    runner = TaskRunner()
    task = runner.jalankan(Task(tugas_id="tugas-3", user_id="u1", agen_id="puspa",
                                tujuan="kirim email ke tim keuangan",
                                rencana=[lg]))
    assert task.status == "menunggu_persetujuan"
    assert task.hasil == {}


def test_langkah_tidak_lolos_skema_tugas_gagal():
    runner = TaskRunner(pelaksana=lambda nama, masukan: {"jenis": "salah"})
    task = runner.jalankan(Task(tugas_id="tugas-4", user_id="u1", agen_id="puspa",
                                tujuan="riset dan rangkum lima artikel",
                                rencana=[_langkah()]))
    assert task.status == "gagal"


def test_anggaran_habis_tugas_berhenti_dan_melapor():
    budget = BudgetTracker(rupiah_max=2000, langkah_max=1)
    runner = TaskRunner(budget=budget,
                        pelaksana=lambda nama, masukan: {"jenis": "hasil", "nilai": 1})
    task = runner.jalankan(Task(tugas_id="tugas-5", user_id="u1", agen_id="puspa",
                                tujuan="riset dan rangkum lima artikel",
                                rencana=[_langkah("a"), _langkah("b")]))
    assert task.status == "gagal"
    assert "anggaran_habis" in [e["jenis"] for e in runner.kejadian_memori()]


def test_rencana_lebih_dari_25_langkah_ditolak():
    with pytest.raises(TaskError):
        buat_rencana("tujuan panjang untuk uji batas langkah", [_langkah(f"l{i}") for i in range(26)])


def test_tombstone_hapus_tugas_tanpa_update_ledger(tmp_path):
    """C129: hapus tugas = append tombstone, rantai tetap utuh; kaset ikut terhapus."""
    from puspa.cassette.cassette import simpan_kaset
    from puspa.runtime.tasks import tombstone_tugas

    simpan_kaset({"tugas_id": "t-hapus-2", "rekaman": []}, "kaset-hapus-2", kaset_dir=tmp_path)
    kejadian = []
    hasil = tombstone_tugas("t-hapus-2", "user-1", "puspa",
                            sink=lambda jenis, isi: kejadian.append({"jenis": jenis, "isi": isi}),
                            kaset_dir=tmp_path)
    assert hasil["tombstone"] is True
    assert hasil["kaset_dihapus"] == ["kaset-hapus-2"]
    assert kejadian[0]["jenis"] == "tugas_dihentikan"
    assert kejadian[0]["isi"]["dihapus"] is True
