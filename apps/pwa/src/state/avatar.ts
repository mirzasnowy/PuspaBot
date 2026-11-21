/**
 * Agent avatar state (V2-26): idle vs working, driven by the
 * REAL agent state, never by a timer.
 *
 * Working means the agent is actually doing something right
 * now: either a chat turn is in flight (the user sent a
 * message and the reply has not arrived yet) or the live
 * office projection (the same SSE stream the task rail reads)
 * lists at least one agent with status "working". The moment
 * both go quiet, the avatar is idle again. The Chat screen
 * passes both inputs in; this file only derives the state, so
 * the rule is unit-testable without a browser.
 */
import type { AvatarStatus, OfficeProjection } from "../api/client";
import { workingCount } from "./office";

export type AgentAvatarState = "idle" | "working";

/** Default avatar: the Puspa logo, served from the PWA static dir. */
export const DEFAULT_AVATAR_SRC = "/static/puspa-logo.png";

export function avatarStatus(
  sending: boolean,
  office: OfficeProjection | null,
): AgentAvatarState {
  if (sending) return "working";
  if (workingCount(office) > 0) return "working";
  return "idle";
}

/**
 * The <img> source for the avatar: the uploaded photo when one
 * is set (with ?v=<updated_at> so a changed photo is not served
 * from the browser cache), else the default Puspa logo.
 */
export function avatarPhotoSrc(status: AvatarStatus | null): string {
  if (status?.has_photo && status.photo_url) {
    const bust = status.updated_at ? `?v=${status.updated_at}` : "";
    return `${status.photo_url}${bust}`;
  }
  return DEFAULT_AVATAR_SRC;
}
