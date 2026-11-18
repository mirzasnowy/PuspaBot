import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { HintBanner } from "./HintBanner";
import type { OnboardingHint } from "../state/onboarding";

const VISITED_KEY = "puspa:visited-screens";

function modulesHint(): OnboardingHint {
  return {
    flag: "first_modules_visit",
    trigger: "first_modules_visit",
    title: "Modules add abilities",
    body: "A module adds one ability to Puspa.",
    screen: "modules",
    action_label: "Open Modules",
  };
}

function mockFetchForHint(hint: OnboardingHint | null) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.startsWith("/api/onboarding/hints")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ hints: hint ? [hint] : [] }),
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ user_id: "user-1", flag: "first_modules_visit", seen: true }),
    });
  });
}

describe("HintBanner", () => {
  it("shows the first due hint for the current screen and records the visit", async () => {
    window.localStorage.clear();
    const fetchMock = mockFetchForHint(modulesHint());
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<HintBanner screen="modules" bootstrap={{}} />);
      expect(await screen.findByText("Modules add abilities")).toBeTruthy();
      expect(screen.getByText("A module adds one ability to Puspa.")).toBeTruthy();
      // The hint points at the screen already open, so no action link.
      expect(screen.queryByRole("link")).toBeNull();
      const query = String(fetchMock.mock.calls[0][0]);
      expect(query).toContain("screen=modules");
      expect(query).toContain("first_visit=true");
      expect(JSON.parse(window.localStorage.getItem(VISITED_KEY) ?? "[]")).toEqual([
        "modules",
      ]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows nothing when no hint is due, and reports a repeat visit", async () => {
    window.localStorage.setItem(VISITED_KEY, JSON.stringify(["chat"]));
    const fetchMock = mockFetchForHint(null);
    vi.stubGlobal("fetch", fetchMock);
    try {
      const { container } = render(<HintBanner screen="chat" bootstrap={{}} />);
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalled();
      });
      expect(container.innerHTML).toBe("");
      expect(String(fetchMock.mock.calls[0][0])).toContain("first_visit=false");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("dismiss posts the hint as seen and hides the banner", async () => {
    window.localStorage.clear();
    const fetchMock = mockFetchForHint(modulesHint());
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<HintBanner screen="tasks" bootstrap={{}} />);
      expect(await screen.findByText("Modules add abilities")).toBeTruthy();
      // From another screen the hint offers its action link.
      expect(screen.getByRole("link", { name: "Open Modules" }).getAttribute("href")).toBe(
        "/modules",
      );
      fireEvent.click(screen.getByRole("button", { name: /Tutup petunjuk/ }));
      expect(screen.queryByText("Modules add abilities")).toBeNull();
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/onboarding/hints/first_modules_visit/seen",
        expect.objectContaining({ method: "POST" }),
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows nothing when the hints fetch fails", async () => {
    window.localStorage.clear();
    const fetchMock = vi.fn().mockRejectedValue(new Error("jaringan putus"));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const { container } = render(<HintBanner screen="chat" bootstrap={{}} />);
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalled();
      });
      expect(container.innerHTML).toBe("");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("passes billing context through for the insights hints", async () => {
    window.localStorage.clear();
    const fetchMock = mockFetchForHint(null);
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<HintBanner screen="insights" billingRows={3} taskCostIdr={2500} />);
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalled();
      });
      const query = String(fetchMock.mock.calls[0][0]);
      expect(query).toContain("billing_rows=3");
      expect(query).toContain("task_cost_idr=2500");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
