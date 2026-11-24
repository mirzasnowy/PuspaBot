/**
 * Percakapan (Chat tab) + Ruang Chat, Space port.
 *
 * The list is real data: the Puspa 1:1 thread first, then the
 * user's shared rooms from GET /api/rooms. No unread counts —
 * there is no unread API, so none is shown. The room thread
 * shows the room's real jobs and board tasks (there is no room
 * chat-messages API); its composer creates a room job via
 * POST /api/rooms/{id}/jobs.
 */
import { useEffect, useState } from "react";
import {
  addRoomAgent,
  createRoom,
  enqueueRoomJob,
  fetchAgents,
  fetchChatMessages,
  fetchRoomJobs,
  fetchRooms,
  type Agent,
  type RoomJob,
  type RoomSummary,
} from "../../api/client";
import { useLanguage } from "../../state/language";
import { builtinAvatarSrc } from "../../state/avatar";
import { AgentMascot } from "../../components/Mascot";
import { AgentAvatarPhoto, useAvatarStatus } from "../../components/AgentAvatar";
import { DynamicIsland } from "../../components/DynamicIsland";
import { ScrollToBottomButton } from "../../components/ScrollToBottom";
import { avatarPhotoSrc } from "../../state/avatar";
import { Icon } from "../../components/icons";
import { PageHead, useToast } from "./shared";

/**
 * Inbox: one row per conversation — Puspa 1:1, the user's other
 * agents, then shared rooms, newest first. Every row shows its
 * icon, its name, and a preview line: the last chat text for
 * Puspa and for rooms (their newest job), the agent's purpose
 * for agents that have no chat thread of their own. The "+"
 * button (Nero, 24 Nov) opens "Tambah Agent" (the Tim Agen
 * screen, where agents are created) and "Buat Grup Baru" (a
 * form: group name + the agents to invite, wired to the real
 * rooms API — POST /api/rooms, then one POST
 * /api/rooms/{id}/agents per invited agent).
 */
export function ConversationsScreen() {
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [lastPuspa, setLastPuspa] = useState("");
  const [roomPreview, setRoomPreview] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const { toast } = useToast();
  const { t } = useLanguage();
  // Same photo as the chat bubbles (Nero, 24 Nov).
  const avatar = useAvatarStatus();
  const [menuOpen, setMenuOpen] = useState(false);
  const [groupFormOpen, setGroupFormOpen] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [invited, setInvited] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [groupError, setGroupError] = useState("");
  const [createdRoom, setCreatedRoom] = useState<RoomSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRooms()
      .then((result) => {
        if (!cancelled) setRooms(result.rooms);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : t("ui.common.load_rooms_failed", "Gagal memuat ruang."));
      });
    fetchAgents()
      .then((result) => {
        if (!cancelled) setAgents(result.agents ?? []);
      })
      .catch(() => undefined);
    fetchChatMessages()
      .then((result) => {
        if (cancelled) return;
        const messages = result.messages ?? [];
        const last = messages[messages.length - 1];
        if (last && typeof last.text === "string" && last.text.trim()) {
          setLastPuspa(last.text.trim());
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // One preview per room: its newest job's text (jobs come
  // oldest first, so the last one is the newest).
  useEffect(() => {
    let cancelled = false;
    if (rooms.length === 0) return;
    void Promise.all(
      rooms.map((room) =>
        fetchRoomJobs(room.id)
          .then((result) => {
            if (cancelled) return;
            const jobs = result.jobs ?? [];
            const last = jobs[jobs.length - 1];
            const text =
              typeof last?.payload?.text === "string" && last.payload.text.trim()
                ? last.payload.text.trim()
                : typeof last?.result === "string" && last.result.trim()
                  ? last.result.trim()
                  : "";
            if (text) {
              setRoomPreview((prev) => ({ ...prev, [room.id]: text }));
            }
          })
          .catch(() => undefined),
      ),
    );
    return () => {
      cancelled = true;
    };
  }, [rooms]);

  const q = query.trim().toLowerCase();
  const visibleRooms = rooms.filter((r) => !q || r.name.toLowerCase().includes(q));
  const customAgents = agents.filter((a) => !a.required);
  const visibleAgents = customAgents.filter(
    (a) => !q || a.name.toLowerCase().includes(q),
  );
  const puspaAgent = agents.find((a) => a.required);

  function openGroupForm() {
    setMenuOpen(false);
    setGroupFormOpen(true);
    setGroupError("");
    setCreatedRoom(null);
    setGroupName("");
    setInvited(puspaAgent ? [puspaAgent.agent_id] : []);
  }

  function toggleInvite(agentId: string) {
    setInvited((prev) =>
      prev.includes(agentId)
        ? prev.filter((id) => id !== agentId)
        : [...prev, agentId],
    );
  }

  async function onCreateGroup(event: React.FormEvent) {
    event.preventDefault();
    const name = groupName.trim();
    if (!name || creating) return;
    setCreating(true);
    setGroupError("");
    try {
      const room = await createRoom(name);
      // Invite each checked agent; one failing invite must not
      // lose the room that was just created.
      const failed: string[] = [];
      for (const agentId of invited) {
        try {
          await addRoomAgent(room.id, agentId);
        } catch {
          failed.push(agentId);
        }
      }
      const summary: RoomSummary = { id: room.id, name: room.name, role: "owner" };
      setRooms((prev) => [summary, ...prev.filter((r) => r.id !== room.id)]);
      setCreatedRoom(summary);
      setGroupFormOpen(false);
      if (failed.length > 0) {
        setGroupError(
          t("ui.chat.group_invite_failed", "Grup “{name}” dibuat, tapi {count} agent gagal diundang. Coba lagi dari layar grup.", { name: room.name, count: failed.length }),
        );
      }
    } catch (err) {
      setGroupError(err instanceof Error ? err.message : t("ui.chat.create_group_failed", "Gagal membuat grup."));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="page">
      <div className="chatlist-head">
        <PageHead title={t("ui.chat.title", "Percakapan")} />
        <button
          type="button"
          className="fab-compose"
          aria-label={t("ui.chat.new_button", "Buat baru")}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <Icon name="plus" size={24} />
        </button>
      </div>
      {menuOpen ? (
        <div className="card compose-menu" role="menu" aria-label={t("ui.chat.new_button", "Buat baru")}>
          <a className="compose-menu-item" role="menuitem" href="/konektor">
            <Icon name="plus" size={18} /> {t("ui.inbox.add_agent", "Tambah Agent")}
          </a>
          <button
            type="button"
            className="compose-menu-item"
            role="menuitem"
            onClick={openGroupForm}
          >
            <Icon name="users" size={18} /> {t("ui.chat.create_group_title", "Buat Grup Baru")}
          </button>
        </div>
      ) : null}
      {groupFormOpen ? (
        <form className="card stacked-form" onSubmit={onCreateGroup}>
          <b>{t("ui.chat.create_group_form_title", "Buat Grup Baru")}</b>
          <label>
            {t("ui.chat.group_name", "Nama grup")}
            <input
              className="input"
              type="text"
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              placeholder={t("ui.chat.group_name_placeholder", "Contoh: Grup Riset")}
              aria-label={t("ui.chat.group_name", "Nama grup")}
            />
          </label>
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
            <legend style={{ fontWeight: 700, marginBottom: 6 }}>
              {t("ui.chat.invite_agents", "Undang agent")}
            </legend>
            {agents.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                {t("ui.chat.loading_agents", "Memuat agent…")}
              </p>
            ) : (
              agents.map((agent) => (
                <label
                  key={agent.agent_id}
                  style={{ display: "flex", gap: 8, alignItems: "center", padding: "6px 0" }}
                >
                  <input
                    type="checkbox"
                    checked={invited.includes(agent.agent_id)}
                    onChange={() => toggleInvite(agent.agent_id)}
                  />
                  <span>
                    <strong>{agent.name}</strong>
                    {agent.purpose ? (
                      <span className="muted"> — {agent.purpose}</span>
                    ) : null}
                  </span>
                </label>
              ))
            )}
          </fieldset>
          {groupError ? (
            <p role="alert" className="error">
              {groupError}
            </p>
          ) : null}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="btn dark"
              type="submit"
              disabled={creating || !groupName.trim()}
              style={{ flex: 1 }}
            >
              {creating ? t("ui.chat.creating", "Membuat…") : t("ui.chat.create_group", "Buat Grup")}
            </button>
            <button
              className="btn ghost"
              type="button"
              onClick={() => setGroupFormOpen(false)}
            >
              {t("ui.common.cancel", "Batal")}
            </button>
          </div>
        </form>
      ) : null}
      {createdRoom ? (
        <div className="okbox" style={{ marginTop: 12 }}>
          {t("ui.chat.create_group_success", "Grup “{name}” dibuat dan muncul di paling atas daftar.", { name: createdRoom.name })}{" "}
          <a href={`/chat/ruang?room=${encodeURIComponent(createdRoom.id)}`}>
            {t("ui.chat.open_group", "Buka grup")}
          </a>
        </div>
      ) : null}
      <div className="searchbar" style={{ marginTop: 18 }}>
        <Icon name="search" size={20} />
        <input
          type="search"
          placeholder={t("ui.chat.search_placeholder", "Cari percakapan")}
          aria-label={t("ui.chat.search_placeholder", "Cari percakapan")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <div style={{ marginTop: 8 }}>
        <a
          className="conv"
          href="/chat/puspa"
          style={{ textDecoration: "none", color: "inherit" }}
        >
          <span className="av">
            <AgentAvatarPhoto
              state="idle"
              photoSrc={avatarPhotoSrc(avatar)}
              size={56}
            />
            <span className="online" aria-hidden="true" />
          </span>
          <span className="grow">
            <span className="nm">Puspa</span>
            <span className="preview">
              {lastPuspa || t("ui.chat.assistant_main", "Asisten Utama")}
            </span>
          </span>
          <span className="meta">
            <span className="tm">{t("ui.chat.assistant_main", "Asisten Utama")}</span>
          </span>
        </a>
        {visibleAgents.map((agent) => (
          <a
            key={agent.agent_id}
            className="conv"
            href="/agen"
            style={{ textDecoration: "none", color: "inherit" }}
          >
            <span className="av">
              {builtinAvatarSrc(agent.avatar) ? (
                <img
                  src={builtinAvatarSrc(agent.avatar) as string}
                  alt=""
                  width={56}
                  height={56}
                  style={{ borderRadius: "50%" }}
                />
              ) : (
                <span className="agent-initial" aria-hidden="true">
                  {agent.name.trim().charAt(0).toUpperCase() || "A"}
                </span>
              )}
            </span>
            <span className="grow">
              <span className="nm">{agent.name}</span>
              <span className="preview">
                {agent.purpose || agent.role || t("ui.chat.agent_role", "Agen")}
              </span>
            </span>
            <span className="meta">
              <span className="tm">{t("ui.chat.agent_role", "Agen")}</span>
            </span>
          </a>
        ))}
        {visibleRooms.map((room) => (
          <a
            key={room.id}
            className="conv"
            href={`/chat/ruang?room=${encodeURIComponent(room.id)}`}
            style={{ textDecoration: "none", color: "inherit" }}
          >
            <span className="av">
              <span
                className="cluster"
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 2,
                  width: 56,
                  height: 56,
                }}
              >
                <AgentMascot agent="scout" size={27} state="idle" />
                <AgentMascot agent="pulse" size={27} state="idle" />
                <AgentMascot agent="forge" size={27} state="idle" />
                <AgentMascot agent="puspa" size={27} state="idle" />
              </span>
            </span>
            <span className="grow">
              <span className="nm">{room.name}</span>
              <span className="preview">
                {roomPreview[room.id] || t("ui.chat.room_label", "Ruang bersama")}
              </span>
            </span>
            <span className="meta">
              <span className="tm">{t("ui.chat.room_label", "Ruang bersama")}</span>
            </span>
          </a>
        ))}
        {visibleRooms.length === 0 && visibleAgents.length === 0 && !error ? (
          <p className="foot-note">
            {t("ui.chat.no_rooms", "Belum ada ruang bersama. Buat grup baru dari tombol + di atas.")}
          </p>
        ) : null}
      </div>
      <p className="foot-note">
        {t("ui.chat.forward_note", "Forward pesan dari WhatsApp/Telegram ke Puspa lewat Share Target — lihat layar Bagikan ke Puspa di menu Saya.")}
      </p>
      {toast}
    </div>
  );
}

export function RoomChatScreen() {
  const roomId = new URLSearchParams(window.location.search).get("room") ?? "";
  const [jobs, setJobs] = useState<RoomJob[]>([]);
  const [roomName, setRoomName] = useState("Ruang Bersama");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { show, toast } = useToast();
  // Same photo as the chat bubbles (Nero, 24 Nov).
  const avatar = useAvatarStatus();

  useEffect(() => {
    if (!roomId) return;
    let cancelled = false;
    fetchRooms()
      .then((result) => {
        const found = result.rooms.find((r) => r.id === roomId);
        if (!cancelled && found) setRoomName(found.name);
      })
      .catch(() => undefined);
    fetchRoomJobs(roomId)
      .then((result) => {
        if (!cancelled) setJobs(result.jobs ?? []);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Gagal memuat pekerjaan ruang.");
      });
    return () => {
      cancelled = true;
    };
  }, [roomId]);

  async function onSend(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy || !roomId) return;
    setDraft("");
    setBusy(true);
    setError("");
    try {
      const created = await enqueueRoomJob(roomId, "note", { text });
      setJobs((prev) => [created, ...prev]);
      show("Pekerjaan ditambahkan ke ruang.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menambah pekerjaan.");
    } finally {
      setBusy(false);
    }
  }

  const runningJob = jobs.find(
    (job) => job.status === "running" || job.status === "queued",
  );
  const working = Boolean(runningJob);

  return (
    <div className="page">
      <DynamicIsland
        name={roomName}
        working={working}
        avatar={avatar}
        activity={
          working
            ? (typeof runningJob?.payload?.text === "string"
                ? (runningJob?.payload.text as string)
                : runningJob?.kind) ?? "Bekerja…"
            : null
        }
      />
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <div className="thread">
        {jobs.length === 0 ? (
          <p className="foot-note">
            Belum ada pekerjaan di ruang ini. Tulis tugas pertama di bawah —
            Puspa membaginya ke tim.
          </p>
        ) : (
          jobs.map((job) => {
            const text =
              typeof job.payload?.text === "string"
                ? (job.payload.text as string)
                : (job.result ?? job.kind);
            return (
              <div className="msg" key={job.id}>
                <div className="msg-head">
                  <AgentAvatarPhoto
                    state={job.status === "running" ? "working" : "idle"}
                    photoSrc={avatarPhotoSrc(avatar)}
                    size={30}
                  />
                  <span className="who">Puspa</span>
                </div>
                <div className="bubble">
                  {text}
                  <div style={{ marginTop: 8 }}>
                    <span className="tag gray">{job.status}</span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
      <ScrollToBottomButton />
      <form className="composer" onSubmit={onSend}>
        <div className="bar">
          <input
            type="text"
            placeholder="Hold to speak, tap to type"
            aria-label="Pesan ke ruang"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" className="send-fab" disabled={busy || !draft.trim()} aria-label="Kirim">
            <Icon name="send" size={20} />
          </button>
        </div>
      </form>
      {toast}
    </div>
  );
}
