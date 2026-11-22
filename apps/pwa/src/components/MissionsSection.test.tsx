import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MissionsSection } from "./MissionsSection";

function mission() {
  return {
    id: "misi-abc",
    user_id: "user-1",
    title: "Riset harga laptop",
    goal: "Riset harga laptop",
    status: "waiting",
    steps: [
      {
        title: "Cari tiga harga",
        status: "done",
        result: "3 harga ditemukan",
        question: null,
        options: [],
        answer: null,
      },
      {
        title: "Pilih toko",
        status: "active",
        result: null,
        question: "Toko mana?",
        options: ["Tokopedia", "Shopee"],
        answer: null,
      },
      { title: "Beli", status: "pending", result: null, question: null, options: [], answer: null },
    ],
    webhook_token: "tok-1",
    created_at: "2026-11-22T12:00:00+00:00",
    updated_at: "2026-11-22T12:00:00+00:00",
  };
}

function stubFetch(calls: string[]) {
  return vi.fn().mockImplementation((url: string, init?: { method?: string }) => {
    calls.push(`${init?.method ?? "GET"} ${String(url)}`);
    const path = String(url);
    let body: unknown = { missions: [] };
    if (path === "/api/missions") body = { missions: [mission()] };
    else if (path.endsWith("/answer")) body = { ...mission(), status: "active" };
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MissionsSection", () => {
  it("shows the checklist with statuses and the open step-ask", async () => {
    vi.stubGlobal("fetch", stubFetch([]));
    render(<MissionsSection />);
    expect(await screen.findByText("Riset harga laptop")).toBeTruthy();
    expect(screen.getByText(/Cari tiga harga — Selesai/)).toBeTruthy();
    expect(screen.getByText(/3 harga ditemukan/)).toBeTruthy();
    expect(screen.getByText(/Pilih toko — Berjalan/)).toBeTruthy();
    expect(screen.getByText(/Beli — Menunggu/)).toBeTruthy();
    expect(screen.getByText(/Toko mana\?/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Tokopedia" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Shopee" })).toBeTruthy();
  });

  it("answers the open question from an option button", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    vi.stubGlobal("fetch", stubFetch(calls));
    render(<MissionsSection />);
    await user.click(await screen.findByRole("button", { name: "Tokopedia" }));
    expect(calls).toContain("POST /api/missions/misi-abc/answer");
  });

  it("says plainly when there are no missions yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ missions: [] }),
      }),
    );
    render(<MissionsSection />);
    expect(await screen.findByText("Belum ada misi.")).toBeTruthy();
  });
});
