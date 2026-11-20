/**
 * Morning summary + notes-to-actions client (P-07, plan 12.8).
 *
 * Lives in its own module (not `client.ts`) so the shared client
 * stays under the 1000-line file-size ratchet. Types come from the
 * generated file `generated-types.ts` (openapi-typescript from
 * `docs/openapi-pwa.yaml`, which $refs `docs/openapi-morning.yaml`);
 * the fetch helpers are the shared ones from `client.ts`.
 */
import type { components } from "./generated-types";
import {
  getJson,
  postJson,
  postMultipart,
  type FetchFn,
} from "./client";

export type MorningSummary = components["schemas"]["MorningSummary"];
export type MorningStatus = components["schemas"]["MorningStatus"];
export type MorningNote = components["schemas"]["MorningNote"];
export type MorningNoteAction = components["schemas"]["MorningNoteAction"];
export type MorningNoteList = components["schemas"]["MorningNoteList"];
export type MorningNoteCreated = components["schemas"]["MorningNoteCreated"];
export type MorningVoiceNoteCreated =
  components["schemas"]["MorningVoiceNoteCreated"];

/**
 * Morning summary for one day (P-07): schedule, bill candidates,
 * open tasks, holidays, and prayer times. `date` is YYYY-MM-DD
 * (defaults to today on the server); `city` defaults to Jakarta.
 * Each section carries its own status — the screen shows which
 * sources were available instead of pretending.
 */
export function fetchMorningSummary(
  date?: string,
  city?: string,
  fetchFn: FetchFn = fetch,
): Promise<MorningSummary> {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (city) params.set("city", city);
  const query = params.toString();
  return getJson<MorningSummary>(
    query ? `/api/morning/summary?${query}` : "/api/morning/summary",
    fetchFn,
  );
}

/** Per-source availability for the morning summary (P-07). */
export function fetchMorningStatus(
  fetchFn: FetchFn = fetch,
): Promise<MorningStatus> {
  return getJson<MorningStatus>("/api/morning/status", fetchFn);
}

/** The user's notes, newest first (P-07). */
export function fetchMorningNotes(
  fetchFn: FetchFn = fetch,
): Promise<MorningNoteList> {
  return getJson<MorningNoteList>("/api/morning/notes", fetchFn);
}

/**
 * Turn a pasted note into a summary and tracked actions (P-07).
 * `source` names where the note came from (meeting, lecture,
 * study, or plain text).
 */
export function createMorningNoteFromText(
  text: string,
  source: string,
  title: string,
  fetchFn: FetchFn = fetch,
): Promise<MorningNoteCreated> {
  return postJson<MorningNoteCreated>(
    "/api/morning/notes/text",
    { text, source, title },
    fetchFn,
  );
}

/**
 * Turn a voice note into a summary and tracked actions (P-07).
 * The server transcribes the audio (transcriber still a stub —
 * see /api/voice/status) and deletes it right away.
 */
export function uploadMorningVoiceNote(
  audio: Blob,
  source: string,
  title: string,
  fetchFn: FetchFn = fetch,
): Promise<MorningVoiceNoteCreated> {
  const form = new FormData();
  form.append("audio", audio, "recording.webm");
  form.append("source", source);
  form.append("title", title);
  return postMultipart<MorningVoiceNoteCreated>(
    "/api/morning/notes/voice",
    form,
    fetchFn,
  );
}
