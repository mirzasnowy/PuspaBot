import { describe, expect, it, vi } from "vitest";
import {
  CONNECTOR_CHOICES,
  FIRST_TASK_TYPES,
  SPEAKING_STYLES,
  describeConnector,
  describeFirstTaskType,
  fetchDueHints,
  hintQueryString,
  isOnboardingDone,
  nextStep,
} from "./onboarding";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

describe("onboarding state", () => {
  it("offers exactly the three speaking styles from the persona module", () => {
    expect(SPEAKING_STYLES.map((style) => style.id)).toEqual([
      "santai",
      "rapi",
      "campur",
    ]);
    for (const style of SPEAKING_STYLES) {
      expect(style.examples.length).toBeGreaterThan(0);
    }
  });

  it("offers the connector choices step 3 accepts", () => {
    expect([...CONNECTOR_CHOICES]).toEqual(["nanti", "gmail", "drive", "calendar", "all"]);
    expect(describeConnector("nanti")).toMatch(/nanti/);
  });

  it("offers the two guided first-task types", () => {
    expect([...FIRST_TASK_TYPES]).toEqual(["compare_prices", "check_bill"]);
    expect(describeFirstTaskType("compare_prices")).toMatch(/Bandingkan harga/);
    expect(describeFirstTaskType("check_bill")).toMatch(/tagihan/);
  });

  it("nextStep walks 1..4 and is null when done", () => {
    expect(nextStep(null)).toBe(1);
    expect(nextStep({ started: false, current_step: 0 })).toBe(1);
    expect(nextStep({ started: true, current_step: 0 })).toBe(1);
    expect(nextStep({ started: true, current_step: 2 })).toBe(3);
    expect(nextStep({ started: true, current_step: 4 })).toBeNull();
    expect(isOnboardingDone({ started: true, current_step: 4 })).toBe(true);
    expect(isOnboardingDone({ started: true, current_step: 3 })).toBe(false);
  });

  it("hintQueryString only carries the branch facts given", () => {
    expect(hintQueryString({})).toBe("");
    expect(hintQueryString({ screen: "modules", first_visit: true })).toBe(
      "?screen=modules&first_visit=true",
    );
    expect(hintQueryString({ task_cost_idr: 2500 })).toBe("?task_cost_idr=2500");
  });

  it("fetchDueHints asks the server for the reported branch", async () => {
    const fetchMock = mockFetch({ hints: [] });
    await fetchDueHints({ screen: "modules", first_visit: true }, fetchMock);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/onboarding/hints?screen=modules&first_visit=true",
      { credentials: "same-origin" },
    );
  });
});
