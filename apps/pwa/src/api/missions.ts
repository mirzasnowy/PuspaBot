/**
 * Missions with checklist client (V2-42, issue #219).
 *
 * Lives in its own module (not `client.ts`) so the shared client
 * stays under the file-size ratchet: the missions types and calls
 * are one feature area, and the shared client budget says to split
 * per feature area when it grows again. Types are hand-written
 * from `docs/openapi-missions.yaml` because `npm run generate`
 * is broken at this base (see the V2-35 note in `client.ts`);
 * the fetch helpers are the shared ones from `client.ts`.
 */
import { getJson, postJson, type FetchFn } from "./client";

/**
 * Missions with checklist (V2-42), hand-written here instead of
 * generated: `npm run generate` is broken at this base (see the
 * V2-35 note above), so these shapes mirror
 * docs/openapi-missions.yaml exactly; move them to
 * generated-types.ts once generation is repaired.
 */

/** One checklist step of a mission. */
export interface MissionStep {
  title: string;
  status: "pending" | "active" | "done" | "failed" | "skipped";
  result: string | null;
  question: string | null;
  options: string[];
  answer: string | null;
}

/** One multi-step goal the agent works through in the background. */
export interface Mission {
  id: string;
  user_id: string;
  title: string;
  goal: string;
  status: "active" | "waiting" | "done" | "failed" | "stopped";
  steps: MissionStep[];
  webhook_token: string;
  created_at: string;
  updated_at: string;
}

/** The caller's missions, newest first. */
export interface MissionList {
  missions: Mission[];
}

/** A step to create: a title, or a title with a step-ask question. */
export type MissionStepInput =
  | string
  | { title: string; question?: string; options?: string[] };

/** Fields for creating a mission. */
export interface MissionInput {
  goal: string;
  title?: string;
  steps: MissionStepInput[];
}

/** A queued run: the one-shot trigger the serve loop will fire. */
export interface MissionQueued {
  queued: boolean;
  mission_id: string;
  trigger_id: string;
}

/** A scheduled mission: the trigger id and its next fire time. */
export interface MissionScheduled {
  scheduled: boolean;
  mission_id: string;
  trigger_id: string;
  next_trigger_at: string;
}

/** List the caller's missions. */
export function fetchMissions(fetchFn: FetchFn = fetch): Promise<MissionList> {
  return getJson<MissionList>("/api/missions", fetchFn);
}

/** Create a mission with its checklist. */
export function createMission(
  input: MissionInput,
  fetchFn: FetchFn = fetch,
): Promise<Mission> {
  return postJson<Mission>("/api/missions", input, fetchFn);
}

/** Queue the mission's next step now (background, via the scheduler). */
export function runMission(
  missionId: string,
  fetchFn: FetchFn = fetch,
): Promise<MissionQueued> {
  return postJson<MissionQueued>(
    `/api/missions/${encodeURIComponent(missionId)}/run`,
    {},
    fetchFn,
  );
}

/** Answer the mission's open step-ask; go=false declines and stops it. */
export function answerMission(
  missionId: string,
  answer: string,
  go: boolean,
  fetchFn: FetchFn = fetch,
): Promise<Mission> {
  return postJson<Mission>(
    `/api/missions/${encodeURIComponent(missionId)}/answer`,
    { answer, go },
    fetchFn,
  );
}

/** Stop a mission. */
export function cancelMission(
  missionId: string,
  fetchFn: FetchFn = fetch,
): Promise<Mission> {
  return postJson<Mission>(
    `/api/missions/${encodeURIComponent(missionId)}/cancel`,
    {},
    fetchFn,
  );
}

/** Register the mission on the scheduler (schedule dict or short string). */
export function scheduleMission(
  missionId: string,
  schedule: unknown,
  fetchFn: FetchFn = fetch,
): Promise<MissionScheduled> {
  return postJson<MissionScheduled>(
    `/api/missions/${encodeURIComponent(missionId)}/schedule`,
    { schedule },
    fetchFn,
  );
}

