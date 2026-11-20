import { describe, expect, it } from "vitest";
import {
  appendMessage,
  applySseEvent,
  initialChatState,
  localEcho,
  reconcileEcho,
  workingAgents,
} from "./chat";

describe("chat store", () => {
  it("starts empty and appends messages in order", () => {
    const state = initialChatState([]);
    const next = appendMessage(state, { sender: "users", text: "halo", created_at: 1 });
    expect(next.messages).toHaveLength(1);
    expect(next.messages[0].text).toBe("halo");
    expect(state.messages).toHaveLength(0);
  });

  it("replaces the local echo with the server echo", () => {
    const state = appendMessage(initialChatState([]), localEcho("halo Puspa"));
    const next = reconcileEcho(state, { sender: "users", text: "halo Puspa", created_at: 42 });
    expect(next.messages).toHaveLength(1);
    expect(next.messages[0].created_at).toBe(42);
  });

  it("appends the server message when no echo matches", () => {
    const state = initialChatState([]);
    const next = reconcileEcho(state, { sender: "puspa", text: "hai", created_at: 2 });
    expect(next.messages).toHaveLength(1);
  });

  it("folds office SSE events into the live strip", () => {
    const state = initialChatState([]);
    const office = {
      agent: { puspa: { agent_id: "puspa", status: "working" as const, cost_rp: 0 } },
    };
    const next = applySseEvent(state, { type: "office", content: office });
    expect(next.liveConnected).toBe(true);
    expect(workingAgents(next.liveOffice)).toEqual(["puspa"]);
    const heartbeat = applySseEvent(next, { type: "heartbeat", created_at: 3 });
    expect(heartbeat.liveConnected).toBe(true);
    expect(workingAgents(heartbeat.liveOffice)).toEqual(["puspa"]);
  });

  it("reports no working agents when the projection is empty", () => {
    expect(workingAgents(null)).toEqual([]);
    expect(workingAgents({ agent: {} })).toEqual([]);
  });

  it("reads working agents from the list shape the server sends", () => {
    const office = {
      agents: [{ agent_id: "puspa", status: "working" as const, cost_rp: 0 }],
    };
    expect(workingAgents(office)).toEqual(["puspa"]);
  });
});
