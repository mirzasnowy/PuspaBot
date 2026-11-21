import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArtifactsScreen } from "./ArtifactsScreen";
import type { ArtifactSummary, ReplayTaskSummary, ReplayTimeline } from "../api/client";

function artifacts(): ArtifactSummary[] {
  return [
    {
      artifact_id: "art-1",
      agent_id: "puspa-1",
      run_id: "tugas-1",
      name: "laporan.md",
      kind: "markdown",
      media_type: "text/markdown",
      size_bytes: 8,
      created_at: "2026-11-20T15:00:00+00:00",
    },
    {
      artifact_id: "art-2",
      agent_id: "puspa-1",
      run_id: "tugas-1",
      name: "replay-tugas-1.gif",
      kind: "gif",
      media_type: "image/gif",
      size_bytes: 2048,
      created_at: "2026-11-20T15:01:00+00:00",
    },
  ];
}

function runs(): ReplayTaskSummary[] {
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

function timeline(): ReplayTimeline {
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
  };
}

function stubFetch() {
  return vi.fn().mockImplementation((url: string) => {
    const path = String(url);
    let body: unknown;
    if (path.startsWith("/api/artifacts/")) body = artifacts();
    else if (path.includes("/tasks")) body = runs();
    else body = timeline();
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => body,
    });
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ArtifactsScreen", () => {
  it("lists the run's artifacts: name, kind, size, and open/download links", async () => {
    vi.stubGlobal("fetch", stubFetch());
    render(
      <ArtifactsScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText("laporan.md")).toBeTruthy();
    expect(screen.getByText(/Markdown/)).toBeTruthy();
    expect(screen.getByText(/8 B/)).toBeTruthy();
    expect(screen.getByText("replay-tugas-1.gif")).toBeTruthy();
    expect(screen.getByText(/2 KB/)).toBeTruthy();
    const openLinks = screen.getAllByText("Buka");
    expect(openLinks[0].getAttribute("href")).toBe("/api/artifacts/puspa-1/art-1");
    const downloadLinks = screen.getAllByText("Unduh");
    expect(downloadLinks[0].getAttribute("href")).toBe("/api/artifacts/puspa-1/art-1");
    expect(downloadLinks[0].getAttribute("download")).toBe("laporan.md");
  });

  it("switches to the run-log tab and shows each step with reason and result", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", stubFetch());
    render(
      <ArtifactsScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText("laporan.md")).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "Log Run" }));
    expect(await screen.findByText(/Langkah 1: task_created/)).toBeTruthy();
    expect(screen.getByText(/Bot memulai tugas ini/)).toBeTruthy();
    expect(screen.getByText(/Langkah 2: tool_called/)).toBeTruthy();
    expect(screen.getByText(/Bot memanggil tool search_listings/)).toBeTruthy();
    expect(screen.getByText(/Langkah 3: tool_completed/)).toBeTruthy();
    expect(screen.getByText(/3 listings found/)).toBeTruthy();
    expect(screen.getByText(/rantai hash utuh/)).toBeTruthy();
  });

  it("says plainly when an agent has no artifacts yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        const path = String(url);
        const body = path.startsWith("/api/artifacts/")
          ? []
          : path.includes("/tasks")
            ? []
            : timeline();
        return Promise.resolve({ ok: true, status: 200, json: async () => body });
      }),
    );
    render(
      <ArtifactsScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText(/belum menghasilkan artefak/)).toBeTruthy();
  });
});
