import { useEffect, useState } from "react";
import {
  approveOverlay,
  fetchApprovalOverlays,
  fetchLiveFrame,
  rejectOverlay,
  releaseTakeover,
  takeoverAgent,
  type ApprovalOverlay,
  type ApprovalOverlayList,
  type Bootstrap,
  type LiveFrame,
} from "../api/client";
import { ApprovalOverlayPanel } from "./ApprovalOverlay";

/** The live frame is polled: the SSE stream carries one frame for the first paint. */
export const LIVE_FRAME_POLL_MS = 5000;

const STATUS_LABEL: Record<string, string> = {
  held: "Diambil alih",
  working: "Bekerja",
  idle: "Diam",
};

/**
 * Live view of a bot (P-09, plan 12.9).
 *
 * What you see is the bot's live activity from the ledger: the
 * open task, the last tool call, and the recent event stream —
 * not a video of a desktop. Puspa has no real computer to watch
 * yet, so there is no screen recording here, and the takeover
 * button pauses the bot's autonomous work instead of driving
 * its mouse or keyboard (click-and-type control is V2, out of
 * scope). Two taps to take over, like the stop-all button: the
 * first arms it, the second pauses the bot and records the
 * takeover in the ledger.
 */
export function LiveViewScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const agents = bootstrap.agents ?? [];
  const [agentId, setAgentId] = useState(agents[0]?.agent_id ?? "");
  const [frame, setFrame] = useState<LiveFrame | null>(null);
  const [overlays, setOverlays] = useState<ApprovalOverlayList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!agentId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      try {
        const next = await fetchLiveFrame(agentId);
        if (!cancelled) {
          setFrame(next);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Gagal memuat live view.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
      // The overlays ride along with the frame but never break
      // it: when the overlay read fails, the frame still shows.
      try {
        const nextOverlays = await fetchApprovalOverlays(agentId);
        if (!cancelled) setOverlays(nextOverlays);
      } catch {
        if (!cancelled) setOverlays(null);
      }
    }
    load();
    const timer = setInterval(load, LIVE_FRAME_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [agentId]);

  async function onTakeover() {
    if (!confirming) {
      setConfirming(true);
      setNotice("");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const report = await takeoverAgent(agentId, "diambil alih dari layar Live");
      setFrame((prev) =>
        prev === null
          ? prev
          : { ...prev, status: "held", hold: { agent_id: agentId, held_by_user_id: report.held_by, reason: report.reason, task_id: report.task_id ?? null, held_at: report.held_at } },
      );
      setNotice("Bot dijeda: kerja otonom berhenti sampai kendali dilepas.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengambil alih.");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  async function onRelease() {
    setBusy(true);
    setError(null);
    try {
      await releaseTakeover(agentId);
      setFrame((prev) =>
        prev === null ? prev : { ...prev, status: "idle", hold: null },
      );
      setNotice("Kendali dilepas: bot lanjut bekerja di tugas berikutnya.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal melepas kendali.");
    } finally {
      setBusy(false);
    }
  }

  async function onDecideOverlay(
    requestId: string,
    decision: "approved" | "rejected",
    reason: string,
  ) {
    setBusy(true);
    setError(null);
    try {
      const decided =
        decision === "approved"
          ? await approveOverlay(agentId, requestId, reason)
          : await rejectOverlay(agentId, requestId, reason);
      setOverlays((prev) =>
        prev === null
          ? prev
          : {
              ...prev,
              pending: prev.pending.filter((item) => item.request_id !== requestId),
              decided: [decided, ...prev.decided],
            },
      );
      setNotice(
        decision === "approved"
          ? "Disetujui: aksi berjalan."
          : "Ditolak: aksi dibatalkan dan dicatat di Buku Kerja.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memutuskan persetujuan.");
    } finally {
      setBusy(false);
    }
  }

  const events = [...(frame?.events ?? [])].reverse().slice(0, 10);
  const pending = overlays?.pending ?? [];
  const decided = overlays?.decided ?? [];
  // Which frame rows the pending overlays point at, so the
  // screen marks exactly those rows as highlighted.
  const highlightedRefs = new Set(
    pending.flatMap((item) => (item.highlight?.frame_ref ? [item.highlight.frame_ref] : [])),
  );
  const highlightedEventIds = new Set(
    pending.flatMap((item) => (item.highlight?.event_id ? [item.highlight.event_id] : [])),
  );

  return (
    <div>
      <h1>Live</h1>
      <p className="muted">
        Yang sedang dikerjakan botmu, langsung dari Buku Kerja. Bukan rekaman layar: yang tampil
        adalah tugas, panggilan tool, dan kejadian — tangkapan layar belum tersedia karena belum
        ada backend yang bisa menghasilkan frame. Kalau bot mau melakukan aksi yang butuh
        persetujuan, elemen targetnya disorot di bawah dan kamu bisa setuju atau tolak per aksi.
      </p>
      {agents.length === 0 ? (
        <p className="muted">Belum ada agen untuk ditonton.</p>
      ) : (
        <>
          <label>
            Agen{" "}
            <select
              value={agentId}
              onChange={(event) => {
                setAgentId(event.target.value);
                setFrame(null);
                setOverlays(null);
                setLoading(true);
                setNotice("");
              }}
            >
              {agents.map((agent) => (
                <option key={agent.agent_id} value={agent.agent_id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>
          {loading && frame === null ? <p className="muted">Memuat…</p> : null}
          {error ? (
            <p aria-live="polite" className="error">
              {error}
            </p>
          ) : null}
          {frame ? (
            <>
              <p>
                Status: <strong>{STATUS_LABEL[frame.status] ?? frame.status}</strong>
                {frame.current_task_id ? (
                  <span className={highlightedRefs.has("current_task") ? "overlay-highlight" : undefined}>
                    {` · tugas ${frame.current_task_id}`}
                  </span>
                ) : (
                  ""
                )}
                {frame.last_tool ? (
                  <span className={highlightedRefs.has("last_tool") ? "overlay-highlight" : undefined}>
                    {` · tool terakhir ${frame.last_tool.tool}`}
                  </span>
                ) : (
                  ""
                )}
              </p>
              {frame.hold ? (
                <>
                  <p className="muted">
                    Diambil alih oleh {frame.hold.held_by_user_id} sejak {frame.hold.held_at}
                    {frame.hold.reason ? ` · ${frame.hold.reason}` : ""}
                  </p>
                  <button type="button" onClick={() => void onRelease()} disabled={busy}>
                    {busy ? "Melepas…" : "Lepas kendali"}
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="button-danger"
                    onClick={() => void onTakeover()}
                    disabled={busy}
                  >
                    {busy ? "Mengambil alih…" : confirming ? "Yakin ambil alih?" : "Ambil alih"}
                  </button>
                  {confirming && !busy ? (
                    <button type="button" className="button-secondary" onClick={() => setConfirming(false)}>
                      Batal
                    </button>
                  ) : null}
                </>
              )}
              {notice ? (
                <p aria-live="polite">
                  {notice} <a href="/api/proof/audit-trail">Lihat audit</a>
                </p>
              ) : null}
              {pending.length > 0 || decided.length > 0 ? (
                <>
                  <h2>Menunggu persetujuan</h2>
                  <p className="muted">
                    Elemen yang mau diklik atau dijalankan bot disorot di bawah — dan baris yang
                    sama ikut disorot di frame. Setuju atau tolak per aksi; yang ditolak batal dan
                    tercatat.
                  </p>
                  {pending.map((overlay: ApprovalOverlay) => (
                    <ApprovalOverlayPanel
                      key={overlay.request_id}
                      overlay={overlay}
                      onDecide={onDecideOverlay}
                      busy={busy}
                    />
                  ))}
                  {decided.slice(0, 3).map((overlay: ApprovalOverlay) => (
                    <ApprovalOverlayPanel
                      key={overlay.request_id}
                      overlay={overlay}
                      onDecide={async () => {}}
                      busy
                    />
                  ))}
                </>
              ) : null}
              <h2>Kejadian terakhir</h2>
              {events.length === 0 ? (
                <p className="muted">Belum ada kejadian.</p>
              ) : (
                <ul>
                  {events.map((event) => (
                    <li
                      key={`${event.id || event.type}-${event.created_at}`}
                      className={event.id && highlightedEventIds.has(event.id) ? "overlay-highlight" : undefined}
                    >
                      {event.type}
                      {event.content && typeof event.content === "object" && "tool" in event.content
                        ? ` · ${(event.content as { tool?: string }).tool}`
                        : ""}
                      {` · ${event.created_at}`}
                      {event.source === "live" ? " · live" : ""}
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
