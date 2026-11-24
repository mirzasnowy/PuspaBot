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
import { MissionsSection } from "../components/MissionsSection";
import { PageHead } from "./space/shared";
import { Icon } from "../components/icons";
import { useLanguage } from "../state/language";

export function TasksScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const { t: tr } = useLanguage();
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
        if (!cancelled) setBoardsError(err instanceof Error ? err.message : tr("ui.tasks.load_failed", "Gagal memuat papan tugas."));
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
        error: err instanceof Error ? err.message : tr("ui.tasks.create_failed", "Gagal membuat tugas."),
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
        error: err instanceof Error ? err.message : tr("ui.tasks.stop_failed", "Gagal menghentikan tugas."),
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
        error: err instanceof Error ? err.message : tr("ui.tasks.decide_failed", "Gagal menyimpan keputusan."),
      }));
    }
  }

  const pending = pendingApprovals(approvals);
  const decided = decidedApprovals(approvals);
  const running = runningTasks(tasks);

  return (
    <div className="page">
      <PageHead title={tr("ui.tasks.title", "Tugas")} />
      <form onSubmit={onCreate} className="askbar">
        <input
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder={tr("ui.tasks.purpose_placeholder", "Contoh: bandingkan harga laptop...")}
          aria-label={tr("ui.tasks.purpose_label", "Tujuan tugas")}
          disabled={tasks.creating}
        />
        <button
          type="submit"
          className="send-fab"
          disabled={tasks.creating || purpose.trim().length === 0}
          aria-label={tr("ui.tasks.run", "Jalankan tugas")}
        >
          <Icon name="send" size={20} />
        </button>
      </form>
      {tasks.error ? <p role="alert" className="error">{tasks.error}</p> : null}

      <h2>{tr("ui.tasks.session", "Tugas sesi ini")}</h2>
      {tasks.tasks.length === 0 ? (
        <p className="muted">{tr("ui.tasks.session_empty", "Belum ada tugas dibuat di sesi ini.")}</p>
      ) : (
        <div className="card">
          {tasks.tasks.map((t) => (
            <div
              key={t.taskId}
              className={`task${t.status === "completed" ? " done" : ""}`}
            >
              <span className="check" aria-hidden="true">
                {t.status === "completed" ? "✓" : ""}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span className="tt">{t.purpose}</span>
                <span
                  style={{
                    display: "block",
                    color: "var(--ink-2)",
                    fontSize: 13,
                  }}
                >
                  Status: {t.status}
                  {t.stage ? ` · ${tr("ui.tasks.stage", "tahap:")} ${t.stage}` : ""}
                </span>
                {t.question ? <span>{tr("ui.tasks.question", "Pertanyaan:")} {t.question}</span> : null}
              </span>
              {!t.stopped && t.status !== "completed" ? (
                <button
                  type="button"
                  className="btn ghost sm"
                  onClick={() => onStop(t.taskId)}
                >
                  {tr("ui.tasks.stop", "Hentikan")}
                </button>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {running.length > 0 ? <p className="muted">{tr("ui.tasks.running_count", "{count} tugas berjalan.", { count: running.length })}</p> : null}

      <h2>{tr("ui.tasks.board", "Papan tugas bersama")}</h2>
      <p className="muted">
        {tr("ui.tasks.board_note", "Tugas yang dibagikan di grup: pemilik, tenggat, status, dan siapa yang mengerjakannya. Langkah yang tidak bisa dibatalkan menunggu persetujuan pemilik tugas.")}
      </p>
      {boardsError ? <p role="alert" className="error">{boardsError}</p> : null}
      {boards.length === 0 && !boardsError ? (
        <p className="muted">{tr("ui.tasks.no_groups", "Belum ada grup yang kamu ikuti.")}</p>
      ) : (
        boards.map((board) => (
          <div key={board.room_id}>
            <h3>{board.room_name}</h3>
            {board.tasks.length === 0 ? (
              <p className="muted">{tr("ui.tasks.board_empty", "Belum ada tugas di papan ini.")}</p>
            ) : (
              board.tasks.map((task) => (
                <div key={task.id} className="card">
                  <strong>{task.title}</strong>
                  <p className="muted">
                    Status: {task.status}
                    {task.due_at ? ` · ${tr("ui.tasks.due", "tenggat:")} ${task.due_at}` : ` · ${tr("ui.tasks.no_due", "tanpa tenggat")}`}
                    {task.delegation
                      ? ` · ${tr(task.delegation.delegate_kind === "agent" ? "ui.tasks.delegated_by_agent" : "ui.tasks.delegated_by_peer", task.delegation.delegate_kind === "agent" ? "dikerjakan agen" : "dikerjakan rekan")} (${task.delegation.status})`
                      : ""}
                  </p>
                  {task.delegation?.last_report ? <p>{tr("ui.tasks.report", "Laporan:")} {task.delegation.last_report}</p> : null}
                  {task.delegation?.blocked_reason ? (
                    <p>{tr("ui.tasks.blocked", "Terhambat:")} {task.delegation.blocked_reason}</p>
                  ) : null}
                  {task.pending_steps > 0 ? (
                    <p>{tr("ui.tasks.pending_steps", "{count} langkah menunggu persetujuan atau eksekusi.", { count: task.pending_steps })}</p>
                  ) : null}
                </div>
              ))
            )}
          </div>
        ))
      )}

      <h2>{tr("ui.tasks.approvals", "Antrean persetujuan")}</h2>
      <p className="muted">
        {tr("ui.tasks.approvals_note", "Semua tindakan keluar menunggu di sini. Persetujuan terikat isi: kalau isi berubah setelah disetujui, persetujuan batal dan diminta ulang.")}
      </p>
      {approvals.error ? <p role="alert" className="error">{approvals.error}</p> : null}
      {pending.length === 0 ? (
        <p className="muted">{tr("ui.tasks.approvals_empty", "Tidak ada persetujuan menunggu.")}</p>
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
          <h3>{tr("ui.tasks.decided", "Sudah diputuskan")}</h3>
          {decided.map((item) => (
            <ApprovalCard key={item.request_id} item={item} deciding={false} onDecide={onDecide} />
          ))}
        </>
      ) : null}

      <MissionsSection />
      <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
        <a className="btn ghost sm" href="/misi">
          {tr("ui.tasks.missions", "Misi")}
        </a>
        <a className="btn ghost sm" href="/delegasi">
          {tr("ui.tasks.delegation", "Tugas Delegasi")}
        </a>
        <a className="btn ghost sm" href="/progres">
          {tr("ui.tasks.progress", "Progres Tugas")}
        </a>
      </div>
    </div>
  );
}
