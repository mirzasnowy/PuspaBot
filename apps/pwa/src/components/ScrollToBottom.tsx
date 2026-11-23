import { useEffect, useState } from "react";
import { Icon } from "./icons";

/**
 * Scroll-to-bottom button (Nero, 24 Nov): a floating down
 * arrow that appears ONLY while the user has scrolled up in
 * the chat. The moment the chat is back at the bottom, the
 * button disappears — it never sits on top of the composer
 * while the user is already reading the latest message.
 *
 * The app scrolls inside `.view` (the shell's scroll
 * container), not the window, so this listens there.
 */
export function ScrollToBottomButton() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const scroller = document.querySelector(".view") as HTMLElement | null;
    if (!scroller) return;
    const update = () => {
      const distanceFromBottom =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      setVisible(distanceFromBottom > 80);
    };
    update();
    scroller.addEventListener("scroll", update, { passive: true });
    return () => scroller.removeEventListener("scroll", update);
  }, []);

  if (!visible) return null;
  return (
    <button
      type="button"
      className="icon-btn scroll-bottom-btn"
      aria-label="Scroll ke bawah"
      onClick={() => {
        const scroller = document.querySelector(".view") as HTMLElement | null;
        scroller?.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
      }}
    >
      <Icon name="down" size={20} strokeWidth={2.4} />
    </button>
  );
}
