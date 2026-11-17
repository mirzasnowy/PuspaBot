"""Menjalankan alur (§2.12 revisi 7).

- Tiap jalan alur adalah tugas biasa di Buku Kerja: tiap langkah
  dicatat, angka dirender dari bukti, Pemeriksa tetap berlaku
  (vonis dari blok baca; angka tanpa bukti_id maksimal Belum pasti).
- Aksi keluar TIDAK dieksekusi langsung: masuk antrean persetujuan
  (approvals.py); jalan berhenti sampai user memutuskan.
- Uji kering: mode baca saja — blok aksi keluar diganti simulasi.
- Alur tidak bisa mengubah dirinya: runner hanya MEMBACA definisi
  alur; tidak ada alat edit alur di dalam eksekutor, dan teks apa pun
  dari data (email, listing, jawaban) diperlakukan sebagai data.
- Gagal terlihat: gagal 2x berturut-turut → dijeda otomatis + alasan
  dikembalikan ke pemanggil (untuk diberi tahu ke user).
- Sukses 3x + lolos Pemeriksa → tercatat sebagai prosedur percobaan
  di Graf Alur Layanan (§2.12, §3.3).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from puspa.workflow import approvals
from puspa.workflow.blocks import (
    BLOCKS, MAX_ACTIVE_WORKFLOWS, CEILING_COST_PER_RUN_RP, validate_workflow,
)
from puspa.workflow.manifest import compute_manifest, subset_permission
from puspa.workflow.triggers import validate_trigger

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


class WorkflowError(Exception):
    """Alur tidak valid, di luar batas MVP, atau tidak bisa jalan."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def _record_ledger(task_id, agent_id, event_type, content, dsn=None):
    """Tulis kejadian ke Buku Kerja (bila ledger tersedia). Gagal tulis
    ledger tidak menggagalkan jalan alur — tapi dicatat di alasan."""
    try:
        from puspa.ledger import Ledger

        Ledger(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)).append_event(
            task_id=task_id, agent_id=agent_id, event_type=event_type, content=content)
    except Exception as exc:  # noqa: BLE001 — ledger sekunder di runner
        return str(exc)
    return None


# ---------------------------------------------------------------- CRUD

def create_workflow(*, user_id: str, agent_id: str, name: str, trigger: dict,
              step: list, dsn=None) -> dict:
    """Buat alur baru berstatus 'draf' + simpan versi 1."""
    problems = validate_trigger(trigger) + validate_workflow(step)
    if problems:
        raise WorkflowError("; ".join(problems))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workflows (user_id, agent_id, name, trigger, step, status)"
                " VALUES (%s, %s, %s, %s, %s, 'draft') RETURNING *",
                (user_id, agent_id, name, json.dumps(trigger), json.dumps(step)))
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO workflow_versions (workflow_id, versi, trigger, step)"
                " VALUES (%s, 1, %s, %s)",
                (row["id"], json.dumps(trigger), json.dumps(step)))
        conn.commit()
    _record_ledger(f"alur-{row['id']}", agent_id, "workflow_proposed",
                  {"workflow_id": str(row["id"]), "name": name}, dsn)
    return _row(row)


def get_workflow(workflow_id: str, *, user_id: str, dsn=None) -> dict | None:
    """Baca satu alur — hanya milik user itu."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM workflows WHERE id = %s AND user_id = %s",
                        (workflow_id, user_id))
            row = cur.fetchone()
    return _row(row) if row else None


def list_workflows(user_id: str, *, status: str | None = None, dsn=None) -> list[dict]:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute("SELECT * FROM workflows WHERE user_id = %s AND status = %s"
                            " ORDER BY created_at", (user_id, status))
            else:
                cur.execute("SELECT * FROM workflows WHERE user_id = %s ORDER BY created_at",
                            (user_id,))
            return [_row(r) for r in cur.fetchall()]


def _row(row: dict) -> dict:
    row = dict(row)
    for k in ("id", "user_id"):
        if k in row and row[k] is not None:
            row[k] = str(row[k])
    return row


def change_workflow(workflow_id: str, *, user_id: str, trigger: dict | None = None,
              step: list | None = None, dsn=None) -> dict:
    """Ubah definisi alur (hanya lewat sesi penyusunan user — runner
    tidak pernah memanggil ini). Tiap perubahan = versi baru."""
    workflow = get_workflow(workflow_id, user_id=user_id, dsn=dsn)
    if workflow is None:
        raise WorkflowError("alur tidak ditemukan")
    if workflow["status"] == "deleted":
        raise WorkflowError("alur sudah dihapus")
    trigger_new = trigger if trigger is not None else workflow["trigger"]
    step_new = step if step is not None else workflow["step"]
    problems = validate_trigger(trigger_new) + validate_workflow(step_new)
    if problems:
        raise WorkflowError("; ".join(problems))
    new_version = int(workflow["active_version"]) + 1
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflows SET trigger = %s, step = %s, active_version = %s,"
                " updated_at = now() WHERE id = %s RETURNING *",
                (json.dumps(trigger_new), json.dumps(step_new),
                 new_version, workflow_id))
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO workflow_versions (workflow_id, versi, trigger, step)"
                " VALUES (%s, %s, %s, %s)",
                (workflow_id, new_version, json.dumps(trigger_new),
                 json.dumps(step_new)))
        conn.commit()
    return _row(row)


def rollback_workflow(workflow_id: str, *, user_id: str, version: int, dsn=None) -> dict:
    """Pulihkan versi lama (menjadi versi baru — riwayat tidak ditimpa)."""
    workflow = get_workflow(workflow_id, user_id=user_id, dsn=dsn)
    if workflow is None:
        raise WorkflowError("alur tidak ditemukan")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trigger, step FROM workflow_versions"
                " WHERE workflow_id = %s AND versi = %s", (workflow_id, version))
            old = cur.fetchone()
            if old is None:
                raise WorkflowError(f"versi {version} tidak ada")
    return change_workflow(workflow_id, user_id=user_id,
                     trigger=old["trigger"], step=old["step"], dsn=dsn)


def delete_workflow(workflow_id: str, *, user_id: str, dsn=None) -> dict:
    """Hapus alur (status 'dihapus'). Riwayat jalan di Buku Kerja dan
    tabel jalan_alur TIDAK dihapus (§2.12)."""
    workflow = get_workflow(workflow_id, user_id=user_id, dsn=dsn)
    if workflow is None:
        raise WorkflowError("alur tidak ditemukan")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflows SET status = 'deleted', updated_at = now()"
                " WHERE id = %s RETURNING *", (workflow_id,))
            row = dict(cur.fetchone())
        conn.commit()
    return _row(row)


def activate_workflow(workflow_id: str, *, user_id: str, permission_user: dict,
                  dry_run_result: dict | None = None, dsn=None) -> dict:
    """Aktifkan alur. Syarat (semua oleh kode):
    1. maks 5 alur aktif per pengguna;
    2. manifes izin turunan himpunan bagian dari izin user;
    3. uji kering sudah dijalankan dan sukses (hasil_kering wajib).
    """
    workflow = get_workflow(workflow_id, user_id=user_id, dsn=dsn)
    if workflow is None:
        raise WorkflowError("alur tidak ditemukan")
    if workflow["status"] == "deleted":
        raise WorkflowError("alur sudah dihapus")
    if dry_run_result is None or not dry_run_result.get("success"):
        raise WorkflowError(
            "alur belum lolos uji kering — jalankan uji kering dulu, "
            "lihat hasilnya, baru aktifkan")
    active = [a for a in list_workflows(user_id, status="active", dsn=dsn)
             if str(a["id"]) != str(workflow_id)]
    if len(active) >= MAX_ACTIVE_WORKFLOWS:
        raise WorkflowError(
            f"sudah ada {MAX_ACTIVE_WORKFLOWS} alur aktif — maks di MVP")
    manifest = compute_manifest(workflow["step"])
    passed, less = subset_permission(manifest, permission_user)
    if not passed:
        raise WorkflowError("manifes izin di luar izin user: " + "; ".join(less))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflows SET status = 'active', updated_at = now()"
                " WHERE id = %s RETURNING *", (workflow_id,))
            row = dict(cur.fetchone())
        conn.commit()
    _record_ledger(f"alur-{workflow_id}", workflow["agent_id"], "workflow_activated",
                  {"workflow_id": str(workflow_id), "manifest": manifest}, dsn)
    return _row(row)


def pause_workflow(workflow_id: str, *, user_id: str, reason: str = "", dsn=None) -> dict:
    workflow = get_workflow(workflow_id, user_id=user_id, dsn=dsn)
    if workflow is None:
        raise WorkflowError("alur tidak ditemukan")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflows SET status = 'paused', updated_at = now()"
                " WHERE id = %s RETURNING *", (workflow_id,))
            row = dict(cur.fetchone())
        conn.commit()
    _record_ledger(f"alur-{workflow_id}", workflow["agent_id"], "workflow_paused",
                  {"workflow_id": str(workflow_id), "reason": reason}, dsn)
    return _row(row)


# ---------------------------------------------------------------- eksekutor

def executor_fixture():
    """Eksekutor default: data uji (fixture), tanpa HTTP live.
    - cek_tagihan: DATA_UJI Fase 3 (PLN Rp132.500 dst.) — berlabel fixture.
    - bandingkan/pantau harga: gerbang ToS Fase 3 (boleh_baca) — live
      ditahan keputusan 15, kembalikan tidak_bisa_dicek TANPA HTTP.
    - email/draf/event/bayar: draf + simulasi (tanpa konektor live).
    """
    from puspa.billing.fixture import DATA_TEST

    def check_bill(provider, customer_number=""):
        data = DATA_TEST.get(str(provider).lower())
        if data is None:
            return {"success": False, "reason": "provider_di_luar_mvp",
                    "message": f"Provider {provider!r} bukan provider MVP."}
        return {"success": True, "amount": data["amount"],
                "due_date": data["due_date"], "status": data["status"],
                "evidence_id": f"fixture:{provider}", "verdict": "Uncertain",
                "source": "fixture"}

    def compare_price(name_product):
        from puspa.pricing.tos import may_read

        if not may_read("tokopedia"):
            return {"success": False, "reason": "syarat_service_pending_keputusan_nero",
                    "message": "Tidak bisa cek harga live — syarat layanan marketplace "
                             "menunggu keputusan Nero (keputusan 15)."}
        return {"success": False, "reason": "not_diimplementasi",
                "message": "Jalur live belum dibuka (keputusan 15)."}

    def watch_price(product_id, price_threshold):
        result = compare_price(product_id)
        return result

    return {
        "check_bill": check_bill,
        "compare_prices": compare_price,
        "watch_prices": watch_price,
        "search_email": lambda kata_key: {"success": True, "email": [], "source": "fixture"},
        "read_email": lambda email_id: {"success": True, "sender": "", "subject": "",
                                        "content": "", "source": "fixture"},
        "create_draft": lambda recipient, subject, content: {
            "success": True, "draft_id": "draf-fixture", "source": "fixture"},
        "create_event": lambda title, created_at, attendees: {
            "success": True, "event_id": "event-fixture", "source": "fixture"},
        "prepare_payment": lambda provider, amount, purpose: {
            "success": True, "payment_draft_id": "bayar-fixture",
            "content_hash": approvals.content_hash(
                {"provider": provider, "amount": amount, "purpose": purpose}),
            "source": "fixture"},
        "send_notification": lambda message: {"success": True, "sent": True},
        "ask_user": lambda question: {"success": False, "answer": None,
                                          "reason": "pending_jawaban_user"},
        "save_memory": lambda fact, source="user_stated": {
            "success": True, "saved": True},
        "save_to_module_table": lambda modules, table, rows: {
            "success": True, "saved": len(rows or [])},
    }


# ---------------------------------------------------------------- evaluasi kondisi

def evaluate_expression(ungkapan: str, context: dict) -> bool:
    """Evaluasi ungkapan kondisi sederhana: `<field> <op> <nilai>`.
    Field diambil dari hasil langkah sebelumnya (konteks). Operator:
    == != > < >= <=. Nilai: angka, atau teks tanpa spasi. Ungkapan
    yang tidak bisa diparse → WorkflowError (gagal terlihat)."""
    match = re.match(r"^\s*([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$", str(ungkapan))
    if not match:
        raise WorkflowError(f"ungkapan kondisi tidak bisa diparse: {ungkapan!r}")
    field, op, value_text = match.groups()
    context_value = context
    for parts in field.split("."):
        if isinstance(context_value, dict) and parts in context_value:
            context_value = context_value[parts]
        else:
            raise WorkflowError(f"ungkapan kondisi: field {field!r} tidak ada")
    try:
        value = float(value_text)
        compared = float(context_value)
    except (TypeError, ValueError):
        value = value_text.strip("'\"")
        compared = str(context_value)
    if op == "==":
        return compared == value
    if op == "!=":
        return compared != value
    if op == ">":
        return compared > value
    if op == "<":
        return compared < value
    if op == ">=":
        return compared >= value
    return compared <= value


# ---------------------------------------------------------------- jalan alur

def _save_run(*, workflow_id, user_id, task_id, trigger_type, status,
                  cost_rp, step_results, reason, dsn=None):
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workflow_runs (workflow_id, user_id, task_id, trigger_jenis,"
                " status, cost_rp, step_results, reason, completed_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id",
                (workflow_id, user_id, task_id, trigger_type, status, cost_rp,
                 json.dumps(step_results), reason))
            run_id = str(cur.fetchone()["id"])
        conn.commit()
    return run_id


def _recent_run_statuses(user_id: str, workflow_id: str, dsn=None) -> int:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM workflow_runs WHERE workflow_id = %s"
                " ORDER BY started_at DESC LIMIT 2", (workflow_id,))
            return [r["status"] for r in cur.fetchall()]


def dry_run(workflow_id: str, *, user_id: str, context=None, dsn=None) -> dict:
    """Jalankan alur sekali mode baca saja: blok aksi keluar diganti
    simulasi. Kembalikan hasil jalan (lihat jalankan_alur)."""
    return run_workflow(workflow_id, user_id=user_id, trigger_type="manual",
                         context=context, mode="dry", dsn=dsn)


def run_workflow(workflow_id: str, *, user_id: str, trigger_type: str = "manual",
                  context=None, mode: str = "run",
                  answers: dict | None = None, dsn=None) -> dict:
    """Jalankan satu alur. `mode`: 'jalan' atau 'kering'. `konteks`:
    eksekutor (default fixture). `jawaban`: jawaban user untuk blok
    tanya_user ({judul_langkah: jawaban}). Kembalikan:
    {sukses, jalan_id, status, langkah_hasil, biaya_rp, alasan,
     persetujuan_id (bila berhenti di persetujuan)}.
    """
    workflow = get_workflow(workflow_id, user_id=user_id, dsn=dsn)
    if workflow is None:
        raise WorkflowError("alur tidak ditemukan")
    if mode == "run" and workflow["status"] != "active":
        raise WorkflowError(f"alur berstatus {workflow['status']!r} — aktifkan dulu")
    executor = context or executor_fixture()
    task_id = f"jalan-{uuid.uuid4().hex[:12]}"
    _record_ledger(task_id, workflow["agent_id"], "task_created",
                  {"workflow_id": str(workflow_id), "triggers": trigger_type, "mode": mode}, dsn)
    _record_ledger(task_id, workflow["agent_id"], "trigger_fired",
                  {"workflow_id": str(workflow_id), "triggers": trigger_type}, dsn)

    result_context: dict = {}
    step_results: list[dict] = []
    cost = 0
    status = "completed"
    reason = ""
    approval_id = None

    def _run_steps(items, depth=0):
        nonlocal cost, status, reason, approval_id
        for step in items:
            if status != "completed":
                return
            name = step.get("block")
            if name not in BLOCKS:
                status, reason = "failed", f"blok {name!r} tidak ada di pustaka"
                return
            inputs = dict(step.get("inputs", {}) or {})
            # Resolusi nilai dari hasil langkah sebelumnya: masukan
            # {"jumlah": "{cek_tagihan.jumlah}"} diisi dari konteks.
            for k, v in list(inputs.items()):
                if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                    key = v[1:-1]
                    inputs[k] = result_context.get(key, v)
            cost += int(BLOCKS[name].get("cost_rp", 0))
            if cost > CEILING_COST_PER_RUN_RP:
                status, reason = "failed", (
                    f"cost ceiling per run Rp{CEILING_COST_PER_RUN_RP:,} exceeded"
                    .replace(",", "."))
                return
            if name == "condition":
                try:
                    correct = evaluate_expression(inputs.get("expression", ""), result_context)
                except WorkflowError as exc:
                    status, reason = "failed", str(exc)
                    return
                branch = step.get("then", []) if correct else step.get("else", [])
                step_results.append({"block": name, "correct": correct, "success": True})
                _run_steps(branch, depth + 1)
                continue
            if name == "for_each":
                items = inputs.get("items", [])
                if not isinstance(items, list):
                    items = result_context.get(str(inputs.get("items", "")), [])
                if not isinstance(items, list):
                    status, reason = "failed", "for_each: 'daftar' bukan daftar"
                    return
                if len(items) > 20:
                    status, reason = "failed", "for_each: maks 20 item"
                    return
                for item in items:
                    result_context["item"] = item
                    _run_steps(step.get("nested_steps", []), depth + 1)
                    if status != "completed":
                        return
                step_results.append({"block": name, "item_count": len(items),
                                      "success": True})
                continue
            # Blok aksi keluar: kering -> simulasi; jalan -> antre persetujuan.
            if BLOCKS[name].get("outbound_action"):
                action = {"block": name, "inputs": inputs}
                if mode == "dry":
                    step_results.append({"block": name, "success": True,
                                          "simulated": True, "action": action})
                    result_context[name] = {"simulated": True}
                    continue
                # Jalan biasa: buat jalan dulu? persetujuan butuh jalan_id.
                # Simpan jalan 'menunggu_persetujuan' di bawah (setelah loop).
                status = "pending_approval"
                reason = f"menunggu persetujuan: {name}"
                step_results.append({"block": name, "success": None,
                                      "pending_approval": True, "action": action})
                approval_id = action  # ditukar id setelah jalan disimpan
                return
            if name == "ask_user":
                title = step.get("title") or name
                if answers and title in answers:
                    result = {"success": True, "answer": answers[title]}
                else:
                    status = "pending_answer"
                    reason = f"menunggu jawaban user: {inputs.get('question', '')}"
                    step_results.append({"block": name, "success": None,
                                          "pending_answer": True})
                    return
                step_results.append({"block": name, "success": True, "result": result})
                result_context[name] = result
                continue
            fn = executor.get(name)
            if fn is None:
                status, reason = "failed", f"eksekutor belum menyediakan blok {name!r}"
                return
            try:
                result = fn(**inputs) if inputs else fn()
            except TypeError:
                try:
                    result = fn(inputs)
                except Exception as exc:  # noqa: BLE001
                    status, reason = "failed", f"blok {name} gagal: {exc}"
                    return
            except Exception as exc:  # noqa: BLE001
                status, reason = "failed", f"blok {name} gagal: {exc}"
                return
            if not isinstance(result, dict):
                status, reason = "failed", f"blok {name}: keluaran bukan objek"
                return
            if result.get("success") is False:
                # Gagal terlihat: blok bilang tidak bisa — jalan gagal
                # dengan alasan blok (bukan ditebak).
                status, reason = "failed", result.get("message") or result.get("reason") or name
                step_results.append({"block": name, "success": False, "result": result})
                return
            # Pemeriksa tetap berlaku: angka dari blok baca tanpa bukti_id
            # maksimal Belum pasti (bukan Terverifikasi).
            if name in ("check_bill", "compare_prices", "watch_prices"):
                if not result.get("evidence_id"):
                    result = dict(result, verdict="Uncertain")
            step_results.append({"block": name, "success": True, "result": result})
            result_context[name] = result
            # Simpan juga per field supaya kondisi bisa membaca
            # "{cek_tagihan.jumlah}" — konteks datar per blok sudah cukup;
            # field diakses sebagai "blok.field" di evaluasi_ungkapan.
            for k, v in result.items():
                result_context[f"{name}.{k}"] = v

    _run_steps(workflow["step"])

    # Persetujuan (bila berhenti di aksi keluar): jalan harus ada dulu.
    run_id = _save_run(
        workflow_id=workflow_id, user_id=user_id, task_id=task_id,
        trigger_type=trigger_type,
        status="pending_approval" if status == "pending_approval" else (
            "completed" if status == "completed" else (
                "pending_answer" if status == "pending_answer" else "failed")),
        cost_rp=cost, step_results=step_results,
        reason=reason, dsn=dsn)
    if status == "pending_approval" and isinstance(approval_id, dict):
        outbound_action = approval_id
        created_at = approvals.request_approval(
            run_id=run_id, workflow_id=workflow_id, user_id=user_id,
            action=outbound_action, dsn=dsn)
        approval_id = str(created_at["id"])
        _record_ledger(task_id, workflow["agent_id"], "approval_requested",
                      {"approval_id": approval_id, "action": outbound_action}, dsn)

    if status == "completed":
        _record_ledger(task_id, workflow["agent_id"], "task_completed",
                      {"workflow_id": str(workflow_id), "cost_rp": cost}, dsn)
        # Uji kering TIDAK menghitung jalan sukses (bukan jalan nyata):
        # hitungan sukses + promosi ke prosedur hanya untuk mode jalan.
        if mode != "run":
            return {"success": True, "run_id": run_id, "status": "completed",
                    "step_results": step_results, "cost_rp": cost, "reason": ""}
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE workflows SET successful_runs = successful_runs + 1,"
                    " consecutive_failures = 0 WHERE id = %s RETURNING successful_runs",
                    (workflow_id,))
                success_to = int(cur.fetchone()["successful_runs"])
            conn.commit()
        try:
            from puspa.moat.library import record_workflow_success

            record_workflow_success(user_id=user_id, name=workflow["name"],
                              block=[l.get("block") for l in workflow["step"]], dsn=dsn)
        except Exception:  # noqa: BLE001 — pustaka sekunder
            pass
        if success_to == 3:
            try:
                from puspa.memory.procedural import record_successful_task

                record_successful_task(
                    user_id, f"alur:{workflow['name']}", workflow["step"],
                    agent_id=workflow["agent_id"], dsn=dsn)
            except Exception:  # noqa: BLE001 — prosedur sekunder
                pass
        return {"success": True, "run_id": run_id, "status": "completed",
                "step_results": step_results, "cost_rp": cost, "reason": ""}
    if status in ("pending_approval", "pending_answer"):
        return {"success": False, "run_id": run_id, "status": status,
                "step_results": step_results, "cost_rp": cost,
                "reason": reason, "approval_id": approval_id}
    # Gagal: 2x berturut-turut -> dijeda otomatis + diberi tahu.
    # Uji kering yang gagal TIDAK menambah hitungan gagal (bukan jalan nyata).
    _record_ledger(task_id, workflow["agent_id"], "task_failed",
                  {"workflow_id": str(workflow_id), "reason": reason}, dsn)
    if mode != "run":
        return {"success": False, "run_id": run_id, "status": "failed",
                "step_results": step_results, "cost_rp": cost,
                "reason": reason, "paused_automatic": False}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflows SET consecutive_failures = consecutive_failures + 1"
                " WHERE id = %s RETURNING consecutive_failures",
                (workflow_id,))
            failed = int(cur.fetchone()["consecutive_failures"])
        conn.commit()
    paused = False
    if failed >= 2 and workflow["status"] == "active":
        pause_workflow(workflow_id, user_id=user_id, reason=reason, dsn=dsn)
        paused = True
    return {"success": False, "run_id": run_id, "status": "failed",
            "step_results": step_results, "cost_rp": cost,
            "reason": reason, "paused_automatic": paused}


def execute_approved_action(approval_id: str, *, user_id: str,
                            context=None, dsn=None) -> dict:
    """Eksekusi aksi keluar SETELAH user menyetujui (persetujuan sudah
    'diberikan'). Isi aksi diambil dari antrean — bukan dari input
    baru — jadi isi yang dieksekusi = isi yang disetujui."""
    from puspa.workflow.approvals import get_approval

    row = get_approval(approval_id, user_id=user_id, dsn=dsn)
    if row is None:
        raise WorkflowError("persetujuan tidak ditemukan")
    if row["status"] != "granted":
        raise WorkflowError(f"persetujuan berstatus {row['status']!r} — tidak dieksekusi")
    action = row["action"]
    name = action.get("block")
    inputs = action.get("inputs", {}) or {}
    executor = context or executor_fixture()
    fn = executor.get(name)
    if fn is None:
        raise WorkflowError(f"eksekutor belum menyediakan blok {name!r}")
    result = fn(**inputs)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflow_runs SET status = 'completed', completed_at = now()"
                " WHERE id = %s", (row["run_id"],))
        conn.commit()
    return {"success": True, "result": result}


# ---------------------------------------------------------------- tick penjadwal

def tick_workflow(*, dsn=None, now: datetime | None = None,
              missed: bool = False) -> list[dict]:
    """Satu putaran penjadwal untuk alur (§2.14): jalankan pemicu
    jadwal alur yang jatuh tempo (atau terlewat, bila `terlewat`).

    Memakai ulang `jalan_pemicu` Fase 2 (kunci idempotensi +
    `pemicu_terpicu` / `pemicu_terlewat` di Buku Kerja), lalu
    menjalankan alurnya. KONFLIK DICATAT: `jalan_pemicu` Fase 2
    menggeser `pemicu_berikutnya` +1 hari (asumsi jadwal harian) —
    untuk alur bulanan/mingguan itu salah, jadi tick ini MENIMPA
    jadwal berikutnya dari definisi pemicu alur sendiri
    (`triggers.pemicu_berikutnya`). Perbaikan +1 hari di penjadwal
    Fase 2 adalah tindak lanjut wrap-up (Fase 2 tidak diubah di sini).
    """
    from puspa.scheduler.scheduler import (
        list_due_triggers, run_trigger, run_missed,
    )
    from puspa.workflow.triggers import next_trigger_at as compute_next_trigger

    now = now or datetime.now(timezone.utc)
    if missed:
        due = run_missed(dsn=dsn, now=now)
        # jalankan_yang_terlewat sudah memanggil jalan_pemicu; ambil
        # daftar pemicunya dari hasil (pemicu_id + tugas_id).
        trigger_due = [(j["trigger_id"], j) for j in due]
    else:
        due = list_due_triggers(dsn=dsn, now=now)
        trigger_due = [(p.id, None) for p in due]

    result: list[dict] = []
    for trigger_id, trigger_run_result in trigger_due:
        # Ambil baris pemicu (untuk user_id + alur + jadwal).
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, workflow, schedule FROM triggers WHERE id = %s",
                    (trigger_id,))
                rows = cur.fetchone()
        if rows is None or not (rows["schedule"] or "").startswith("alur:"):
            continue
        user_id = str(rows["user_id"])
        workflow_id = rows["workflow"]
        workflow = get_workflow(workflow_id, user_id=user_id, dsn=dsn)
        if workflow is None or workflow["status"] != "active":
            continue
        if trigger_run_result is None:
            # Tick biasa: klaim idempotensi dulu.
            from puspa.scheduler.scheduler import Trigger as Phase2Trigger

            with _connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT next_trigger_at FROM triggers WHERE id = %s",
                        (trigger_id,))
                    next_time = cur.fetchone()["next_trigger_at"]
            trigger_obj = Phase2Trigger(
                id=trigger_id, user_id=user_id, event_type="schedule",
                schedule=rows["schedule"], workflow=workflow_id,
                next_trigger_at=next_time)
            trigger_run_result = run_trigger(
                trigger_obj, dsn=dsn, now=now, missed=missed)
            if trigger_run_result is None:
                continue  # sudah pernah jalan (idempotensi)
        # Perbaiki jadwal berikutnya dari definisi pemicu alur.
        try:
            new_next_time = compute_next_trigger(workflow["trigger"], now)
        except Exception:  # noqa: BLE001
            new_next_time = None
        if new_next_time is not None:
            with _connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE triggers SET next_trigger_at = %s WHERE id = %s",
                        (new_next_time, trigger_id))
                conn.commit()
        run = run_workflow(workflow_id, user_id=user_id, trigger_type="schedule",
                              dsn=dsn)
        _record_ledger(trigger_run_result["task_id"], workflow["agent_id"],
                      "trigger_missed" if missed else "trigger_fired",
                      {"workflow_id": str(workflow_id), "trigger_id": trigger_id,
                       "missed": missed}, dsn)
        result.append({"trigger_id": trigger_id, "workflow_id": str(workflow_id),
                      "missed": missed, "run": run})
    return result
