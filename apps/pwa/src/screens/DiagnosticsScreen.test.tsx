import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DiagnosticsScreen } from "./DiagnosticsScreen";

describe("DiagnosticsScreen", () => {
  it("offers the one-click redacted export download", () => {
    render(<DiagnosticsScreen />);
    const link = screen.getByRole("link", { name: /Unduh diagnostik/ });
    expect(link.getAttribute("href")).toBe("/api/diagnostics/export");
    expect(link.getAttribute("download")).toBe("puspa-diagnostics.json");
  });

  it("says what never leaves the server", () => {
    render(<DiagnosticsScreen />);
    expect(screen.getByText(/tidak pernah ikut/i)).toBeTruthy();
    expect(screen.getByText(/«redacted»/)).toBeTruthy();
  });
});
