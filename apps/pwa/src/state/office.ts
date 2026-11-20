/**
 * Office screen store: live team projection seeded from the
 * bootstrap and refreshed by SSE frames.
 *
 * Wire shape (P-13): the spec carries the projection two ways —
 * the keyed `office.agent` map and the `office.agents` list —
 * because the server actually sends the list
 * (`puspa.evidence.projections.office` returns
 * `{"agents": [...]}`) while the spec originally described only
 * the map. This store accepts both and merges them by agent id,
 * so the Office screen, the WorkStrip, and the Chat task rail
 * all read the same live data whatever shape arrives.
 */
import type { OfficeAgent, OfficeProjection, SseEvent } from "../api/client";

/** One running task's progress, as the office projection carries it. */
export interface TaskProgress {
  agentId: string;
  taskId: string;
  workingSubagents: number;
  costRp: number;
}

export function officeAgents(office: OfficeProjection | null | undefined): OfficeAgent[] {
  if (!office) return [];
  const byId = new Map<string, OfficeAgent>();
  if (office.agent) {
    for (const agent of Object.values(office.agent)) {
      if (agent.agent_id) byId.set(agent.agent_id, agent);
    }
  }
  // The list shape the server sends; a repeated id keeps the
  // map entry, so one agent never appears twice.
  for (const agent of office.agents ?? []) {
    if (agent.agent_id && !byId.has(agent.agent_id)) byId.set(agent.agent_id, agent);
  }
  return [...byId.values()].sort((a, b) =>
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

/**
 * Running tasks, one row per working agent, from the same live
 * office projection the Office screen renders. A row carries
 * only what the projection carries: the agent, the task id it
 * last started, how many of its subagents are still working,
 * and the cost so far. Purpose and stage are NOT on this
 * stream — they live only in the Tasks screen's session state —
 * so the rail never invents them.
 */
export function runningTaskProgress(
  office: OfficeProjection | null | undefined,
): TaskProgress[] {
  return officeAgents(office)
    .filter((a) => a.status === "working")
    .map((a) => ({
      agentId: a.agent_id ?? "",
      taskId: a.last_task ?? "",
      workingSubagents: (a.subagents ?? []).filter((s) => s.status === "working").length,
      costRp: a.cost_rp ?? 0,
    }));
}
