import { useEffect, useState } from "react";
import {
  createTask,
  decideApproval,
  fetchRoomBoard,
  fetchRooms,
  stopTask,
  type Bootstrap,
  type RoomBoard,
} from "../api/client";
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
  const [boards, setBoards] = useState<RoomBoard[]>([]);
  const [boardsError, setBoardsError] = useState<string | null>(null);

  // Shared task boards (B9): the user's rooms and each room's
  // board, fetched once when the screen opens. A room with no
  // tasks still renders, so the group board is never hidden.
  useEffect(() => {
    let cancelled = false;
    fetchRooms()
      .then((result) => Promise.all(result.rooms.map((room) => fetchRoomBoard(room.id))))
      .then((loaded) => {
        if (!cancelled) setBoards(loaded);
      })
      .catch((err: unknown) => {
        if (!cancelled) setBoardsError(err instanceof Error ? err.message : "Gagal memuat papan tugas.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

      <h2>Papan tugas bersama</h2>
      <p className="muted">
        Tugas yang dibagikan di grup: pemilik, tenggat, status, dan siapa yang mengerjakannya.
        Langkah yang tidak bisa dibatalkan menunggu persetujuan pemilik tugas.
      </p>
      {boardsError ? <p role="alert" className="error">{boardsError}</p> : null}
      {boards.length === 0 && !boardsError ? (
        <p className="muted">Belum ada grup yang kamu ikuti.</p>
      ) : (
        boards.map((board) => (
          <div key={board.room_id}>
            <h3>{board.room_name}</h3>
            {board.tasks.length === 0 ? (
              <p className="muted">Belum ada tugas di papan ini.</p>
            ) : (
              board.tasks.map((task) => (
                <div key={task.id} className="card">
                  <strong>{task.title}</strong>
                  <p className="muted">
                    Status: {task.status}
                    {task.due_at ? ` · tenggat: ${task.due_at}` : " · tanpa tenggat"}
                    {task.delegation
                      ? ` · dikerjakan ${task.delegation.delegate_kind === "agent" ? "agen" : "rekan"} (${task.delegation.status})`
                      : ""}
                  </p>
                  {task.delegation?.last_report ? <p>Laporan: {task.delegation.last_report}</p> : null}
                  {task.delegation?.blocked_reason ? (
                    <p>Terhambat: {task.delegation.blocked_reason}</p>
                  ) : null}
                  {task.pending_steps > 0 ? (
                    <p>{task.pending_steps} langkah menunggu persetujuan atau eksekusi.</p>
                  ) : null}
                </div>
              ))
            )}
          </div>
        ))
      )}

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
