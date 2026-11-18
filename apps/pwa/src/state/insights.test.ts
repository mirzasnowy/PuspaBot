import { describe, expect, it, vi } from "vitest";
import { emptyInsights, fetchInsights, hasUsage, validateDays } from "./insights";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

describe("insights state", () => {
  it("empty report is honest zeros with no rate label", () => {
    const report = emptyInsights(7);
    expect(report.turns).toBe(0);
    expect(report.tokens.total).toBe(0);
    expect(report.cost.label).toBe("Rp0");
    expect(report.cost.per_1k_tokens_label).toBeNull();
    expect(hasUsage(report)).toBe(false);
  });

  it("hasUsage is true once a turn is recorded", () => {
    const report = emptyInsights(7);
    expect(hasUsage({ ...report, turns: 1 })).toBe(true);
  });

  it("rejects window lengths outside 1..90 days", () => {
    expect(validateDays(7)).toBeNull();
    expect(validateDays(90)).toBeNull();
    expect(validateDays(0)).not.toBeNull();
    expect(validateDays(91)).not.toBeNull();
    expect(validateDays(7.5)).not.toBeNull();
  });

  it("fetchInsights asks the server for the chosen window", async () => {
    const fetchMock = mockFetch(emptyInsights(30));
    await fetchInsights(30, fetchMock);
    expect(fetchMock).toHaveBeenCalledWith("/api/insights?days=30", {
      credentials: "same-origin",
    });
  });

  it("fetchInsights refuses an invalid window without calling the server", async () => {
    const fetchMock = mockFetch(emptyInsights(7));
    await expect(fetchInsights(0, fetchMock)).rejects.toThrow(/1 sampai 90/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
