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
import { useState } from "react";
import type { AgentAvatarState } from "../state/avatar";

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
