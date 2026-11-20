/**
 * Morning screen (P-07, plan 12.8): the morning summary plus
 * notes-to-actions.
 *
 * The summary shows the day's schedule, bill candidates, open
 * tasks, national holidays, and prayer times — each section with
 * its own honest status, so a source that is not connected or not
 * reachable says so instead of the screen pretending the data
 * exists. Bill candidates are keyword matches from Gmail, shown
 * as unverified: the user opens the email to confirm the amount
 * and due date. Imsak is shown only during Ramadan.
 *
 * Below the summary, a note (pasted text, or a recorded voice
 * message) becomes a summary plus a list of actions with owners;
 * every action is a tracked task. Voice notes are transcribed by
 * the P-03 pipeline (transcriber still a stub — see
 * /api/voice/status) and the audio is deleted right away.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createMorningNoteFromText,
  fetchMorningNotes,
  fetchMorningStatus,
  fetchMorningSummary,
  uploadMorningVoiceNote,
  type MorningNote,
  type MorningStatus,
  type MorningSummary,
} from "../api/morning";

const NOTE_SOURCES = [
  { value: "text", label: "Catatan teks" },
  { value: "meeting", label: "Rapat" },
  { value: "lecture", label: "Kuliah" },
  { value: "study", label: "Pengajian" },
] as const;

function recordingSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined"
  );
}

function pickMimeType(): string | undefined {
  for (const type of ["audio/webm", "audio/mp4", "audio/ogg"]) {
    if (window.MediaRecorder.isTypeSupported(type)) return type;
  }
  return undefined;
}

export function MorningScreen() {
  const [summary, setSummary] = useState<MorningSummary | null>(null);
  const [status, setStatus] = useState<MorningStatus | null>(null);
  const [notes, setNotes] = useState<MorningNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [date, setDate] = useState("");
  const [city, setCity] = useState("Jakarta");
  const [noteText, setNoteText] = useState("");
  const [noteSource, setNoteSource] = useState<string>("meeting");
  const [noteTitle, setNoteTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [loadedSummary, loadedStatus, loadedNotes] = await Promise.all([
        fetchMorningSummary(date || undefined, city || undefined),
        fetchMorningStatus(),
        fetchMorningNotes(),
      ]);
      setSummary(loadedSummary);
      setStatus(loadedStatus);
      setNotes(loadedNotes.notes);
    } catch (err) {
      setError(
        `Gagal memuat ringkasan pagi: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setLoading(false);
    }
  }, [date, city]);

  useEffect(() => {
    let cancelled = false;
    load().catch(() => {
      if (!cancelled) setError("Gagal memuat ringkasan pagi.");
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function submitNote(event: React.FormEvent) {
    event.preventDefault();
    if (!noteText.trim()) return;
    setSaving(true);
    setSaveError("");
    setSaveMessage("");
    try {
      const created = await createMorningNoteFromText(
        noteText,
        noteSource,
        noteTitle,
      );
      setSaveMessage(
        `Catatan tersimpan: ${created.actions.length} tindakan dibuat sebagai tugas.`,
      );
      setNoteText("");
      setNoteTitle("");
      const loadedNotes = await fetchMorningNotes();
      setNotes(loadedNotes.notes);
    } catch (err) {
      setSaveError(
        `Gagal menyimpan catatan: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setSaving(false);
    }
  }

  function stopStream() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }

  async function startRecording() {
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
        void sendVoiceNote(blob);
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      setSaveError("Tidak bisa mulai merekam: mikrofon tidak tersedia.");
    }
  }

  function stopRecording() {
    recorderRef.current?.stop();
    setRecording(false);
  }

  async function sendVoiceNote(blob: Blob) {
    setSaving(true);
    setSaveError("");
    setSaveMessage("");
    try {
      const created = await uploadMorningVoiceNote(blob, noteSource, noteTitle);
      setSaveMessage(
        `Pesan suara tersimpan (${created.actions.length} tindakan). Transkrip: ${created.transcript}`,
      );
      const loadedNotes = await fetchMorningNotes();
      setNotes(loadedNotes.notes);
    } catch (err) {
      setSaveError(
        `Gagal mengirim pesan suara: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setSaving(false);
    }
  }

  const prayer = summary?.prayer ?? null;
  const timings = prayer?.timings;

  return (
    <div>
      <h1>Ringkasan Pagi</h1>
      <p className="muted">
        Jadwal, tagihan, tugas, libur nasional, dan jam salat hari ini. Setiap
        bagian menunjukkan sumbernya — kalau sumbernya belum tersambung, layar
        ini bilang begitu, bukan mengarang data.
      </p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void load();
        }}
      >
        <label>
          Tanggal{" "}
          <input
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        </label>{" "}
        <label>
          Kota{" "}
          <input
            type="text"
            value={city}
            onChange={(event) => setCity(event.target.value)}
          />
        </label>{" "}
        <button type="submit" disabled={loading}>
          Muat
        </button>
      </form>

      {loading ? <p>Memuat…</p> : null}
      {error ? <p role="alert">{error}</p> : null}

      {summary ? (
        <>
          <h2>Jadwal hari ini</h2>
          {summary.schedule.events.length === 0 ? (
            <p className="muted">Tidak ada jadwal hari ini.</p>
          ) : (
            <ul>
              {summary.schedule.events.map((event, index) => (
                <li key={`${event.summary}-${index}`}>
                  {event.summary} — {event.start}
                  {event.location ? ` (${event.location})` : ""}
                </li>
              ))}
            </ul>
          )}

          <h2>Tagihan</h2>
          {summary.bills.candidates.length === 0 ? (
            <p className="muted">Tidak ada kandidat tagihan dari email.</p>
          ) : (
            <ul>
              {summary.bills.candidates.map((bill) => (
                <li key={bill.message_id}>
                  {bill.subject}
                  {bill.due_hint ? ` — jatuh tempo: ${bill.due_hint}` : ""}
                  <br />
                  <span className="muted">
                    Belum terverifikasi — buka emailnya untuk memastikan jumlah
                    dan tanggal jatuh tempo.
                  </span>
                </li>
              ))}
            </ul>
          )}

          <h2>Tugas terbuka</h2>
          {summary.tasks.open.length === 0 ? (
            <p className="muted">Tidak ada tugas terbuka.</p>
          ) : (
            <ul>
              {summary.tasks.open.map((task) => (
                <li key={task.task_id}>{task.purpose}</li>
              ))}
            </ul>
          )}

          <h2>Libur nasional</h2>
          {summary.holidays.today.length === 0 &&
          summary.holidays.upcoming.length === 0 ? (
            <p className="muted">Tidak ada libur nasional hari ini atau 30 hari ke depan.</p>
          ) : (
            <ul>
              {summary.holidays.today.map((holiday) => (
                <li key={holiday.date}>
                  Hari ini: {holiday.local_name} ({holiday.date})
                </li>
              ))}
              {summary.holidays.upcoming.map((holiday) => (
                <li key={holiday.date}>
                  {holiday.local_name} — {holiday.date}
                </li>
              ))}
            </ul>
          )}

          <h2>Jam salat {prayer ? `— ${prayer.city}` : ""}</h2>
          {prayer && timings ? (
            <>
              <ul>
                <li>Subuh: {timings.fajr}</li>
                <li>Terbit: {timings.sunrise}</li>
                <li>Zuhur: {timings.dhuhr}</li>
                <li>Asar: {timings.asr}</li>
                <li>Magrib: {timings.maghrib}</li>
                <li>Isya: {timings.isha}</li>
                {timings.imsak ? <li>Imsak: {timings.imsak}</li> : null}
              </ul>
              <p className="muted">
                {prayer.imsak_note ?? `Hijriah: ${prayer.hijri.date} ${prayer.hijri.month} ${prayer.hijri.year} H. Metode: ${prayer.method_name}.`}
              </p>
            </>
          ) : (
            <p className="muted">Jam salat tidak tersedia.</p>
          )}

          <h2>Status sumber</h2>
          <ul>
            {(
              [
                ["Kalender", summary.source_status.calendar],
                ["Gmail", summary.source_status.gmail],
                ["Tugas", summary.source_status.tasks],
                ["Libur", summary.source_status.holidays],
                ["Jam salat", summary.source_status.prayer],
              ] as const
            ).map(([label, source]) => (
              <li key={label}>
                {label}: {source.status} — {source.detail}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {status ? (
        <p className="muted">
          Transkripsi suara: {status.transcriber.engine}
          {status.transcriber.engine_selected ? "" : " (mesin belum dipilih — masih stub)"}
        </p>
      ) : null}

      <h2>Catatan jadi aksi</h2>
      <p className="muted">
        Tempel catatan rapat, kuliah, atau pengajian — atau rekam pesan suara —
        dan Puspa mengubahnya jadi ringkasan plus daftar tindakan dengan
        penanggung jawab. Setiap tindakan tersimpan sebagai tugas yang bisa
        dilacak.
      </p>
      <form onSubmit={submitNote}>
        <label>
          Jenis catatan{" "}
          <select
            value={noteSource}
            onChange={(event) => setNoteSource(event.target.value)}
          >
            {NOTE_SOURCES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          Judul{" "}
          <input
            type="text"
            value={noteTitle}
            onChange={(event) => setNoteTitle(event.target.value)}
            placeholder="Pengajian malam Jumat"
          />
        </label>
        <br />
        <label>
          Isi catatan
          <br />
          <textarea
            value={noteText}
            onChange={(event) => setNoteText(event.target.value)}
            rows={5}
            cols={60}
            placeholder={"- @Budi tolong hubungi ustadz besok\n- Bayar iuran kas"}
          />
        </label>
        <br />
        <button type="submit" disabled={saving || !noteText.trim()}>
          Simpan jadi tindakan
        </button>{" "}
        {recordingSupported() ? (
          recording ? (
            <button type="button" onClick={stopRecording}>
              Berhenti merekam
            </button>
          ) : (
            <button type="button" onClick={() => void startRecording()} disabled={saving}>
              Rekam pesan suara
            </button>
          )
        ) : (
          <span className="muted">Perekaman suara tidak didukung browser ini.</span>
        )}
      </form>
      {saveMessage ? <p>{saveMessage}</p> : null}
      {saveError ? <p role="alert">{saveError}</p> : null}

      <h3>Catatan tersimpan</h3>
      {notes.length === 0 ? (
        <p className="muted">Belum ada catatan.</p>
      ) : (
        <ul>
          {notes.map((note) => (
            <li key={note.note_id}>
              <strong>{note.title || note.summary}</strong> ({note.source})
              <br />
              <span className="muted">{note.summary}</span>
              <ul>
                {note.actions.map((action) => (
                  <li key={action.task_id}>
                    {action.text} — {action.owner ?? "Belum ada penanggung jawab"}
                    {action.due_hint ? ` — ${action.due_hint}` : ""}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
