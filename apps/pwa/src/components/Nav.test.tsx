import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Nav } from "./Nav";

describe("Nav shell (Space port)", () => {
  it("shows the PuspaBot brand and exactly four tabs, no Beranda", () => {
    render(<Nav current="percakapan" botName="Puspa" />);
    expect(screen.getByText("PuspaBot")).toBeTruthy();
    for (const label of ["Chat", "Tugas", "Memori", "Saya"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText("Beranda")).toBeNull();
  });

  it("has no bell and no ... menu anywhere (Nero, 23 Nov)", () => {
    // Notifications reach the user through chat, never a header
    // bell — so no screen may render one.
    for (const current of [
      "percakapan",
      "chat",
      "ruangchat",
      "tugas",
      "memori",
      "saya",
    ] as const) {
      const { unmount } = render(<Nav current={current} />);
      expect(screen.queryByLabelText("Antrean persetujuan")).toBeNull();
      expect(screen.queryByLabelText("Menu aplikasi")).toBeNull();
      expect(screen.queryByLabelText("Menu chat")).toBeNull();
      unmount();
    }
  });

  it("renders children without the shell on onboarding", () => {
    render(
      <Nav current="onboarding">
        <p>Welcome step</p>
      </Nav>,
    );
    expect(screen.getByText("Welcome step")).toBeTruthy();
    expect(screen.queryByText("PuspaBot")).toBeNull();
  });

  it("marks the active tab for a sub-screen", () => {
    render(<Nav current="keuangan" />);
    const sayaLinks = screen
      .getAllByText("Saya")
      .map((el) => el.closest("a"));
    expect(sayaLinks.some((a) => a?.getAttribute("aria-current") === "page")).toBe(
      true,
    );
  });
});
