import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConversationsScreen } from "./Conversations";
import {
  addRoomAgent,
  createRoom,
  fetchAgents,
  fetchChatMessages,
  fetchRoomJobs,
  fetchRooms,
} from "../../api/client";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    fetchRooms: vi.fn(),
    fetchAgents: vi.fn(),
    fetchChatMessages: vi.fn(),
    fetchRoomJobs: vi.fn(),
    createRoom: vi.fn(),
    addRoomAgent: vi.fn(),
    fetchAgentAvatar: vi.fn().mockResolvedValue(null),
  };
});

const mockedRooms = vi.mocked(fetchRooms);
const mockedAgents = vi.mocked(fetchAgents);
const mockedChat = vi.mocked(fetchChatMessages);
const mockedJobs = vi.mocked(fetchRoomJobs);
const mockedCreateRoom = vi.mocked(createRoom);
const mockedAddAgent = vi.mocked(addRoomAgent);

const AGENTS = [
  {
    agent_id: "puspa-user-1",
    name: "Puspa",
    role: "agen utama",
    purpose: "Agen wajib Puspa",
    required: true,
  },
  {
    agent_id: "agen-scout",
    name: "Scout",
    role: "riset",
    purpose: "Riset harga",
    required: false,
    avatar: "sakura",
  },
];

const ROOMS = [{ id: "room-1", name: "Grup Lama", role: "owner" }];

function seed() {
  mockedRooms.mockResolvedValue({ rooms: ROOMS } as never);
  mockedAgents.mockResolvedValue({ agents: AGENTS } as never);
  mockedChat.mockResolvedValue({
    messages: [
      { sender: "users", text: "Halo Puspa", created_at: 1 },
      { sender: "puspa", text: "Harga S24 termurah Rp12 juta", created_at: 2 },
    ],
  } as never);
  mockedJobs.mockResolvedValue({
    room_id: "room-1",
    jobs: [
      {
        id: "job-1",
        room_id: "room-1",
        kind: "note",
        payload: { text: "Tugas pertama grup" },
        status: "done",
        requested_by: "user-1",
        result: null,
        error: null,
        created_at: "2026-11-24T00:00:00Z",
        started_at: null,
        finished_at: null,
      },
    ],
  } as never);
  mockedCreateRoom.mockResolvedValue({
    id: "room-2",
    name: "Grup Baru",
    created_by: "user-1",
    created_at: "2026-11-24T01:00:00Z",
    role: "owner",
    members: [],
  } as never);
  mockedAddAgent.mockResolvedValue({
    agent_id: "puspa-user-1",
    name: "Puspa",
    added_by: "user-1",
    added_at: "2026-11-24T01:00:00Z",
  } as never);
}

describe("ConversationsScreen inbox (Nero, 24 Nov)", () => {
  it("shows each row with its icon, name, and last-chat preview", async () => {
    seed();
    render(<ConversationsScreen />);
    expect(await screen.findByText("Harga S24 termurah Rp12 juta")).toBeTruthy();
    expect(await screen.findByText("Tugas pertama grup")).toBeTruthy();
    // Custom agents appear as rows with their purpose as preview.
    expect(screen.getByText("Scout")).toBeTruthy();
    expect(screen.getByText("Riset harga")).toBeTruthy();
    expect(screen.getByText("Grup Lama")).toBeTruthy();
    // Scout picked the sakura icon in the Tambah Agent form, so
    // the inbox row shows the sakura flower, not an initial letter.
    expect(
      document.querySelector('img[src*="agent-flower-sakura-nobg"]'),
    ).toBeTruthy();
  });

  it("the + button opens Tambah Agent and Buat Grup Baru", async () => {
    seed();
    render(<ConversationsScreen />);
    await screen.findByText("Puspa");
    fireEvent.click(screen.getByRole("button", { name: "Buat baru" }));
    const addAgent = screen.getByRole("menuitem", { name: "Tambah Agent" });
    expect(addAgent.getAttribute("href")).toBe("/konektor");
    expect(
      screen.getByRole("menuitem", { name: "Buat Grup Baru" }),
    ).toBeTruthy();
  });

  it("Buat Grup Baru creates the room, invites the checked agents, and puts it on top", async () => {
    seed();
    render(<ConversationsScreen />);
    await screen.findByText("Grup Lama");
    fireEvent.click(screen.getByRole("button", { name: "Buat baru" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Buat Grup Baru" }));
    fireEvent.change(screen.getByLabelText("Nama grup"), {
      target: { value: "Grup Baru" },
    });
    // Puspa is pre-checked; also invite Scout.
    fireEvent.click(screen.getByRole("checkbox", { name: /Scout/ }));
    fireEvent.click(screen.getByRole("button", { name: "Buat Grup" }));
    await waitFor(() =>
      expect(mockedCreateRoom).toHaveBeenCalledWith("Grup Baru"),
    );
    expect(mockedAddAgent).toHaveBeenCalledWith("room-2", "puspa-user-1");
    expect(mockedAddAgent).toHaveBeenCalledWith("room-2", "agen-scout");
    // The new room is first in the list, with a link to open it.
    const rows = screen.getAllByText("Grup Baru");
    expect(rows.length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Buka grup" })).toBeTruthy();
    const list = screen.getByText("Grup Baru").closest("a");
    expect(list?.getAttribute("href")).toBe("/chat/ruang?room=room-2");
  });
});
