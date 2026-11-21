import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ComputerPanel } from "./ComputerPanel";

function stopState(active: boolean) {
  return {
    active,
    actor: "user",
    reason: active ? "halt" : "",
    updated_at: "2026-11-22T01:00:00Z",
  };
}

function auditList() {
  return {
    entries: [
      {
        id: "1",
        created_at: "2026-11-22T01:00:00Z",
        type: "tool_called",
        tool: "computer.click",
        computer_action: "click",
        target: "n3",
        app: "Notes",
        tier: "normal",
        decision: "allow",
        outcome: "performed",
        reason: "element changed after the click",
      },
    ],
    ledger_available: true,
  };
}

describe("ComputerPanel", () => {
  it("lists allowlisted apps and the newest audit entries", async () => {
    render(
      <ComputerPanel
        loadAllowlist={async () => ({ apps: ["Notes"] })}
        loadStop={async () => stopState(false)}
        loadAudit={async () => auditList()}
      />,
    );
    expect(await screen.findByText("Notes")).toBeTruthy();
    expect(await screen.findByText(/click/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Hentikan tangan komputer" })).toBeTruthy();
  });

  it("adds an app from the input", async () => {
    const user = userEvent.setup();
    const addApp = vi.fn(async (app: string) => ({ app, allowed: true, changed: true }));
    render(
      <ComputerPanel
        loadAllowlist={async () => ({ apps: [] })}
        loadStop={async () => stopState(false)}
        loadAudit={async () => ({ entries: [], ledger_available: true })}
        addApp={addApp}
      />,
    );
    await screen.findByText("Belum ada: semua klik masih tanya dulu.");
    await user.type(screen.getByLabelText(/Tambah aplikasi/), "Notes");
    await user.click(screen.getByRole("button", { name: "Tambah" }));
    expect(addApp).toHaveBeenCalledWith("Notes");
    expect(await screen.findByText(/kini diizinkan/)).toBeTruthy();
  });

  it("asks twice before stopping and shows the stopped state", async () => {
    const user = userEvent.setup();
    const requestStop = vi.fn(async () => stopState(true));
    render(
      <ComputerPanel
        loadAllowlist={async () => ({ apps: [] })}
        loadStop={async () => stopState(false)}
        loadAudit={async () => ({ entries: [], ledger_available: true })}
        requestStop={requestStop}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "Hentikan tangan komputer" }));
    expect(requestStop).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Yakin hentikan tangan komputer?" }));
    expect(requestStop).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Dihentikan/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Jalankan lagi" })).toBeTruthy();
  });

  it("resumes the hands from the stopped state", async () => {
    const user = userEvent.setup();
    const clearStop = vi.fn(async () => stopState(false));
    render(
      <ComputerPanel
        loadAllowlist={async () => ({ apps: [] })}
        loadStop={async () => stopState(true)}
        loadAudit={async () => ({ entries: [], ledger_available: true })}
        clearStop={clearStop}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "Jalankan lagi" }));
    expect(clearStop).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/jalan lagi/)).toBeTruthy();
  });
});
