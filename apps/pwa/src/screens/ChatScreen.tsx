import { useEffect, useRef, useState } from "react";
import {
  parseSseEvent,
  sendChat,
  type Bootstrap,
} from "../api/client";
import { resolveSenderName, requiredAgentName } from "../state/names";
import {
  appendMessage,
  applySseEvent,
  initialChatState,
  localEcho,
  reconcileEcho,
} from "../state/chat";
import { VoiceRecorder } from "../components/VoiceRecorder";
import { ReceiptUpload } from "../components/ReceiptUpload";
import { TaskProgressRail } from "../components/TaskProgressRail";
import { WorkStrip } from "../components/WorkStrip";
import { ChatCard } from "../components/ChatCard";
import { ChatWidget } from "../components/ChatWidget";
import { AgentAvatarPhoto, useAvatarStatus } from "../components/AgentAvatar";
import { DynamicIsland } from "../components/DynamicIsland";
import { ScrollToBottomButton } from "../components/ScrollToBottom";
import { avatarPhotoSrc } from "../state/avatar";
import { RowItem } from "./space/shared";
import { Icon } from "../components/icons";
import { officeAgents } from "../state/office";
import { speakReply } from "../state/speech";
import { useLanguage } from "../state/language";

export function ChatScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const { t, locale } = useLanguage();
  const [state, setState] = useState(() => initialChatState(bootstrap.messages ?? []));
  // P-03: text shared into the PWA from another app arrives via the
  // bootstrap as shared_text; it prefills the draft so the user can
  // review it and tap Send. Nothing is sent automatically.
  const [draft, setDraft] = useState(bootstrap.shared_text ?? "");
  const bottomRef = useRef<HTMLDivElement>(null);
  // V2-27: the bot's own name comes from the bootstrap agents list
  // (the required agent), never a hardcoded string, so a renamed
  // bot is called by its own name on every message.
  const botName = requiredAgentName(bootstrap.agents ?? []);
  // The avatar photo (Nero, 24 Nov): the island and the bubbles
  // show the SAME photo — the uploaded photo, a built-in flower,
  // or the default Puspa logo — fetched once here.
  const avatar = useAvatarStatus();
  // V2-30: per-message speak status for the "Dengarkan" button
  // on assistant replies (Edge TTS by default, browser voice as
  // fallback). Speaking is always a tap, never automatic:
  // browsers block autoplay without a user gesture, and a reply
  // the user did not ask to hear should stay silent.
  const [speakStatus, setSpeakStatus] = useState<Record<string, string>>({});

  async function speakMessage(key: string, text: string) {
    setSpeakStatus((prev) => ({ ...prev, [key]: t("ui.chat.reading_aloud", "Membacakan…") }));
    const result = await speakReply(text, { speechSynthesis: window.speechSynthesis });
    setSpeakStatus((prev) => ({
      ...prev,
      [key]:
        result.engine === "none"
          ? (result.error ?? t("ui.chat.read_failed", "Gagal membacakan."))
          : result.fallback
            ? t("ui.chat.read_browser", "Dibacakan dengan suara browser.")
            : t("ui.chat.read_done", "Dibacakan."),
    }));
  }

  useEffect(() => {
    const source = new EventSource("/sse");
    source.onmessage = (event) => {
      const parsed = parseSseEvent(event.data);
      if (parsed) setState((prev) => applySseEvent(prev, parsed));
    };
    source.onerror = () => setState((prev) => ({ ...prev, liveConnected: false }));
    return () => source.close();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [state.messages.length]);

  async function sendText(text: string) {
    if (!text || state.sending) return;
    setState((prev) => ({
      ...appendMessage(prev, localEcho(text)),
      sending: true,
      error: null,
    }));
    try {
      const reply = await sendChat(text);
      setState((prev) => ({ ...reconcileEcho(prev, reply.message), sending: false }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        sending: false,
        error: err instanceof Error ? err.message : t("ui.chat.send_failed", "Gagal mengirim pesan."),
      }));
    }
  }

  async function onSend(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || state.sending) return;
    setDraft("");
    await sendText(text);
  }

  // The island's live activity line: sending -> typing; a
  // working agent in the live office projection -> its current
  // task; otherwise idle (no activity line at all).
  const workingAgent = officeAgents(state.liveOffice).find(
    (agent) => agent.status === "working",
  );
  const working = state.sending || Boolean(workingAgent);
  const activity = state.sending
    ? t("ui.chat.typing", "Mengetik…")
    : (workingAgent?.last_task ?? t("ui.island.working", "Bekerja…"));

  function dayLabel(createdAt: string | number): string {
    const date = new Date(createdAt);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(locale === "en" ? "en-US" : "id-ID", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  }

  const pendingApprovals = (bootstrap.approvals ?? []).filter(
    (approval) => approval.status === "pending",
  ).length;

  return (
    <div className="page">
      <DynamicIsland
        name={botName}
        working={working}
        activity={working ? activity : null}
        avatar={avatar}
        office={state.liveOffice}
      />
      {/* Approvals reach the user through chat, not a bell (Nero,
          23 Nov): this pill appears in the thread itself when
          something waits, and the agent also asks inside the
          thread via approval cards. */}
      {pendingApprovals > 0 && (
        <div style={{ marginTop: 4 }}>
          <RowItem
            href="/antrean"
            icon="alert"
            title={t("ui.chat.approvals_pending", "{count} persetujuan menunggu", { count: pendingApprovals })}
            meta={<span className="muted">{t("ui.chat.approvals_note", "Puspa minta persetujuanmu")}</span>}
          />
        </div>
      )}
      <WorkStrip office={state.liveOffice} connected={state.liveConnected} />
      <div className="chat-layout">
        <div className="chat-thread thread" aria-live="polite">
          {state.messages.length === 0 ? (
            <p className="muted">{t("ui.chat.empty", "Belum ada pesan. Sapa {bot} dulu.", { bot: botName })}</p>
          ) : (
            state.messages.map((m, i) => {
              const isMe = m.sender === "users";
              const prev = state.messages[i - 1];
              const showDate =
                !prev || dayLabel(prev.created_at) !== dayLabel(m.created_at);
              return (
                <div key={`${m.created_at}-${i}`}>
                  {showDate && dayLabel(m.created_at) && (
                    <div className="date-sep">{dayLabel(m.created_at)}</div>
                  )}
                  <div className={`msg${isMe ? " me" : ""}`}>
                    <div className="msg-head">
                      {!isMe && (
                        <span className="mav">
                          <AgentAvatarPhoto
                            state={working ? "working" : "idle"}
                            photoSrc={avatarPhotoSrc(avatar)}
                            size={30}
                          />
                        </span>
                      )}
                      <span className="who">
                        {resolveSenderName(m.sender, { botName })}
                      </span>
                    </div>
                    <div className="bubble">
                      {m.text}
                      {!isMe ? (
                        <div>
                          <button
                            type="button"
                            onClick={() =>
                              void speakMessage(`${m.created_at}-${i}`, m.text)
                            }
                          >
                            {t("ui.chat.listen", "Dengarkan")}
                          </button>
                          {speakStatus[`${m.created_at}-${i}`] ? (
                            <span role="status" className="muted">
                              {" "}
                              {speakStatus[`${m.created_at}-${i}`]}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                    {m.card ? (
                      <ChatCard
                        card={m.card}
                        onPickOption={(option) => void sendText(option.label)}
                      />
                    ) : null}
                    {m.widget ? (
                      <ChatWidget
                        widget={m.widget}
                        onNewMessage={(message) =>
                          setState((prev) => appendMessage(prev, message))
                        }
                      />
                    ) : null}
                  </div>
                </div>
              );
            })
          )}
          {state.sending && (
            <div className="msg">
              <div className="msg-head">
                <span className="mav">
                  <AgentAvatarPhoto
                    state="working"
                    photoSrc={avatarPhotoSrc(avatar)}
                    size={30}
                  />
                </span>
                <span className="who">{botName}</span>
              </div>
              <div className="bubble">
                <span className="typing" aria-label={t("ui.chat.typing_label", "Mengetik")}>
                  <i />
                  <i />
                  <i />
                </span>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        <TaskProgressRail office={state.liveOffice} connected={state.liveConnected} />
      </div>
      <ScrollToBottomButton />
      {state.error ? <p role="alert" className="error">{state.error}</p> : null}
      <form onSubmit={onSend} className="composer">
        <div className="bar">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t("ui.chat.composer_placeholder", "Tahan untuk bicara, ketuk untuk mengetik")}
            aria-label={t("ui.chat.composer_label", "Pesan ke {bot}", { bot: botName })}
            disabled={state.sending}
          />
          <button
            type="submit"
            className="send-fab"
            disabled={state.sending || draft.trim().length === 0}
            aria-label={t("ui.chat.send", "Kirim")}
          >
            <Icon name="send" size={20} />
          </button>
        </div>
      </form>
      <VoiceRecorder onSent={(message) => setState((prev) => appendMessage(prev, message))} />
      <ReceiptUpload onRecorded={(message) => setState((prev) => appendMessage(prev, message))} />
      <p className="muted">
        Kirim foto struk untuk dicatat sebagai pengeluaran. Angka bermakna punya tombol Bukti di
        panelnya.{" "}
        <a href="/studio" target="_blank" rel="noreferrer">
          Buat Desain di Kanvas
        </a>
      </p>
    </div>
  );
}
