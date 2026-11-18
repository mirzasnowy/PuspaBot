import { useState } from "react";
import { createTask, decideApproval, stopTask, type Bootstrap } from "../api/client";
import {
  addTaskOutcome,
  initialTasksState,
  markTaskStopped,
  runningTasks,
} from "../state/tasks";
import {
  applyDecision,
  initialApprovalsState,
  markDeciding,
  pendingApprovals,
  decidedApprovals,
} from "../state/approvals";
import { ApprovalCard } from "../components/ApprovalCard";

export function TasksScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const [tasks, setTasks] = useState(initialTasksState);
  const [approvals, setApprovals] = useState(() =>
    initialApprovalsState(bootstrap.approvals ?? []),
  );
  const [purpose, setPurpose] = useState("");

  async function onCreate(event: React.FormEvent) {
    event.preventDefault();
    const value = purpose.trim();
    if (!value || tasks.creating) return;
    setPurpose("");
    setTasks((prev) => ({ ...prev, creating: true, error: null }));
    try {
      const outcome = await createTask(value);
      setTasks((prev) => ({ ...addTaskOutcome(prev, value, outcome), creating: false }));
    } catch (err) {
      setTasks((prev) => ({
        ...prev,
        creating: false,
        error: err instanceof Error ? err.message : "Gagal membuat tugas.",
      }));
    }
  }

  async function onStop(taskId: string) {
    try {
      await stopTask(taskId);
      setTasks((prev) => markTaskStopped(prev, taskId));
    } catch (err) {
      setTasks((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "Gagal menghentikan tugas.",
      }));
    }
  }

  async function onDecide(requestId: string, approve: boolean) {
    setApprovals((prev) => markDeciding(prev, requestId));
    try {
      const result = await decideApproval(requestId, approve);
      setApprovals((prev) => applyDecision(prev, requestId, result.status));
    } catch (err) {
      setApprovals((prev) => ({
        ...prev,
        deciding: { ...prev.deciding, [requestId]: false },
        error: err instanceof Error ? err.message : "Gagal menyimpan keputusan.",
      }));
    }
  }

  const pending = pendingApprovals(approvals);
  const decided = decidedApprovals(approvals);
  const running = runningTasks(tasks);

  return (
    <div>
      <h1>Tugas</h1>
      <h2>Buat tugas baru</h2>
      <form onSubmit={onCreate} className="composer">
        <input
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder="Contoh: bandingkan harga laptop..."
          aria-label="Tujuan tugas"
          disabled={tasks.creating}
        />
        <button type="submit" disabled={tasks.creating || purpose.trim().length === 0}>
          {tasks.creating ? "Menjalankan…" : "Jalankan"}
        </button>
      </form>
      {tasks.error ? <p role="alert" className="error">{tasks.error}</p> : null}

      <h2>Tugas sesi ini</h2>
      {tasks.tasks.length === 0 ? (
        <p className="muted">Belum ada tugas dibuat di sesi ini.</p>
      ) : (
        tasks.tasks.map((t) => (
          <div key={t.taskId} className="card">
            <strong>{t.purpose}</strong>
            <p className="muted">
              Status: {t.status}
              {t.stage ? ` · tahap: ${t.stage}` : ""}
            </p>
            {t.question ? <p>Pertanyaan: {t.question}</p> : null}
            {!t.stopped && t.status !== "completed" ? (
              <button type="button" onClick={() => onStop(t.taskId)}>
                Hentikan
              </button>
            ) : null}
          </div>
        ))
      )}
      {running.length > 0 ? <p className="muted">{running.length} tugas berjalan.</p> : null}

      <h2>Antrean persetujuan</h2>
      <p className="muted">
        Semua tindakan keluar menunggu di sini. Persetujuan terikat isi: kalau isi berubah setelah
        disetujui, persetujuan batal dan diminta ulang.
      </p>
      {approvals.error ? <p role="alert" className="error">{approvals.error}</p> : null}
      {pending.length === 0 ? (
        <p className="muted">Tidak ada persetujuan menunggu.</p>
      ) : (
        pending.map((item) => (
          <ApprovalCard
            key={item.request_id}
            item={item}
            deciding={approvals.deciding[item.request_id] ?? false}
            onDecide={onDecide}
          />
        ))
      )}
      {decided.length > 0 ? (
        <>
          <h3>Sudah diputuskan</h3>
          {decided.map((item) => (
            <ApprovalCard key={item.request_id} item={item} deciding={false} onDecide={onDecide} />
          ))}
        </>
      ) : null}

      <h2>Jadwal otomatis</h2>
      <p className="muted">
        Penjadwal berjalan di server dan memicu tugas dari pemicu terjadwal. Daftar jadwal belum
        punya endpoint baca di API ini, jadi jadwal hanya terlihat di layar Kantor saat tugasnya
        berjalan — endpoint baca jadwal adalah follow-up.
      </p>
    </div>
  );
}
