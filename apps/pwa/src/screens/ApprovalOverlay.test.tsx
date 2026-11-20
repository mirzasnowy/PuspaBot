import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalOverlayPanel } from "./ApprovalOverlay";
import type { ApprovalOverlay } from "../api/client";

function overlay(overrides: Partial<ApprovalOverlay> = {}): ApprovalOverlay {
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
    ...overrides,
  };
}

describe("ApprovalOverlayPanel (P-11)", () => {
  it("shows the target element, the card lines, and the highlight note", () => {
    render(
      <ApprovalOverlayPanel overlay={overlay()} onDecide={async () => {}} busy={false} />,
    );
    expect(screen.getByText("Approve send")).toBeTruthy();
    expect(screen.getByText(/Tombol yang mau diklik/)).toBeTruthy();
    expect(screen.getByText("Kirim")).toBeTruthy();
    // The target shows twice: in the highlighted target box and
    // in the card's Recipient line.
    expect(screen.getAllByText(/sari@contoh.id/).length).toBe(2);
    expect(screen.getByText("Recipient")).toBeTruthy();
    expect(screen.getByText(/Disorot di frame/)).toBeTruthy();
    expect(screen.getByText("Setuju")).toBeTruthy();
    expect(screen.getByText("Tolak")).toBeTruthy();
  });

  it("approves with the request id and no reason when none is typed", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalOverlayPanel overlay={overlay()} onDecide={onDecide} busy={false} />);
    await user.click(screen.getByText("Setuju"));
    expect(onDecide).toHaveBeenCalledWith("req-1", "approved", "");
  });

  it("rejects with the typed reason", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalOverlayPanel overlay={overlay()} onDecide={onDecide} busy={false} />);
    await user.type(screen.getByPlaceholderText("Kenapa disetujui atau ditolak"), "jangan dulu");
    await user.click(screen.getByText("Tolak"));
    expect(onDecide).toHaveBeenCalledWith("req-1", "rejected", "jangan dulu");
  });

  it("shows a decided overlay without buttons", () => {
    render(
      <ApprovalOverlayPanel
        overlay={overlay({
          status: "rejected",
          decided_by: "user-1",
          decision_reason: "jangan dulu",
        })}
        onDecide={async () => {}}
        busy={false}
      />,
    );
    expect(screen.getByText(/Ditolak oleh user-1/)).toBeTruthy();
    expect(screen.getByText(/jangan dulu/)).toBeTruthy();
    expect(screen.queryByText("Setuju")).toBeNull();
    expect(screen.queryByText("Tolak")).toBeNull();
  });

  it("names the tool when there is no button label", () => {
    render(
      <ApprovalOverlayPanel
        overlay={overlay({
          button_label: null,
          target_element: {
            element_kind: "tool",
            label: "delete",
            tool_name: "delete",
            target: "catatan lama",
            detail: [{ label: "Target", value: "catatan lama" }],
            content_hash: "ccdd",
          },
        })}
        onDecide={async () => {}}
        busy={false}
      />,
    );
    expect(screen.getByText(/Tool yang mau dijalankan/)).toBeTruthy();
    expect(screen.getByText("delete")).toBeTruthy();
  });
});
