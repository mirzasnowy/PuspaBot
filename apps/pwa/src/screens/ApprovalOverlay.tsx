import { useState } from "react";
import type { ApprovalOverlay } from "../api/client";

/**
 * One approval overlay on the live view (P-11, plan 12.10).
 *
 * The target element the bot is about to act on is shown in a
 * highlighted box — the button it is about to click, or the tool
 * it is about to run — with the server-rendered card lines
 * underneath, so the user sees exactly what the approval covers.
 * Approve or reject per action; a rejection cancels the action
 * and is recorded in the ledger. There is no screenshot behind
 * this box: the highlight points at the matching row of the
 * event-based live frame, not at a pixel on a desktop.
 */
export function ApprovalOverlayPanel({
  overlay,
  onDecide,
  busy,
}: {
  overlay: ApprovalOverlay;
  onDecide: (
    requestId: string,
    decision: "approved" | "rejected",
    reason: string,
  ) => Promise<void>;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  const [deciding, setDeciding] = useState(false);
  const target = overlay.target_element;
  const lines = overlay.card.lines ?? [];

  async function decide(decision: "approved" | "rejected") {
    setDeciding(true);
    try {
      await onDecide(overlay.request_id, decision, reason);
    } finally {
      setDeciding(false);
    }
  }

  if (overlay.status !== "pending") {
    return (
      <div className="card">
        <p>
          {overlay.card.title} ·{" "}
          {overlay.status === "approved" ? "Disetujui" : "Ditolak"}
          {overlay.decided_by ? ` oleh ${overlay.decided_by}` : ""}
          {overlay.decision_reason ? ` · ${overlay.decision_reason}` : ""}
        </p>
        <p className="muted">
          {target.element_kind === "button" ? "Tombol " : "Tool "}
          {target.label}
          {target.target ? ` · ${target.target}` : ""}
        </p>
      </div>
    );
  }

  return (
    <div className="card overlay-card">
      <p>
        <strong>{overlay.card.title}</strong>
        {overlay.kind ? ` · ${overlay.kind}` : ""}
      </p>
      <div className="overlay-target">
        <p className="muted">
          {target.element_kind === "button"
            ? "Tombol yang mau diklik:"
            : "Tool yang mau dijalankan:"}
        </p>
        <p>
          <strong>{target.label}</strong>
          {target.target ? ` · ${target.target}` : ""}
        </p>
        <p className="muted">Tool: {target.tool_name}</p>
      </div>
      <dl>
        {lines.map((line) => (
          <div key={line.label}>
            <dt>{line.label}</dt>
            <dd>{line.value}</dd>
          </div>
        ))}
      </dl>
      {overlay.card.warnings.length > 0 ? (
        <p className="error">{overlay.card.warnings.join(" ")}</p>
      ) : null}
      {overlay.highlight ? (
        <p className="muted">Disorot di frame: {overlay.highlight.note}</p>
      ) : null}
      <label>
        Alasan (opsional){" "}
        <input
          type="text"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Kenapa disetujui atau ditolak"
        />
      </label>
      <div>
        <button
          type="button"
          onClick={() => void decide("approved")}
          disabled={busy || deciding}
        >
          {deciding ? "Memproses…" : "Setuju"}
        </button>{" "}
        <button
          type="button"
          className="button-danger"
          onClick={() => void decide("rejected")}
          disabled={busy || deciding}
        >
          {deciding ? "Memproses…" : "Tolak"}
        </button>
      </div>
    </div>
  );
}
