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

const EMPTY_OVERLAYS = { agent_id: "puspa-1", pending: [], decided: [] };

function pendingOverlay(): Record<string, unknown> {
  return {
    request_id: "req-1",
    agent_id: "puspa-1",
    task_id: "tugas-1",
    kind: "send_to_other",
    tool_name: "gmail.kirim",
    button_label: "Kirim",
    target_element: {
      element_kind: "button",
      label: "Kirim",
      tool_name: "gmail.kirim",
      target: "sari@contoh.id",
      detail: [{ label: "Recipient", value: "sari@contoh.id" }],
      content_hash: "aabb",
    },
    card: {
      request_id: "req-1",
      kind: "send_to_other",
      tool_name: "gmail.kirim",
      title: "Approve send",
      lines: [{ label: "Recipient", value: "sari@contoh.id" }],
      content_hash: "aabb",
      warnings: [],
      complete: true,
      narrative_ignored: false,
    },
    status: "pending",
    created_at: "2026-11-21T00:00:00+00:00",
    decided_at: null,
    decided_by: null,
    decision_reason: "",
    gate_rejected: false,
    proposed_ledger_event_id: "ev-1",
    decided_ledger_event_id: null,
    highlight: {
      frame_ref: "last_tool",
      event_id: null,
      note: "the frame's last tool call is 'gmail.kirim': the highlighted row",
    },
  };
}

function stubFetch(frameBody: unknown, overlaysBody: unknown = EMPTY_OVERLAYS) {
  return vi.fn().mockImplementation((url: string) => {
    const body = String(url).includes("/approvals") ? overlaysBody : frameBody;
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
    const takeoverReport = {
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
    };
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/takeover")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => takeoverReport,
        });
      }
      const body = String(url).includes("/approvals") ? EMPTY_OVERLAYS : frame();
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
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
    const takeoverCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/takeover"),
    );
    expect(takeoverCall).toBeTruthy();
    const [, init] = takeoverCall as [string, RequestInit];
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

  it("shows a pending overlay and highlights the matching frame row (P-11)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch(frame(), {
        agent_id: "puspa-1",
        pending: [pendingOverlay()],
        decided: [],
      }),
    );
    render(
      <LiveViewScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText("Menunggu persetujuan")).toBeTruthy();
    expect(screen.getByText("Approve send")).toBeTruthy();
    expect(screen.getByText(/Tombol yang mau diklik/)).toBeTruthy();
    expect(screen.getByText("Setuju")).toBeTruthy();
    expect(screen.getByText("Tolak")).toBeTruthy();
    // The highlight points at the last tool call: that row is marked.
    expect(screen.getByText(/tool terakhir search_listings/).className).toContain(
      "overlay-highlight",
    );
  });

  it("approves one overlay and moves it to decided (P-11)", async () => {
    const user = userEvent.setup();
    const overlay = pendingOverlay();
    let approved = false;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/approvals/req-1/approve")) {
        approved = true;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...overlay,
            status: "approved",
            decided_by: "user-1",
            decided_ledger_event_id: "ev-9",
          }),
        });
      }
      if (String(url).includes("/approvals")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            approved
              ? {
                  agent_id: "puspa-1",
                  pending: [],
                  decided: [{ ...overlay, status: "approved", decided_by: "user-1" }],
                }
              : { agent_id: "puspa-1", pending: [overlay], decided: [] },
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => frame() });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <LiveViewScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText("Setuju")).toBeTruthy();
    await user.click(screen.getByText("Setuju"));
    expect(await screen.findByText(/Disetujui: aksi berjalan/)).toBeTruthy();
    expect(screen.getByText(/Disetujui oleh user-1/)).toBeTruthy();
    expect(screen.queryByText("Tolak")).toBeNull();
    const approveCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/approvals/req-1/approve"),
    );
    expect(approveCall).toBeTruthy();
    expect((approveCall as [string, RequestInit])[1].method).toBe("POST");
  });

  it("rejects one overlay with the typed reason (P-11)", async () => {
    const user = userEvent.setup();
    const overlay = pendingOverlay();
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/approvals/req-1/reject")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...overlay,
            status: "rejected",
            decided_by: "user-1",
            decision_reason: "jangan dulu",
            decided_ledger_event_id: "ev-9",
          }),
        });
      }
      if (String(url).includes("/approvals")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ agent_id: "puspa-1", pending: [overlay], decided: [] }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => frame() });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <LiveViewScreen bootstrap={{ agents: [{ agent_id: "puspa-1", name: "Puspa" }] }} />,
    );
    expect(await screen.findByText("Tolak")).toBeTruthy();
    await user.type(screen.getByPlaceholderText("Kenapa disetujui atau ditolak"), "jangan dulu");
    await user.click(screen.getByText("Tolak"));
    expect(await screen.findByText(/Ditolak: aksi dibatalkan/)).toBeTruthy();
    const rejectCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/approvals/req-1/reject"),
    );
    expect(rejectCall).toBeTruthy();
    const [, init] = rejectCall as [string, RequestInit];
    expect(JSON.parse(String(init.body)).reason).toBe("jangan dulu");
  });
});
