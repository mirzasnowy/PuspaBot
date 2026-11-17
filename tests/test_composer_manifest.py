"""Tes penyusun alur lewat chat (§2.12) + manifes izin turunan."""

from puspa.workflow.composer import card_step, compose_draft
from puspa.workflow.manifest import format_manifest, compute_manifest, subset_permission


def test_compose_example_definition(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft(
        "tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, siapkan bayar",
        user_id=user_id, dsn=test_dsn)
    assert draft["triggers"] == {"type": "schedule", "mode": "date",
                              "date": 25, "start_time": "07:00"}
    block = [l["block"] for l in draft["step"]]
    assert block == ["check_bill", "check_bill", "send_notification", "prepare_payment"]
    assert draft["card"][0]["block"] == "check_bill"
    assert draft["block_requests"] == []


def test_compose_recap_all_bills(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft("ingatkan semua tagihan yang jatuh tempo minggu ini",
                      user_id=user_id, dsn=test_dsn)
    assert draft["triggers"]["type"] in ("schedule", "due_date")
    block = [l["block"] for l in draft["step"]]
    assert block.count("check_bill") == 4
    assert block[-1] == "send_notification"


def test_compose_block_not_yet_exists_records_block_request(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft("tiap tanggal 25 cek PLN, lalu kirim email ke ibu",
                      user_id=user_id, dsn=test_dsn)
    assert "send_email" in draft["block_requests"]
    assert "belum ada" in draft["message"]
    # Tercatat di tabel permintaan_blok Fase 3 (bahan peta jalan).
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM block_requests"
                        " WHERE user_id = %s AND block = 'send_email'",
                        (user_id,))
            assert cur.fetchone()[0] == 1


def test_compose_when_not_understood_gives_honest_message(make_user, test_dsn):
    user_id = make_user()["user_id"]
    draft = compose_draft("asdf qwer zxcv", user_id=user_id, dsn=test_dsn)
    assert draft["step"] == []
    assert "belum paham" in draft["message"]


def test_card_step_marks_logout_action():
    card = card_step({"block": "prepare_payment",
                           "inputs": {"provider": "PLN", "amount": 0, "purpose": ""},
                           "title": "Siapkan bayar"})
    assert card["outbound_action"] is True
    kartu2 = card_step({"block": "check_bill",
                            "inputs": {"provider": "PLN", "customer_number": ""}})
    assert kartu2["outbound_action"] is False


def test_derived_manifest_combines_blocks():
    step = [
        {"block": "check_bill", "inputs": {"provider": "PLN", "customer_number": ""}},
        {"block": "read_email", "inputs": {"email_id": "x"}},
        {"block": "prepare_payment", "inputs": {"provider": "PLN", "amount": 132500,
                                              "purpose": "PLN"}},
    ]
    manifest = compute_manifest(step)
    assert manifest["connectors"] == ["gmail"]
    assert manifest["scope"] == ["gmail.readonly"]
    assert "site_provider_bill" in manifest["domain"]
    assert manifest["spending_limit"] == 132500


def test_manifest_derived_login_condition_and_for_each():
    step = [
        {"block": "condition", "inputs": {"expression": "a == b"},
         "then": [{"block": "create_event",
                   "inputs": {"title": "x", "created_at": "2026-11-20", "attendees": []}}],
         "else": []},
    ]
    manifest = compute_manifest(step)
    assert manifest["connectors"] == ["calendar"]
    assert manifest["scope"] == ["calendar.events"]


def test_subset_permission_less_connector_rejected():
    manifest = {"connectors": ["gmail"], "scope": ["gmail.readonly"],
               "domain": [], "spending_limit": 0, "block": ["read_email"]}
    passed, less = subset_permission(manifest, {"connectors": [], "scope": [],
                                          "domain": [], "spending_limit": 0})
    assert passed is False
    assert less and "gmail" in less[0]


def test_subset_permission_spending_limit_rejected():
    manifest = {"connectors": [], "scope": [], "domain": [],
               "spending_limit": 500000, "block": ["prepare_payment"]}
    passed, less = subset_permission(manifest, {"connectors": [], "scope": [],
                                          "domain": [], "spending_limit": 0})
    assert passed is False
    assert any("batas belanja" in k for k in less)


def test_format_manifest_readable_by_user():
    text = format_manifest({"connectors": ["gmail"], "scope": ["gmail.readonly"],
                           "domain": [], "spending_limit": 0,
                           "block": ["read_email"]})
    assert "gmail" in text and "Rp0" in text
