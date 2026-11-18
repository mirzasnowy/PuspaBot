import { useEffect, useRef, useState } from "react";
import { parseSseEvent, sendChat, type Bootstrap } from "../api/client";
import {
  appendMessage,
  applySseEvent,
  initialChatState,
  localEcho,
  reconcileEcho,
} from "../state/chat";
import { WorkStrip } from "../components/WorkStrip";

export function ChatScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const [state, setState] = useState(() => initialChatState(bootstrap.messages ?? []));
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

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

  async function onSend(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || state.sending) return;
    setDraft("");
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

  return (
    <div>
      <h1>Chat</h1>
      <p className="muted">
        Group chat: kamu, Puspa, dan agenmu di satu thread. Sub-agen terlipat secara default;
        pesanmu selalu menyela tugas yang berjalan.
      </p>
      <WorkStrip office={state.liveOffice} connected={state.liveConnected} />
      <div aria-live="polite">
        {state.messages.length === 0 ? (
          <p className="muted">Belum ada pesan. Sapa Puspa dulu.</p>
        ) : (
          state.messages.map((m, i) => (
            <div key={`${m.created_at}-${i}`} className="card">
              <strong>{m.sender}</strong>: {m.text}
            </div>
          ))
        )}
        <div ref={bottomRef} />
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
      <p className="muted">
        Kirim foto untuk cek harga (fase 3). Angka bermakna punya tombol Bukti di panelnya.
      </p>
    </div>
  );
}
