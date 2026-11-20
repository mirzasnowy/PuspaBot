import { useState } from "react";
import { stopAll } from "../api/client";

/**
 * The always-visible red stop-all button (P-02, plan 12.7).
 *
 * It lives in the header (`Nav`), so it is on every screen. Two
 * taps: the first arms it ("Yakin hentikan semua?"), the second
 * stops everything for the logged-in user — every agent they own
 * plus every live subagent task — and reports what actually
 * stopped, with a link to the audit trail. A tap elsewhere (or
 * a second tap on the armed button after a pause) is not needed:
 * the armed state only clears when the press finishes or fails,
 * so an accidental single tap can never stop anything.
 */
export function StopAllButton() {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  async function press() {
    if (!confirming) {
      setConfirming(true);
      setResult("");
      setError("");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const report = await stopAll();
      const agents = report.agents_stopped.length;
      setResult(
        `Semua dihentikan: ${agents} agen, ${report.subagents_stopped} subagen.`,
      );
    } catch (err) {
      setError(
        `Gagal menghentikan semua: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
      <button
        type="button"
        className="button-danger"
        onClick={() => void press()}
        disabled={busy}
      >
        {busy ? "Menghentikan…" : confirming ? "Yakin hentikan semua?" : "Hentikan semua"}
      </button>
      {confirming && !busy ? (
        <button type="button" className="button-secondary" onClick={() => setConfirming(false)}>
          Batal
        </button>
      ) : null}
      {result ? (
        <span aria-live="polite">
          {result}{" "}
          <a href="/api/proof/audit-trail">Lihat audit</a>
        </span>
      ) : null}
      {error ? (
        <span aria-live="polite" className="error">
          {error}
        </span>
      ) : null}
    </span>
  );
}
