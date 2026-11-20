import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LiveViewScreen } from "./LiveViewScreen";
import type { LiveFrame } from "../api/client";

function frame(overrides: Partial<LiveFrame> = {}): LiveFrame {
  return {
    agent_id: "puspa-1",
    status: "working",
    hold: null,
    current_task_id: "tugas-1",
    last_tool: {
      tool: "search_listings",
      task_id: "tugas-1",
      type: "tool_called",
      created_at: "2026-11-20T15:00:00+00:00",
    },
    last_event_at: "2026-11-20T15:00:00+00:00",
    events: [
      {
        id: "ev-1",
        type: "task_created",
        task_id: "tugas-1",
        agent_id: "puspa-1",
        subagent_id: null,
        created_at: "2026-11-20T14:59:00+00:00",
        content: {},
        hash: "aabb",
        previous_hash: "GENESIS",
        source: "ledger",
      },
      {
        id: "ev-2",
        type: "tool_called",
        task_id: "tugas-1",
        agent_id: "puspa-1",
        subagent_id: null,
        created_at: "2026-11-20T15:00:00+00:00",
        content: { tool: "search_listings" },
        hash: "ccdd",
        previous_hash: "aabb",
        source: "ledger",
      },
    ],
    live_tail: [],
    screenshot: null,
    generated_at: "2026-11-20T15:00:01+00:00",
    ...overrides,
  };
}

function stubFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("LiveViewScreen (P-09)", () => {
  it("shows the working bot: status, task, last tool, and recent events", async () => {
    vi.stubGlobal("fetch", stubFetch(frame()));
    render(
      <LiveViewScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/Bekerja/)).toBeTruthy();
    expect(screen.getByText(/tugas tugas-1/)).toBeTruthy();
    expect(screen.getByText(/tool terakhir search_listings/)).toBeTruthy();
    expect(screen.getByText(/tool_called · search_listings/)).toBeTruthy();
    expect(screen.getByText("Ambil alih")).toBeTruthy();
  });

  it("says there is no screen recording, honestly", async () => {
    vi.stubGlobal("fetch", stubFetch(frame()));
    render(
      <LiveViewScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/Bukan rekaman layar/)).toBeTruthy();
  });

  it("takes over on the second tap and shows the held state", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch(frame());
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => frame(),
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        agent_id: "puspa-1",
        status: "held",
        held_by: "user-1",
        actor: "user-1",
        reason: "diambil alih dari layar Live",
        task_id: "tugas-1",
        held_at: "2026-11-20T15:01:00+00:00",
        interrupted_subagents: [],
        pool_stopped: 0,
        lifecycle_wired: false,
        pool_wired: false,
        ledger_event_id: "ev-3",
        audit_trail_url: "/api/proof/audit-trail",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <LiveViewScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText("Ambil alih")).toBeTruthy();
    await user.click(screen.getByText("Ambil alih"));
    expect(screen.getByText("Yakin ambil alih?")).toBeTruthy();
    await user.click(screen.getByText("Yakin ambil alih?"));
    expect(await screen.findByText(/Bot dijeda/)).toBeTruthy();
    expect(screen.getByText("Lepas kendali")).toBeTruthy();
    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(String(init.body)).toContain("diambil alih dari layar Live");
  });

  it("shows the release button and the hold note when already held", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch(
        frame({
          status: "held",
          hold: {
            agent_id: "puspa-1",
            held_by_user_id: "user-1",
            reason: "checking the bot",
            task_id: "tugas-1",
            held_at: "2026-11-20T15:01:00+00:00",
          },
        }),
      ),
    );
    render(
      <LiveViewScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/Diambil alih oleh user-1/)).toBeTruthy();
    expect(screen.getByText("Lepas kendali")).toBeTruthy();
    expect(screen.getByText(/checking the bot/)).toBeTruthy();
  });

  it("says so when there is no agent to watch", () => {
    render(<LiveViewScreen bootstrap={{ agents: [] }} />);
    expect(screen.getByText("Belum ada agen untuk ditonton.")).toBeTruthy();
  });
});
