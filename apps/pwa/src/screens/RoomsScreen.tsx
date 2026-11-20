import { useCallback, useEffect, useState } from "react";
import {
  addRoomMemory,
  ApiError,
  bringMemoryToRoom,
  createRoom,
  deleteRoom,
  deleteRoomMemory,
  enqueueRoomJob,
  fetchMemoryFacts,
  fetchRoomBoard,
  fetchRoomDetail,
  fetchRoomInvites,
  fetchRoomJobs,
  fetchRoomMemory,
  fetchRooms,
  issueRoomInvite,
  joinRoom,
  leaveRoom,
  parseSseEvent,
  removeMember,
  revokeRoomInvite,
  runRoomJob,
  setMemberRole,
  type IssuedInvite,
  type MemoryFactItem,
  type RoomBoard,
  type RoomDetail,
  type RoomInvite,
  type RoomJob,
  type RoomMemory,
  type RoomSummary,
} from "../api/client";
import { canWriteInRoom, roleLabel, sortRooms } from "../state/rooms";

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Gagal memuat ruang.";
}

export function RoomsScreen() {
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [roomsError, setRoomsError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RoomDetail | null>(null);
  const [board, setBoard] = useState<RoomBoard | null>(null);
  const [memories, setMemories] = useState<RoomMemory[]>([]);
  const [jobs, setJobs] = useState<RoomJob[]>([]);
  const [invites, setInvites] = useState<RoomInvite[]>([]);
  const [facts, setFacts] = useState<MemoryFactItem[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [newName, setNewName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [inviteKind, setInviteKind] = useState<"link" | "code">("link");
  const [inviteRole, setInviteRole] = useState<"member" | "guest">("member");
  const [issued, setIssued] = useState<IssuedInvite | null>(null);
  const [noteContent, setNoteContent] = useState("");
  const [noteVisibility, setNoteVisibility] = useState<"room" | "private">("room");
  const [jobContent, setJobContent] = useState("");

  const refreshRooms = useCallback(async () => {
    try {
      const data = await fetchRooms();
      setRooms(sortRooms(data.rooms));
      setRoomsError("");
    } catch (err) {
      setRoomsError(errorText(err));
    }
  }, []);

  const refreshDetail = useCallback(async (roomId: string) => {
    try {
      const [roomDetail, roomBoard, roomMemory, roomJobs, roomInvites] = await Promise.all([
        fetchRoomDetail(roomId),
        fetchRoomBoard(roomId),
        fetchRoomMemory(roomId),
        fetchRoomJobs(roomId),
        fetchRoomInvites(roomId).catch(() => ({ room_id: roomId, invites: [] as RoomInvite[] })),
      ]);
      setDetail(roomDetail);
      setBoard(roomBoard);
      setMemories(roomMemory.memories);
      setJobs(roomJobs.jobs);
      setInvites(roomInvites.invites);
      setError("");
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  useEffect(() => {
    void refreshRooms();
  }, [refreshRooms]);

  useEffect(() => {
    if (selectedId) void refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  // Room activity arrives on the same SSE stream the Office screen
  // uses; a frame for the open room refreshes it live.
  useEffect(() => {
    const source = new EventSource("/sse");
    source.onmessage = (event) => {
      const parsed = parseSseEvent(event.data);
      if (
        parsed?.type === "room_activity" &&
        parsed.room_activity?.room_id &&
        parsed.room_activity.room_id === selectedId
      ) {
        void refreshDetail(selectedId);
        void refreshRooms();
      }
    };
    return () => source.close();
  }, [selectedId, refreshDetail, refreshRooms]);

  useEffect(() => {
    let cancelled = false;
    fetchMemoryFacts()
      .then((data) => {
        if (!cancelled) setFacts(data.facts.filter((f) => f.status === "active"));
      })
      .catch(() => {
        // Bring-to-room is optional: the room still works without it.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onCreateRoom() {
    if (!newName.trim()) return;
    try {
      const room = await createRoom(newName.trim());
      setNewName("");
      setNotice(`Ruang ${room.name} dibuat. Undang anggota kedua lewat tautan atau kode.`);
      await refreshRooms();
      setSelectedId(room.id);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onJoinByCode() {
    if (!joinCode.trim()) return;
    try {
      const joined = await joinRoom({ code: joinCode.trim() });
      setJoinCode("");
      setNotice("Berhasil gabung ke ruang.");
      await refreshRooms();
      setSelectedId(joined.room_id);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onIssueInvite() {
    if (!selectedId) return;
    try {
      setIssued(await issueRoomInvite(selectedId, inviteKind, inviteRole));
      setNotice(
        inviteKind === "code"
          ? "Kode sekali pakai ini cuma tampil sekali — sampaikan langsung ke yang diundang."
          : "Tautan ini cuma tampil sekali — kirim langsung ke yang diundang.",
      );
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onRevokeInvite(inviteId: string) {
    if (!selectedId) return;
    try {
      await revokeRoomInvite(selectedId, inviteId);
      setNotice("Undangan dicabut.");
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onSetRole(memberId: string, role: "owner" | "member" | "guest") {
    if (!selectedId) return;
    try {
      await setMemberRole(selectedId, memberId, role);
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onRemoveMember(memberId: string) {
    if (!selectedId) return;
    try {
      await removeMember(selectedId, memberId);
      setNotice("Anggota dikeluarkan — aksesnya langsung putus.");
      await refreshDetail(selectedId);
      await refreshRooms();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onAddNote() {
    if (!selectedId || !noteContent.trim()) return;
    try {
      await addRoomMemory(selectedId, noteContent.trim(), noteVisibility);
      setNoteContent("");
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onBringFact(factId: string) {
    if (!selectedId) return;
    try {
      await bringMemoryToRoom(selectedId, factId);
      setNotice("Memori pribadi dibawa ke ruang sebagai catatan bersama.");
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onDeleteMemory(memoryId: string) {
    if (!selectedId) return;
    try {
      await deleteRoomMemory(selectedId, memoryId);
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onEnqueueNoteJob() {
    if (!selectedId || !jobContent.trim()) return;
    try {
      await enqueueRoomJob(selectedId, "note", { content: jobContent.trim() });
      setJobContent("");
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onRunJob(jobId: string) {
    if (!selectedId) return;
    try {
      await runRoomJob(selectedId, jobId);
      await refreshDetail(selectedId);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onLeave() {
    if (!selectedId) return;
    try {
      await leaveRoom(selectedId);
      setSelectedId(null);
      setDetail(null);
      await refreshRooms();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function onDeleteRoom() {
    if (!selectedId) return;
    try {
      await deleteRoom(selectedId);
      setSelectedId(null);
      setDetail(null);
      await refreshRooms();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const canWrite = detail ? canWriteInRoom(detail.role) : false;
  const isOwner = detail?.role === "owner";

  return (
    <div>
      <h1>Ruang</h1>
      <p className="muted">
        Ruang bersama untuk keluarga, tim kecil, arisan, atau komunitas (2-5 orang, tanpa dana
        bersama). Yang terlihat di sini cuma milik anggota ruang ini.
      </p>
      {roomsError ? <p role="alert" className="error">{roomsError}</p> : null}
      {error ? <p role="alert" className="error">{error}</p> : null}
      {notice ? <p className="muted">{notice}</p> : null}

      <h2>Ruang saya</h2>
      {rooms.length === 0 ? (
        <p className="muted">Belum ada ruang. Buat satu, atau gabung pakai kode undangan.</p>
      ) : (
        rooms.map((room) => (
          <div key={room.id} className="card">
            <button type="button" onClick={() => setSelectedId(room.id)}>
              {room.name}
            </button>{" "}
            <span className="badge">{roleLabel(room.role)}</span>
          </div>
        ))
      )}

      <h2>Buat ruang</h2>
      <label>
        Nama ruang{" "}
        <input value={newName} onChange={(e) => setNewName(e.target.value)} />
      </label>{" "}
      <button type="button" onClick={() => void onCreateRoom()}>
        Buat ruang
      </button>

      <h2>Gabung pakai kode</h2>
      <label>
        Kode 6 digit{" "}
        <input value={joinCode} onChange={(e) => setJoinCode(e.target.value)} />
      </label>{" "}
      <button type="button" onClick={() => void onJoinByCode()}>
        Gabung
      </button>

      {detail ? (
        <div>
          <h2>{detail.name}</h2>
          <p className="muted">
            Peran saya: {roleLabel(detail.role)}
            {detail.role === "guest" ? " — tamu hanya bisa membaca." : ""}
          </p>

          <h3>Anggota</h3>
          {detail.members.map((member) => (
            <div key={member.user_id} className="card">
              <span>{member.user_id}</span> <span className="badge">{roleLabel(member.role)}</span>
              {isOwner && member.user_id !== detail.created_by ? (
                <span>
                  {" "}
                  <select
                    value={member.role}
                    onChange={(e) =>
                      void onSetRole(
                        member.user_id,
                        e.target.value as "owner" | "member" | "guest",
                      )
                    }
                    aria-label={`Peran ${member.user_id}`}
                  >
                    <option value="owner">Pemilik</option>
                    <option value="member">Anggota</option>
                    <option value="guest">Tamu</option>
                  </select>{" "}
                  <button type="button" onClick={() => void onRemoveMember(member.user_id)}>
                    Keluarkan
                  </button>
                </span>
              ) : null}
            </div>
          ))}

          {canWrite ? (
            <div>
              <h3>Undang anggota</h3>
              <label>
                Jenis{" "}
                <select
                  value={inviteKind}
                  onChange={(e) => setInviteKind(e.target.value as "link" | "code")}
                >
                  <option value="link">Tautan (7 hari)</option>
                  <option value="code">Kode sekali pakai (24 jam)</option>
                </select>
              </label>{" "}
              <label>
                Peran{" "}
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as "member" | "guest")}
                >
                  <option value="member">Anggota</option>
                  <option value="guest">Tamu</option>
                </select>
              </label>{" "}
              <button type="button" onClick={() => void onIssueInvite()}>
                Buat undangan
              </button>
              {issued ? (
                <p className="muted">
                  {issued.kind === "code" ? (
                    <>Kode: <strong>{issued.code}</strong></>
                  ) : (
                    <>Tautan: <strong>{issued.join_link}</strong></>
                  )}{" "}
                  (berlaku sampai {issued.expires_at})
                </p>
              ) : null}
              {invites.filter((invite) => invite.status === "pending").length > 0 ? (
                <div>
                  <h4>Undangan aktif</h4>
                  {invites
                    .filter((invite) => invite.status === "pending")
                    .map((invite) => (
                      <div key={invite.id} className="card">
                        <span>
                          {invite.kind === "code" ? "Kode" : "Tautan"} ·{" "}
                          {roleLabel(invite.role)} · berlaku sampai {invite.expires_at}
                        </span>{" "}
                        <button type="button" onClick={() => void onRevokeInvite(invite.id)}>
                          Cabut
                        </button>
                      </div>
                    ))}
                </div>
              ) : null}
            </div>
          ) : null}

          <h3>Memori ruang</h3>
          {memories.length === 0 ? (
            <p className="muted">Belum ada catatan ruang.</p>
          ) : (
            memories.map((memory) => (
              <div key={memory.id} className="card">
                <p>
                  {memory.content}
                  {memory.visibility === "private" ? " (pribadi, cuma saya)" : ""}
                </p>
                <p className="muted">
                  {memory.kind} · {memory.author_user_id}
                </p>
                <button type="button" onClick={() => void onDeleteMemory(memory.id)}>
                  Hapus
                </button>
              </div>
            ))
          )}
          {canWrite ? (
            <div>
              <label>
                Catatan baru{" "}
                <input value={noteContent} onChange={(e) => setNoteContent(e.target.value)} />
              </label>{" "}
              <label>
                Terlihat{" "}
                <select
                  value={noteVisibility}
                  onChange={(e) => setNoteVisibility(e.target.value as "room" | "private")}
                >
                  <option value="room">Semua anggota</option>
                  <option value="private">Cuma saya</option>
                </select>
              </label>{" "}
              <button type="button" onClick={() => void onAddNote()}>
                Simpan catatan
              </button>
            </div>
          ) : null}

          {canWrite && facts.length > 0 ? (
            <div>
              <h3>Bawa memori pribadi ke ruang</h3>
              <p className="muted">
                Pilih satu item — cuma item itu yang disalin ke ruang, sebagai catatan bersama.
              </p>
              {facts.map((fact) => (
                <div key={fact.id} className="card">
                  <span>
                    {fact.key}: {fact.content ?? ""}
                  </span>{" "}
                  <button type="button" onClick={() => void onBringFact(fact.id)}>
                    Bawa ke ruang
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <h3>Tugas bersama</h3>
          {board && board.tasks.length === 0 ? (
            <p className="muted">Belum ada tugas di ruang ini.</p>
          ) : (
            board?.tasks.map((task) => (
              <div key={task.id} className="card">
                <strong>{task.title}</strong> <span className="badge">{task.status}</span>
              </div>
            ))
          )}

          <h3>Antrean tugas ruang</h3>
          {jobs.length === 0 ? (
            <p className="muted">Antrean kosong.</p>
          ) : (
            jobs.map((job) => (
              <div key={job.id} className="card">
                <span>
                  {job.kind} · {job.status}
                  {job.result ? ` — ${job.result.slice(0, 80)}` : ""}
                  {job.error ? ` — gagal: ${job.error}` : ""}
                </span>{" "}
                {job.status === "queued" && canWrite ? (
                  <button type="button" onClick={() => void onRunJob(job.id)}>
                    Jalankan
                  </button>
                ) : null}
              </div>
            ))
          )}
          {canWrite ? (
            <div>
              <label>
                Catatan antrean{" "}
                <input value={jobContent} onChange={(e) => setJobContent(e.target.value)} />
              </label>{" "}
              <button type="button" onClick={() => void onEnqueueNoteJob()}>
                Antrekan catatan
              </button>
            </div>
          ) : null}

          <h3>Ruang ini</h3>
          <button type="button" onClick={() => void onLeave()}>
            Keluar dari ruang
          </button>{" "}
          {isOwner ? (
            <button type="button" onClick={() => void onDeleteRoom()}>
              Hapus ruang
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
