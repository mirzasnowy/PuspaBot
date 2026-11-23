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
import type { AvatarStatusWithSource, OfficeProjection } from "../api/client";
import { workingCount } from "./office";

export type AgentAvatarState = "idle" | "working";

/** Default avatar: the Puspa logo, served from the PWA static dir. */
export const DEFAULT_AVATAR_SRC = "/static/puspa-logo.png";

/**
 * Built-in flower avatars (Nero, 24 Nov — flowers only, no
 * animals): the user picks one in onboarding or the agent edit
 * screen; the server copies the file into the avatar dir, so
 * the photo pipeline serves it like an upload. The default
 * (Puspa flower logo) is not in this list: it is what shows
 * while no photo is set.
 */
export const BUILTIN_AVATARS: { id: string; label: string; labelKey: string; src: string }[] = [
  { id: "tulip", label: "Tulip", labelKey: "ui.avatar.tulip", src: "/static/avatars/agent-flower-tulip-nobg.png" },
  {
    id: "sunflower",
    label: "Bunga matahari",
    labelKey: "ui.avatar.sunflower",
    src: "/static/avatars/agent-flower-sunflower-nobg.png",
  },
  { id: "rose", label: "Mawar", labelKey: "ui.avatar.rose", src: "/static/avatars/agent-flower-rose-nobg.png" },
  { id: "sakura", label: "Sakura", labelKey: "ui.avatar.sakura", src: "/static/avatars/agent-flower-sakura-nobg.png" },
];

/** The image source for a custom agent's picked icon, or null for the default logo. */
export function builtinAvatarSrc(id: string | null | undefined): string | null {
  if (!id || id === "default") return null;
  return BUILTIN_AVATARS.find((option) => option.id === id)?.src ?? null;
}

/** The builtin id active right now, or null for default/upload. */
export function activeBuiltinId(status: AvatarStatusWithSource | null): string | null {
  const source = status?.source ?? "";
  return source.startsWith("builtin:") ? source.slice("builtin:".length) : null;
}

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
export function avatarPhotoSrc(status: AvatarStatusWithSource | null): string {
  if (status?.has_photo && status.photo_url) {
    const bust = status.updated_at ? `?v=${status.updated_at}` : "";
    return `${status.photo_url}${bust}`;
  }
  return DEFAULT_AVATAR_SRC;
}
