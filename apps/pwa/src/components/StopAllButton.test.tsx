import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StopAllButton } from "./StopAllButton";
import { stopAll } from "../api/client";

vi.mock("../api/client", () => ({ stopAll: vi.fn() }));

const mockedStopAll = vi.mocked(stopAll);

beforeEach(() => {
  mockedStopAll.mockClear();
});

function stopReport() {
  return {
    user_id: "user-1",
    actor: "user-1",
    reason: "stop all requested from the PWA",
    task_id: "stop-all-abc",
    agents_stopped: [{ agent_id: "agent-1", interrupted_subagents: 1, refresh_blocked: true, elapsed_seconds: 0.1 }],
    subagents_stopped: 2,
    pool_wired: false,
    lifecycle_wired: false,
    ledger_recorded: true,
    elapsed_seconds: 0.2,
    stopped_at: "2026-11-20T10:00:00Z",
    audit_trail_url: "/api/proof/audit-trail",
  };
}

describe("StopAllButton", () => {
  it("is always visible as a red button and does nothing on one tap", async () => {
    const user = userEvent.setup();
    render(<StopAllButton />);
    const button = screen.getByRole("button", { name: "Hentikan semua" });
    expect(button.className).toContain("button-danger");
    await user.click(button);
    expect(mockedStopAll).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Yakin hentikan semua?" })).toBeTruthy();
  });

  it("stops everything on the second tap and reports what stopped", async () => {
    const user = userEvent.setup();
    mockedStopAll.mockResolvedValue(stopReport());
    render(<StopAllButton />);
    await user.click(screen.getByRole("button", { name: "Hentikan semua" }));
    await user.click(screen.getByRole("button", { name: "Yakin hentikan semua?" }));
    expect(mockedStopAll).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Semua dihentikan: 1 agen, 2 subagen/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Lihat audit" })).toBeTruthy();
  });

  it("cancels the armed state without stopping", async () => {
    const user = userEvent.setup();
    render(<StopAllButton />);
    await user.click(screen.getByRole("button", { name: "Hentikan semua" }));
    await user.click(screen.getByRole("button", { name: "Batal" }));
    expect(mockedStopAll).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Hentikan semua" })).toBeTruthy();
  });

  it("shows the server error when the stop fails", async () => {
    const user = userEvent.setup();
    mockedStopAll.mockRejectedValue(new Error("revocation table missing"));
    render(<StopAllButton />);
    await user.click(screen.getByRole("button", { name: "Hentikan semua" }));
    await user.click(screen.getByRole("button", { name: "Yakin hentikan semua?" }));
    expect(await screen.findByText(/Gagal menghentikan semua/)).toBeTruthy();
  });
});
