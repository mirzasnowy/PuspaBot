import { useEffect, useState } from "react";
import {
  addComputerAllowlistApp,
  clearComputerStop,
  fetchComputerAllowlist,
  fetchComputerAudit,
  fetchComputerStop,
  removeComputerAllowlistApp,
  requestComputerStop,
  type ComputerAllowlist,
  type ComputerAllowlistChange,
  type ComputerAuditEntry,
  type ComputerAuditList,
  type ComputerStopState,
} from "../api/client";

/**
 * The desktop-hands panel (V2-35), shown on the Settings screen.
 *
 * Three controls in one card:
 *
 * - Allowlist: apps whose normal clicks run without asking. Add
 *   an app once and the agent clicks freely inside it; take it
 *   off and its clicks ask again. Destructive actions (clipboard
 *   write, drag, closing a window) always ask, even in an
 *   allowlisted app.
 * - Emergency stop: two taps to halt the hands immediately (a
 *   running task stops at the next step). The stopped state
 *   stays visible until the user taps "Jalankan lagi".
 * - Audit: the newest computer actions from the ledger (what
 *   action, which target, tier, decision, outcome). Never shows
 *   clipboard content or typed text, only their lengths.
 *
 * All data comes from the server (`/api/computer/*`); the panel
 * never keeps its own copy of the allowlist or the stop flag.
 */
export function ComputerPanel({
  loadAllowlist = fetchComputerAllowlist,
  addApp = addComputerAllowlistApp,
  removeApp = removeComputerAllowlistApp,
  loadStop = fetchComputerStop,
  requestStop = requestComputerStop,
  clearStop = clearComputerStop,
  loadAudit = fetchComputerAudit,
}: {
  loadAllowlist?: () => Promise<ComputerAllowlist>;
  addApp?: (app: string) => Promise<ComputerAllowlistChange>;
  removeApp?: (app: string) => Promise<ComputerAllowlistChange>;
  loadStop?: () => Promise<ComputerStopState>;
  requestStop?: (reason: string) => Promise<ComputerStopState>;
  clearStop?: (reason: string) => Promise<ComputerStopState>;
  loadAudit?: (limit: number) => Promise<ComputerAuditList>;
}) {
  const [apps, setApps] = useState<string[]>([]);
  const [stop, setStop] = useState<ComputerStopState | null>(null);
  const [entries, setEntries] = useState<ComputerAuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [newApp, setNewApp] = useState("");
  const [confirmingStop, setConfirmingStop] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([loadAllowlist(), loadStop(), loadAudit(20)])
      .then(([allowlist, stopState, audit]) => {
        if (cancelled) return;
        if (!allowlist || !Array.isArray(allowlist.apps)) {
          throw new Error("daftar aplikasi tidak bisa dibaca dari server");
        }
        if (!stopState || typeof stopState.active !== "boolean") {
          throw new Error("status berhenti tidak bisa dibaca dari server");
        }
        if (!audit || !Array.isArray(audit.entries)) {
          throw new Error("audit tidak bisa dibaca dari server");
        }
        setApps(allowlist.apps);
        setStop(stopState);
        setEntries(audit.entries);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(
            `Gagal memuat panel komputer: ${err instanceof Error ? err.message : "error tak dikenal"}`,
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadAllowlist, loadStop, loadAudit]);

  async function add() {
    const name = newApp.trim();
    if (!name) return;
    setBusy(true);
    setStatus("");
    setError("");
    try {
      const changed = await addApp(name);
      if (changed.changed) setApps((current) => [...current, changed.app]);
      setStatus(`${changed.app} kini diizinkan: klik biasa di dalamnya tidak tanya lagi.`);
      setNewApp("");
    } catch (err) {
      setError(
        `Gagal menambah ${name}: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function revoke(app: string) {
    setBusy(true);
    setStatus("");
    setError("");
    try {
      const changed = await removeApp(app);
      if (changed.changed) setApps((current) => current.filter((item) => item !== app));
      setStatus(`${app} dicabut: klik di dalamnya tanya lagi.`);
    } catch (err) {
      setError(
        `Gagal mencabut ${app}: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function pressStop() {
    if (!confirmingStop) {
      setConfirmingStop(true);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const state = await requestStop("dihentikan dari panel komputer");
      setStop(state);
      setStatus("Tangan komputer dihentikan.");
    } catch (err) {
      setError(
        `Gagal menghentikan: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
      setConfirmingStop(false);
    }
  }

  async function resume() {
    setBusy(true);
    setError("");
    try {
      const state = await clearStop("dijalankan lagi dari panel komputer");
      setStop(state);
      setStatus("Tangan komputer jalan lagi.");
    } catch (err) {
      setError(
        `Gagal menjalankan lagi: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function reloadAudit() {
    setBusy(true);
    try {
      const audit = await loadAudit(20);
      setEntries(audit.entries);
    } catch (err) {
      setError(
        `Gagal memuat audit: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="card">
        <strong>Tangan komputer</strong>
        <p className="muted">Memuat…</p>
      </div>
    );
  }

  return (
    <div className="card">
      <strong>Tangan komputer</strong>
      <p>
        Klik biasa gratis di aplikasi yang diizinkan; yang merusak (tulis papan klip, seret,
        tutup jendela) selalu tanya dulu. Semua tindakan tercatat di audit.
      </p>
      {error ? (
        <p aria-live="polite" className="error">
          {error}
        </p>
      ) : null}
      {status ? <p aria-live="polite">{status}</p> : null}

      <strong>Aplikasi yang diizinkan</strong>
      {apps.length === 0 ? (
        <p className="muted">Belum ada: semua klik masih tanya dulu.</p>
      ) : (
        <ul>
          {apps.map((app) => (
            <li key={app}>
              {app}{" "}
              <button type="button" onClick={() => void revoke(app)} disabled={busy}>
                Cabut
              </button>
            </li>
          ))}
        </ul>
      )}
      <label>
        Tambah aplikasi
        <input
          type="text"
          value={newApp}
          onChange={(event) => setNewApp(event.target.value)}
          placeholder="Contoh: Notes"
        />
      </label>
      <button type="button" onClick={() => void add()} disabled={busy || !newApp.trim()}>
        Tambah
      </button>

      <strong>Berhenti darurat</strong>
      {stop?.active ? (
        <p aria-live="polite">
          Dihentikan{stop.reason ? `: ${stop.reason}` : ""}.{" "}
          <button type="button" onClick={() => void resume()} disabled={busy}>
            Jalankan lagi
          </button>
        </p>
      ) : (
        <p>
          <button
            type="button"
            className="button-danger"
            onClick={() => void pressStop()}
            disabled={busy}
          >
            {busy ? "Menghentikan…" : confirmingStop ? "Yakin hentikan tangan komputer?" : "Hentikan tangan komputer"}
          </button>{" "}
          {confirmingStop && !busy ? (
            <button type="button" className="button-secondary" onClick={() => setConfirmingStop(false)}>
              Batal
            </button>
          ) : null}
        </p>
      )}

      <strong>Audit terakhir</strong>
      <button type="button" onClick={() => void reloadAudit()} disabled={busy}>
        Muat ulang
      </button>
      {entries.length === 0 ? (
        <p className="muted">Belum ada tindakan tercatat.</p>
      ) : (
        <ul>
          {entries.slice(0, 20).map((entry, index) => (
            <li key={entry.id ?? index}>
              {entry.computer_action ?? entry.computer_stop ?? entry.type}{" "}
              {entry.target ? `(${entry.target})` : ""} {entry.tier ? `[${entry.tier}]` : ""}{" "}
              {entry.decision ?? ""} {entry.outcome ?? ""}{" "}
              {entry.created_at ? new Date(entry.created_at).toLocaleString() : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
