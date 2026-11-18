import { useEffect, useState } from "react";
import {
  fetchOperatorStatus,
  formatRupiah,
  parseSseEvent,
  stopTask,
  type Bootstrap,
  type OfficeProjection,
  type OperatorStatus,
} from "../api/client";
import { applyOfficeEvent, officeAgents, totalRunningCostRp, workingCount } from "../state/office";
import { OperatorPanel } from "../components/OperatorPanel";

export const SPENDING_CAP_RP = 2000;

/** The operator status is polled: the SSE stream carries the office
 * projection, not the operator panels (G31). */
export const OPERATOR_POLL_MS = 5000;

export function OfficeScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const [office, setOffice] = useState<OfficeProjection | null>(bootstrap.office ?? null);
  const [error, setError] = useState<string | null>(null);
  const [operatorStatus, setOperatorStatus] = useState<OperatorStatus | null>(null);
  const [operatorLoading, setOperatorLoading] = useState(true);
  const [operatorError, setOperatorError] = useState<string | null>(null);

  useEffect(() => {
    const source = new EventSource("/sse");
    source.onmessage = (event) => {
      const parsed = parseSseEvent(event.data);
      if (parsed) setOffice((prev) => applyOfficeEvent(prev, parsed));
    };
    return () => source.close();
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadOperatorStatus() {
      try {
        const status = await fetchOperatorStatus();
        if (!cancelled) {
          setOperatorStatus(status);
          setOperatorError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setOperatorError(err instanceof Error ? err.message : "Gagal memuat status operator.");
        }
      } finally {
        if (!cancelled) setOperatorLoading(false);
      }
    }
    loadOperatorStatus();
    const timer = setInterval(loadOperatorStatus, OPERATOR_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const agents = officeAgents(office);
  const handoffs = (bootstrap.handoffs ?? []) as Record<string, unknown>[];

  async function onStop(taskId: string) {
    try {
      await stopTask(taskId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghentikan tugas.");
    }
  }

  return (
    <div>
      <h1>Kantor</h1>
      <p className="muted">
        Tim yang sedang bekerja. Sumbernya Buku Kerja, bukan animasi: status, progress, biaya, dan
        log adalah proyeksi dari log kejadian.
      </p>
      <p className="muted">
        {workingCount(office)} agen bekerja · biaya berjalan {formatRupiah(totalRunningCostRp(office))}{" "}
        dari plafon {formatRupiah(SPENDING_CAP_RP)} per tugas.
      </p>
      {error ? <p role="alert" className="error">{error}</p> : null}
      {agents.length === 0 ? (
        <p className="muted">Tidak ada agen bekerja saat ini.</p>
      ) : (
        agents.map((a) => (
          <div key={a.agent_id ?? "unknown"} className="card">
            <strong>{a.agent_id ?? "—"}</strong>{" "}
            <span className="badge">{a.status === "working" ? "Bekerja" : "Siaga"}</span>
            <p className="muted">
              Tugas: {a.last_task || "—"}
              <br />
              Biaya: {formatRupiah(a.cost_rp ?? 0)} dari plafon {formatRupiah(SPENDING_CAP_RP)}
            </p>
            <p className="muted">
              Sub-agen:{" "}
              {(a.subagents ?? [])
                .map((s) => `${s.permission_class ?? "?"} (${s.status ?? "?"})`)
                .join(", ") || "—"}
            </p>
            {a.status === "working" && a.last_task ? (
              <button type="button" onClick={() => onStop(a.last_task as string)}>
                Hentikan
              </button>
            ) : null}
          </div>
        ))
      )}
      <h2>Log handoff live</h2>
      {handoffs.length === 0 ? (
        <p className="muted">Belum ada handoff.</p>
      ) : (
        handoffs.map((h, i) => (
          <div key={i} className="card">
            {String(h.type ?? h.event_type ?? "handoff")}
            {" — "}
            {String(((h.content as Record<string, unknown> | undefined)?.purpose ?? ""))}
          </div>
        ))
      )}
      <OperatorPanel status={operatorStatus} loading={operatorLoading} error={operatorError} />
    </div>
  );
}
