import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InsightsScreen } from "./InsightsScreen";
import { emptyInsights, type InsightsReport } from "../state/insights";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

function reportWithUsage(): InsightsReport {
  const report = emptyInsights(7);
  return {
    ...report,
    turns: 2,
    tokens: { input: 2000, output: 400, cached: 200, total: 2600 },
    cost: {
      total_idr: 1500,
      label: "Rp1.500",
      avg_per_turn_idr: 750,
      avg_per_turn_label: "Rp750",
      per_1k_tokens_idr: 576.9231,
      per_1k_tokens_label: "Rp576.9231 / 1k tokens",
    },
    per_model: [
      { model: "cheap-model", turns: 2, tokens: 2600, cost_idr: 1500, share: 1 },
    ],
    per_driver: [
      { driver: "openrouter", turns: 2, tokens: 2600, cost_idr: 1500, share: 1 },
    ],
    tool_usage: [{ tool: "read", uses: 2 }],
    module_usage: [{ module_id: "briefing", turns: 2, cost_idr: 1500 }],
    activity_per_day: [
      { date: "2026-11-18", turns: 2, tokens: 2600, cost_idr: 1500 },
    ],
    top_costly_tasks: [{ task_id: "task-1-abcdef", turns: 2, tokens: 2600, cost_idr: 1500 }],
    tool_errors: 0,
    task_cost_ceiling_idr: 2000,
  };
}

describe("InsightsScreen", () => {
  it("shows totals and the per-model table from the server report", async () => {
    const fetchMock = mockFetch(reportWithUsage());
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<InsightsScreen />);
      expect(await screen.findByText(/Total biaya: Rp1\.500/)).toBeTruthy();
      expect(screen.getByText(/2 putaran tercatat/)).toBeTruthy();
      expect(screen.getByText(/Rp576\.9231 \/ 1k tokens/)).toBeTruthy();
      expect(screen.getByRole("rowheader", { name: "cheap-model" })).toBeTruthy();
      expect(screen.getByText("read: 2 kali")).toBeTruthy();
      expect(screen.getByText(/briefing: 2 putaran/)).toBeTruthy();
      expect(fetchMock).toHaveBeenCalledWith("/api/insights?days=7", {
        credentials: "same-origin",
      });
      // No approximate "~$0.00"-style label anywhere on the page.
      expect(document.body.textContent).not.toContain("~");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("refetches for the chosen window when the range changes", async () => {
    const fetchMock = mockFetch(reportWithUsage());
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<InsightsScreen />);
      await screen.findByText(/Total biaya/);
      const user = userEvent.setup();
      await user.click(screen.getByRole("radio", { name: "30 hari" }));
      expect(await screen.findByText(/2 putaran tercatat/)).toBeTruthy();
      expect(fetchMock).toHaveBeenCalledWith("/api/insights?days=30", {
        credentials: "same-origin",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows an honest empty state when nothing is recorded", async () => {
    vi.stubGlobal("fetch", mockFetch(emptyInsights(7)));
    try {
      render(<InsightsScreen />);
      expect(await screen.findByText("Belum ada pemakaian tercatat")).toBeTruthy();
      expect(screen.getByText(/0 putaran tercatat/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows an error and a retry button when loading fails", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("jaringan putus"));
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<InsightsScreen />);
      expect(await screen.findByText(/Gagal memuat insights/)).toBeTruthy();
      const callsBefore = fetchMock.mock.calls.length;
      fireEvent.click(screen.getByRole("button", { name: "Coba lagi" }));
      expect(await screen.findByText(/Gagal memuat insights/)).toBeTruthy();
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
