/**
 * Typed fetch wrapper for the Puspa PWA API.
 *
 * Types come from the generated file `generated-types.ts` (generated
 * with openapi-typescript from `docs/openapi-pwa.yaml` — never edit
 * the generated file by hand). This wrapper is hand-written against
 * those generated types: it only knows how to send form posts and
 * JSON posts and how to turn HTTP failures into ApiError.
 */
import type { components } from "./generated-types";

export type ChatMessage = components["schemas"]["ChatMessage"];
export type Agent = components["schemas"]["Agent"];
export type TaskOutcome = components["schemas"]["TaskOutcome"];
export type StopResult = components["schemas"]["StopResult"];
export type ApprovalRequest = components["schemas"]["ApprovalRequest"];
export type ConnectorResult = components["schemas"]["ConnectorResult"];
export type OfficeProjection = components["schemas"]["OfficeProjection"];
export type OfficeAgent = components["schemas"]["OfficeAgent"];
export type ProfileFact = components["schemas"]["ProfileFact"];
export type SseEvent = components["schemas"]["SseEvent"];
export type Bootstrap = components["schemas"]["Bootstrap"];
export type OperatorStatus = components["schemas"]["OperatorStatus"];
export type PairingStatus = components["schemas"]["PairingStatus"];
export type PairingSession = components["schemas"]["PairingSession"];
export type PairingLock = components["schemas"]["PairingLock"];
export type GatewayStatus = components["schemas"]["GatewayStatus"];
export type GatewayPolicy = components["schemas"]["GatewayPolicy"];
export type GatewayDecision = components["schemas"]["GatewayDecision"];
export type OperatorLogLine = components["schemas"]["OperatorLogLine"];
export type ModelSettings = components["schemas"]["ModelSettings"];
export type ModelSource = ModelSettings["source"];
export type InsightsReport = components["schemas"]["InsightsReport"];
export type TokenTotals = components["schemas"]["TokenTotals"];
export type CostSummary = components["schemas"]["CostSummary"];
export type ModelBreakdown = components["schemas"]["ModelBreakdown"];
export type DriverBreakdown = components["schemas"]["DriverBreakdown"];
export type ToolUsage = components["schemas"]["ToolUsage"];
export type ModuleUsage = components["schemas"]["ModuleUsage"];
export type DayActivity = components["schemas"]["DayActivity"];
export type CostlyTask = components["schemas"]["CostlyTask"];
export type OnboardingStatus = components["schemas"]["OnboardingStatus"];
export type FirstTaskResult = components["schemas"]["FirstTaskResult"];
export type OnboardingHint = components["schemas"]["Hint"];
export type HintsResponse = components["schemas"]["HintsResponse"];
export type HintSeenResult = components["schemas"]["HintSeenResult"];

export type FetchFn = typeof fetch;

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: string };
    if (typeof body.error === "string" && body.error.length > 0) return body.error;
  } catch {
    // Fall through to the status-based message.
  }
  return `Request failed with status ${response.status}`;
}

async function postForm<T>(
  path: string,
  fields: Record<string, string>,
  fetchFn: FetchFn = fetch,
): Promise<T> {
  const response = await fetchFn(path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as T;
}

/**
 * The single shared JSON POST helper (G31 discipline: one JSON
 * helper, in this file). The G28 state clients use this directly;
 * the old `state/http.ts` duplicate is gone.
 */
export async function postJson<T>(
  path: string,
  body: unknown,
  fetchFn: FetchFn = fetch,
): Promise<T> {
  const response = await fetchFn(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as T;
}

/** The single shared JSON GET helper (see `postJson`). */
export async function getJson<T>(path: string, fetchFn: FetchFn = fetch): Promise<T> {
  const response = await fetchFn(path, { credentials: "same-origin" });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as T;
}

/**
 * Fetch the operator status for the Office screen's operator panels
 * (G31). In local serve mode the payload carries pairing status,
 * gateway status, and live log lines read from live server state;
 * in hosted mode `operator_panels` is false and the panel fields
 * are null, and the screen hides the panels.
 */
export function fetchOperatorStatus(fetchFn: FetchFn = fetch): Promise<OperatorStatus> {
  return getJson<OperatorStatus>("/api/operator/status", fetchFn);
}

/** Send a chat message. Returns the stored message. */
export function sendChat(
  text: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean; message: ChatMessage }> {
  return postForm("/api/chat", { text }, fetchFn);
}

/** Create and run a task synchronously. Returns the outcome. */
export function createTask(
  purpose: string,
  fetchFn: FetchFn = fetch,
): Promise<TaskOutcome> {
  return postForm("/api/tugas", { purpose }, fetchFn);
}

/** Stop a task: stops its sub-agents and revokes their tokens. */
export function stopTask(taskId: string, fetchFn: FetchFn = fetch): Promise<StopResult> {
  return postForm(`/api/tugas/${encodeURIComponent(taskId)}/hentikan`, {}, fetchFn);
}

/**
 * Decide an approval request with one tap. `approve=true` sends
 * `approve=ya`, `approve=false` sends `approve=tidak`.
 */
export function decideApproval(
  requestId: string,
  approve: boolean,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean; status: "approved" | "rejected" }> {
  return postForm(
    `/api/persetujuan/${encodeURIComponent(requestId)}`,
    { approve: approve ? "ya" : "tidak" },
    fetchFn,
  );
}

/** Create an agent (max 3 per user: Puspa + 2 user agents). */
export function createAgent(
  input: { name: string; role?: string; purpose?: string },
  fetchFn: FetchFn = fetch,
): Promise<Agent> {
  return postForm(
    "/api/agen",
    { name: input.name, role: input.role ?? "", purpose: input.purpose ?? "" },
    fetchFn,
  );
}

/** Delete an agent. The mandatory Puspa agent cannot be deleted. */
export async function deleteAgent(
  agentId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  const response = await fetchFn(`/api/agen/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as { ok: boolean };
}

/**
 * Grant or revoke a connector for an agent. `action="cabut"` revokes;
 * any other value grants with the connector's default minimal scope.
 */
export function setConnector(
  connector: "gmail" | "drive" | "calendar",
  agentId: string,
  action: "grant" | "cabut",
  fetchFn: FetchFn = fetch,
): Promise<ConnectorResult> {
  return postForm(
    `/api/konektor/${encodeURIComponent(connector)}`,
    { agent_id: agentId, action: action === "cabut" ? "cabut" : "beri" },
    fetchFn,
  );
}

export interface PushSubscriptionPayload {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

/** Save a Web Push subscription for the current user. */
export function subscribePush(
  subscription: PushSubscriptionPayload,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean; subscription_id: string }> {
  return postJson("/api/push/langganan", subscription, fetchFn);
}

/** Record how many seconds the user spent in onboarding. */
export function saveOnboardingTime(
  seconds: number,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  return postJson("/api/onboarding/waktu", { seconds }, fetchFn);
}

/** Read the user's own-model selection (G33). Never returns key material. */
export function getModelSettings(fetchFn: FetchFn = fetch): Promise<ModelSettings> {
  return getJson<ModelSettings>("/api/model-settings", fetchFn);
}

/** Save the user's own-model selection (G33). Never send a key here. */
export function saveModelSettings(
  settings: Omit<ModelSettings, "has_byok_key">,
  fetchFn: FetchFn = fetch,
): Promise<ModelSettings> {
  return postJson("/api/model-settings", settings, fetchFn);
}

/**
 * Save a BYOK key (G33). The server stores it in the vault only
 * and never returns it.
 */
export function saveByokKey(
  provider: string,
  key: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  return postJson("/api/model-settings/byok-key", { provider, key }, fetchFn);
}

/** Delete a BYOK key from the vault (G33). Idempotent. */
export async function deleteByokKey(
  provider: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  const response = await fetchFn(
    `/api/model-settings/byok-key?provider=${encodeURIComponent(provider)}`,
    { method: "DELETE", credentials: "same-origin" },
  );
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as { ok: boolean };
}

/**
 * Parse one SSE `data:` frame body into an SseEvent. Returns null
 * when the frame is not valid JSON or not an event object.
 */
export function parseSseEvent(data: string): SseEvent | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (parsed !== null && typeof parsed === "object" && "type" in parsed) {
      return parsed as SseEvent;
    }
    return null;
  } catch {
    return null;
  }
}

/** Format an amount as Indonesian Rupiah, e.g. 2000 -> "Rp2.000". */
export function formatRupiah(amountRp: number): string {
  return `Rp${Math.trunc(amountRp)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ".")}`;
}
