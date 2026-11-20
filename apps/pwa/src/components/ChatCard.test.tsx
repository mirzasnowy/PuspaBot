import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatCard } from "./ChatCard";
import type { ChatCard as ChatCardPayload } from "../api/client";

const draftCard: ChatCardPayload = {
  kind: "draft",
  title: "Kabar barang",
  body: "Halo Sari, barang sudah sampai.",
  channel: "email",
  recipient: "sari@contoh.id",
  subject: "Barang sampai",
  mailto: "mailto:sari@contoh.id?subject=Barang%20sampai&body=Halo%20Sari",
};

const optionsCard: ChatCardPayload = {
  kind: "options",
  question: "Mau lanjut yang mana?",
  options: [
    { id: "a", label: "Kirim email", detail: "ke Sari" },
    { id: "b", label: "Tunggu besok" },
  ],
};

const chartCard: ChatCardPayload = {
  kind: "chart",
  title: "Cost per day — last 7 days",
  metric: "cost_idr",
  points: [
    { label: "2026-11-15", value: 500 },
    { label: "2026-11-16", value: 0 },
    { label: "2026-11-17", value: 1200 },
  ],
};

const mapCard: ChatCardPayload = {
  kind: "map",
  title: "Rute ke kantor",
  center: { lat: -6.2, lon: 106.8 },
  zoom: 15,
  markers: [{ lat: -6.2, lon: 106.8, label: "Kantor" }],
  route: [
    { lat: -6.2, lon: 106.8 },
    { lat: -6.21, lon: 106.81 },
  ],
};

describe("ChatCard draft", () => {
  it("shows the draft body with copy and Mail actions", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<ChatCard card={draftCard} />);
    expect(screen.getByText("Halo Sari, barang sudah sampai.")).toBeTruthy();
    expect(screen.getByText("Ke: sari@contoh.id")).toBeTruthy();
    const mailLink = screen.getByRole("link", { name: "Buka di Mail" });
    expect(mailLink.getAttribute("href")).toBe(draftCard.mailto);
    // fireEvent, not userEvent: userEvent.setup() replaces
    // navigator.clipboard with its own stub, so the mock above
    // would never be called.
    fireEvent.click(screen.getByRole("button", { name: "Salin draft" }));
    expect(writeText).toHaveBeenCalledWith("Halo Sari, barang sudah sampai.");
    expect(await screen.findByRole("button", { name: "Disalin" })).toBeTruthy();
  });

  it("a message draft has no Mail link", () => {
    render(<ChatCard card={{ ...draftCard, channel: "message", mailto: undefined }} />);
    expect(screen.queryByRole("link", { name: "Buka di Mail" })).toBeNull();
  });
});

describe("ChatCard options", () => {
  it("sends the picked option label and locks the card", async () => {
    const user = userEvent.setup();
    const onPickOption = vi.fn();
    render(<ChatCard card={optionsCard} onPickOption={onPickOption} />);
    expect(screen.getByText("Mau lanjut yang mana?")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Kirim email/ }));
    expect(onPickOption).toHaveBeenCalledTimes(1);
    expect(onPickOption).toHaveBeenCalledWith(optionsCard.options[0]);
    expect(screen.getByText("Dipilih: Kirim email")).toBeTruthy();
    // A second tap does nothing: the choice is already sent.
    await user.click(screen.getByRole("button", { name: /Tunggu besok/ }));
    expect(onPickOption).toHaveBeenCalledTimes(1);
  });
});

describe("ChatCard chart", () => {
  it("draws one bar per point, tallest for the max value", () => {
    render(<ChatCard card={chartCard} />);
    expect(screen.getByText("Cost per day — last 7 days")).toBeTruthy();
    const bars = screen.getAllByTestId("chart-bar");
    expect(bars).toHaveLength(3);
    const heights = bars.map((bar) => Number(bar.getAttribute("height")));
    expect(heights[2]).toBeGreaterThan(heights[0]);
    expect(heights[1]).toBe(0);
    expect(screen.getByText(/Tertinggi: Rp1.200/)).toBeTruthy();
  });
});

describe("ChatCard map", () => {
  it("shows OSM tiles with a marker and a route", () => {
    render(<ChatCard card={mapCard} />);
    const tiles = screen.getAllByTestId("map-tile");
    expect(tiles.length).toBeGreaterThanOrEqual(4);
    expect(tiles.every((tile) => tile.getAttribute("src")?.startsWith("https://tile.openstreetmap.org/15/"))).toBe(true);
    const marker = screen.getByTestId("map-marker");
    expect(marker.textContent).toContain("Kantor");
    const route = screen.getByTestId("map-route");
    const polyline = route.querySelector("polyline");
    expect(polyline).not.toBeNull();
    expect(polyline?.getAttribute("points")?.split(" ")).toHaveLength(2);
    expect(screen.getByText(/Peta: OpenStreetMap/)).toBeTruthy();
  });
});
