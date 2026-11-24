import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ScrollToBottomButton } from "./ScrollToBottom";

/** A fake `.view` scroller with controllable geometry (jsdom has no layout). */
function mountScroller(scrollHeight: number, clientHeight: number, scrollTop: number) {
  const scroller = document.createElement("div");
  scroller.className = "view";
  document.body.appendChild(scroller);
  Object.defineProperty(scroller, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { value: clientHeight, configurable: true });
  Object.defineProperty(scroller, "scrollTop", {
    value: scrollTop,
    writable: true,
    configurable: true,
  });
  scroller.scrollTo = vi.fn();
  return scroller;
}

describe("ScrollToBottomButton (Nero, 24 Nov)", () => {
  it("is hidden at the bottom and appears once the user scrolls up", () => {
    const scroller = mountScroller(2000, 800, 0);
    try {
      render(<ScrollToBottomButton />);
      expect(screen.getByRole("button", { name: "Scroll ke bawah" })).toBeTruthy();
      // Scroll to the bottom: the button disappears.
      Object.defineProperty(scroller, "scrollTop", { value: 1200, configurable: true });
      fireEvent.scroll(scroller);
      expect(screen.queryByRole("button", { name: "Scroll ke bawah" })).toBeNull();
      // Scroll back up a little: it comes back.
      Object.defineProperty(scroller, "scrollTop", { value: 500, configurable: true });
      fireEvent.scroll(scroller);
      expect(screen.getByRole("button", { name: "Scroll ke bawah" })).toBeTruthy();
    } finally {
      scroller.remove();
    }
  });

  it("stays hidden when the chat fits without scrolling", () => {
    const scroller = mountScroller(600, 800, 0);
    try {
      render(<ScrollToBottomButton />);
      expect(screen.queryByRole("button", { name: "Scroll ke bawah" })).toBeNull();
    } finally {
      scroller.remove();
    }
  });

  it("clicking it scrolls the chat to the bottom", () => {
    const scroller = mountScroller(2000, 800, 0);
    try {
      render(<ScrollToBottomButton />);
      fireEvent.click(screen.getByRole("button", { name: "Scroll ke bawah" }));
      expect(scroller.scrollTo).toHaveBeenCalledWith({
        top: 2000,
        behavior: "smooth",
      });
    } finally {
      scroller.remove();
    }
  });
});
