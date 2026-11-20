import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutonomyDialPanel } from "./AutonomyDialPanel";
import type { DialState } from "../api/client";

function dialState(): DialState {
  return {
    threshold: 10,
    threshold_text: "10 accepted drafts in a row",
    areas: [
      {
        area: "money",
        label: "Money",
        locked: true,
        description: "Payments, billing, and purchases. Always asks first.",
        score: 0.5,
        stage: "propose",
        effective_stage: "propose",
        ceiling: "ask_first",
        streak: 25,
        threshold: 10,
        threshold_text: "10 accepted drafts in a row",
        suggestion: null,
      },
      {
        area: "messaging",
        label: "Outgoing messages",
        locked: false,
        description: "Sending, sharing, or posting to another person.",
        score: 0.5,
        stage: "propose",
        effective_stage: "propose",
        ceiling: "ask_first",
        streak: 10,
        threshold: 10,
        threshold_text: "10 accepted drafts in a row",
        suggestion: {
          area: "messaging",
          from_ceiling: "ask_first",
          to_ceiling: "balanced",
          threshold: 10,
          streak: 10,
          threshold_text: "10 accepted drafts in a row",
        },
      },
      {
        area: "files",
        label: "Delete",
        locked: false,
        description: "Deleting or changing files and system state.",
        score: 0.5,
        stage: "propose",
        effective_stage: "propose",
        ceiling: "ask_first",
        streak: 3,
        threshold: 10,
        threshold_text: "10 accepted drafts in a row",
        suggestion: null,
      },
      {
        area: "scheduling",
        label: "Schedule",
        locked: false,
        description: "Calendar events and reminders.",
        score: 0.5,
        stage: "propose",
        effective_stage: "propose",
        ceiling: "ask_first",
        streak: 0,
        threshold: 10,
        threshold_text: "10 accepted drafts in a row",
        suggestion: null,
      },
      {
        area: "shopping",
        label: "Shopping",
        locked: false,
        description: "Finding, comparing, and drafting a purchase.",
        score: 0.5,
        stage: "propose",
        effective_stage: "propose",
        ceiling: "ask_first",
        streak: 0,
        threshold: 10,
        threshold_text: "10 accepted drafts in a row",
        suggestion: null,
      },
    ],
  };
}

describe("AutonomyDialPanel", () => {
  it("shows all five areas with the visible threshold", async () => {
    render(<AutonomyDialPanel loadDial={() => Promise.resolve(dialState())} />);
    for (const label of ["Money", "Outgoing messages", "Delete", "Schedule", "Shopping"]) {
      expect(await screen.findByText(label, { exact: false })).toBeTruthy();
    }
    expect(screen.getAllByText(/10 draf diterima berturut-turut/).length).toBeGreaterThan(0);
  });

  it("locks Money: every position disabled even with a long streak", async () => {
    render(<AutonomyDialPanel loadDial={() => Promise.resolve(dialState())} />);
    await screen.findByText("Money", { exact: false });
    const moneyGroup = screen.getByText("Money", { exact: false }).closest("fieldset");
    expect(moneyGroup).toBeTruthy();
    const radios = moneyGroup!.querySelectorAll('input[type="radio"]');
    expect(radios.length).toBe(3);
    for (const radio of radios) expect((radio as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/Terkunci: selalu tanya dulu/)).toBeTruthy();
  });

  it("offers the raise only where the suggestion exists", async () => {
    render(<AutonomyDialPanel loadDial={() => Promise.resolve(dialState())} />);
    await screen.findByText("Siap naik ke Seimbang", { exact: false });
    const messagingGroup = screen
      .getByText("Outgoing messages", { exact: false })
      .closest("fieldset");
    const balanced = messagingGroup!.querySelector('input[value="balanced"]') as HTMLInputElement;
    expect(balanced.disabled).toBe(false);
    const filesGroup = screen.getByText("Delete", { exact: false }).closest("fieldset");
    const filesBalanced = filesGroup!.querySelector(
      'input[value="balanced"]',
    ) as HTMLInputElement;
    expect(filesBalanced.disabled).toBe(true);
    expect(screen.getByText("3 / 10 draf diterima berturut-turut", { exact: false })).toBeTruthy();
  });

  it("saves the new ceiling and confirms it", async () => {
    const user = userEvent.setup();
    const moved = { ...dialState().areas[1], ceiling: "balanced" as const };
    const saveCeiling = vi.fn().mockResolvedValue(moved);
    render(
      <AutonomyDialPanel loadDial={() => Promise.resolve(dialState())} saveCeiling={saveCeiling} />,
    );
    await screen.findByText("Siap naik ke Seimbang", { exact: false });
    const messagingGroup = screen
      .getByText("Outgoing messages", { exact: false })
      .closest("fieldset");
    await user.click(
      messagingGroup!.querySelector('input[value="balanced"]') as HTMLInputElement,
    );
    expect(saveCeiling).toHaveBeenCalledWith("messaging", "balanced");
    expect(await screen.findByText("Outgoing messages kini Seimbang.")).toBeTruthy();
  });

  it("shows the server error when saving fails", async () => {
    const user = userEvent.setup();
    const saveCeiling = vi.fn().mockRejectedValue(new Error("needs 10 accepted drafts"));
    render(
      <AutonomyDialPanel loadDial={() => Promise.resolve(dialState())} saveCeiling={saveCeiling} />,
    );
    await screen.findByText("Siap naik ke Seimbang", { exact: false });
    const messagingGroup = screen
      .getByText("Outgoing messages", { exact: false })
      .closest("fieldset");
    await user.click(
      messagingGroup!.querySelector('input[value="balanced"]') as HTMLInputElement,
    );
    expect(await screen.findByText(/Gagal mengubah Outgoing messages/)).toBeTruthy();
  });
});
