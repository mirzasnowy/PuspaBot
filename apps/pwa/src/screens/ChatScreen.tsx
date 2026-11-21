import { useEffect, useRef, useState } from "react";
import {
  fetchAgentAvatar,
  parseSseEvent,
  sendChat,
  type AvatarStatus,
  type Bootstrap,
} from "../api/client";
import { avatarPhotoSrc, avatarStatus } from "../state/avatar";
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
import { AgentAvatar } from "../components/AgentAvatar";

export function ChatScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const [state, setState] = useState(() => initialChatState(bootstrap.messages ?? []));
  // P-03: text shared into the PWA from another app arrives via the
  // bootstrap as shared_text; it prefills the draft so the user can
  // review it and tap Send. Nothing is sent automatically.
  const [draft, setDraft] = useState(bootstrap.shared_text ?? "");
  const bottomRef = useRef<HTMLDivElement>(null);
  // V2-26: the agent's photo (default logo while none is set).
  // Loaded once; the photo only changes from Settings, so no
  // polling is needed here.
  const [avatar, setAvatar] = useState<AvatarStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAgentAvatar()
      .then((loaded) => {
        if (!cancelled) setAvatar(loaded);
      })
      .catch(() => {
        // No photo info: the default logo applies.
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        error: err instanceof Error ? err.message : "Gagal mengirim pesan.",
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

  return (
    <div>
      <h1>Chat</h1>
      <AgentAvatar
        state={avatarStatus(state.sending, state.liveOffice)}
        photoSrc={avatarPhotoSrc(avatar)}
      />
      <p className="muted">
        Group chat: kamu, Puspa, dan agenmu di satu thread. Sub-agen terlipat secara default;
        pesanmu selalu menyela tugas yang berjalan.
      </p>
      <WorkStrip office={state.liveOffice} connected={state.liveConnected} />
      <div className="chat-layout">
        <div className="chat-thread" aria-live="polite">
          {state.messages.length === 0 ? (
            <p className="muted">Belum ada pesan. Sapa Puspa dulu.</p>
          ) : (
            state.messages.map((m, i) => (
              <div key={`${m.created_at}-${i}`} className="card">
                <strong>{m.sender}</strong>: {m.text}
                {m.card ? (
                  <ChatCard card={m.card} onPickOption={(option) => void sendText(option.label)} />
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
            ))
          )}
          <div ref={bottomRef} />
        </div>
        <TaskProgressRail office={state.liveOffice} connected={state.liveConnected} />
      </div>
      {state.error ? <p role="alert" className="error">{state.error}</p> : null}
      <form onSubmit={onSend} className="composer">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Tulis pesan ke Puspa..."
          aria-label="Pesan ke Puspa"
          disabled={state.sending}
        />
        <button type="submit" disabled={state.sending || draft.trim().length === 0}>
          {state.sending ? "Mengirim…" : "Kirim"}
        </button>
      </form>
      <VoiceRecorder onSent={(message) => setState((prev) => appendMessage(prev, message))} />
      <ReceiptUpload onRecorded={(message) => setState((prev) => appendMessage(prev, message))} />
      <p className="muted">
        Kirim foto untuk cek harga (fase 3). Angka bermakna punya tombol Bukti di panelnya.
      </p>
    </div>
  );
}
