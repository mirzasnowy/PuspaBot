"""Tests for notes-to-actions (P-07, plan section 12.8).

The extractor is deterministic and rule-based: a line is an
action only when it carries a bullet, an action verb, ``todo``,
or an owner mention. Owners come from ``@Name`` / ``PJ:`` only —
never invented — and due hints are only the date words printed
in the line. The store tests run against the test database
(``morning_notes`` table, migration 0025) and check that actions
land as tracked tasks and that users cannot see each other's notes.
"""

import pytest

from puspa.morning.notes import (
    extract_actions,
    get_note,
    list_notes,
    save_note,
    summarise_note_text,
)

NOTE = """Rapat pengajian malam ini berjalan lancar.
- @Budi tolong hubungi ustadz untuk jadwal kajian besok
- Bayar iuran kas Rp50.000 sebelum tanggal 25
Catatan: jangan lupa siapkan sound system untuk hari Jumat.
Ide: mungkin undang tetangga sebelah juga."""


def test_extract_actions_finds_bullets_verbs_and_owners():
    actions = extract_actions(NOTE)
    assert [action["text"] for action in actions] == [
        "@Budi tolong hubungi ustadz untuk jadwal kajian besok",
        "Bayar iuran kas Rp50.000 sebelum tanggal 25",
        "Catatan: jangan lupa siapkan sound system untuk hari Jumat.",
        "Ide: mungkin undang tetangga sebelah juga.",
    ]
    assert actions[0]["owner"] == "Budi"
    assert actions[0]["due_hint"] == "besok"
    assert actions[0]["source_line"] == 2
    assert actions[1]["owner"] is None
    assert actions[1]["due_hint"] == "tanggal 25"
    assert actions[2]["due_hint"] == "jumat"
    assert actions[3]["owner"] is None
    assert actions[3]["due_hint"] is None
    # The plain context line ("Rapat ... lancar") is not an action.
    assert len(actions) == 4


def test_extract_actions_owner_prefix_and_todo():
    text = "PJ: Sari siapkan daftar hadir\nTODO: kirim undangan ke grup\nRapat dimulai jam 7."
    actions = extract_actions(text)
    assert len(actions) == 2
    assert actions[0]["owner"] == "Sari"
    assert actions[0]["text"] == "PJ: Sari siapkan daftar hadir"
    assert actions[1]["owner"] is None
    assert "Rapat dimulai" not in [action["text"] for action in actions]


def test_extract_actions_prose_splits_into_sentences():
    text = (
        "Kuliah hari ini membahas RAG. Tolong Budi kirim ringkasannya besok. Selebihnya biasa saja."
    )
    actions = extract_actions(text)
    assert len(actions) == 1
    assert actions[0]["text"] == "Tolong Budi kirim ringkasannya besok."
    assert actions[0]["due_hint"] == "besok"
    assert actions[0]["source_line"] == 1


def test_extract_actions_empty_and_plain_text():
    assert extract_actions("") == []
    assert extract_actions("Hari ini cuaca cerah. Saya belajar dengan tenang.") == []


def test_extract_actions_default_owner():
    actions = extract_actions("- cek harga beras", default_owner="Ibu")
    assert actions[0]["owner"] == "Ibu"


def test_summarise_uses_the_notes_own_words():
    assert summarise_note_text(NOTE).startswith("Rapat pengajian malam ini berjalan lancar.")
    assert summarise_note_text("") == ""
    long_text = " ".join(["kata"] * 200)
    summary = summarise_note_text(long_text)
    assert len(summary) <= 281
    assert summary.endswith("…")


def test_save_note_stores_note_and_tracked_tasks(make_user, test_dsn):
    user = make_user()
    saved = save_note(
        user["user_id"],
        source="meeting",
        title="Pengajian",
        raw_text=NOTE,
        dsn=test_dsn,
    )
    assert saved["note_id"]
    assert saved["summary"].startswith("Rapat pengajian")
    assert len(saved["actions"]) == 4
    assert all(action["task_id"] for action in saved["actions"])
    # Each action is a tracked task in the P-02 task store.
    from puspa.db import connect as _db_connect

    with _db_connect(test_dsn) as conn:
        rows = conn.execute(
            "SELECT id, purpose, status FROM tasks WHERE user_id = %s ORDER BY created_at",
            (user["user_id"],),
        ).fetchall()
    assert [row[1] for row in rows] == [action["text"] for action in saved["actions"]]
    assert all(row[2] == "accepted" for row in rows)
    # The note itself round-trips with its full text.
    note = get_note(user["user_id"], saved["note_id"], dsn=test_dsn)
    assert note is not None
    assert note["source"] == "meeting"
    assert note["title"] == "Pengajian"
    assert note["raw_text"] == NOTE
    assert note["actions"][0]["task_id"] == saved["actions"][0]["task_id"]
    listed = list_notes(user["user_id"], dsn=test_dsn)
    assert [item["note_id"] for item in listed] == [saved["note_id"]]


def test_save_note_rejects_bad_source_and_empty_text(make_user, test_dsn):
    user = make_user()
    with pytest.raises(ValueError):
        save_note(user["user_id"], source="email", raw_text="cek harga", dsn=test_dsn)
    with pytest.raises(ValueError):
        save_note(user["user_id"], source="text", raw_text="   ", dsn=test_dsn)
    with pytest.raises(ValueError):
        save_note("", source="text", raw_text="cek harga", dsn=test_dsn)


def test_notes_are_isolated_per_user(make_user, test_dsn):
    first = make_user()
    second = make_user()
    saved = save_note(first["user_id"], source="text", raw_text="- cek harga", dsn=test_dsn)
    assert list_notes(second["user_id"], dsn=test_dsn) == []
    assert get_note(second["user_id"], saved["note_id"], dsn=test_dsn) is None
    assert get_note(first["user_id"], "00000000-0000-0000-0000-000000000000", dsn=test_dsn) is None
