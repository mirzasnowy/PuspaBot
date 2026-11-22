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
export type VoiceUpload = components["schemas"]["VoiceUpload"];
export type VoiceStatus = components["schemas"]["VoiceStatus"];
export type VoiceSettings = components["schemas"]["VoiceSettings"];
export type StopAllReport = components["schemas"]["StopAllReport"];
export type DialState = components["schemas"]["DialState"];
export type DialAreaState = components["schemas"]["DialAreaState"];
export type DialSuggestion = components["schemas"]["DialSuggestion"];
export type CeilingName = components["schemas"]["CeilingRequest"]["ceiling"];
export type WeeklyReport = components["schemas"]["WeeklyReport"];
export type WeeklyLinkedEvent = components["schemas"]["WeeklyLinkedEvent"];

/**
 * V2-35 computer types, hand-written here instead of generated:
 * `npm run generate` is broken at this base (docs/openapi-rooms.yaml
 * is missing from the repo and docs/openapi-voice.yaml has an
 * unquoted colon at line 439), so the generated file cannot be
 * rebuilt right now. These shapes mirror docs/openapi-computer.yaml
 * exactly; move them to generated-types.ts once generation is
 * repaired.
 */
export interface ComputerAllowlist {
  apps: string[];
}

/**
 * AST-09 telemetry toggle, hand-written here instead of
 * generated: `npm run generate` is broken at this base (see the
 * V2-35 note above), so the generated file cannot be rebuilt
 * right now. This shape mirrors docs/openapi-telemetry.yaml
 * exactly; move it to generated-types.ts once generation is
 * repaired.
 */
export interface TelemetrySettings {
  enabled: boolean;
  env_override: boolean | null;
  effective_enabled: boolean;
}
export interface ComputerAllowlistChange {
  app: string;
  allowed: boolean;
  changed: boolean;
}
export interface ComputerStopState {
  active: boolean;
  actor: string;
  reason: string;
  updated_at: string | null;
}
export interface ComputerAuditEntry {
  id?: string;
  created_at?: string;
  type?: string;
  tool?: string;
  computer_action?: string;
  computer_stop?: string;
  target?: string;
  app?: string;
  tier?: string;
  decision?: string;
  outcome?: string;
  length?: number;
  reason?: string;
  actor?: string;
}
export interface ComputerAuditList {
  entries: ComputerAuditEntry[];
  ledger_available: boolean;
}
export type LiveFrame = components["schemas"]["LiveFrame"];
export type LiveFrameEvent = components["schemas"]["LiveFrameEvent"];
export type TakeoverHold = components["schemas"]["TakeoverHold"];
export type TakeoverReport = components["schemas"]["TakeoverReport"];
export type TakeoverReleaseReport = components["schemas"]["TakeoverReleaseReport"];
export type ReplayStep = components["schemas"]["ReplayStep"];
export type ApprovalOverlay = components["schemas"]["ApprovalOverlay"];
export type ApprovalOverlayList = components["schemas"]["ApprovalOverlayList"];
export type TargetElement = components["schemas"]["TargetElement"];
export type OverlayHighlight = components["schemas"]["OverlayHighlight"];
export type ReplayTimeline = components["schemas"]["ReplayTimeline"];
export type ReplayTaskSummary = components["schemas"]["ReplayTaskSummary"];
/**
 * One file a run produced, from the artifacts browser list.
 * A local interface, not a generated type: `npm run generate`
 * cannot run in this tree (the voice spec has an unquoted
 * colon at openapi-voice.yaml:439 and the rooms spec file is
 * absent, both pre-existing), so the generated client carries
 * no ArtifactSummary yet. The shape mirrors the contract in
 * docs/openapi-artifacts.yaml exactly; when generation works
 * again this becomes `components["schemas"]["ArtifactSummary"]`
 * like the replay types above.
 */
export interface ArtifactSummary {
  artifact_id: string;
  agent_id: string;
  run_id: string | null;
  name: string;
  kind: "gif" | "markdown" | "text" | "json" | "image" | "file";
  media_type: string;
  size_bytes: number;
  created_at: string;
}
export type ReceiptUpload = components["schemas"]["ReceiptUpload"];
export type ReceiptDraft = components["schemas"]["ReceiptDraft"];
export type ReceiptExpense = components["schemas"]["ReceiptExpense"];
export type ReceiptStatus = components["schemas"]["ReceiptStatus"];
export type AvatarStatus = components["schemas"]["AvatarStatus"];
export type AvatarDeleted = components["schemas"]["AvatarDeleted"];
/**
 * Per-capability model routing (COW-08). Hand-written interfaces,
 * not generated types: `npm run generate` cannot run in this
 * tree (the voice spec has an unquoted colon at
 * openapi-voice.yaml:439 and the rooms spec file is absent,
 * both pre-existing), so the generated client carries no
 * CapabilityRoute yet. The shapes mirror the contract in
 * docs/openapi-routing.yaml exactly; when generation works
 * again these become `components["schemas"]["CapabilityRoute"]`
 * and `components["schemas"]["CapabilityRouting"]` like the
 * model types above.
 */
export type CapabilityName = "chat" | "vision" | "image" | "speech" | "embeddings";
export interface CapabilityRoute {
  capability: CapabilityName;
  provider: string | null;
  model: string | null;
  base_url: string | null;
  has_key: boolean;
  is_default: boolean;
}
export interface CapabilityRouting {
  capabilities: Record<CapabilityName, CapabilityRoute>;
  providers: Record<CapabilityName, string[]>;
}
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
export type ChatCard = components["schemas"]["ChatCard"];
export type DraftCard = components["schemas"]["DraftCard"];
export type OptionsCard = components["schemas"]["OptionsCard"];
export type CardOption = components["schemas"]["CardOption"];
export type ChartCard = components["schemas"]["ChartCard"];
export type ChartPoint = components["schemas"]["ChartPoint"];
export type MapCard = components["schemas"]["MapCard"];
export type MapPoint = components["schemas"]["MapPoint"];
export type ChatCardRequest = components["schemas"]["ChatCardRequest"];
export type ChatWidget = components["schemas"]["ChatWidget"];
export type CalculatorWidget = components["schemas"]["CalculatorWidget"];
export type CalculatorInput = components["schemas"]["CalculatorInput"];
export type FormWidget = components["schemas"]["FormWidget"];
export type FormField = components["schemas"]["FormField"];
export type ComparisonWidget = components["schemas"]["ComparisonWidget"];
export type ComparisonColumn = components["schemas"]["ComparisonColumn"];
export type ComparisonRow = components["schemas"]["ComparisonRow"];
export type DiagramWidget = components["schemas"]["DiagramWidget"];
export type DiagramNode = components["schemas"]["DiagramNode"];
export type DiagramEdge = components["schemas"]["DiagramEdge"];
export type SimulationWidget = components["schemas"]["SimulationWidget"];
export type SimulationBill = components["schemas"]["SimulationBill"];
export type SimulationHistoryDay = components["schemas"]["SimulationHistoryDay"];
export type ChatWidgetRequest = components["schemas"]["ChatWidgetRequest"];
export type ComputeWidgetRequest = components["schemas"]["ComputeWidgetRequest"];
export type ComputeWidgetResult = components["schemas"]["ComputeWidgetResult"];
export type SubmitWidgetRequest = components["schemas"]["SubmitWidgetRequest"];
export type SimulateWidgetRequest = components["schemas"]["SimulateWidgetRequest"];
export type SimulateWidgetAction = SimulateWidgetRequest["action"];

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

/** The single shared multipart POST helper (voice uploads carry a
 * recorded audio blob, so they cannot use the form or JSON helpers). */
export async function postMultipart<T>(
  path: string,
  form: FormData,
  fetchFn: FetchFn = fetch,
): Promise<T> {
  const response = await fetchFn(path, {
    method: "POST",
    body: form,
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as T;
}

/** Send a chat message. Returns the stored message. */
export function sendChat(
  text: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean; message: ChatMessage }> {
  return postForm("/api/chat", { text }, fetchFn);
}

/**
 * Upload a voice recording for transcription (P-03). Returns the
 * transcript as a pending message: the caller shows it for
 * confirmation and only then calls `confirmVoice`. The server
 * deletes the audio right away unless `keepAudio` is true.
 */
export function uploadVoice(
  audio: Blob,
  keepAudio = false,
  fetchFn: FetchFn = fetch,
): Promise<VoiceUpload> {
  const form = new FormData();
  form.append("audio", audio, "recording.webm");
  if (keepAudio) form.append("keep_audio", "true");
  return postMultipart<VoiceUpload>("/api/voice/upload", form, fetchFn);
}

/**
 * Confirm a pending voice transcript and send it as a chat message.
 * `text` overrides the transcript when the user edited it.
 */
export function confirmVoice(
  pendingId: string,
  text?: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean; message: ChatMessage }> {
  return postJson("/api/voice/confirm", { pending_id: pendingId, text }, fetchFn);
}

/** Discard a pending voice transcript without sending it. */
export function discardVoice(
  pendingId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  return postJson("/api/voice/discard", { pending_id: pendingId }, fetchFn);
}

/** Voice transcription status: which engine, whether one is chosen. */
export function voiceStatus(fetchFn: FetchFn = fetch): Promise<VoiceStatus> {
  return getJson<VoiceStatus>("/api/voice/status", fetchFn);
}

/** Read the user's voice choice (V2-30). Never returns key material. */
export function getVoiceSettings(fetchFn: FetchFn = fetch): Promise<VoiceSettings> {
  return getJson<VoiceSettings>("/api/voice/settings", fetchFn);
}

/** Save the user's voice choice (V2-30). Never send a key here. */
export function saveVoiceSettings(
  settings: Omit<VoiceSettings, "has_asr_key" | "has_tts_key">,
  fetchFn: FetchFn = fetch,
): Promise<VoiceSettings> {
  return postJson("/api/voice/settings", settings, fetchFn);
}

/**
 * Read the user's telemetry toggle (AST-09). Off by default
 * for self-host; `env_override` says whether the operator's
 * `PUSPA_TELEMETRY` flag decides instead.
 */
export function getTelemetrySettings(fetchFn: FetchFn = fetch): Promise<TelemetrySettings> {
  return getJson<TelemetrySettings>("/api/telemetry/settings", fetchFn);
}

/**
 * Save the user's telemetry toggle (AST-09). Saving the toggle
 * never sends anything by itself; it only records the choice.
 */
export function saveTelemetrySettings(
  settings: Pick<TelemetrySettings, "enabled">,
  fetchFn: FetchFn = fetch,
): Promise<TelemetrySettings> {
  return postJson("/api/telemetry/settings", settings, fetchFn);
}

/**
 * Save a voice key (V2-30). The server stores it in the vault
 * only and never returns it. `kind` is "asr" or "tts".
 */
export function saveVoiceKey(
  kind: "asr" | "tts",
  provider: string,
  key: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  return postJson("/api/voice/key", { kind, provider, key }, fetchFn);
}

/** Delete a voice key from the vault (V2-30). Idempotent. */
export async function deleteVoiceKey(
  kind: "asr" | "tts",
  provider: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  const response = await fetchFn(
    `/api/voice/key?kind=${encodeURIComponent(kind)}&provider=${encodeURIComponent(provider)}`,
    { method: "DELETE", credentials: "same-origin" },
  );
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as { ok: boolean };
}

/**
 * Upload a receipt photo for OCR. Returns the parsed expense as a
 * pending draft: the caller shows it for confirmation and only
 * then calls `confirmReceipt`. The photo is kept as evidence.
 */
export function uploadReceipt(
  photo: Blob,
  fetchFn: FetchFn = fetch,
): Promise<ReceiptUpload> {
  const form = new FormData();
  form.append("photo", photo, "receipt.jpg");
  return postMultipart<ReceiptUpload>("/api/receipts/upload", form, fetchFn);
}

/**
 * Confirm a pending receipt draft and record the expense.
 * `edits` overrides the parsed merchant, total, date, or lines
 * when the user corrected them first.
 */
export function confirmReceipt(
  pendingId: string,
  edits?: {
    merchant?: string;
    total?: number;
    expense_date?: string;
    lines?: ReceiptExpense["lines"];
  },
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean; expense: ReceiptExpense }> {
  return postJson("/api/receipts/confirm", { pending_id: pendingId, ...edits }, fetchFn);
}

/** Discard a pending receipt draft (and delete the stored photo). */
export function discardReceipt(
  pendingId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  return postJson("/api/receipts/discard", { pending_id: pendingId }, fetchFn);
}

/** Receipt OCR status: which engine, whether one is chosen. */
export function receiptStatus(fetchFn: FetchFn = fetch): Promise<ReceiptStatus> {
  return getJson<ReceiptStatus>("/api/receipts/status", fetchFn);
}

/**
 * Agent avatar status (V2-26): whether the user set a custom
 * photo, and where to fetch it. photo_url and updated_at are
 * null while no photo is set; the caller then shows the default
 * Puspa logo.
 */
export function fetchAgentAvatar(fetchFn: FetchFn = fetch): Promise<AvatarStatus> {
  return getJson<AvatarStatus>("/api/agent/avatar", fetchFn);
}

/**
 * Upload or replace the agent avatar photo (V2-26). The server
 * keeps one photo per user and refuses anything that is not a
 * displayable image (jpeg, png, webp, gif; at most 2 MiB).
 */
export function uploadAgentPhoto(
  photo: Blob,
  fetchFn: FetchFn = fetch,
): Promise<AvatarStatus> {
  const form = new FormData();
  form.append("photo", photo, "avatar.png");
  return postMultipart<AvatarStatus>("/api/agent/avatar/photo", form, fetchFn);
}

/** Delete the agent avatar photo; the default logo applies again. */
export function deleteAgentPhoto(fetchFn: FetchFn = fetch): Promise<AvatarDeleted> {
  return deleteJson<AvatarDeleted>("/api/agent/avatar/photo", fetchFn);
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

/** Read the live frame for one agent: status, open task, last tool, replayable events. */
export function fetchLiveFrame(
  agentId: string,
  fetchFn: FetchFn = fetch,
): Promise<LiveFrame> {
  return getJson<LiveFrame>(`/api/live-view/${encodeURIComponent(agentId)}`, fetchFn);
}

/** Take over one agent: pause its autonomous work (hold + interrupt + ledger event). */
export function takeoverAgent(
  agentId: string,
  reason: string,
  fetchFn: FetchFn = fetch,
): Promise<TakeoverReport> {
  return postJson<TakeoverReport>(
    `/api/live-view/${encodeURIComponent(agentId)}/takeover`,
    { reason },
    fetchFn,
  );
}

/** Release a takeover so the agent resumes autonomous work on its next turn. */
export function releaseTakeover(
  agentId: string,
  fetchFn: FetchFn = fetch,
): Promise<TakeoverReleaseReport> {
  return postJson<TakeoverReleaseReport>(
    `/api/live-view/${encodeURIComponent(agentId)}/release`,
    {},
    fetchFn,
  );
}

/**
 * Replay timeline (P-10): list the agent's tasks that have ledger
 * events, newest first — the picker the replay screen plays from.
 */
/**
 * Read the approval overlays for one agent: pending actions
 * first, then decided ones, each with its target element and
 * its highlight against the current live frame (P-11).
 */
export function fetchApprovalOverlays(
  agentId: string,
  fetchFn: FetchFn = fetch,
): Promise<ApprovalOverlayList> {
  return getJson<ApprovalOverlayList>(
    `/api/live-view/${encodeURIComponent(agentId)}/approvals`,
    fetchFn,
  );
}

/** Approve one pending overlay: that one action may run (P-11). */
export function approveOverlay(
  agentId: string,
  requestId: string,
  reason: string | undefined,
  fetchFn: FetchFn = fetch,
): Promise<ApprovalOverlay> {
  return postJson<ApprovalOverlay>(
    `/api/live-view/${encodeURIComponent(agentId)}/approvals/${encodeURIComponent(requestId)}/approve`,
    { reason: reason ?? "" },
    fetchFn,
  );
}

/**
 * Reject one pending overlay: that one action is cancelled and
 * the rejection is recorded in the ledger (P-11).
 */
export function rejectOverlay(
  agentId: string,
  requestId: string,
  reason: string | undefined,
  fetchFn: FetchFn = fetch,
): Promise<ApprovalOverlay> {
  return postJson<ApprovalOverlay>(
    `/api/live-view/${encodeURIComponent(agentId)}/approvals/${encodeURIComponent(requestId)}/reject`,
    { reason: reason ?? "" },
    fetchFn,
  );
}

export function fetchReplayTasks(
/** Read the autonomy dial state: five areas, ceilings, streaks, suggestions. */
  agentId: string,
  fetchFn: FetchFn = fetch,
): Promise<ReplayTaskSummary[]> {
  return getJson<ReplayTaskSummary[]>(
    `/api/replay/${encodeURIComponent(agentId)}/tasks`,
    fetchFn,
  );
}

/** Replay timeline (P-10): every step of one task, oldest first. */
export function fetchReplay(
  agentId: string,
  taskId: string,
  fetchFn: FetchFn = fetch,
): Promise<ReplayTimeline> {
  return getJson<ReplayTimeline>(
    `/api/replay/${encodeURIComponent(agentId)}/${encodeURIComponent(taskId)}`,
    fetchFn,
  );
}

/** Replay timeline (P-10): one step by 0-based index, for scrubbing. */
export function fetchReplayStep(
  agentId: string,
  taskId: string,
  index: number,
  fetchFn: FetchFn = fetch,
): Promise<ReplayStep> {
  return getJson<ReplayStep>(
    `/api/replay/${encodeURIComponent(agentId)}/${encodeURIComponent(taskId)}/steps/${index}`,
    fetchFn,
  );
}

/**
 * Replay GIF export (P-10): the download URL for the whole timeline
 * as an animated GIF (rendered frames, not a desktop recording).
 * A plain link — the browser sends the login cookie with it.
 */
export function replayGifUrl(agentId: string, taskId: string): string {
  return `/api/replay/${encodeURIComponent(agentId)}/${encodeURIComponent(taskId)}/export.gif`;
}

/**
 * Artifacts browser: list one agent's produced files, newest
 * first (metadata only — name, kind, size, run, time).
 */
export function fetchArtifacts(
  agentId: string,
  fetchFn: FetchFn = fetch,
): Promise<ArtifactSummary[]> {
  return getJson<ArtifactSummary[]>(
    `/api/artifacts/${encodeURIComponent(agentId)}`,
    fetchFn,
  );
}

/**
 * Artifacts browser: the download URL for one artifact's bytes
 * (its own media type, attached under its name). A plain link —
 * the browser sends the login cookie with it, so it also works
 * as an "open in browser" link for text and image artifacts.
 */
export function artifactDownloadUrl(agentId: string, artifactId: string): string {
  return `/api/artifacts/${encodeURIComponent(agentId)}/${encodeURIComponent(artifactId)}`;
}

/**
 * Skill marketplace hub (COW-03). These types are hand-written
 * here (like `ComputerAllowlist` above), not generated: the hub
 * contract lives in `docs/openapi-hub.yaml`, separate from the
 * generated `docs/openapi-pwa.yaml` types.
 */
export interface HubSkill {
  id: string;
  name: string;
  description: string;
  version: string;
  origin: string;
  status: string;
  source: string;
  license: string;
  price_credits: number;
  installed: boolean;
}

export interface HubPreview {
  slug: string;
  name: string;
  description: string;
  license: string | null;
  sha256: string;
  source: string;
}

export interface HubInstallResult {
  ok: boolean;
  installed: {
    skill_id: string;
    version: string;
    status: string;
    sha256: string;
    source: string;
  };
}

/** Hub: list the locally browsable skills, optionally filtered by query. */
export function fetchHubSkills(
  query: string,
  fetchFn: FetchFn = fetch,
): Promise<{ skills: HubSkill[]; query: string }> {
  const path = query
    ? `/api/hub/skills?q=${encodeURIComponent(query)}`
    : "/api/hub/skills";
  return getJson<{ skills: HubSkill[]; query: string }>(path, fetchFn);
}

/**
 * Hub: preview a skill source (fetch only, install nothing).
 * Throws ApiError with the server's reason when the source
 * cannot be understood or fetched.
 */
export function previewHubSkill(
  source: string,
  fetchFn: FetchFn = fetch,
): Promise<HubPreview> {
  return getJson<HubPreview>(
    `/api/hub/preview?source=${encodeURIComponent(source)}`,
    fetchFn,
  );
}

/**
 * Hub: install the previewed skill. The caller passes the
 * sha256 from the preview the user confirmed; content that
 * changed since the preview is refused (409).
 */
export function installHubSkill(
  source: string,
  expectedSha256: string,
  fetchFn: FetchFn = fetch,
): Promise<HubInstallResult> {
  return postJson<HubInstallResult>(
    "/api/hub/install",
    { source, expected_sha256: expectedSha256 },
    fetchFn,
  );
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
 * V2-35 computer tiers: read the per-app allowlist (apps whose
 * normal clicks run without asking).
 */
export function fetchComputerAllowlist(fetchFn: FetchFn = fetch): Promise<ComputerAllowlist> {
  return getJson<ComputerAllowlist>("/api/computer/allowlist", fetchFn);
}

/** V2-35 computer tiers: allowlist one app by name. */
export function addComputerAllowlistApp(
  app: string,
  fetchFn: FetchFn = fetch,
): Promise<ComputerAllowlistChange> {
  return postJson<ComputerAllowlistChange>("/api/computer/allowlist", { app }, fetchFn);
}

/** V2-35 computer tiers: revoke one app from the allowlist. */
export function removeComputerAllowlistApp(
  app: string,
  fetchFn: FetchFn = fetch,
): Promise<ComputerAllowlistChange> {
  return deleteJson<ComputerAllowlistChange>(
    `/api/computer/allowlist/${encodeURIComponent(app)}`,
    fetchFn,
  );
}

/** V2-35 computer stop: read the emergency stop flag. */
export function fetchComputerStop(fetchFn: FetchFn = fetch): Promise<ComputerStopState> {
  return getJson<ComputerStopState>("/api/computer/stop", fetchFn);
}

/**
 * V2-35 computer stop: halt the desktop hands immediately. A
 * running task stops at the next step boundary at the latest.
 */
export function requestComputerStop(
  reason: string,
  fetchFn: FetchFn = fetch,
): Promise<ComputerStopState> {
  return postJson<ComputerStopState>("/api/computer/stop", { reason }, fetchFn);
}

/** V2-35 computer stop: let the desktop hands run again. */
export function clearComputerStop(
  reason: string,
  fetchFn: FetchFn = fetch,
): Promise<ComputerStopState> {
  return postJson<ComputerStopState>("/api/computer/stop/clear", { reason }, fetchFn);
}

/**
 * V2-35 computer audit: newest-first audit entries from the
 * ledger (what action, which target, tier, decision, outcome).
 * Never carries clipboard content or typed text.
 */
export function fetchComputerAudit(
  limit: number,
  fetchFn: FetchFn = fetch,
): Promise<ComputerAuditList> {
  return getJson<ComputerAuditList>(`/api/computer/audit?limit=${limit}`, fetchFn);
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

/** Read the user's per-capability routing table (COW-08). Never returns key material. */
export function getCapabilityRouting(fetchFn: FetchFn = fetch): Promise<CapabilityRouting> {
  return getJson<CapabilityRouting>("/api/routing", fetchFn);
}

/**
 * Swap one capability's provider with one click (COW-08).
 * The server validates the provider against the capability and
 * refuses with 400 when it cannot serve it. Never send a key here.
 */
export function saveCapabilityRoute(
  capability: CapabilityName,
  route: { provider: string; model?: string | null; base_url?: string | null },
  fetchFn: FetchFn = fetch,
): Promise<CapabilityRoute> {
  return postJson(`/api/routing/${capability}`, route, fetchFn);
}

/** Reset one capability to its default route (COW-08). Idempotent. */
export async function resetCapabilityRoute(
  capability: CapabilityName,
  fetchFn: FetchFn = fetch,
): Promise<CapabilityRoute> {
  const response = await fetchFn(`/api/routing/${capability}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as CapabilityRoute;
}

/**
 * Save a provider key for routing (COW-08). The server stores it
 * in the vault only (the same `byok:<provider>` entry the model
 * settings use) and never returns it.
 */
export function saveRoutingKey(
  provider: string,
  key: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  return postJson("/api/routing/key", { provider, key }, fetchFn);
}

/** Delete a provider key from the vault (COW-08). Idempotent. */
export async function deleteRoutingKey(
  provider: string,
  fetchFn: FetchFn = fetch,
): Promise<{ ok: boolean }> {
  const response = await fetchFn(`/api/routing/key?provider=${encodeURIComponent(provider)}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status, await readError(response));
  return (await response.json()) as { ok: boolean };
}

/** Login state of one subscription provider (V2-28, V2-29). No token ever appears here. */
export type ProviderLoginState = "logged_in" | "expiring" | "expired" | "not_logged_in";

export type ProviderLoginFlow =
  | "pkce"
  | "device"
  | "polling"
  | "setup_token"
  | "import_token"
  | "encoded_token";

export interface ProviderStatus {
  provider: string;
  flow: ProviderLoginFlow;
  state: ProviderLoginState;
  expires_at: string | null;
  login_ready: boolean;
  local_only?: boolean;
}

export type ProviderLoginStart =
  | { flow: "pkce"; url: string; state: string }
  | {
      flow: "device";
      user_code: string;
      verification_uri: string;
      expires_in: number;
      interval: number;
      poll_key: string;
    }
  | {
      flow: "polling";
      verification_uri: string;
      expires_in: number;
      interval: number;
      poll_key: string;
    }
  | {
      flow: "setup_token" | "import_token";
      help: string;
      token_prefix: string | null;
    }
  | { flow: "encoded_token"; help: string };

/** List every subscription provider with its login state (V2-28). */
export function getProviders(fetchFn: FetchFn = fetch): Promise<{ providers: ProviderStatus[] }> {
  return getJson("/api/providers", fetchFn);
}

/** Start a subscription login (V2-28, V2-29): PKCE returns an authorize URL,
 * device returns a user code, polling returns a sign-in URL, and pasted-token
 * flows return the help text for what to paste. */
export function startProviderLogin(
  provider: string,
  redirectUri?: string,
  fetchFn: FetchFn = fetch,
): Promise<ProviderLoginStart> {
  return postJson(`/api/providers/${encodeURIComponent(provider)}/login/start`, { redirect_uri: redirectUri ?? null }, fetchFn);
}

/** Finish a pasted-token login (V2-29): Claude setup token, Cursor import,
 * or the Cline base64 blob. The token is sent once and never returned. */
export function finishProviderTokenLogin(
  provider: string,
  token: string,
  fetchFn: FetchFn = fetch,
): Promise<ProviderStatus> {
  return postJson(`/api/providers/${encodeURIComponent(provider)}/login/finish-token`, { token }, fetchFn);
}

/** Poll one device login started by startProviderLogin (V2-28). */
export function pollProviderDeviceLogin(
  provider: string,
  pollKey: string,
  fetchFn: FetchFn = fetch,
): Promise<{ status: string; state?: ProviderLoginState; provider?: string }> {
  return postJson(`/api/providers/${encodeURIComponent(provider)}/device/poll`, { poll_key: pollKey }, fetchFn);
}

/** Log out of one subscription provider; clears its vault tokens (V2-28). */
export function logoutProvider(
  provider: string,
  fetchFn: FetchFn = fetch,
): Promise<ProviderStatus> {
  return postJson(`/api/providers/${encodeURIComponent(provider)}/logout`, {}, fetchFn);
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

/** The caller's own display name (V2-27): null when none is set. */
export interface ProfileDisplayName {
  user_id: string;
  display_name: string | null;
}

/** Read the caller's own display name (V2-27). */
export function fetchDisplayName(
  fetchFn: FetchFn = fetch,
): Promise<ProfileDisplayName> {
  return getJson<ProfileDisplayName>("/api/profile/display-name", fetchFn);
}

/** Set the caller's own display name (V2-27). Only ever acts on the caller. */
export function saveDisplayName(
  displayName: string,
  fetchFn: FetchFn = fetch,
): Promise<ProfileDisplayName> {
  return postJson<ProfileDisplayName>(
    "/api/profile/display-name",
    { display_name: displayName },
    fetchFn,
  );
}

/**
 * Switchable personas, hand-written here instead of generated:
 * `npm run generate` is broken at this base (see the V2-35 note
 * above), so these shapes mirror docs/openapi-personas.yaml
 * exactly; move them to generated-types.ts once generation is
 * repaired.
 */

/** One persona in the list: name, reply style, and active state. */
export interface PersonaSummary {
  slug: string;
  name: string;
  style: string | null;
  is_active: boolean;
  is_default: boolean;
}

/** The persona list with the active slug. */
export interface PersonaList {
  personas: PersonaSummary[];
  active: string;
}

/** One persona with its file text (prompt, identity, about-you). */
export interface PersonaDetail extends PersonaSummary {
  soul: string;
  identity: string;
  user: string;
}

/** Fields for creating or editing a persona; all but name optional. */
export interface PersonaInput {
  name?: string;
  soul?: string;
  identity?: string;
  user?: string;
  style?: string;
}

/** List every persona with the active slug. */
export function fetchPersonas(fetchFn: FetchFn = fetch): Promise<PersonaList> {
  return getJson<PersonaList>("/api/personas", fetchFn);
}

/** Read one persona, file text included. */
export function fetchPersona(
  slug: string,
  fetchFn: FetchFn = fetch,
): Promise<PersonaDetail> {
  return getJson<PersonaDetail>(`/api/personas/${encodeURIComponent(slug)}`, fetchFn);
}

/** Create a persona (only `name` is required). */
export function createPersona(
  input: PersonaInput,
  fetchFn: FetchFn = fetch,
): Promise<PersonaDetail> {
  return postJson<PersonaDetail>("/api/personas", input, fetchFn);
}

/** Edit a persona; only the given fields change. */
export function updatePersona(
  slug: string,
  input: PersonaInput,
  fetchFn: FetchFn = fetch,
): Promise<PersonaDetail> {
  return patchJson<PersonaDetail>(
    `/api/personas/${encodeURIComponent(slug)}`,
    input,
    fetchFn,
  );
}

/** Delete a persona; the default persona cannot be deleted. */
export function deletePersona(
  slug: string,
  fetchFn: FetchFn = fetch,
): Promise<{ deleted: string; active: string }> {
  return deleteJson<{ deleted: string; active: string }>(
    `/api/personas/${encodeURIComponent(slug)}`,
    fetchFn,
  );
}

/** Make a persona active; it applies from the next turn. */
export function activatePersona(
  slug: string,
  fetchFn: FetchFn = fetch,
): Promise<PersonaList> {
  return postJson<PersonaList>(
    `/api/personas/${encodeURIComponent(slug)}/activate`,
    {},
    fetchFn,
  );
}

/** Read the shared task board of one room (B9). */
export function fetchRoomBoard(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<RoomBoard> {
  return getJson<RoomBoard>(`/api/rooms/${encodeURIComponent(roomId)}/tasks`, fetchFn);
}

/** One member of a shared room, with their role (P-05). V2-27: `display_name` is the name the member list shows; it is absent for a member who never set one, and the screen falls back to "Anggota N" instead of the raw id. */
export interface RoomMember {
  user_id: string;
  role: string;
  joined_at: string;
  display_name?: string | null;
}

/** One shared room with its members and the caller's role (P-05). V2-27: `names` maps each member id to the display name to show, and `bot_names` maps each member id to their own bot's name, so no two bots in one room show the same hardcoded name. */
export interface RoomDetail {
  id: string;
  name: string;
  created_by: string;
  created_at: string;
  role: string;
  members: RoomMember[];
  names?: Record<string, string>;
  bot_names?: Record<string, string>;
}

/** One room invitation as listed: never carries the secret (P-05). */
export interface RoomInvite {
  id: string;
  room_id: string;
  inviter_user_id: string;
  kind: string;
  role: string;
  status: string;
  created_at: string;
  expires_at: string;
}

/** An invite as issued: the only response that carries the secret (P-05). */
export interface IssuedInvite {
  invite_id: string;
  token?: string;
  code?: string;
  join_link?: string;
  kind: string;
  role: string;
  expires_at: string;
}

/** One room-memory row (P-05). V2-27: `author_display_name` is the resolved author name, and `rendered_content` is the stored text with agreed-by ids replaced by names; either may be absent on an older payload, and the screen resolves them locally instead. */
export interface RoomMemory {
  id: string;
  room_id: string;
  author_user_id: string;
  kind: string;
  content: string;
  visibility: string;
  created_at: string;
  author_display_name?: string | null;
  rendered_content?: string | null;
}

/** One job on a room's serial queue (P-05). */
export interface RoomJob {
  id: string;
  room_id: string;
  kind: string;
  payload: Record<string, unknown>;
  status: string;
  requested_by: string;
  result: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** Create a shared room; the caller becomes its first owner (P-05). */
export function createRoom(name: string, fetchFn: FetchFn = fetch): Promise<RoomDetail> {
  return postJson<RoomDetail>("/api/rooms", { name }, fetchFn);
}

/** Read one room with its members (P-05). */
export function fetchRoomDetail(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<RoomDetail> {
  return getJson<RoomDetail>(`/api/rooms/${encodeURIComponent(roomId)}`, fetchFn);
}

/** Delete a room (owner only, P-05). */
export function deleteRoom(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ deleted: boolean; room_id: string }> {
  return deleteJson(`/api/rooms/${encodeURIComponent(roomId)}`, fetchFn);
}

/** Issue an invite: a link (7 days) or a one-time code (24 hours, P-05). */
export function issueRoomInvite(
  roomId: string,
  kind: "link" | "code",
  role: "member" | "guest",
  fetchFn: FetchFn = fetch,
): Promise<IssuedInvite> {
  return postJson<IssuedInvite>(
    `/api/rooms/${encodeURIComponent(roomId)}/invites`,
    { kind, role },
    fetchFn,
  );
}

/** List a room's invites; the list never carries a secret (P-05). */
export function fetchRoomInvites(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ room_id: string; invites: RoomInvite[] }> {
  return getJson(`/api/rooms/${encodeURIComponent(roomId)}/invites`, fetchFn);
}

/** Revoke an invite (P-05). */
export function revokeRoomInvite(
  roomId: string,
  inviteId: string,
  fetchFn: FetchFn = fetch,
): Promise<RoomInvite> {
  return postJson<RoomInvite>(
    `/api/rooms/${encodeURIComponent(roomId)}/invites/${encodeURIComponent(inviteId)}/revoke`,
    {},
    fetchFn,
  );
}

/** Join a room with an invite token or a one-time code (P-05). */
export function joinRoom(
  join: { token: string } | { code: string },
  fetchFn: FetchFn = fetch,
): Promise<{ room_id: string; invite_id: string; role: string; already_member: boolean }> {
  return postJson("/api/rooms/join", join, fetchFn);
}

/** Leave a room; private room memories leave with the caller (P-05). */
export function leaveRoom(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ left: boolean; room_id: string }> {
  return postJson(`/api/rooms/${encodeURIComponent(roomId)}/leave`, {}, fetchFn);
}

/** Change a member's role (owner only, P-05). */
export function setMemberRole(
  roomId: string,
  memberId: string,
  role: "owner" | "member" | "guest",
  fetchFn: FetchFn = fetch,
): Promise<RoomMember> {
  return postJson<RoomMember>(
    `/api/rooms/${encodeURIComponent(roomId)}/members/${encodeURIComponent(memberId)}/role`,
    { role },
    fetchFn,
  );
}

/** Remove a member; they lose access at once (owner only, P-05). */
export function removeMember(
  roomId: string,
  memberId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ removed: boolean; room_id: string; user_id: string }> {
  return postJson(
    `/api/rooms/${encodeURIComponent(roomId)}/members/${encodeURIComponent(memberId)}/remove`,
    {},
    fetchFn,
  );
}

/** Read room memory visible to the caller (P-05). */
export function fetchRoomMemory(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ room_id: string; memories: RoomMemory[] }> {
  return getJson(`/api/rooms/${encodeURIComponent(roomId)}/memory`, fetchFn);
}

/** Record one room-memory row (P-05). */
export function addRoomMemory(
  roomId: string,
  content: string,
  visibility: "room" | "private",
  kind: "note" | "decision" | "summary" = "note",
  fetchFn: FetchFn = fetch,
): Promise<RoomMemory> {
  return postJson<RoomMemory>(
    `/api/rooms/${encodeURIComponent(roomId)}/memory`,
    { kind, content, visibility },
    fetchFn,
  );
}

/** Bring one personal memory item into the room, explicitly (P-05). */
export function bringMemoryToRoom(
  roomId: string,
  factId: string,
  fetchFn: FetchFn = fetch,
): Promise<RoomMemory & { brought_from_fact_id: string }> {
  return postJson(
    `/api/rooms/${encodeURIComponent(roomId)}/memory/bring`,
    { fact_id: factId },
    fetchFn,
  );
}

/** Delete one room-memory row (P-05). */
export function deleteRoomMemory(
  roomId: string,
  memoryId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ deleted: boolean; room_id: string; memory_id: string }> {
  return deleteJson(
    `/api/rooms/${encodeURIComponent(roomId)}/memory/${encodeURIComponent(memoryId)}`,
    fetchFn,
  );
}

/** List a room's job queue, oldest first (P-05). */
export function fetchRoomJobs(
  roomId: string,
  fetchFn: FetchFn = fetch,
): Promise<{ room_id: string; jobs: RoomJob[] }> {
  return getJson(`/api/rooms/${encodeURIComponent(roomId)}/jobs`, fetchFn);
}

/** Enqueue one room job (P-05). */
export function enqueueRoomJob(
  roomId: string,
  kind: "summarize" | "note",
  payload: Record<string, unknown>,
  fetchFn: FetchFn = fetch,
): Promise<RoomJob> {
  return postJson<RoomJob>(
    `/api/rooms/${encodeURIComponent(roomId)}/jobs`,
    { kind, payload },
    fetchFn,
  );
}

/** Run one queued room job now, under the room lock (P-05). */
export function runRoomJob(
  roomId: string,
  jobId: string,
  fetchFn: FetchFn = fetch,
): Promise<RoomJob> {
  return postJson<RoomJob>(
    `/api/rooms/${encodeURIComponent(roomId)}/jobs/${encodeURIComponent(jobId)}/run`,
    {},
    fetchFn,
  );
}

/** One personal memory fact as the memory API returns it (P-05: bring to room). */
export interface MemoryFactItem {
  id: string;
  key: string;
  content: string | null;
  status: string;
}

/** List the caller's own personal memory facts (P-05: pick one to bring to a room). */
export function fetchMemoryFacts(
  fetchFn: FetchFn = fetch,
): Promise<{ facts: MemoryFactItem[]; total_facts: number }> {
  return getJson<{ facts: MemoryFactItem[]; total_facts: number }>("/api/memory", fetchFn);
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

export function addMockupAsset(
  id: string,
  body: { asset_id: string; data_uri: string; name?: string },
  fetchFn: FetchFn = fetch,
): Promise<MockupDoc> {
  return postJson<MockupDoc>(`/api/mockups/${encodeURIComponent(id)}/assets`, body, fetchFn);
}

/**
 * Rich cards in Chat (P-12): post one card payload into the
 * chat (the server validates it and appends a `puspa` message
 * carrying the card), or build a card from server-side data —
 * a stored B13 draft, or the G28 Insights report as a chart.
 */
export function postChatCard(
  body: ChatCardRequest,
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return postJson<ChatMessage>("/api/chat/cards", body, fetchFn);
}

export function postDraftCard(
  draftId: string,
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return postJson<ChatMessage>(`/api/chat/cards/draft/${encodeURIComponent(draftId)}`, {}, fetchFn);
}

export function fetchInsightsCard(
  days = 7,
  metric: "turns" | "tokens" | "cost_idr" = "cost_idr",
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return getJson<ChatMessage>(
    `/api/chat/cards/insights?days=${days}&metric=${metric}`,
    fetchFn,
  );
}

export function postChatWidget(
  body: ChatWidgetRequest,
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return postJson<ChatMessage>("/api/chat/widgets", body, fetchFn);
}

export function computeWidget(
  body: ComputeWidgetRequest,
  fetchFn: FetchFn = fetch,
): Promise<ComputeWidgetResult> {
  return postJson<ComputeWidgetResult>("/api/chat/widgets/compute", body, fetchFn);
}

export function submitWidgetForm(
  widget: FormWidget,
  answers: { [key: string]: unknown },
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return postJson<ChatMessage>("/api/chat/widgets/submit", { widget, answers }, fetchFn);
}

export function simulateWidget(
  widget: SimulationWidget,
  action: SimulateWidgetAction,
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return postJson<ChatMessage>("/api/chat/widgets/simulate", { widget, action }, fetchFn);
}

export function fetchPriceCompareWidget(
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return getJson<ChatMessage>("/api/chat/widgets/price-compare", fetchFn);
}

export function fetchSimulationWidget(
  fetchFn: FetchFn = fetch,
): Promise<ChatMessage> {
  return getJson<ChatMessage>("/api/chat/widgets/simulation", fetchFn);
}
