"""Siklus tugas 5 tahap (§2.4 revisi 7).

Terima -> Rencana -> Jalankan -> Periksa -> Sajikan.

- Terima: agen menyatakan ulang tujuan dalam satu kalimat. Kalau
  ambigu (tujuan kurang dari 3 kata), ia bertanya maks 1 pertanyaan
  sebelum bekerja dan status menjadi `menunggu_user`.
- Rencana: graf langkah; tiap langkah punya masukan, keluaran
  ber-skema, kelas izin sub-agen, anggaran, syarat selesai. Tugas di
  bawah 3 langkah jalan tanpa menampilkan rencana. Tugas yang memuat
  aksi keluar menampilkan rencana dulu dan berhenti di titik
  persetujuan.
- Jalankan: langkah yang tidak saling bergantung jalan paralel
  (dalam batas §2.2); hasil tiap langkah masuk lewat kontrak handoff.
  Di sini eksekusi langkah adalah callable yang disuntikkan pemanggil
  (`pelaksana`), supaya runner bisa diuji tanpa model dan tanpa
  jaringan; runtime produksi menyuntikkan pelaksana yang memanggil
  model + tool lewat chassis Fase 0.
- Periksa: hook Pemeriksa untuk klaim angka (disuntikkan; default
  lolos bila tidak ada klaim angka).
- Sajikan: agen pemilik tugas (juru bicara tunggal) menggabungkan
  hasil, merender pesan dari bukti, dan menutup tugas.

Semua tahap menulis kejadian ke Buku Kerja lewat `sink` (callable
`(jenis, isi)` — di produksi `Ledger.append_event`, di tes sink
in-memory). Antrean tugas memakai Postgres SKIP LOCKED
(`klaim_tugas_berikutnya`, §2.3) — tanpa broker tambahan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from puspa.runtime.budget import BudgetExhausted, BudgetTracker
from puspa.runtime.handoff import Handoff, HandoffContract

STAGES = ("accept", "plan", "run", "verify", "present")

#: Event types the brain wiring records through the sink. They are
#: advisory only: a prediction never gates anything, and the numbers
#: in them are never meaningful numbers (those still come from the
#: code renderer and the Book of Evidence). Note the ledger keeps a
#: closed set of 31 event types (revision 7 section 2.3), so a
#: production Ledger sink drops these two types silently
#: (best-effort, see `make_ledger_sink`); the same prediction is
#: therefore also attached to the `handoff_proposed` /
#: `handoff_handed_over` event content, which the ledger does keep.
WORLD_PREDICTION_EVENT = "world_prediction"
WORLD_MISPREDICTION_EVENT = "world_misprediction"


def _world_action_for_step(step: "Step") -> dict | None:
    """Translate a plan step into a world-model action, or None.

    A step whose inputs carry a ``type`` the world model knows
    (``check_price``, ``check_bill``, ``pay_bill``, ``wait_day``,
    ``buy_from_store``, ``estimate_task``, ``attempt_task``) is
    predicted and scored as that action. Any other step has no
    world-model action: it is not predicted, but its outcome is
    still folded into the model through ``ingest_task_result``
    (capability and schedule beliefs). Returns None when the step
    has no world-model action.
    """
    from puspa.world.model import ACTION_TYPES

    action_type = (step.inputs or {}).get("type")
    if action_type in ACTION_TYPES:
        action = dict(step.inputs)
        action.setdefault("task_kind", step.name)
        return action
    return None


class TaskError(Exception):
    """Siklus tugas gagal / tidak sah."""


@dataclass
class Step:
    """Satu langkah rencana."""

    name: str
    inputs: dict
    expected_output: dict
    permission_class: str = "Pelaksana"
    budget: dict = field(default_factory=lambda: {"step_max": 15, "token_max": 20000, "rupiah_max": 2000})
    completion_condition: str = ""
    outbound_action: bool = False
    parallel_with: list[str] = field(default_factory=list)


@dataclass
class Task:
    """Satu tugas yang sedang berjalan."""

    task_id: str
    user_id: str
    agent_id: str
    purpose: str
    plan: list[Step] = field(default_factory=list)
    status: str = "accepted"
    stage: str = "accept"
    question: str = ""
    result: dict = field(default_factory=dict)
    cost_rp: int = 0


def create_plan(purpose: str, steps: list[Step]) -> list[Step]:
    """Validasi rencana: langkah 1..25, tiap langkah ber-skema."""
    if not 1 <= len(steps) <= 25:
        raise TaskError("rencana harus 1..25 langkah")
    for step in steps:
        if "type" not in step.inputs or "type" not in step.expected_output:
            raise TaskError(f"langkah {step.name!r}: masukan/keluaran harus ber-skema ('jenis')")
        if step.permission_class not in ("Pembaca", "Pelaksana", "Pemeriksa"):
            raise TaskError(f"langkah {step.name!r}: kelas_izin tidak dikenal")
    return steps


class TaskRunner:
    """Menjalankan satu tugas lewat 5 tahap, menulis ke Buku Kerja.

    Brain wiring (advisory only, fail-open): pass a ``world_model``
    (``puspa.world.WorldModel``) and a ``learning_loop``
    (``puspa.memory.learning_loop.LearningLoop``) and the runner
    predicts each step's world action before acting, scores the
    real result against that prediction after the step, and folds
    the outcome into the model. A prediction never gates anything:
    it does not skip the verifier, the permission token, the single
    outbound gate, or the orphan-number rule, and its numbers are
    never presented as meaningful numbers. When the model or the
    loop is missing or raises, the runner keeps working exactly as
    before (fail-open for prediction, fail-closed for action: no
    action is ever taken on the model's word alone). A missed
    prediction is reported to the learning loop through
    ``WorldModel.update`` (``record_misprediction`` seam); when a
    session loop is given here it is also set as the model's
    ``learning_loop`` so the report goes to that loop instead of
    the module-level default.

    A step that aborts the run also teaches the model, through
    ``WorldModel.ingest_step_failure`` (see ``_fold_step_failure``):
    a gate/policy refusal (handoff rejected, budget exhausted)
    teaches capability only and discards the pending prediction
    unscored, while a world failure (the executor raised) scores
    a pending prediction against the failure, so a miss becomes
    a misprediction in the learning loop.
    """

    def __init__(self, *, sink=None, executor=None, verifier=None,
                 budget: BudgetTracker | None = None,
                 world_model=None, learning_loop=None) -> None:
        # sink(jenis, isi): pencatat kejadian; default buang ke memori.
        self._event: list[dict] = []
        self.sink = sink or (lambda event_type, content: self._event.append({"type": event_type, "content": content}))
        # pelaksana(nama_langkah, masukan) -> dict hasil ber-skema.
        self.executor = executor or (lambda name, inputs: {"type": inputs.get("type", "result"), "value": None})
        # pemeriksa(hasil) -> (lolos: bool, alasan: str).
        self.verifier = verifier or (lambda result: (True, ""))
        self.budget = budget or BudgetTracker()
        self.world_model = world_model
        self.learning_loop = learning_loop
        if world_model is not None and learning_loop is not None:
            try:
                world_model.learning_loop = learning_loop
            except Exception:
                pass

    def _predict_step(self, task: Task, step: Step, action: dict):
        """Predict one step's action before acting (advisory).

        Returns the ``Prediction`` or None when there is no model
        or the prediction fails. The prediction is recorded as a
        `world_prediction` event; it never changes the plan.
        """
        try:
            prediction = self.world_model.predict(action)
        except Exception:
            return None
        self._record(WORLD_PREDICTION_EVENT, {
            "task_id": task.task_id, "step": step.name,
            "action": dict(action),
            "predicted": dict(prediction.outcome),
            "confidence": prediction.confidence,
            "advisory": True,
        })
        return prediction

    def _observe_step(self, task: Task, step: Step, action: dict,
                      result: dict):
        """Score one step's result against its prediction.

        Calls ``WorldModel.update`` (which scores the pending
        prediction, corrects the model, and reports a miss to the
        learning loop through the ``record_misprediction`` seam)
        and records a `world_misprediction` event when the
        prediction missed. Returns the update outcome dict, or
        None when there is no model or the update fails. Never
        raises: a failed observation must not fail the task.
        """
        observed = dict(result) if isinstance(result, dict) else {}
        # A step that completed passed its handoff, so it succeeded
        # unless its result says otherwise.
        observed.setdefault("success", True)
        try:
            outcome = self.world_model.update({
                "action": dict(action), "observed": observed,
                "task": step.name,
                "day": observed.get("day"),
            })
        except Exception:
            return None
        if outcome.get("correct") is False:
            self._record(WORLD_MISPREDICTION_EVENT, {
                "task_id": task.task_id, "step": step.name,
                "task_name": step.name,
                "predicted": outcome.get("predicted"),
                "observed": outcome.get("observed"),
                "error": outcome.get("error"),
                "context": {"action": dict(action)},
                "advisory": True,
            })
        return outcome

    def _ingest_step(self, task: Task, step: Step, result: dict) -> None:
        """Fold a step outcome with no world action into the model.

        Only for steps ``_world_action_for_step`` maps to None:
        steps with a world action are already folded by
        ``WorldModel.update`` (price, bill, schedule, capability),
        so ingesting them again would count one outcome twice.
        Never raises.
        """
        if not isinstance(result, dict):
            return
        try:
            self.world_model.ingest_task_result({
                "task": step.name,
                "success": bool(result.get("success", True)),
                "result": result,
            })
        except Exception:
            pass

    def _fold_step_failure(self, task: Task, step: Step, action: dict | None,
                           *, failure_kind: str, reason: str) -> None:
        """Fold a step that aborted the run into the world model.

        Two separate paths, and the difference is deliberate (see
        ``WorldModel.ingest_step_failure``):
        - ``"gate_rejected"`` (handoff refused by the schema,
          budget exhausted): capability learns, the pending
          prediction is discarded unscored, and no price/bill
          belief and no learning-loop correction is touched. A
          refusal is policy evidence, not world evidence.
        - ``"world_failed"`` (the executor raised): a pending
          prediction is scored against the failure, so a miss
          is recorded as a ``world_misprediction`` event and
          reported to the learning loop, and capability learns.
        The failure itself is recorded by the caller as the
        existing ``task_failed`` ledger event (with
        ``failure_kind`` and the step in its content). Never
        raises: a failed fold must not fail the task twice.
        """
        if self.world_model is None:
            return
        try:
            summary = self.world_model.ingest_step_failure(
                task_kind=step.name, action=action,
                failure_kind=failure_kind, reason=reason)
        except Exception:
            return
        scored = summary.get("scored") if isinstance(summary, dict) else None
        if isinstance(scored, dict) and scored.get("correct") is False:
            self._record(WORLD_MISPREDICTION_EVENT, {
                "task_id": task.task_id, "step": step.name,
                "task_name": step.name,
                "predicted": scored.get("predicted"),
                "observed": scored.get("observed"),
                "error": scored.get("error"),
                "context": {"action": dict(action) if action else None},
                "failure_kind": failure_kind,
                "advisory": True,
            })

    def _record(self, event_type: str, content: dict) -> None:
        try:
            self.sink(event_type, content)
        except Exception:
            # Penulisan ledger best-effort (keputusan Fase 0 no. 9):
            # Postgres mati tidak boleh menjatuhkan turn.
            pass

    def run(self, task: Task) -> Task:
        """Jalankan 5 tahap sampai selesai / gagal / menunggu."""
        self._record("task_created", {"task_id": task.task_id, "agent_id": task.agent_id, "purpose": task.purpose})
        # 1. Terima.
        task.stage = "accept"
        if len(task.purpose.split()) < 3:
            task.question = "Boleh dijelaskan sedikit lagi? Tujuanmu masih terlalu singkat."
            task.status = "pending_user"
            self._record("agent_message", {"task_id": task.task_id, "text": task.question})
            return task
        # 2. Rencana.
        task.stage = "plan"
        if task.plan:
            create_plan(task.purpose, task.plan)
        self._record("plan_created", {"task_id": task.task_id,
                                       "step": [step.name for step in task.plan]})
        if any(step.outbound_action for step in task.plan):
            # Berhenti di titik persetujuan sebelum menjalankan apa pun.
            task.status = "pending_approval"
            self._record("approval_requested", {"task_id": task.task_id, "reason": "rencana memuat aksi keluar"})
            return task
        # 3. Jalankan.
        task.stage = "run"
        # The step being attempted, for the failure handlers below:
        # a step that aborts the run still teaches the model, and
        # the fold needs to know which step and world action it was.
        current_step: Step | None = None
        current_action: dict | None = None
        failed_step: Step | None = None
        failure_kind: str | None = None
        try:
            for step in task.plan:
                current_step = step
                current_action = None
                self.budget.record_step(1)
                contract = HandoffContract(
                    from_id=task.agent_id, to_id=f"{task.agent_id}:pelaksana",
                    purpose=step.name, inputs=step.inputs,
                    expected_output=step.expected_output,
                    budget=step.budget, permission_class=step.permission_class,
                    completion_definition=step.completion_condition or step.name,
                )
                handoff = Handoff(contract)
                # Brain (advisory): predict the step's world action
                # before anything runs. A missing model, an
                # unmapped step, or a failed prediction means no
                # prediction, never a failed task.
                action = None
                prediction = None
                if self.world_model is not None:
                    action = _world_action_for_step(step)
                    current_action = action
                    if action is not None:
                        prediction = self._predict_step(task, step, action)
                proposed = {"task_id": task.task_id, "handoff_id": handoff.id, "purpose": step.name}
                if prediction is not None:
                    # Same advisory prediction, kept in the ledger:
                    # the closed event-type set drops the dedicated
                    # `world_prediction` event, this key survives.
                    proposed["world_prediction"] = {
                        "action": dict(action),
                        "predicted": dict(prediction.outcome),
                        "confidence": prediction.confidence,
                        "advisory": True,
                    }
                self._record("handoff_proposed", proposed)
                handoff.accept()
                handoff.start()
                try:
                    result = self.executor(step.name, step.inputs)
                except BudgetExhausted:
                    # The budget policy refused the step (for
                    # example a charging executor whose cost would
                    # pass the ceiling): let it reach the outer
                    # BudgetExhausted handler, which records
                    # `budget_exhausted` and folds it as a
                    # gate refusal. Catching it here would
                    # misclassify the refusal as a world failure
                    # and lose the budget event.
                    raise
                except Exception as exc:
                    # The action ran and the world said no: score a
                    # pending prediction against the failure (a miss
                    # becomes a misprediction in the learning loop),
                    # teach capability, and fail the task instead
                    # of crashing the run.
                    failed_step = step
                    failure_kind = "world_failed"
                    self._fold_step_failure(
                        task, step, action, failure_kind=failure_kind,
                        reason=f"{type(exc).__name__}: {exc}")
                    raise TaskError(f"langkah {step.name!r} gagal: {exc}") from exc
                hand_over_status = handoff.hand_over(result)
                if hand_over_status == "rejected":
                    # serahkan() sudah menandai status 'ditolak' + alasan.
                    self._record("handoff_rejected", {"task_id": task.task_id, "handoff_id": handoff.id})
                    # Refused by policy (schema), not by the world:
                    # capability learns, the pending prediction is
                    # discarded unscored, price/bill beliefs are
                    # never touched, no misprediction is reported.
                    failed_step = step
                    failure_kind = "gate_rejected"
                    self._fold_step_failure(
                        task, step, action, failure_kind=failure_kind,
                        reason="keluaran tidak lolos skema")
                    raise TaskError(f"langkah {step.name!r} ditolak: keluaran tidak lolos skema")
                handoff.mark_passed()
                handed_over = {"task_id": task.task_id, "handoff_id": handoff.id}
                if action is not None and self.world_model is not None:
                    # Brain: score the real result against the
                    # prediction and correct the model.
                    outcome = self._observe_step(task, step, action, result)
                    if outcome is not None:
                        handed_over["world_observation"] = outcome
                self._record("handoff_handed_over", handed_over)
                task.result[step.name] = result
                if action is None and self.world_model is not None:
                    self._ingest_step(task, step, result)
        except BudgetExhausted as exc:
            # The step never ran: refused by the budget policy.
            # Capability learns, the pending prediction (none yet
            # for this step) is discarded unscored, price/bill
            # beliefs are never touched, no misprediction.
            if current_step is not None:
                failed_step = current_step
                failure_kind = "gate_rejected"
                self._fold_step_failure(
                    task, current_step, current_action,
                    failure_kind=failure_kind, reason=str(exc))
            task.status = "failed"
            self._record("budget_exhausted", {"task_id": task.task_id, "reason": str(exc)})
            self._record("task_failed", {
                "task_id": task.task_id, "reason": str(exc),
                "failure_kind": failure_kind,
                "step": failed_step.name if failed_step is not None else None,
            })
            return task
        except TaskError as exc:
            task.status = "failed"
            self._record("task_failed", {
                "task_id": task.task_id, "reason": str(exc),
                "failure_kind": failure_kind,
                "step": failed_step.name if failed_step is not None else None,
            })
            return task
        # 4. Periksa.
        task.stage = "verify"
        passed, reason = self.verifier(task.result)
        self._record("verification_completed", {"task_id": task.task_id, "passed": passed, "reason": reason})
        if not passed:
            task.status = "failed"
            self._record("task_failed", {"task_id": task.task_id, "reason": reason or "pemeriksaan gagal"})
            return task
        # 5. Sajikan (juru bicara tunggal = agen pemilik tugas).
        task.stage = "present"
        task.status = "completed"
        task.cost_rp = self.budget.rupiah_used
        self._record("task_completed", {"task_id": task.task_id, "agent_id": task.agent_id})
        return task

    def event_memory(self) -> list[dict]:
        """Kejadian yang dicatat sink default (untuk tes)."""
        return list(self._event)


def claim_next_task(dsn: str, agent_id: str) -> dict | None:
    """Ambil satu tugas `diterima` lewat SELECT FOR UPDATE SKIP LOCKED.

    Kembalikan baris tugas (dict) atau None bila antrean kosong.
    Tanpa broker tambahan (§2.3).
    """
    import os

    import psycopg

    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM tasks WHERE agent_id = %s AND status = 'accepted'"
                " ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED",
                (agent_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("UPDATE tasks SET status = 'running' WHERE id = %s", (row["id"],))
        conn.commit()
    return dict(row)


def create_task_id() -> str:
    return f"tugas-{uuid.uuid4().hex[:12]}"


def tombstone_task(task_id: str, user_id: str, agent_id: str, *, dsn=None,
                    sink=None, cassette_dir=None) -> dict:
    """Hapus tugas (C129): isi kejadian diganti tanda hapus TANPA
    UPDATE/DELETE di Buku Kerja — append `tugas_dihentikan` dengan
    isi {"dihapus": true}; proyeksi menyembunyikan tugas. Kaset +
    blob tugas ikut dihapus. Rantai hash tetap utuh.

    `sink(jenis, isi)` mencatat ke Buku Kerja; bila None, dipakai
    `Ledger.append_event` langsung.
    """
    from puspa.cassette.cassette import delete_cassette_task

    if sink is None:
        from puspa.ledger import Ledger

        ledger = Ledger(dsn)
        sink = lambda event_type, content: ledger.append_event(
            task_id=task_id, agent_id=agent_id, event_type=event_type, content=content)
    sink("task_stopped", {"task_id": task_id, "deleted": True,
                              "user_id": user_id})
    cassette = delete_cassette_task(task_id, cassette_dir=cassette_dir, dsn=dsn)
    return {"task_id": task_id, "tombstone": True, "cassette_deleted": cassette["deleted"]}
