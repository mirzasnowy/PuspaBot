/**
 * Group chat names (V2-27): the client-side half of the shared
 * name resolver. The server is authoritative (see
 * `puspa/rooms/names.py`): the room detail carries each
 * member's `display_name` and each bot's name, and room memory
 * rows carry a resolved `author_display_name` plus
 * `rendered_content` with agreed-by ids already replaced. These
 * helpers only shape what the screen shows when a field is
 * absent, so a missing name can never leak a raw UUID onto the
 * screen.
 *
 * Rules, mirrored from the backend:
 * - A member with a display name is shown by that name.
 * - A member without one is "Anggota N" (N is their 1-based
 *   position in the member list). A user id that is already a
 *   readable handle rather than a UUID (legacy fixtures) is
 *   shown as-is; only UUID-shaped ids are replaced.
 * - A bot with no name is "Puspa"; a renamed bot is shown by
 *   its own name from the bootstrap agents list.
 * - Stored text that embeds ids (a decision's "Agreed by:"
 *   line) is rendered through `renderDecisionText`.
 */
import type { Agent, RoomMember } from "../api/client";

export const UUID_RE =
  /\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b/g;

/** True when the value looks like a UUID (never shown raw). */
export function isUuid(value: string | null | undefined): boolean {
  if (!value) return false;
  return (
    /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
      value,
    )
  );
}

/** Placeholder for the member at 0-based `index`: "Anggota 1", "Anggota 2", ... */
export function fallbackMemberName(index: number): string {
  return `Anggota ${index + 1}`;
}

/**
 * The name to show for one room member: their display name when
 * set, else the "Anggota N" placeholder for a UUID id, else the
 * id itself when it is already a readable handle.
 */
export function displayNameForMember(
  member: Pick<RoomMember, "user_id" | "display_name">,
  index: number,
): string {
  const display = member.display_name?.trim();
  if (display) return display;
  if (isUuid(member.user_id)) return fallbackMemberName(index);
  return member.user_id;
}

/** Display name per user id for a whole member list. */
export function memberDisplayNames(
  members: Pick<RoomMember, "user_id" | "display_name">[] | null | undefined,
): Record<string, string> {
  const names: Record<string, string> = {};
  for (const [index, member] of (members ?? []).entries()) {
    names[member.user_id] = displayNameForMember(member, index);
  }
  return names;
}

/**
 * Replace every UUID-shaped token in stored text (a decision's
 * "Agreed by:" line) with its resolved name, or "Anggota" when
 * the id has no name. Text with no ids passes through unchanged.
 */
export function renderDecisionText(
  content: string,
  names: Record<string, string>,
): string {
  return content.replace(UUID_RE, (token) => names[token] ?? "Anggota");
}

/** The bot's own name from the bootstrap agents list. */
export function requiredAgentName(agents: Agent[] | null | undefined): string {
  const required = (agents ?? []).find((a) => a.required);
  if (required?.name?.trim()) return required.name.trim();
  const first = (agents ?? [])[0];
  if (first?.name?.trim()) return first.name.trim();
  return "Puspa";
}

/**
 * The name to show for one chat message author. The user's own
 * messages are "users" on the wire and render as their display
 * name, or "Saya" when it is unknown; the bot's messages carry
 * "puspa" (or a UUID) and render as the bot's own name.
 */
export function resolveSenderName(
  sender: string,
  opts: { botName?: string; userDisplayName?: string },
): string {
  const botName = opts.botName?.trim() || "Puspa";
  if (sender === "users") return opts.userDisplayName?.trim() || "Saya";
  if (sender.toLowerCase() === "puspa" || isUuid(sender)) return botName;
  return sender;
}
