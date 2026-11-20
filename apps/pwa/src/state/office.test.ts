import { describe, expect, it } from "vitest";
import {
  applyOfficeEvent,
  officeAgents,
  runningTaskProgress,
  totalRunningCostRp,
  workingCount,
} from "./office";

describe("office store", () => {
  const projection = {
    agent: {
      puspa: { agent_id: "puspa", status: "working" as const, cost_rp: 1200, subagents: [] },
      "agen-1": { agent_id: "agen-1", status: "idle" as const, cost_rp: 300, subagents: [] },
    },
  };

  it("normalizes the keyed projection to a sorted list", () => {
    const agents = officeAgents(projection);
    expect(agents.map((a) => a.agent_id)).toEqual(["agen-1", "puspa"]);
  });

  it("counts working agents and total cost", () => {
    expect(workingCount(projection)).toBe(1);
    expect(totalRunningCostRp(projection)).toBe(1500);
  });

  it("returns empty for a missing projection", () => {
    expect(officeAgents(null)).toEqual([]);
    expect(workingCount(undefined)).toBe(0);
    expect(totalRunningCostRp(null)).toBe(0);
    expect(runningTaskProgress(null)).toEqual([]);
  });

  it("reads the list shape the server actually sends", () => {
    // puspa.evidence.projections.office returns {"agents": [...]};
    // the keyed `agent` map is only the spec's older shape.
    const list = {
      agents: [
        { agent_id: "puspa", status: "working" as const, cost_rp: 250, last_task: "task-1" },
        { agent_id: "agen-1", status: "idle" as const, cost_rp: 100 },
      ],
    };
    expect(officeAgents(list).map((a) => a.agent_id)).toEqual(["agen-1", "puspa"]);
    expect(workingCount(list)).toBe(1);
    expect(totalRunningCostRp(list)).toBe(350);
  });

  it("merges both shapes without duplicating an agent", () => {
    const mixed = {
      agent: {
        puspa: { agent_id: "puspa", status: "working" as const, cost_rp: 250 },
      },
      agents: [
        { agent_id: "puspa", status: "idle" as const, cost_rp: 999 },
        { agent_id: "agen-1", status: "idle" as const, cost_rp: 100 },
      ],
    };
    const agents = officeAgents(mixed);
    expect(agents.map((a) => a.agent_id)).toEqual(["agen-1", "puspa"]);
    expect(agents.find((a) => a.agent_id === "puspa")?.status).toBe("working");
  });

  it("derives running-task progress from working agents only", () => {
    const list = {
      agents: [
        {
          agent_id: "puspa",
          status: "working" as const,
          last_task: "task-1",
          cost_rp: 250,
          subagents: [{ status: "working" }, { status: "idle" }],
        },
        { agent_id: "agen-1", status: "idle" as const, last_task: "task-0", cost_rp: 100 },
      ],
    };
    expect(runningTaskProgress(list)).toEqual([
      { agentId: "puspa", taskId: "task-1", workingSubagents: 1, costRp: 250 },
    ]);
  });

  it("replaces the projection on office SSE events only", () => {
    const next = applyOfficeEvent(null, { type: "office", content: projection });
    expect(next).toEqual(projection);
    const kept = applyOfficeEvent(projection, { type: "heartbeat", created_at: 1 });
    expect(kept).toEqual(projection);
  });
});
