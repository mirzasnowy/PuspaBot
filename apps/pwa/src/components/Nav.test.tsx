import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Nav } from "./Nav";

describe("Nav brand name (V2-27)", () => {
  it("shows the bot's own name instead of a hardcoded one", () => {
    render(<Nav current="chat" botName="Bimo" />);
    expect(screen.getByText("Bimo")).toBeTruthy();
    expect(screen.queryByText("Puspa")).toBeNull();
  });

  it("falls back to Puspa when no bot name is given", () => {
    render(<Nav current="chat" />);
    expect(screen.getByText("Puspa")).toBeTruthy();
  });
});
