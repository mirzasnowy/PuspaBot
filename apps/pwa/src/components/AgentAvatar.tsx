/**
 * Agent avatar (V2-26): the agent's face in Chat, a living
 * character, not a static icon.
 *
 * The photo comes from `photoSrc` (the uploaded photo, or the
 * default Puspa logo when none is set (see
 * `state/avatar.ts`). The animation state comes from the `state`
 * prop, which the Chat screen derives from the real agent state
 * (a turn in flight, or a working agent in the live office
 * projection): idle while waiting (calm breathing and a blink)
 * and visibly active while the agent thinks or runs a task.
 * Both animations are transform/opacity only, run only under
 * `prefers-reduced-motion: no-preference`, and the pause button
 * next to the avatar stops them on demand (a persistent
 * animation needs a visible pause control; the reduced-motion
 * setting alone is not the control).
 */
import { useEffect, useState } from "react";
import type { AgentAvatarState } from "../state/avatar";
import { fetchAgentAvatar, type AvatarStatusWithSource } from "../api/client";

/**
 * The agent's photo alone (compact): the same <img> the full
 * avatar uses, with the same idle/working animation, but no
 * status text and no pause button — for the chat island, the
 * chat bubbles, and the conversation list, where the avatar
 * must be the SAME photo everywhere (Nero, 24 Nov). The
 * `size` is the photo's diameter in px.
 */
export function AgentAvatarPhoto({
  state,
  photoSrc,
  size = 56,
  alt = "Foto agen Puspa",
}: {
  state: AgentAvatarState;
  photoSrc: string;
  size?: number;
  alt?: string;
}) {
  return (
    <span
      className={`agent-avatar--${state}`}
      data-state={state}
      style={{ display: "inline-block", lineHeight: 0 }}
    >
      <span
        className="agent-avatar-frame"
        style={{ width: size, height: size }}
      >
        <img
          className="agent-avatar-photo"
          src={photoSrc}
          alt={alt}
          width={size}
          height={size}
          style={{ width: size, height: size }}
        />
      </span>
    </span>
  );
}

/** Load the avatar photo status once (shared by chat, island, and lists). */
export function useAvatarStatus(): AvatarStatusWithSource | null {
  const [status, setStatus] = useState<AvatarStatusWithSource | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchAgentAvatar()
      .then((loaded) => {
        if (!cancelled) setStatus(loaded);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return status;
}

export function AgentAvatar({
  state,
  photoSrc,
}: {
  state: AgentAvatarState;
  photoSrc: string;
}) {
  const [paused, setPaused] = useState(false);
  const statusText = state === "working" ? "Puspa sedang bekerja" : "Puspa menunggu";
  return (
    <div
      className={`agent-avatar agent-avatar--${state}${paused ? " agent-avatar--paused" : ""}`}
      data-state={state}
    >
      <span className="agent-avatar-frame">
        <img
          className="agent-avatar-photo"
          src={photoSrc}
          alt="Foto agen Puspa"
          width={56}
          height={56}
        />
      </span>
      <span className="agent-avatar-status" role="status">
        {statusText}
      </span>
      <button
        type="button"
        className="button-secondary agent-avatar-pause"
        onClick={() => setPaused((prev) => !prev)}
        aria-label={paused ? "Putar animasi avatar" : "Jeda animasi avatar"}
      >
        {paused ? "Putar" : "Jeda"}
      </button>
    </div>
  );
}
