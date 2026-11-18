import { describe, expect, it } from "vitest";
import type { GatewayStatus, OperatorStatus, PairingStatus } from "../api/client";
import {
  formatClockTime,
  formatLogLine,
  gatewayPolicySummary,
  lastDecisionLabel,
  pairingSummary,
  showOperatorPanels,
} from "./operator";

function localStatus(overrides: Partial<OperatorStatus> = {}): OperatorStatus {
  return {
    mode: "local",
    operator_panels: true,
    pairing: {
      active_codes: 1,
      active_sessions: 2,
      sessions: [],
      locked_addresses: 0,
      locks: [],
    },
    gateway: {
      policy: { loaded: true, stale: false, dry_run: false, deny_rules: [], allow_rules: ["true"] },
      dry_run: false,
      decisions: 3,
      last_decision: { allowed: true, outcome: "allow", rule: "true", reason: "allow rule matched" },
    },
    log: [],
    ...overrides,
  };
}

describe("showOperatorPanels", () => {
  it("shows panels only in local mode with the server flag", () => {
    expect(showOperatorPanels(localStatus())).toBe(true);
    expect(showOperatorPanels(localStatus({ mode: "hosted", operator_panels: false }))).toBe(false);
    expect(showOperatorPanels(localStatus({ operator_panels: false }))).toBe(false);
    expect(showOperatorPanels(null)).toBe(false);
    expect(showOperatorPanels(undefined)).toBe(false);
  });
});

describe("pairingSummary", () => {
  it("counts live sessions, codes, and locks", () => {
    const pairing: PairingStatus = {
      active_codes: 1,
      active_sessions: 2,
      sessions: [],
      locked_addresses: 1,
      locks: [],
    };
    expect(pairingSummary(pairing)).toBe("2 sesi aktif · 1 kode aktif · 1 alamat terkunci");
  });

  it("names missing pairing state instead of inventing numbers", () => {
    expect(pairingSummary(null)).toBe("Status pairing tidak tersedia.");
    expect(pairingSummary(undefined)).toBe("Status pairing tidak tersedia.");
  });
});

describe("gatewayPolicySummary", () => {
  it("counts live deny and allow rules", () => {
    const gateway: GatewayStatus = {
      policy: {
        loaded: true,
        stale: false,
        dry_run: true,
        deny_rules: ['contains(tool.name, "pay")'],
        allow_rules: ["true"],
      },
      dry_run: true,
      decisions: 0,
    };
    expect(gatewayPolicySummary(gateway)).toBe(
      '1 aturan tolak · 1 aturan izinkan · mode dry-run',
    );
  });

  it("names a never-loaded policy as fail-closed", () => {
    expect(
      gatewayPolicySummary({
        policy: { loaded: false, stale: false, deny_rules: [], allow_rules: [] },
      }),
    ).toBe("Kebijakan belum dimuat: gateway menolak semua aksi.");
  });

  it("names a stale policy as fail-closed", () => {
    expect(
      gatewayPolicySummary({
        policy: { loaded: true, stale: true, deny_rules: [], allow_rules: [] },
      }),
    ).toBe("Kebijakan kedaluwarsa: gateway menolak semua aksi.");
  });

  it("names missing gateway state", () => {
    expect(gatewayPolicySummary(null)).toBe("Status gateway tidak tersedia.");
  });
});

describe("lastDecisionLabel", () => {
  it("labels allow and deny decisions with their rule", () => {
    expect(
      lastDecisionLabel({
        allowed: true,
        outcome: "allow",
        rule: "true",
        reason: "ok",
        forwarded: true,
      }),
    ).toBe("Diizinkan · aturan: true · diteruskan");
    expect(
      lastDecisionLabel({
        allowed: false,
        outcome: "deny",
        rule: 'contains(tool.name, "pay")',
        reason: "deny rule matched",
      }),
    ).toBe('Ditolak · aturan: contains(tool.name, "pay")');
  });

  it("says no decision yet when the gateway never decided", () => {
    expect(lastDecisionLabel(null)).toBe("Belum ada keputusan.");
    expect(lastDecisionLabel(undefined)).toBe("Belum ada keputusan.");
  });
});

describe("formatClockTime and formatLogLine", () => {
  it("formats epoch seconds as local HH:MM:SS", () => {
    const formatted = formatClockTime(3661);
    expect(formatted).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(formatClockTime(undefined)).toBe("—");
    expect(formatClockTime(null)).toBe("—");
  });

  it("prefixes a log line with its timestamp", () => {
    const line = formatLogLine({ type: "user_message", created_at: 3661, summary: "halo Puspa" });
    expect(line).toContain("halo Puspa");
    expect(line).toMatch(/^\d{2}:\d{2}:\d{2} /);
  });
});
