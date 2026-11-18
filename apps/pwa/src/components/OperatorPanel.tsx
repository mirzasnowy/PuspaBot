/**
 * Operator panel (G31): pairing status, gateway status, and live log
 * for the Office screen, shown only in local serve mode. Every value
 * comes from `GET /api/operator/status` — live server state, never
 * constants or mocks. While the first fetch is in flight the panel
 * shows a loading note; a failed fetch shows the error instead of
 * silently hiding the panels.
 */
import type { OperatorStatus } from "../api/client";
import {
  formatClockTime,
  formatLogLine,
  gatewayPolicySummary,
  lastDecisionLabel,
  pairingSummary,
  showOperatorPanels,
} from "../state/operator";

export function OperatorPanel({
  status,
  loading,
  error,
}: {
  status: OperatorStatus | null;
  loading: boolean;
  error: string | null;
}) {
  if (!status && loading) {
    return <p className="muted">Memuat status operator…</p>;
  }
  if (!status && error) {
    return (
      <p role="alert" className="error">
        Status operator gagal dimuat: {error}
      </p>
    );
  }
  if (!status || !showOperatorPanels(status)) return null;

  const pairing = status.pairing ?? null;
  const gateway = status.gateway ?? null;
  const lines = status.log ?? [];
  const sessions = pairing?.sessions ?? [];
  const locks = pairing?.locks ?? [];
  const denyRules = gateway?.policy?.deny_rules ?? [];
  const allowRules = gateway?.policy?.allow_rules ?? [];

  return (
    <section>
      <h2>Panel Operator</h2>
      <p className="muted">
        Mode serve lokal: status pairing, gateway, dan log live dari server, bukan data contoh.
      </p>
      {error ? (
        <p role="alert" className="error">
          Pembaruan terakhir gagal: {error}
        </p>
      ) : null}

      <h3>Status Pairing</h3>
      <p data-testid="pairing-summary">{pairingSummary(pairing)}</p>
      {sessions.length === 0 ? (
        <p className="muted">Tidak ada sesi pairing aktif.</p>
      ) : (
        sessions.map((session) => (
          <div
            key={`${session.device_label ?? "?"}-${session.expires_at ?? 0}`}
            className="card"
          >
            <strong>{session.device_label ?? "—"}</strong>{" "}
            <span className="muted">berlaku sampai {formatClockTime(session.expires_at)}</span>
          </div>
        ))
      )}
      {locks.length > 0 ? (
        <p role="alert">
          Alamat terkunci: {locks.map((lock) => lock.address ?? "?").join(", ")}
        </p>
      ) : null}

      <h3>Status Gateway</h3>
      <p data-testid="gateway-summary">{gatewayPolicySummary(gateway)}</p>
      <p className="muted">Keputusan tercatat: {gateway?.decisions ?? 0}</p>
      <p data-testid="gateway-last-decision">
        Keputusan terakhir: {lastDecisionLabel(gateway?.last_decision ?? null)}
      </p>
      {denyRules.length > 0 ? (
        <p className="muted">Tolak: {denyRules.join("; ")}</p>
      ) : null}
      {allowRules.length > 0 ? (
        <p className="muted">Izinkan: {allowRules.join("; ")}</p>
      ) : null}

      <h3>Log Live</h3>
      {lines.length === 0 ? (
        <p className="muted">Belum ada kejadian.</p>
      ) : (
        <ul>
          {lines.map((line, index) => (
            <li key={`${line.created_at ?? 0}-${index}`}>{formatLogLine(line)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
