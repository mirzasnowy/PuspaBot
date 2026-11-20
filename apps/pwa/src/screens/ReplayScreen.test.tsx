import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReplayScreen } from "./ReplayScreen";
import type { ReplayTaskSummary, ReplayTimeline } from "../api/client";

function tasks(): ReplayTaskSummary[] {
  return [
    {
      task_id: "tugas-1",
      event_count: 3,
      first_event_at: "2026-11-20T14:59:00+00:00",
      last_event_at: "2026-11-20T15:00:00+00:00",
      status: "running",
    },
  ];
}

function timeline(overrides: Partial<ReplayTimeline> = {}): ReplayTimeline {
  return {
    agent_id: "puspa-1",
    task_id: "tugas-1",
    step_count: 3,
    status: "running",
    chain_ok: true,
    broken_event_ids: [],
    started_at: "2026-11-20T14:59:00+00:00",
    ended_at: "2026-11-20T15:00:00+00:00",
    generated_at: "2026-11-20T15:00:01+00:00",
    steps: [
      {
        index: 0,
        event_id: "ev-1",
        type: "task_created",
        task_id: "tugas-1",
        agent_id: "puspa-1",
        subagent_id: null,
        created_at: "2026-11-20T14:59:00+00:00",
        tool: null,
        reason: "Bot memulai tugas ini. Tujuannya: compare prices",
        result: null,
        content: { purpose: "compare prices" },
        hash: "aabb",
        previous_hash: "GENESIS",
        screenshot: null,
      },
      {
        index: 1,
        event_id: "ev-2",
        type: "tool_called",
        task_id: "tugas-1",
        agent_id: "puspa-1",
        subagent_id: null,
        created_at: "2026-11-20T15:00:00+00:00",
        tool: "search_listings",
        reason: "Bot memanggil tool search_listings — need fresh prices.",
        result: null,
        content: { tool: "search_listings", reason: "need fresh prices" },
        hash: "ccdd",
        previous_hash: "aabb",
        screenshot: null,
      },
      {
        index: 2,
        event_id: "ev-3",
        type: "tool_completed",
        task_id: "tugas-1",
        agent_id: "puspa-1",
        subagent_id: null,
        created_at: "2026-11-20T15:00:00+00:00",
        tool: "search_listings",
        reason: "Tool search_listings selesai.",
        result: "3 listings found",
        content: { tool: "search_listings", result: "3 listings found" },
        hash: "eeff",
        previous_hash: "ccdd",
        screenshot: null,
      },
    ],
    ...overrides,
  };
}

function stubFetchFor(timelineBody: ReplayTimeline) {
  return vi.fn().mockImplementation((url: string) => {
    const body = String(url).includes("/tasks") ? tasks() : timelineBody;
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => body,
    });
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("ReplayScreen (P-10)", () => {
  it("shows the first step: reason, no result yet, and the chain state", async () => {
    vi.stubGlobal("fetch", stubFetchFor(timeline()));
    render(
      <ReplayScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/Langkah 1 dari 3/)).toBeTruthy();
    expect(screen.getByText(/Bot memulai tugas ini/)).toBeTruthy();
    expect(screen.getByText(/belum ada hasil tercatat/)).toBeTruthy();
    expect(screen.getByText(/rantai hash utuh/)).toBeTruthy();
    expect(screen.getByText("Putar")).toBeTruthy();
  });

  it("says the frames are rendered, not a screen recording", async () => {
    vi.stubGlobal("fetch", stubFetchFor(timeline()));
    render(
      <ReplayScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/Bukan rekaman layar/)).toBeTruthy();
  });

  it("scrubs forward and back with the step buttons and the slider", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", stubFetchFor(timeline()));
    render(
      <ReplayScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/Langkah 1 dari 3/)).toBeTruthy();
    await user.click(screen.getByText("Langkah berikutnya →"));
    expect(screen.getByText(/Langkah 2 dari 3/)).toBeTruthy();
    expect(screen.getByText(/Bot memanggil tool search_listings/)).toBeTruthy();
    await user.click(screen.getByText("Langkah berikutnya →"));
    expect(screen.getByText(/Langkah 3 dari 3/)).toBeTruthy();
    expect(screen.getByText(/3 listings found/)).toBeTruthy();
    expect(screen.getByText("Putar ulang")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Geser ke langkah"), {
      target: { value: "0" },
    });
    expect(screen.getByText(/Langkah 1 dari 3/)).toBeTruthy();
  });

  it("plays forward one step per frame and stops at the last step", async () => {
    vi.stubGlobal("fetch", stubFetchFor(timeline()));
    render(
      <ReplayScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/Langkah 1 dari 3/)).toBeTruthy();
    vi.useFakeTimers();
    // fireEvent, not userEvent: userEvent's async click waits on
    // real timers, which are faked here and would hang the test.
    // Timer advances go through act() so React flushes the step.
    fireEvent.click(screen.getByText("Putar"));
    expect(screen.getByText("Jeda")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(screen.getByText(/Langkah 2 dari 3/)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(screen.getByText(/Langkah 3 dari 3/)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(screen.getByText(/Langkah 3 dari 3/)).toBeTruthy();
    expect(screen.getByText("Putar ulang")).toBeTruthy();
  });

  it("offers the GIF export for the whole timeline", async () => {
    vi.stubGlobal("fetch", stubFetchFor(timeline()));
    render(
      <ReplayScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    const link = await screen.findByText(/Unduh GIF \(3 frame\)/);
    expect(link.closest("a")?.getAttribute("href")).toBe(
      "/api/replay/puspa-1/tugas-1/export.gif",
    );
  });

  it("warns when the hash chain is broken", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchFor(timeline({ chain_ok: false, broken_event_ids: ["ev-2"] })),
    );
    render(
      <ReplayScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/rantai hash PUTUS \(1 langkah\)/)).toBeTruthy();
  });

  it("says so when there is no agent to replay", () => {
    render(<ReplayScreen bootstrap={{ agents: [] }} />);
    expect(screen.getByText("Belum ada agen untuk diputar ulang.")).toBeTruthy();
  });
});
