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
import type { MockupDoc, MockupOp, MockupVariant } from "../state/mockup";

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
export type StopAllReport = components["schemas"]["StopAllReport"];
export type DialState = components["schemas"]["DialState"];
export type DialAreaState = components["schemas"]["DialAreaState"];
export type DialSuggestion = components["schemas"]["DialSuggestion"];
export type CeilingName = components["schemas"]["CeilingRequest"]["ceiling"];
export type WeeklyReport = components["schemas"]["WeeklyReport"];
export type WeeklyLinkedEvent = components["schemas"]["WeeklyLinkedEvent"];
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
export type GraphNode = components["schemas"]["GraphNode"];
export type GraphEdge = components["schemas"]["GraphEdge"];
export type GraphPage = components["schemas"]["GraphPage"];
export type GraphNodeDetail = components["schemas"]["GraphNodeDetail"];
export type GraphNodeInput = components["schemas"]["GraphNodeInput"];
export type GraphEdgeInput = components["schemas"]["GraphEdgeInput"];
export type GraphEdgeChangeInput = components["schemas"]["GraphEdgeChangeInput"];
export type GraphSearchResult = components["schemas"]["GraphSearchResult"];
export type GraphSearchResponse = components["schemas"]["GraphSearchResponse"];

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

/** The single shared JSON PATCH helper (see `postJson`). */
export async function patchJson<T>(
  path: string,
  body: unknown,
  fetchFn: FetchFn = fetch,
): Promise<T> {
  const response = await fetchFn(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as T;
}

/** The single shared JSON DELETE helper (see `postJson`). */
export async function deleteJson<T>(path: string, fetchFn: FetchFn = fetch): Promise<T> {
  const response = await fetchFn(path, {
    method: "DELETE",
    credentials: "same-origin",
  });
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
 * Stop everything for the logged-in user (P-02): every agent
 * they own plus every live subagent task. Returns the stop
 * report, with the audit-trail link.
 */
export function stopAll(fetchFn: FetchFn = fetch): Promise<StopAllReport> {
  return postJson<StopAllReport>("/api/stop-all", {}, fetchFn);
}

/** Read the autonomy dial state: five areas, ceilings, streaks, suggestions. */
export function fetchAutonomyDial(fetchFn: FetchFn = fetch): Promise<DialState> {
  return getJson<DialState>("/api/autonomy-dial", fetchFn);
}

/**
 * Move one dial area's ceiling. Lowering always works; raising
 * needs an active suggestion (10 accepted drafts in a row) and
 * Money refuses any raise (403). Throws ApiError with the
 * server's message otherwise (400 unknown area/ceiling, 409
 * raise needs evidence).
 */
export function setDialCeiling(
  area: string,
  ceiling: CeilingName,
  fetchFn: FetchFn = fetch,
): Promise<DialAreaState> {
  return postJson<DialAreaState>(
    `/api/autonomy-dial/${encodeURIComponent(area)}/ceiling`,
    { ceiling },
    fetchFn,
  );
}

/** Read the weekly report: tasks completed and corrected, cost, audit links. */
export function fetchWeeklyReport(
  days: number,
  fetchFn: FetchFn = fetch,
): Promise<WeeklyReport> {
  return getJson<WeeklyReport>(`/api/weekly-report?days=${days}`, fetchFn);
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

/** One shared room the user belongs to (B9). */
export interface RoomSummary {
  id: string;
  name: string;
  role: string;
}

/** One task on a shared room board (B9). */
export interface RoomBoardTask {
  id: string;
  room_id: string;
  title: string;
  owner_user_id: string | null;
  due_at: string | null;
  status: string;
  created_by: string;
  created_at: string;
  completed_at: string | null;
  delegation: {
    delegate_kind: "agent" | "teammate";
    assignee_ref: string;
    status: string;
    last_report: string | null;
    reported_at: string | null;
    started_at: string | null;
    blocked_reason: string | null;
  } | null;
  pending_steps: number;
}

/** The shared task board of one room (B9). */
export interface RoomBoard {
  room_id: string;
  room_name: string;
  tasks: RoomBoardTask[];
}

/** List the user's shared rooms (B9). */
export function fetchRooms(fetchFn: FetchFn = fetch): Promise<{ rooms: RoomSummary[] }> {
  return getJson<{ rooms: RoomSummary[] }>("/api/rooms", fetchFn);
}

/** Read the shared task board of one room (B9). */
export function fetchRoomBoard(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<RoomBoard> {
  return getJson<RoomBoard>(`/api/rooms/${encodeURIComponent(roomId)}/tasks`, fetchFn);
}

/** Read one bounded page of the user's knowledge graph (B18). */
export function fetchGraphPage(
  params: { limit?: number; offset?: number; kind?: string; since?: string } = {},
  fetchFn: FetchFn = fetch,
): Promise<GraphPage> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.kind) query.set("kind", params.kind);
  if (params.since) query.set("since", params.since);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return getJson<GraphPage>(`/api/graph${suffix}`, fetchFn);
}

/** Inspect one graph node: its facts and provenance (B18). */
export function fetchGraphNode(
  name: string,
  fetchFn: FetchFn = fetch,
): Promise<GraphNodeDetail> {
  return getJson<GraphNodeDetail>(`/api/graph/nodes/${encodeURIComponent(name)}`, fetchFn);
}

/** Add a node to the user's graph (B18). */
export function addGraphNode(
  input: GraphNodeInput,
  fetchFn: FetchFn = fetch,
): Promise<GraphNode> {
  return postJson<GraphNode>("/api/graph/nodes", input, fetchFn);
}

/** Rename a graph node (B18). */
export function renameGraphNode(
  oldName: string,
  newName: string,
  fetchFn: FetchFn = fetch,
): Promise<GraphNode> {
  return patchJson<GraphNode>(
    `/api/graph/nodes/${encodeURIComponent(oldName)}`,
    { name: newName },
    fetchFn,
  );
}

/** Delete a graph node and its edges (B18). */
export function deleteGraphNode(
  name: string,
  fetchFn: FetchFn = fetch,
): Promise<{ deleted: string }> {
  return deleteJson<{ deleted: string }>(
    `/api/graph/nodes/${encodeURIComponent(name)}`,
    fetchFn,
  );
}

/** Add an edge to the user's graph (B18). */
export function addGraphEdge(
  input: GraphEdgeInput,
  fetchFn: FetchFn = fetch,
): Promise<GraphEdge> {
  return postJson<GraphEdge>("/api/graph/edges", input, fetchFn);
}

/** Change an edge, or correct a fact (B18). */
export function changeGraphEdge(
  input: GraphEdgeChangeInput,
  fetchFn: FetchFn = fetch,
): Promise<GraphEdge> {
  return patchJson<GraphEdge>("/api/graph/edges", input, fetchFn);
}

/** Delete an edge from the user's graph (B18). */
export function deleteGraphEdge(
  input: GraphEdgeInput,
  fetchFn: FetchFn = fetch,
): Promise<{ deleted: GraphEdgeInput }> {
  const query = new URLSearchParams({
    subject: input.subject,
    relation: input.relation,
    object: input.object,
  });
  return deleteJson<{ deleted: GraphEdgeInput }>(`/api/graph/edges?${query.toString()}`, fetchFn);
}

/** Retrieve passages from the user's graph (B18). */
export function searchGraph(
  query: string,
  topK = 5,
  fetchFn: FetchFn = fetch,
): Promise<GraphSearchResponse> {
  const params = new URLSearchParams({ q: query, top_k: String(topK) });
  return getJson<GraphSearchResponse>(`/api/graph/search?${params.toString()}`, fetchFn);
}

/** Mockup editor API (B7). Types live in `../state/mockup`; the
 * fetch helpers here are thin wrappers over the shared helpers. */
export interface MockupTemplate {
  id: string;
  fields: string[];
}

export interface MockupSummary {
  id: string;
  title: string;
  template: string;
  updated_at: string;
}

export function fetchMockupTemplates(fetchFn: FetchFn = fetch): Promise<{ templates: MockupTemplate[] }> {
  return getJson<{ templates: MockupTemplate[] }>("/api/mockups/templates", fetchFn);
}

export function generateMockups(
  prompt: string,
  fetchFn: FetchFn = fetch,
): Promise<{ template: string; fields: Record<string, string>; variants: MockupVariant[] }> {
  return postJson("/api/mockups/generate", { prompt }, fetchFn);
}

export function createMockup(
  body: { doc: MockupDoc } | { template: string; fields: Record<string, string> },
  fetchFn: FetchFn = fetch,
): Promise<MockupDoc> {
  return postJson<MockupDoc>("/api/mockups", body, fetchFn);
}

export function fetchMockups(fetchFn: FetchFn = fetch): Promise<{ mockups: MockupSummary[] }> {
  return getJson<{ mockups: MockupSummary[] }>("/api/mockups", fetchFn);
}

export function fetchMockup(id: string, fetchFn: FetchFn = fetch): Promise<MockupDoc> {
  return getJson<MockupDoc>(`/api/mockups/${encodeURIComponent(id)}`, fetchFn);
}

export function applyMockupOp(id: string, op: MockupOp, fetchFn: FetchFn = fetch): Promise<MockupDoc> {
  return postJson<MockupDoc>(`/api/mockups/${encodeURIComponent(id)}/ops`, { op }, fetchFn);
}

export function patchMockup(id: string, instruction: string, fetchFn: FetchFn = fetch): Promise<MockupDoc> {
  return postJson<MockupDoc>(`/api/mockups/${encodeURIComponent(id)}/patch`, { instruction }, fetchFn);
}

export function undoMockup(id: string, fetchFn: FetchFn = fetch): Promise<MockupDoc> {
  return postJson<MockupDoc>(`/api/mockups/${encodeURIComponent(id)}/undo`, {}, fetchFn);
}

export function redoMockup(id: string, fetchFn: FetchFn = fetch): Promise<MockupDoc> {
  return postJson<MockupDoc>(`/api/mockups/${encodeURIComponent(id)}/redo`, {}, fetchFn);
}
