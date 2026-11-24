import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DynamicIsland } from "./DynamicIsland";

describe("DynamicIsland (Nero, 23 Nov)", () => {
  it("idle: one line, agent name only, centered — no '>', no activity", () => {
    render(<DynamicIsland name="Puspa" working={false} activity={null} />);
    const island = screen.getByRole("status");
    expect(island.getAttribute("data-state")).toBe("idle");
    expect(island.className).not.toContain("dynamic-island--working");
    expect(island.textContent).toBe("Puspa");
    expect(screen.queryByText(/mengetik|mencari|bekerja/i)).toBeNull();
    expect(screen.queryByText(/online|siap membantu/i)).toBeNull();
  });

  it("working: expands and only then shows the live task", () => {
    render(
      <DynamicIsland name="Puspa" working activity="Mencari harga…" />,
    );
    const island = screen.getByRole("status");
    expect(island.getAttribute("data-state")).toBe("working");
    expect(island.className).toContain("dynamic-island--working");
    expect(screen.getByText("Mencari harga…")).toBeTruthy();
    expect(screen.getByText("Puspa")).toBeTruthy();
  });

  it("header is back + island + ... : no bell anywhere", () => {
    const { container } = render(
      <DynamicIsland name="Puspa" working={false} activity={null} />,
    );
    const wrap = container.querySelector(".chat-island-wrap");
    expect(wrap).not.toBeNull();
    const buttons = wrap?.querySelectorAll("button") ?? [];
    expect(buttons.length).toBe(2);
    expect(screen.getByLabelText("Kembali")).toBeTruthy();
    expect(screen.getByLabelText("Menu chat")).toBeTruthy();
    // Back and "..." are the same standard icon button (Nero,
    // 24 Nov): light, bordered — no dark circles in Chat.
    for (const button of Array.from(buttons)) {
      expect(button.className).toContain("icon-btn");
    }
    // No bell, no approval pip, no spacer.
    expect(screen.queryByLabelText("Antrean persetujuan")).toBeNull();
    expect(container.querySelector(".pip")).toBeNull();
    expect(wrap?.querySelector(".island-spacer")).toBeNull();
    expect(wrap?.querySelector(".bell")).toBeNull();
  });

  it("... opens a menu with the approval queue (no bell needed)", () => {
    render(<DynamicIsland name="Puspa" working={false} activity={null} />);
    expect(screen.queryByText("Antrean Persetujuan")).toBeNull();
    fireEvent.click(screen.getByLabelText("Menu chat"));
    expect(screen.getByText("Antrean Persetujuan")).toBeTruthy();
    expect(screen.getByText("Buat Desain di Kanvas")).toBeTruthy();
  });

  it("shows the agent photo (the same photo as the bubbles)", () => {
    render(<DynamicIsland name="Puspa" working={false} activity={null} />);
    // No photo set: the default Puspa logo, animated idle.
    const photo = screen.getByAltText("Foto agen Puspa");
    expect(photo.getAttribute("src")).toContain("/static/puspa-logo.png");
    expect(photo.closest("[data-state]")?.getAttribute("data-state")).toBe("idle");
  });

  it("shows the custom photo once one is set, and animates working", () => {
    render(
      <DynamicIsland
        name="Puspa"
        working
        activity="Mengetik…"
        avatar={{
          ok: true,
          has_photo: true,
          photo_url: "/api/agent/avatar/photo",
          updated_at: 123,
          mime_type: "image/webp",
          source: "builtin:sakura",
        }}
      />,
    );
    const photo = screen.getByAltText("Foto agen Puspa");
    expect(photo.getAttribute("src")).toContain("/api/agent/avatar/photo?v=123");
    expect(photo.closest("[data-state]")?.getAttribute("data-state")).toBe("working");
  });

  it("back and ... share one standard icon-button size (same class list)", () => {
    const { container } = render(
      <DynamicIsland name="Puspa" working={false} activity={null} />,
    );
    const wrap = container.querySelector(".chat-island-wrap");
    const buttons = Array.from(wrap?.querySelectorAll("button") ?? []);
    expect(buttons.length).toBe(2);
    // Same class list = same size by construction; the 40px rule
    // lives in .icon-btn (styles.css) and is checked in the
    // screenshot pass, not in jsdom (no layout engine here).
    expect(buttons[0].className).toBe(buttons[1].className);
  });
});
