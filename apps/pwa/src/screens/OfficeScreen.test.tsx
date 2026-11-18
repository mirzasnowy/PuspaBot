import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { OfficeScreen } from "./OfficeScreen";
import type { OperatorStatus } from "../api/client";

class FakeEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  close = vi.fn();
  constructor(_url: string) {}
}

function localStatus(): OperatorStatus {
  return {
    mode: "local",
    operator_panels: true,
    pairing: {
      active_codes: 0,
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
        deny_rules: [],
        allow_rules: ["true"],
      },
      dry_run: false,
      decisions: 1,
      last_decision: { allowed: true, outcome: "allow", rule: "true", reason: "ok" },
    },
    log: [{ type: "user_message", created_at: 1730000000, summary: "user_message: halo Puspa" }],
  };
}

function stubFetch(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OfficeScreen operator panels (G31)", () => {
  it("shows the operator panels in local mode, fed by the status endpoint", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    stubFetch(localStatus());
    render(<OfficeScreen bootstrap={{}} />);
    expect(await screen.findByText("Panel Operator")).toBeTruthy();
    expect(screen.getByTestId("pairing-summary").textContent).toBe(
      "1 sesi aktif · 0 kode aktif · 0 alamat terkunci",
    );
    expect(screen.getByText("laptop")).toBeTruthy();
    expect(screen.getByText(/user_message: halo Puspa/)).toBeTruthy();
  });

  it("hides the operator panels in hosted mode", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    stubFetch({
      mode: "hosted",
      operator_panels: false,
      pairing: null,
      gateway: null,
      log: [],
    });
    render(<OfficeScreen bootstrap={{}} />);
    // The office screen itself still renders while the panels stay hidden.
    expect(await screen.findByText("Kantor")).toBeTruthy();
    expect(screen.queryByText("Panel Operator")).toBeNull();
  });

  it("shows a fetch error instead of silently hiding the panels", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    stubFetch({ error: "belum masuk" }, 401);
    render(<OfficeScreen bootstrap={{}} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
