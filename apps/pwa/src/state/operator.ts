/**
 * Operator panel store (G31): pure helpers over the
 * `GET /api/operator/status` payload. The payload itself is live
 * server state (pairing store, gateway, app event log); these
 * helpers only decide what to show and how to phrase it.
 */
import type {
  GatewayDecision,
  GatewayStatus,
  OperatorLogLine,
  OperatorStatus,
  PairingStatus,
} from "../api/client";

/**
 * True only when the operator panels must be shown: local serve
 * mode, as the server reports it. Hosted mode (or any unknown
 * payload) hides the panels; the client never overrides the flag.
 */
export function showOperatorPanels(status: OperatorStatus | null | undefined): boolean {
  return status?.mode === "local" && status.operator_panels === true;
}

/** One-line pairing summary, e.g. "1 sesi aktif · 0 kode aktif · 0 alamat terkunci". */
export function pairingSummary(pairing: PairingStatus | null | undefined): string {
  if (!pairing) return "Status pairing tidak tersedia.";
  const sessions = pairing.active_sessions ?? 0;
  const codes = pairing.active_codes ?? 0;
  const locks = pairing.locked_addresses ?? 0;
  return `${sessions} sesi aktif · ${codes} kode aktif · ${locks} alamat terkunci`;
}

/** One-line gateway policy summary; a never-loaded or stale policy is named as fail-closed. */
export function gatewayPolicySummary(gateway: GatewayStatus | null | undefined): string {
  const policy = gateway?.policy;
  if (!policy) return "Status gateway tidak tersedia.";
  if (!policy.loaded) return "Kebijakan belum dimuat: gateway menolak semua aksi.";
  if (policy.stale) return "Kebijakan kedaluwarsa: gateway menolak semua aksi.";
  const deny = policy.deny_rules?.length ?? 0;
  const allow = policy.allow_rules?.length ?? 0;
  const dryRun = gateway?.dry_run ? " · mode dry-run" : "";
  return `${deny} aturan tolak · ${allow} aturan izinkan${dryRun}`;
}

/** One-line label for the most recent gateway decision; "none yet" is honest, not a placeholder. */
export function lastDecisionLabel(decision: GatewayDecision | null | undefined): string {
  if (!decision) return "Belum ada keputusan.";
  const verdict = decision.outcome === "allow" ? "Diizinkan" : "Ditolak";
  const rule = decision.rule ? ` · aturan: ${decision.rule}` : "";
  const forwarded = decision.forwarded ? " · diteruskan" : "";
  return `${verdict}${rule}${forwarded}`;
}

/** "HH:MM:SS" local time for an epoch-seconds timestamp ("—" when missing). */
export function formatClockTime(epochSeconds: number | null | undefined): string {
  if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) return "—";
  const date = new Date(epochSeconds * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** One log line as shown: timestamp plus the server's summary. */
export function formatLogLine(line: OperatorLogLine): string {
  return `${formatClockTime(line.created_at)} ${line.summary ?? line.type ?? ""}`.trim();
}
