/**
 * Onboarding + hints state for the Onboarding screen (G28).
 *
 * Wire types come from `../api/client` (generated from
 * `docs/openapi-pwa.yaml`); they mirror what
 * `puspa/pwa/onboarding_api.py` returns.
 *
 * The three speaking styles and their examples mirror
 * `puspa/persona/persona.py` (`STYLES`); if that module changes,
 * this picker must change with it.
 */
import { getJson, postJson, type FetchFn } from "../api/client";
import type { FirstTaskResult, OnboardingHint, OnboardingStatus } from "../api/client";

export type { FirstTaskResult, OnboardingHint, OnboardingStatus };

export interface SpeakingStyle {
  id: string;
  name: string;
  description: string;
  examples: string[];
}

/** The three speaking styles the first step offers, in order. */
export const SPEAKING_STYLES: SpeakingStyle[] = [
  {
    id: "santai",
    name: "Santai",
    description: "Bahasa sehari-hari, slang, singkat, hangat.",
    examples: [
      "Siap, gue cek dulu ya.",
      "Oke, udah ketemu yang paling murah.",
      "Santai, nanti gue ingetin sebelum jatuh tempo.",
    ],
  },
  {
    id: "rapi",
    name: "Rapi",
    description: "Bahasa baku yang sopan, kalimat lengkap.",
    examples: [
      "Baik, saya periksa terlebih dahulu.",
      "Saya sudah menemukan harga termurah untuk Anda.",
      "Saya akan mengingatkan Anda sebelum jatuh tempo.",
    ],
  },
  {
    id: "campur",
    name: "Campur kode",
    description: "Campur bahasa Indonesia dan Inggris, informal.",
    examples: [
      "Got it, aku cek dulu ya.",
      "Done — ini yang paling murah so far.",
      "Reminder set, nanti aku kabarin sebelum due date.",
    ],
  },
];

/** Connector choices for step 3, in display order. */
export const CONNECTOR_CHOICES = ["nanti", "gmail", "drive", "calendar", "all"] as const;

export type ConnectorChoice = (typeof CONNECTOR_CHOICES)[number];

export function describeConnector(choice: ConnectorChoice): string {
  switch (choice) {
    case "nanti":
      return "Lewati dulu, sambungkan nanti dari layar Konektor.";
    case "gmail":
      return "Baca dan kirim email atas namamu.";
    case "drive":
      return "Baca file di Google Drive.";
    case "calendar":
      return "Baca dan buat acara kalender.";
    case "all":
      return "Sambungkan Gmail, Drive, dan Kalender sekaligus.";
  }
}

/** First-task types for step 4, in display order. */
export const FIRST_TASK_TYPES = ["compare_prices", "check_bill"] as const;

export type FirstTaskType = (typeof FIRST_TASK_TYPES)[number];

export function describeFirstTaskType(type: FirstTaskType): string {
  return type === "compare_prices"
    ? "Bandingkan harga satu barang di beberapa toko."
    : "Cek satu tagihan (misalnya PLN).";
}

/** Step the user is on: 1..4, or null when onboarding is done. */
export function nextStep(status: OnboardingStatus | null): number | null {
  if (!status || !status.started) return 1;
  if (status.current_step >= 4) return null;
  return status.current_step + 1;
}

/** True when all four steps are complete. */
export function isOnboardingDone(status: OnboardingStatus | null): boolean {
  return nextStep(status) === null;
}

/** Branch facts the client reports when asking which hints are due. */
export interface HintContext {
  screen?: string;
  first_visit?: boolean;
  approval_requested?: boolean;
  memory_used?: boolean;
  task_cost_idr?: number;
  billing_rows?: number;
}

/** Turn branch facts into the query string for GET /api/onboarding/hints. */
export function hintQueryString(context: HintContext): string {
  const params = new URLSearchParams();
  if (context.screen) params.set("screen", context.screen);
  if (context.first_visit !== undefined) {
    params.set("first_visit", String(context.first_visit));
  }
  if (context.approval_requested !== undefined) {
    params.set("approval_requested", String(context.approval_requested));
  }
  if (context.memory_used !== undefined) {
    params.set("memory_used", String(context.memory_used));
  }
  if (context.task_cost_idr !== undefined) {
    params.set("task_cost_idr", String(context.task_cost_idr));
  }
  if (context.billing_rows !== undefined) {
    params.set("billing_rows", String(context.billing_rows));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** Read the user's onboarding status. */
export function fetchOnboardingStatus(
  fetchFn: FetchFn = fetch,
): Promise<OnboardingStatus> {
  return getJson<OnboardingStatus>("/api/onboarding", fetchFn);
}

/** Start onboarding (idempotent) and return the status. */
export function startOnboarding(fetchFn: FetchFn = fetch): Promise<OnboardingStatus> {
  return postJson<OnboardingStatus>("/api/onboarding/start", {}, fetchFn);
}

/** Complete step 1: name + speaking style. */
export function completeStep1(
  input: { name: string; style: string; example_message?: string[] },
  fetchFn: FetchFn = fetch,
): Promise<OnboardingStatus> {
  return postJson<OnboardingStatus>("/api/onboarding/step1", input, fetchFn);
}

/** Complete step 2: notification test. */
export function completeStep2(fetchFn: FetchFn = fetch): Promise<OnboardingStatus> {
  return postJson<OnboardingStatus>("/api/onboarding/step2", {}, fetchFn);
}

/** Complete step 3: connector choice. */
export function completeStep3(
  connector: ConnectorChoice,
  fetchFn: FetchFn = fetch,
): Promise<OnboardingStatus> {
  return postJson<OnboardingStatus>("/api/onboarding/step3", { connector }, fetchFn);
}

/** Run the guided first task (step 4). */
export function runFirstTask(
  input: { event_type: FirstTaskType; target: string },
  fetchFn: FetchFn = fetch,
): Promise<FirstTaskResult> {
  return postJson<FirstTaskResult>("/api/onboarding/first-task", input, fetchFn);
}

/** Hints due right now for the branch the user just reached. */
export function fetchDueHints(
  context: HintContext,
  fetchFn: FetchFn = fetch,
): Promise<{ hints: OnboardingHint[] }> {
  return getJson<{ hints: OnboardingHint[] }>(
    `/api/onboarding/hints${hintQueryString(context)}`,
    fetchFn,
  );
}

/** Mark one hint seen (shown or dismissed); never shown again. */
export function markHintSeen(
  flag: string,
  fetchFn: FetchFn = fetch,
): Promise<{ user_id: string; flag: string; seen: boolean }> {
  return postJson(`/api/onboarding/hints/${encodeURIComponent(flag)}/seen`, {}, fetchFn);
}
