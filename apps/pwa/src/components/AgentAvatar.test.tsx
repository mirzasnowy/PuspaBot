import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentAvatar } from "./AgentAvatar";

describe("AgentAvatar", () => {
  it("shows the idle state and the default logo while waiting", () => {
    const { container } = render(
      <AgentAvatar state="idle" photoSrc="/static/puspa-logo.png" />,
    );
    const img = screen.getByAltText("Foto agen Puspa") as HTMLImageElement;
    expect(img.src).toContain("/static/puspa-logo.png");
    expect(container.querySelector(".agent-avatar")?.getAttribute("data-state")).toBe("idle");
    expect(screen.getByText("Puspa menunggu")).toBeTruthy();
  });

  it("shows the working state and the uploaded photo while the agent works", () => {
    const { container } = render(
      <AgentAvatar state="working" photoSrc="/api/agent/avatar/photo?v=123" />,
    );
    const img = screen.getByAltText("Foto agen Puspa") as HTMLImageElement;
    expect(img.src).toContain("/api/agent/avatar/photo?v=123");
    expect(container.querySelector(".agent-avatar")?.getAttribute("data-state")).toBe("working");
    expect(screen.getByText("Puspa sedang bekerja")).toBeTruthy();
  });

  it("switches idle to working to idle when the state prop changes", () => {
    const { container, rerender } = render(
      <AgentAvatar state="idle" photoSrc="/static/puspa-logo.png" />,
    );
    const root = () => container.querySelector(".agent-avatar");
    expect(root()?.getAttribute("data-state")).toBe("idle");
    rerender(<AgentAvatar state="working" photoSrc="/static/puspa-logo.png" />);
    expect(root()?.getAttribute("data-state")).toBe("working");
    expect(screen.getByText("Puspa sedang bekerja")).toBeTruthy();
    rerender(<AgentAvatar state="idle" photoSrc="/static/puspa-logo.png" />);
    expect(root()?.getAttribute("data-state")).toBe("idle");
    expect(screen.getByText("Puspa menunggu")).toBeTruthy();
  });

  it("pauses and resumes the animation from the pause button", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AgentAvatar state="idle" photoSrc="/static/puspa-logo.png" />,
    );
    const root = () => container.querySelector(".agent-avatar");
    expect(root()?.classList.contains("agent-avatar--paused")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Jeda animasi avatar" }));
    expect(root()?.classList.contains("agent-avatar--paused")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Putar animasi avatar" }));
    expect(root()?.classList.contains("agent-avatar--paused")).toBe(false);
  });
});
