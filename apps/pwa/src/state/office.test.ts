import { describe, expect, it } from "vitest";
import { applyOfficeEvent, officeAgents, totalRunningCostRp, workingCount } from "./office";

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
  });

  it("replaces the projection on office SSE events only", () => {
    const next = applyOfficeEvent(null, { type: "office", content: projection });
    expect(next).toEqual(projection);
    const kept = applyOfficeEvent(projection, { type: "heartbeat", created_at: 1 });
    expect(kept).toEqual(projection);
  });
});
