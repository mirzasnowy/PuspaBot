import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RoomsScreen } from "./RoomsScreen";

class FakeEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  close = vi.fn();
  constructor(_url: string) {}
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(handler: (url: string) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) => {
      const body = handler(url);
      if (body instanceof Error) {
        return { ok: false, status: 500, json: async () => ({ error: body.message }) };
      }
      return { ok: true, status: 200, json: async () => body };
    }),
  );
}

const ROOM_ID = "11111111-1111-1111-1111-111111111111";

function roomPayload(role = "owner") {
  return {
    rooms: [{ id: ROOM_ID, name: "Keluarga", role }],
  };
}

function detailPayload(role = "owner") {
  return {
    id: ROOM_ID,
    name: "Keluarga",
    created_by: "owner-1",
    created_at: "2026-11-20T00:00:00Z",
    role,
    members: [
      { user_id: "owner-1", role: "owner", joined_at: "2026-11-20T00:00:00Z" },
      { user_id: "member-1", role: "member", joined_at: "2026-11-20T00:00:00Z" },
    ],
  };
}

describe("RoomsScreen (P-05)", () => {
  it("lists rooms and shows the create and join forms", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    stubFetch((url) => {
      if (url === "/api/rooms") return roomPayload();
      if (url === "/api/memory") return { facts: [], total_facts: 0 };
      throw new Error(`unexpected fetch: ${url}`);
    });
    render(<RoomsScreen />);
    expect(await screen.findByText("Keluarga")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Buat ruang" })).toBeTruthy();
    expect(screen.getByText("Gabung pakai kode")).toBeTruthy();
  });

  it("opens a room and shows members, memory, tasks, and jobs", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    stubFetch((url) => {
      if (url === "/api/rooms") return roomPayload();
      if (url === "/api/memory") return { facts: [], total_facts: 0 };
      if (url === `/api/rooms/${ROOM_ID}`) return detailPayload();
      if (url === `/api/rooms/${ROOM_ID}/tasks`) {
        return {
          room_id: ROOM_ID,
          room_name: "Keluarga",
          tasks: [
            {
              id: "t1",
              room_id: ROOM_ID,
              title: "Beli beras",
              owner_user_id: "member-1",
              due_at: null,
              status: "open",
              created_by: "owner-1",
              created_at: "2026-11-20T00:00:00Z",
              completed_at: null,
              delegation: null,
              pending_steps: 0,
            },
          ],
        };
      }
      if (url === `/api/rooms/${ROOM_ID}/memory`) {
        return {
          room_id: ROOM_ID,
          memories: [
            {
              id: "m1",
              room_id: ROOM_ID,
              author_user_id: "owner-1",
              kind: "note",
              content: "rencana arisan",
              visibility: "room",
              created_at: "2026-11-20T00:00:00Z",
            },
          ],
        };
      }
      if (url === `/api/rooms/${ROOM_ID}/jobs`) return { room_id: ROOM_ID, jobs: [] };
      if (url === `/api/rooms/${ROOM_ID}/invites`) return { room_id: ROOM_ID, invites: [] };
      throw new Error(`unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    render(<RoomsScreen />);
    await user.click(await screen.findByText("Keluarga"));
    expect(await screen.findByText("rencana arisan")).toBeTruthy();
    expect(screen.getByText("Beli beras")).toBeTruthy();
    expect(screen.getByText("member-1")).toBeTruthy();
    expect(screen.getByText("Antrean kosong.")).toBeTruthy();
  });

  it("hides every write control from a guest", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    stubFetch((url) => {
      if (url === "/api/rooms") return roomPayload("guest");
      if (url === "/api/memory") return { facts: [], total_facts: 0 };
      if (url === `/api/rooms/${ROOM_ID}`) return detailPayload("guest");
      if (url === `/api/rooms/${ROOM_ID}/tasks`) {
        return { room_id: ROOM_ID, room_name: "Keluarga", tasks: [] };
      }
      if (url === `/api/rooms/${ROOM_ID}/memory`) return { room_id: ROOM_ID, memories: [] };
      if (url === `/api/rooms/${ROOM_ID}/jobs`) return { room_id: ROOM_ID, jobs: [] };
      if (url === `/api/rooms/${ROOM_ID}/invites`) return { room_id: ROOM_ID, invites: [] };
      throw new Error(`unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    render(<RoomsScreen />);
    await user.click(await screen.findByText("Keluarga"));
    await screen.findByText(/tamu hanya bisa membaca/);
    expect(screen.queryByText("Buat undangan")).toBeNull();
    expect(screen.queryByText("Simpan catatan")).toBeNull();
    expect(screen.queryByText("Antrekan catatan")).toBeNull();
  });
});
