import { describe, expect, it } from "vitest";
import type { SseEvent } from "../api/client";
import { canWriteInRoom, roleLabel, roomActivityForRoom, sortRooms, visibleMemories } from "./rooms";

describe("rooms store", () => {
  it("labels roles and gates writes to owners and members", () => {
    expect(roleLabel("owner")).toBe("Pemilik");
    expect(roleLabel("member")).toBe("Anggota");
    expect(roleLabel("guest")).toBe("Tamu");
    expect(roleLabel(null)).toBe("—");
    expect(canWriteInRoom("owner")).toBe(true);
    expect(canWriteInRoom("member")).toBe(true);
    expect(canWriteInRoom("guest")).toBe(false);
    expect(canWriteInRoom(null)).toBe(false);
  });

  it("sorts rooms by name", () => {
    const rooms = [
      { id: "2", name: "Warung", role: "member" },
      { id: "1", name: "Arisan", role: "owner" },
    ];
    expect(sortRooms(rooms).map((r) => r.name)).toEqual(["Arisan", "Warung"]);
    expect(sortRooms(null)).toEqual([]);
  });

  it("hides another member's private rows client-side too", () => {
    const memories = [
      {
        id: "1",
        room_id: "r",
        author_user_id: "a",
        kind: "note",
        content: "shared",
        visibility: "room",
        created_at: "2026-11-20T00:00:00Z",
      },
      {
        id: "2",
        room_id: "r",
        author_user_id: "a",
        kind: "note",
        content: "secret",
        visibility: "private",
        created_at: "2026-11-20T00:00:00Z",
      },
      {
        id: "3",
        room_id: "r",
        author_user_id: "me",
        kind: "note",
        content: "mine",
        visibility: "private",
        created_at: "2026-11-20T00:00:00Z",
      },
    ];
    expect(visibleMemories(memories, "me").map((m) => m.content)).toEqual([
      "shared",
      "mine",
    ]);
  });

  it("keeps only this room's activity frames", () => {
    const events: SseEvent[] = [
      { type: "office", content: {} },
      {
        type: "room_activity",
        room_activity: {
          room_id: "r1",
          kind: "memory_added",
          actor: "a",
          summary: "a note",
          created_at: 1,
        },
      },
      {
        type: "room_activity",
        room_activity: {
          room_id: "r2",
          kind: "memory_added",
          actor: "a",
          summary: "other room",
          created_at: 2,
        },
      },
    ];
    expect(roomActivityForRoom(events, "r1").map((a) => a.summary)).toEqual(["a note"]);
    expect(roomActivityForRoom(events, "r2")).toHaveLength(1);
    expect(roomActivityForRoom(null, "r1")).toEqual([]);
  });
});
