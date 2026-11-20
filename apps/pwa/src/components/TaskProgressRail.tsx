/**
 * TaskProgressRail (P-13): the running-task rail beside Chat.
 *
 * It reads the SAME live office projection the Chat screen
 * already receives over its existing `/sse` EventSource — no
 * new fetch, no polling, no new endpoint. Each row is one
 * working agent: the task id it last started, how many of its
 * subagents are still working, and the cost so far. When the
 * stream reports the agent idle again, the row disappears:
 * the rail follows task status live because the projection
 * itself is rebuilt from ledger events on every SSE frame.
 *
 * What it cannot show: the task's purpose and stage are not
 * in the office projection (they live only in the Tasks
 * screen's session state), so the rail shows the task id, not
 * a purpose line — it never invents one.
 */
import { formatRupiah, type OfficeProjection } from "../api/client";
import { runningTaskProgress } from "../state/office";

export function TaskProgressRail({
  office,
  connected,
}: {
  office: OfficeProjection | null;
  connected: boolean;
}) {
  const running = runningTaskProgress(office);
  return (
    <aside className="card task-rail" data-testid="task-progress-rail" aria-label="Progres tugas">
      <h2>Tugas berjalan</h2>
      {!connected ? (
        <p className="muted">Live: menunggu SSE…</p>
      ) : running.length === 0 ? (
        <p className="muted">Tidak ada tugas berjalan saat ini.</p>
      ) : (
        running.map((row) => (
          <div key={row.agentId} className="task-rail-row">
            <strong>{row.agentId}</strong>
            <p className="muted">
              Tugas: {row.taskId === "" ? "—" : row.taskId}
              {row.workingSubagents > 0
                ? ` · ${row.workingSubagents} sub-agen bekerja`
                : ""}
              {` · ${formatRupiah(row.costRp)}`}
            </p>
          </div>
        ))
      )}
    </aside>
  );
}
