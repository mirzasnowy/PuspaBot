"""Tes anggaran + siklus tugas 5 tahap (§2.4)."""

import pytest

from puspa.runtime.budget import (AnggaranHabis, BudgetTracker,
                                  kalibrasi_dari_ledger)
from puspa.runtime.tasks import (Langkah, Task, TaskRunner, create_rencana,
                                 TaskError)


def _step(name="search", jenis_logout="result"):
    return Langkah(name=name, masukan={"type": "search", "q": "x"},
                   expected_output={"type": jenis_logout},
                   syarat_completed="hasil ditemukan")


def test_anggaran_step_and_rupiah():
    b = BudgetTracker(rupiah_max=2000, step_max=25)
    b.record_step(25)
    with pytest.raises(AnggaranHabis):
        b.record_step(1)
    b2 = BudgetTracker()
    b2.record_cost(1999)
    with pytest.raises(AnggaranHabis):
        b2.record_cost(2)


def test_anggaran_subagen_menyempit_to_bawah():
    b = BudgetTracker(rupiah_max=2000, step_max=25)
    b.record_step(20)
    b.record_cost(1500)
    sub = b.anggaran_subagen()
    assert sub["step_max"] == 5
    assert sub["rupiah_max"] == 500


def test_kalibrasi_dari_ledger_without_data_jujur():
    result = kalibrasi_dari_ledger([])
    assert result["amount_task"] == 0 and result["rata_rata_rp"] is None


def test_kalibrasi_dari_ledger_dengan_data():
    event = [
        {"task_id": "t1", "content": {"cost_rp": 500}},
        {"task_id": "t1", "content": {"cost_rp": 300}},
        {"task_id": "t2", "content": {"cost_rp": 1200}},
    ]
    result = kalibrasi_dari_ledger(event)
    assert result["amount_task"] == 2
    assert result["rata_rata_rp"] == 1000
    assert result["maks_rp"] == 1200


def test_siklus_lima_tahap_completed():
    runner = TaskRunner(pelaksana=lambda name, masukan: {"type": "result", "value": 42})
    task = runner.jalankan(Task(task_id="tugas-1", user_id="u1", agent_id="puspa",
                                purpose="riset dan rangkum lima artikel",
                                rencana=[_step("search"), _step("rangkum")]))
    assert task.status == "completed" and task.tahap == "sajikan"
    jenis = [e["type"] for e in runner.event_memory()]
    for wajib in ("task_created", "plan_created", "handoff_proposed",
                  "handoff_handed_over", "verification_completed", "task_completed"):
        assert wajib in jenis


def test_purpose_ambigu_ask_satu_pertanyaan():
    runner = TaskRunner()
    task = runner.jalankan(Task(task_id="tugas-2", user_id="u1", agent_id="puspa",
                                purpose="cek harga"))
    assert task.status == "pending_user"
    assert task.pertanyaan  # maks 1 pertanyaan


def test_rencana_action_logout_berhenti_di_approval():
    lg = _step("send")
    lg.action_logout = True
    runner = TaskRunner()
    task = runner.jalankan(Task(task_id="tugas-3", user_id="u1", agent_id="puspa",
                                purpose="kirim email ke tim keuangan",
                                rencana=[lg]))
    assert task.status == "pending_approval"
    assert task.result == {}


def test_step_not_passed_skema_task_failed():
    runner = TaskRunner(pelaksana=lambda name, masukan: {"type": "wrong"})
    task = runner.jalankan(Task(task_id="tugas-4", user_id="u1", agent_id="puspa",
                                purpose="riset dan rangkum lima artikel",
                                rencana=[_step()]))
    assert task.status == "failed"


def test_anggaran_habis_task_berhenti_and_melapor():
    budget = BudgetTracker(rupiah_max=2000, step_max=1)
    runner = TaskRunner(budget=budget,
                        pelaksana=lambda name, masukan: {"type": "result", "value": 1})
    task = runner.jalankan(Task(task_id="tugas-5", user_id="u1", agent_id="puspa",
                                purpose="riset dan rangkum lima artikel",
                                rencana=[_step("a"), _step("b")]))
    assert task.status == "failed"
    assert "budget_exhausted" in [e["type"] for e in runner.event_memory()]


def test_rencana_more_dari_25_step_rejected():
    with pytest.raises(TaskError):
        create_rencana("tujuan panjang untuk uji batas langkah", [_step(f"l{i}") for i in range(26)])


def test_tombstone_delete_task_without_update_ledger(tmp_path):
    """C129: hapus tugas = append tombstone, rantai tetap utuh; kaset ikut terhapus."""
    from puspa.cassette.cassette import save_cassette
    from puspa.runtime.tasks import tombstone_task

    save_cassette({"task_id": "t-hapus-2", "recording": []}, "kaset-hapus-2", cassette_dir=tmp_path)
    event = []
    result = tombstone_task("t-hapus-2", "user-1", "puspa",
                            sink=lambda jenis, content: event.append({"type": jenis, "content": content}),
                            cassette_dir=tmp_path)
    assert result["tombstone"] is True
    assert result["cassette_deleted"] == ["kaset-hapus-2"]
    assert event[0]["type"] == "task_stopped"
    assert event[0]["content"]["deleted"] is True
