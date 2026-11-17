"""Tes modul kustom (§2.13): gerbang manifes, isolasi, versi, hapus+ekspor,
Tagihan sebagai manifes."""

import pytest

from puspa.modules.manifest import (
    ModuleError, aktifkan_module, take_module, create_module, items_module,
    delete_module, jalankan_gerbang, rollback_module, change_module,
)
from puspa.modules.notifications import evaluasi_notif
from puspa.modules.tables import (
    TableError, read_table, read_table_module_lain, items_table, add_rows,
)
from puspa.modules.bills import MANIFEST_BILL, create_module_bill
from puspa.modules.views import render_module

MANIFEST_MINIMAL = {
    "name": "Catatan belanja",
    "tampilan": [
        {"type": "table", "title": "Daftar belanja", "table": "belanja",
         "kolom": ["product", "price"]},
    ],
    "table": [
        {"name": "belanja", "kolom": [
            {"name": "product", "tipe": "text"},
            {"name": "price", "tipe": "Rupiah"},
        ]},
    ],
    "example_data": {
        "belanja": [{"product": "Beras 5 kg", "price": 65000}],
    },
}


def test_bill_sebagai_manifest_passed_kelima_gerbang(make_user, test_dsn,
                                                     permission_user_penuh):
    user_id = make_user()["user_id"]
    result = jalankan_gerbang(dict(MANIFEST_BILL), dsn=test_dsn)
    assert result["passed"] is True, result["masalah"]
    module = create_module_bill(user_id=user_id, dsn=test_dsn)
    assert module["status"] == "draft"
    active = aktifkan_module(module["id"], user_id=user_id,
                           permission_user=permission_user_penuh, dsn=test_dsn)
    assert active["status"] == "active"
    # Tabel milik modul dibuat dari manifes.
    table = items_table(module["id"], user_id=user_id, dsn=test_dsn)
    assert len(table) == 1 and table[0]["name"] == "bill"
    # Pratinjau dengan data contoh: angka ringkas + tabel + grafik.
    html = render_module(MANIFEST_BILL)
    assert "220.000" in html  # 132.500 + 87.500
    assert "PLN" in html and "<svg" in html


def test_module_minimal_passed_and_active(make_user, test_dsn, permission_user_penuh):
    user_id = make_user()["user_id"]
    module = create_module(user_id=user_id, manifest=dict(MANIFEST_MINIMAL), dsn=test_dsn)
    active = aktifkan_module(module["id"], user_id=user_id,
                           permission_user=permission_user_penuh, dsn=test_dsn)
    assert active["status"] == "active"


def test_manifest_nakal_rejected():
    nakal = [
        # HTML/JS di dalam manifes.
        dict(MANIFEST_MINIMAL, tampilan=[
            {"type": "card", "title": "x",
             "text": "<script>alert(1)</script>"}]),
        # Komponen tidak diizinkan.
        dict(MANIFEST_MINIMAL, tampilan=[
            {"type": "video", "title": "x"}]),
        # Tipe kolom tidak diizinkan.
        dict(MANIFEST_MINIMAL, table=[
            {"name": "belanja", "kolom": [{"name": "product", "tipe": "gambar"}]}]),
        # SQL di dalam manifes.
        dict(MANIFEST_MINIMAL, tampilan=[
            {"type": "card", "title": "x",
             "text": "SELECT * FROM users"}]),
        # 4 tampilan (maks 3).
        dict(MANIFEST_MINIMAL, tampilan=[
            {"type": "card", "title": f"k{i}", "text": "x"} for i in range(4)]),
        # 6 tabel (maks 5).
        dict(MANIFEST_MINIMAL, table=[
            {"name": f"t{i}", "kolom": [{"name": "a", "tipe": "text"}]}
            for i in range(6)]),
    ]
    for m in nakal:
        result = jalankan_gerbang(m, dsn=None)
        assert result["passed"] is False, m
        assert result["masalah"]


def test_create_module_nakal_menolak_dengan_reason(make_user, test_dsn):
    user_id = make_user()["user_id"]
    nakal = dict(MANIFEST_MINIMAL, tampilan=[{"type": "video", "title": "x"}])
    with pytest.raises(ModuleError, match="tidak diizinkan"):
        create_module(user_id=user_id, manifest=nakal, dsn=test_dsn)


def test_isolasi_module_not_can_read_data_module_lain(make_user, test_dsn,
                                                      permission_user_penuh):
    user_a = make_user()["user_id"]
    user_b = make_user()["user_id"]
    module_a = create_module(user_id=user_a, manifest=dict(MANIFEST_MINIMAL), dsn=test_dsn)
    aktifkan_module(module_a["id"], user_id=user_a, permission_user=permission_user_penuh,
                   dsn=test_dsn)
    table_a = items_table(module_a["id"], user_id=user_a, dsn=test_dsn)[0]
    add_rows(table_a["id"], user_id=user_a,
                 data={"product": "Beras", "price": 65000}, dsn=test_dsn)
    # Modul lain (milik user B) mencoba baca tabel modul A -> ditolak.
    module_b = create_module(user_id=user_b, manifest=dict(MANIFEST_MINIMAL), dsn=test_dsn)
    with pytest.raises(TableError, match="tidak bisa membaca data modul lain"):
        read_table_module_lain(table_a["id"], module_id_peminta=module_b["id"],
                              user_id=user_b, dsn=test_dsn)
    # Pengguna lain juga tidak bisa baca langsung.
    with pytest.raises(TableError, match="pengguna lain"):
        read_table(table_a["id"], user_id=user_b, dsn=test_dsn)
    # Pemilik sendiri bisa baca.
    assert read_table(table_a["id"], user_id=user_a, dsn=test_dsn) == [
        {"product": "Beras", "price": 65000}]


def test_tipe_kolom_ditegakkan_saat_add_rows(make_user, test_dsn):
    user_id = make_user()["user_id"]
    module = create_module(user_id=user_id, manifest=dict(MANIFEST_MINIMAL), dsn=test_dsn)
    table = items_table(module["id"], user_id=user_id, dsn=test_dsn)[0]
    with pytest.raises(TableError, match="harus angka"):
        add_rows(table["id"], user_id=user_id,
                     data={"product": "Beras", "price": "expensive"}, dsn=test_dsn)
    with pytest.raises(TableError, match="tidak boleh negatif"):
        add_rows(table["id"], user_id=user_id,
                     data={"product": "Beras", "price": -100}, dsn=test_dsn)
    ok = add_rows(table["id"], user_id=user_id,
                      data={"product": "Beras", "price": 65000}, dsn=test_dsn)
    assert ok["data"]["price"] == 65000


def test_notif_module_dari_data(make_user, test_dsn):
    user_id = make_user()["user_id"]
    module = create_module_bill(user_id=user_id, dsn=test_dsn)
    data = MANIFEST_BILL["example_data"]
    notif = evaluasi_notif(MANIFEST_BILL, data)
    assert len(notif) == 2  # dua tagihan belum_bayar
    assert "belum dibayar" in notif[0]["message"]
    paid_off = {"bill": [dict(r, status="already_pay")
                         for r in data["bill"]]}
    assert evaluasi_notif(MANIFEST_BILL, paid_off) == []


def test_change_module_versi_new_and_rollback(make_user, test_dsn):
    user_id = make_user()["user_id"]
    module = create_module(user_id=user_id, manifest=dict(MANIFEST_MINIMAL), dsn=test_dsn)
    new = dict(MANIFEST_MINIMAL)
    new["tampilan"] = [
        {"type": "table", "title": "Daftar belanja", "table": "belanja",
         "kolom": ["product", "price"]},
        {"type": "number", "title": "Total", "table": "belanja",
         "kolom": "price", "format": "Rupiah"},
    ]
    updated_at = change_module(module["id"], user_id=user_id, manifest=new, dsn=test_dsn)
    assert updated_at["active_version"] == 2
    pulih = rollback_module(module["id"], user_id=user_id, versi=1, dsn=test_dsn)
    assert pulih["active_version"] == 3
    assert len(pulih["manifest"]["tampilan"]) == 1


def test_delete_module_menawarkan_export_dulu(make_user, test_dsn):
    user_id = make_user()["user_id"]
    module = create_module(user_id=user_id, manifest=dict(MANIFEST_MINIMAL), dsn=test_dsn)
    table = items_table(module["id"], user_id=user_id, dsn=test_dsn)[0]
    add_rows(table["id"], user_id=user_id,
                 data={"product": "Beras", "price": 65000}, dsn=test_dsn)
    result = delete_module(module["id"], user_id=user_id, dsn=test_dsn)
    assert result["deleted"]["status"] == "deleted"
    assert result["export"]["table"]["belanja"] == [
        {"product": "Beras", "price": 65000}]
    # Data baris ikut terhapus sesudah diekspor.
    assert items_table(module["id"], user_id=user_id, dsn=test_dsn) == []


def test_maks_3_module_active(make_user, test_dsn, permission_user_penuh):
    user_id = make_user()["user_id"]
    for i in range(3):
        m = dict(MANIFEST_MINIMAL, name=f"Modul {i}")
        module = create_module(user_id=user_id, manifest=m, dsn=test_dsn)
        aktifkan_module(module["id"], user_id=user_id, permission_user=permission_user_penuh,
                       dsn=test_dsn)
    keempat = create_module(user_id=user_id,
                         manifest=dict(MANIFEST_MINIMAL, name="Modul 4"), dsn=test_dsn)
    with pytest.raises(ModuleError, match="3 modul aktif"):
        aktifkan_module(keempat["id"], user_id=user_id, permission_user=permission_user_penuh,
                       dsn=test_dsn)
