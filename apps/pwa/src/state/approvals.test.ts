import { describe, expect, it } from "vitest";
import {
  applyDecision,
  decidedApprovals,
  initialApprovalsState,
  isDecisionVoid,
  markDeciding,
  pendingApprovals,
  shortHash,
} from "./approvals";
import type { ApprovalRequest } from "../api/client";

function item(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    request_id: "req-1",
    kind: "spend",
    status: "pending",
    summary: "Pay Rp50.000 to PLN",
    amount_rp: 50000,
    target: "PLN",
    content_hash: "abc123hashvalue",
    ...overrides,
  };
}

describe("approvals store", () => {
  it("splits pending from decided", () => {
    const state = initialApprovalsState([
      item(),
      item({ request_id: "req-2", status: "approved" }),
    ]);
    expect(pendingApprovals(state)).toHaveLength(1);
    expect(decidedApprovals(state)).toHaveLength(1);
  });

  it("applies a one-tap decision and locks the card", () => {
    const state = initialApprovalsState([item()]);
    const deciding = markDeciding(state, "req-1");
    expect(deciding.deciding["req-1"]).toBe(true);
    const decided = applyDecision(deciding, "req-1", "approved");
    expect(decided.items[0].status).toBe("approved");
    expect(decided.deciding["req-1"]).toBe(false);
  });

  it("never re-decides an already decided card", () => {
    const state = initialApprovalsState([item({ status: "rejected" })]);
    const next = applyDecision(state, "req-1", "approved");
    expect(next.items[0].status).toBe("rejected");
  });

  it("voids a decision when the content hash changes", () => {
    const decided = item({ status: "approved" });
    expect(isDecisionVoid(decided, "abc123hashvalue")).toBe(false);
    expect(isDecisionVoid(decided, "differenthash")).toBe(true);
    expect(isDecisionVoid(decided, undefined)).toBe(false);
  });

  it("shortens long hashes for display", () => {
    expect(shortHash("abc123hashvalue")).toBe("abc123hashva…");
    expect(shortHash("short")).toBe("short");
    expect(shortHash(undefined)).toBe("—");
  });
});
