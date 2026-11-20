import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WeeklyReport } from "./WeeklyReport";
import type { WeeklyReport as WeeklyReportData } from "../api/client";

function report(): WeeklyReportData {
  return {
    window_days: 7,
    since: "2026-11-14T00:00:00Z",
    until: "2026-11-20T23:59:59Z",
    tasks_completed: 5,
    tasks_corrected: 2,
    approvals_granted: 8,
    approvals_rejected: 2,
    tasks_stopped: 1,
    tasks_failed: 0,
    time_saved_minutes: null,
    money_saved_idr: null,
    savings_note:
      "No savings figure is claimed: time saved needs a manual-time baseline.",
    cost_idr: 2500,
    cost_label: "Rp2.500",
    audit_trail_url: "/api/proof/audit-trail",
    linked_events: [
      {
        event_id: "event-1",
        type: "task_completed",
        task_id: "task-1",
        created_at: "2026-11-19T10:00:00Z",
        receipt_url: "/api/proof/receipts/event-1",
      },
      {
        event_id: "event-2",
        type: "approval_rejected",
        task_id: "task-1",
        created_at: "2026-11-19T09:00:00Z",
        receipt_url: "/api/proof/receipts/event-2",
      },
    ],
  };
}

describe("WeeklyReport", () => {
  it("shows completed and corrected counts with the cost", async () => {
    render(<WeeklyReport days={7} loadReport={() => Promise.resolve(report())} />);
    expect(await screen.findByText(/5 tugas selesai · 2 tugas dikoreksi/)).toBeTruthy();
    expect(screen.getByText(/Biaya dalam 7 hari terakhir: Rp2\.500/)).toBeTruthy();
  });

  it("says savings are not measured instead of inventing a figure", async () => {
    render(<WeeklyReport days={7} loadReport={() => Promise.resolve(report())} />);
    expect(await screen.findByText(/belum diukur/)).toBeTruthy();
    expect(screen.queryByText(/Rp0/)).toBeNull();
  });

  it("links every counted event to its audit receipt", async () => {
    render(<WeeklyReport days={7} loadReport={() => Promise.resolve(report())} />);
    await screen.findByText(/5 tugas selesai/);
    expect(screen.getByRole("link", { name: "Lihat audit lengkap" }).getAttribute("href")).toBe(
      "/api/proof/audit-trail",
    );
    const receipt = screen.getByRole("link", { name: "task_completed — task-1" });
    expect(receipt.getAttribute("href")).toBe("/api/proof/receipts/event-1");
  });

  it("shows the server error when the report fails to load", async () => {
    render(
      <WeeklyReport days={7} loadReport={() => Promise.reject(new Error("belum masuk"))} />,
    );
    expect(await screen.findByText(/Gagal memuat rapor mingguan/)).toBeTruthy();
  });
});
