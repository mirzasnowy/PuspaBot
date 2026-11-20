/**
 * Rooms screen store: pure helpers over the room API shapes in
 * `../api/client`. The server is authoritative for every wall
 * (membership, private rows, guest read-only); these helpers only
 * shape what the screen shows, so a wrong client value can never
 * leak a row the server withheld.
 */
import type { RoomMemory, RoomSummary, SseEvent } from "../api/client";

export const ROLE_LABELS: Record<string, string> = {
  owner: "Pemilik",
  member: "Anggota",
  guest: "Tamu",
};

export function roleLabel(role: string | null | undefined): string {
  if (!role) return "—";
  return ROLE_LABELS[role] ?? role;
}

/** Guests read the room; only owners and members write to it. */
export function canWriteInRoom(role: string | null | undefined): boolean {
  return role === "owner" || role === "member";
}

/** Rooms sorted by name, for the list. */
export function sortRooms(rooms: RoomSummary[] | null | undefined): RoomSummary[] {
  if (!rooms) return [];
  return [...rooms].sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));
}

/**
 * Room-memory rows the viewer may see: every room-visible row
 * plus their own private rows. Mirrors the server SQL filter
 * (`list_room_memory`); the server still enforces it.
 */
export function visibleMemories(
  memories: RoomMemory[] | null | undefined,
  viewerUserId: string,
): RoomMemory[] {
  if (!memories) return [];
  return memories.filter(
    (m) => m.visibility === "room" || m.author_user_id === viewerUserId,
  );
}

/**
 * Room-activity SSE frames for one room, newest last. Other
 * rooms' frames and office/heartbeat frames are ignored, so the
 * screen only refreshes on its own room's activity.
 */
export function roomActivityForRoom(
  events: SseEvent[] | null | undefined,
  roomId: string,
): NonNullable<SseEvent["room_activity"]>[] {
  if (!events) return [];
  const out: NonNullable<SseEvent["room_activity"]>[] = [];
  for (const event of events) {
    if (event.type !== "room_activity" || !event.room_activity) continue;
    if (event.room_activity.room_id === roomId) out.push(event.room_activity);
  }
  return out;
}
