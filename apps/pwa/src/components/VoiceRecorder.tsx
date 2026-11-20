/**
 * Voice message recorder for the Chat screen (P-03, plan 12.4).
 *
 * Flow, always in this order: record -> upload -> transcribe ->
 * show the transcript for confirmation -> send on confirm, or
 * discard. Nothing is ever sent from audio alone: the transcript
 * is the message, the user may edit it first, and the server
 * deletes the recording right after transcribing it.
 *
 * Recording needs a microphone and a browser with MediaRecorder
 * (Android Chrome and iOS Safari 14.5+); when either is missing
 * the button stays disabled and says so instead of failing on tap.
 */
import { useRef, useState } from "react";
import {
  confirmVoice,
  discardVoice,
  uploadVoice,
  type ChatMessage,
} from "../api/client";

type Phase =
  | { kind: "idle" }
  | { kind: "recording" }
  | { kind: "uploading" }
  | { kind: "confirming"; pendingId: string; transcript: string }
  | { kind: "error"; message: string };

function recordingSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined"
  );
}

/** First MediaRecorder mime type this browser accepts, if any. */
function pickMimeType(): string | undefined {
  for (const type of ["audio/webm", "audio/mp4", "audio/ogg"]) {
    if (window.MediaRecorder.isTypeSupported(type)) return type;
  }
  return undefined;
}

export function VoiceRecorder({ onSent }: { onSent: (message: ChatMessage) => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [edited, setEdited] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const supported = recordingSupported();

  function stopStream() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }

  async function startRecording() {
    if (!supported) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const recorder = new MediaRecorder(stream, { mimeType: pickMimeType() });
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        stopStream();
        void sendForTranscription(blob);
      };
      recorderRef.current = recorder;
      recorder.start();
      setPhase({ kind: "recording" });
    } catch {
      setPhase({ kind: "error", message: "Mikrofon tidak bisa dipakai. Cek izin browser." });
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }

  async function sendForTranscription(blob: Blob) {
    setPhase({ kind: "uploading" });
    try {
      const result = await uploadVoice(blob);
      setEdited(result.transcript);
      setPhase({ kind: "confirming", pendingId: result.pending_id, transcript: result.transcript });
    } catch {
      setPhase({ kind: "error", message: "Transkripsi gagal. Coba rekam ulang." });
    }
  }

  async function confirm() {
    if (phase.kind !== "confirming") return;
    const text = edited.trim();
    if (!text) {
      setPhase({ kind: "error", message: "Transkrip kosong — tidak ada yang dikirim." });
      return;
    }
    try {
      const reply = await confirmVoice(phase.pendingId, text);
      setPhase({ kind: "idle" });
      setEdited("");
      onSent(reply.message);
    } catch {
      setPhase({ kind: "error", message: "Gagal mengirim pesan suara. Coba lagi." });
    }
  }

  async function discard() {
    if (phase.kind !== "confirming") return;
    try {
      await discardVoice(phase.pendingId);
    } catch {
      // The pending transcript expires server-side with the session;
      // a failed discard must not block the user from moving on.
    }
    setPhase({ kind: "idle" });
    setEdited("");
  }

  if (!supported) {
    return (
      <p className="muted">Pesan suara tidak didukung browser ini (butuh mikrofon + MediaRecorder).</p>
    );
  }

  return (
    <div>
      {phase.kind === "idle" && (
        <button type="button" onClick={() => void startRecording()}>
          Rekam pesan suara
        </button>
      )}
      {phase.kind === "recording" && (
        <div>
          <p>Merekam… ketuk Berhenti kalau sudah selesai.</p>
          <button type="button" onClick={stopRecording}>
            Berhenti
          </button>
        </div>
      )}
      {phase.kind === "uploading" && <p>Mentranskripsi rekaman…</p>}
      {phase.kind === "confirming" && (
        <div>
          <p>Cek dulu transkripnya sebelum dikirim — Puspa hanya membaca teks ini:</p>
          <textarea
            value={edited}
            onChange={(e) => setEdited(e.target.value)}
            aria-label="Transkrip pesan suara"
            rows={3}
          />
          <div>
            <button type="button" onClick={() => void confirm()}>
              Kirim transkrip
            </button>
            <button type="button" onClick={() => void discard()}>
              Buang
            </button>
          </div>
        </div>
      )}
      {phase.kind === "error" && (
        <div>
          <p role="alert" className="error">
            {phase.message}
          </p>
          <button type="button" onClick={() => setPhase({ kind: "idle" })}>
            Coba lagi
          </button>
        </div>
      )}
    </div>
  );
}
