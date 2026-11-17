"""Tes kolam sub-agen (§2.2): batas, satu level, subset token, Hentikan."""

import pytest

from puspa.runtime.subagents import SubagentPool, SubagentError


def test_spawn_satu_level_dan_subset_token(token_payloads):
    kolam = SubagentPool()
    with pytest.raises(SubagentError, match="satu level"):
        kolam.spawn(agen_id="agen-1", tugas_id="t1", kelas_izin="Pembaca",
                    token_agen=token_payloads["agen"], token_subagen=token_payloads["sub"],
                    pembuat_adalah_subagen=True)
    bukan_subset = dict(token_payloads["sub"], tools=["baca", "hapus_semua"])
    with pytest.raises(SubagentError, match="himpunan bagian"):
        kolam.spawn(agen_id="agen-1", tugas_id="t1", kelas_izin="Pembaca",
                    token_agen=token_payloads["agen"], token_subagen=bukan_subset)
    sub = kolam.spawn(agen_id="agen-1", tugas_id="t1", kelas_izin="Pembaca",
                      token_agen=token_payloads["agen"], token_subagen=token_payloads["sub"])
    assert sub.status == "aktif"


def test_batas_empat_per_agen_enam_sistem(token_payloads):
    kolam = SubagentPool()
    for i in range(4):
        s = kolam.spawn(agen_id="agen-1", tugas_id="t1", kelas_izin="Pembaca",
                        token_agen=token_payloads["agen"], token_subagen=token_payloads["sub"])
        assert s.status == "aktif"
    antre = kolam.spawn(agen_id="agen-1", tugas_id="t1", kelas_izin="Pembaca",
                        token_agen=token_payloads["agen"], token_subagen=token_payloads["sub"])
    assert antre.status == "antre"
    # Agen lain masih bisa sampai batas sistem 6.
    for _ in range(2):
        kolam.spawn(agen_id="agen-2", tugas_id="t2", kelas_izin="Pelaksana",
                    token_agen=token_payloads["agen"], token_subagen=token_payloads["sub"])
    assert kolam.jumlah_aktif() == 6
    penuh = kolam.spawn(agen_id="agen-2", tugas_id="t2", kelas_izin="Pelaksana",
                        token_agen=token_payloads["agen"], token_subagen=token_payloads["sub"])
    assert penuh.status == "antre"
    # Satu selesai -> antrean maju.
    korban = kolam.aktif_per_agen("agen-1")[0]
    kolam.selesai(korban.sub_agen_id)
    assert antre.status == "aktif"


def test_hentikan_matikan_semua_dan_cabut_token_dalam_5_detik(token_payloads):
    kolam = SubagentPool()
    for _ in range(3):
        kolam.spawn(agen_id="agen-1", tugas_id="t-stop", kelas_izin="Pembaca",
                    token_agen=token_payloads["agen"], token_subagen=token_payloads["sub"])
    hasil = kolam.hentikan_tugas("t-stop")
    assert hasil["dihentikan"] == 3
    assert hasil["detik"] < 5
    assert kolam.jumlah_aktif() == 0
