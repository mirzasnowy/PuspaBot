"""Tes penyusun alur lewat chat (§2.12) + manifes izin turunan."""

from puspa.workflow.composer import card_step, compose_draft
from puspa.workflow.manifest import format_manifest, hitung_manifest, subset_permission


def test_compose_example_definisi(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft(
        "tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, siapkan bayar",
        user_id=user_id, dsn=test_dsn)
    assert draft["triggers"] == {"type": "schedule", "mode": "date",
                              "date": 25, "start_time": "07:00"}
    block = [l["block"] for l in draft["step"]]
    assert block == ["check_bill", "check_bill", "send_notification", "prepare_payment"]
    assert draft["card"][0]["block"] == "check_bill"
    assert draft["permintaan_block"] == []


def test_compose_rekap_all_bill(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft("ingatkan semua tagihan yang jatuh tempo minggu ini",
                      user_id=user_id, dsn=test_dsn)
    assert draft["triggers"]["type"] in ("schedule", "due_date")
    block = [l["block"] for l in draft["step"]]
    assert block.count("check_bill") == 4
    assert block[-1] == "send_notification"


def test_compose_block_not_yet_ada_dicatat_permintaan_block(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft("tiap tanggal 25 cek PLN, lalu kirim email ke ibu",
                      user_id=user_id, dsn=test_dsn)
    assert "send_email" in draft["permintaan_block"]
    assert "belum ada" in draft["message"]
    # Tercatat di tabel permintaan_blok Fase 3 (bahan peta jalan).
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM block_requests"
                        " WHERE user_id = %s AND block = 'send_email'",
                        (user_id,))
            assert cur.fetchone()[0] == 1


def test_compose_not_paham_memberi_message_jujur(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft("asdf qwer zxcv", user_id=user_id, dsn=test_dsn)
    assert draft["step"] == []
    assert "belum paham" in draft["message"]


def test_card_step_tandai_action_logout():
    card = card_step({"block": "prepare_payment",
                           "masukan": {"provider": "PLN", "amount": 0, "purpose": ""},
                           "title": "Siapkan bayar"})
    assert card["action_logout"] is True
    kartu2 = card_step({"block": "check_bill",
                            "masukan": {"provider": "PLN", "number_pelanggan": ""}})
    assert kartu2["action_logout"] is False


def test_manifest_turunan_gabungan_block():
    step = [
        {"block": "check_bill", "masukan": {"provider": "PLN", "number_pelanggan": ""}},
        {"block": "read_email", "masukan": {"email_id": "x"}},
        {"block": "prepare_payment", "masukan": {"provider": "PLN", "amount": 132500,
                                              "purpose": "PLN"}},
    ]
    manifest = hitung_manifest(step)
    assert manifest["connectors"] == ["gmail"]
    assert manifest["scope"] == ["gmail.readonly"]
    assert "situs_provider_bill" in manifest["domain"]
    assert manifest["batas_belanja"] == 132500


def test_manifest_turunan_login_condition_and_for_each():
    step = [
        {"block": "condition", "masukan": {"ungkapan": "a == b"},
         "maka": [{"block": "create_event",
                   "masukan": {"title": "x", "created_at": "2026-11-20", "peserta": []}}],
         "kalau_not": []},
    ]
    manifest = hitung_manifest(step)
    assert manifest["connectors"] == ["calendar"]
    assert manifest["scope"] == ["calendar.events"]


def test_subset_permission_less_connector_rejected():
    manifest = {"connectors": ["gmail"], "scope": ["gmail.readonly"],
               "domain": [], "batas_belanja": 0, "block": ["read_email"]}
    passed, less = subset_permission(manifest, {"connectors": [], "scope": [],
                                          "domain": [], "batas_belanja": 0})
    assert passed is False
    assert less and "gmail" in less[0]


def test_subset_permission_batas_belanja_rejected():
    manifest = {"connectors": [], "scope": [], "domain": [],
               "batas_belanja": 500000, "block": ["prepare_payment"]}
    passed, less = subset_permission(manifest, {"connectors": [], "scope": [],
                                          "domain": [], "batas_belanja": 0})
    assert passed is False
    assert any("batas belanja" in k for k in less)


def test_format_manifest_can_dibaca_user():
    text = format_manifest({"connectors": ["gmail"], "scope": ["gmail.readonly"],
                           "domain": [], "batas_belanja": 0,
                           "block": ["read_email"]})
    assert "gmail" in text and "Rp0" in text
