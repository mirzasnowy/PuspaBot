/**
 * Office screen store: live team projection seeded from the
 * bootstrap and refreshed by SSE frames. The projection is keyed
 * by agent id on the wire (`office.agent` is a record); this store
 * normalizes it to a sorted list for rendering.
 */
import type { OfficeAgent, OfficeProjection, SseEvent } from "../api/client";

export function officeAgents(office: OfficeProjection | null | undefined): OfficeAgent[] {
  if (!office?.agent) return [];
  return Object.values(office.agent).sort((a, b) =>
    (a.agent_id ?? "").localeCompare(b.agent_id ?? ""),
  );
}

export function applyOfficeEvent(
  current: OfficeProjection | null,
  event: SseEvent,
): OfficeProjection | null {
  if (event.type === "office" && event.content) return event.content;
  return current;
}

export function totalRunningCostRp(office: OfficeProjection | null | undefined): number {
  return officeAgents(office).reduce((sum, a) => sum + (a.cost_rp ?? 0), 0);
}

export function workingCount(office: OfficeProjection | null | undefined): number {
  return officeAgents(office).filter((a) => a.status === "working").length;
}
