import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { OperatorPanel } from "./OperatorPanel";
import type { OperatorStatus } from "../api/client";

function localStatus(overrides: Partial<OperatorStatus> = {}): OperatorStatus {
  return {
    mode: "local",
    operator_panels: true,
    pairing: {
      active_codes: 1,
      active_sessions: 1,
      sessions: [{ user_id: "user-1", device_label: "laptop", expires_at: 1730000000 }],
      locked_addresses: 0,
      locks: [],
    },
    gateway: {
      policy: {
        loaded: true,
        stale: false,
        dry_run: false,
        deny_rules: ['contains(tool.name, "pay")'],
        allow_rules: ["true"],
      },
      dry_run: false,
      decisions: 2,
      last_decision: {
        allowed: false,
        outcome: "deny",
        rule: 'contains(tool.name, "pay")',
        reason: "deny rule matched",
        forwarded: false,
      },
    },
    log: [
      { type: "user_message", created_at: 1730000000, summary: "user_message: halo Puspa" },
      { type: "gateway_decision", created_at: 1730000001, summary: "gateway_decision: deny" },
    ],
    ...overrides,
  };
}

describe("OperatorPanel", () => {
  it("renders pairing, gateway, and live log from live state", () => {
    render(<OperatorPanel status={localStatus()} loading={false} error={null} />);
    expect(screen.getByText("Panel Operator")).toBeTruthy();
    expect(screen.getByTestId("pairing-summary").textContent).toBe(
      "1 sesi aktif · 1 kode aktif · 0 alamat terkunci",
    );
    expect(screen.getByText("laptop")).toBeTruthy();
    expect(screen.getByTestId("gateway-summary").textContent).toBe(
      "1 aturan tolak · 1 aturan izinkan",
    );
    expect(screen.getByTestId("gateway-last-decision").textContent).toContain("Ditolak");
    expect(screen.getByText(/user_message: halo Puspa/)).toBeTruthy();
    expect(screen.getByText(/gateway_decision: deny/)).toBeTruthy();
  });

  it("renders empty live state honestly: no sessions, no decisions, no log lines", () => {
    const empty = localStatus({
      pairing: { active_codes: 0, active_sessions: 0, sessions: [], locked_addresses: 0, locks: [] },
      gateway: {
        policy: { loaded: true, stale: false, dry_run: false, deny_rules: [], allow_rules: ["true"] },
        dry_run: false,
        decisions: 0,
        last_decision: undefined,
      },
      log: [],
    });
    render(<OperatorPanel status={empty} loading={false} error={null} />);
    expect(screen.getByText("Tidak ada sesi pairing aktif.")).toBeTruthy();
    expect(screen.getByTestId("gateway-last-decision").textContent).toContain("Belum ada keputusan.");
    expect(screen.getByText("Belum ada kejadian.")).toBeTruthy();
  });

  it("shows a locked address as an alert", () => {
    const locked = localStatus({
      pairing: {
        active_codes: 0,
        active_sessions: 0,
        sessions: [],
        locked_addresses: 1,
        locks: [{ address: "10.0.0.9", failures: 5, locked_until: 1730000600 }],
      },
    });
    render(<OperatorPanel status={locked} loading={false} error={null} />);
    expect(screen.getByRole("alert").textContent).toContain("10.0.0.9");
  });

  it("shows a loading note while the first fetch is in flight", () => {
    render(<OperatorPanel status={null} loading={true} error={null} />);
    expect(screen.getByText("Memuat status operator…")).toBeTruthy();
    expect(screen.queryByText("Panel Operator")).toBeNull();
  });

  it("shows a fetch error instead of hiding the panels silently", () => {
    render(<OperatorPanel status={null} loading={false} error={"Request failed with status 401"} />);
    expect(screen.getByRole("alert").textContent).toContain("401");
  });

  it("renders nothing in hosted mode", () => {
    const { container } = render(
      <OperatorPanel
        status={localStatus({ mode: "hosted", operator_panels: false, pairing: undefined, gateway: undefined, log: [] })}
        loading={false}
        error={null}
      />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing when there is no status and no error", () => {
    const { container } = render(<OperatorPanel status={null} loading={false} error={null} />);
    expect(container.innerHTML).toBe("");
  });
});
