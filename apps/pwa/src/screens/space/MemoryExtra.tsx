/**
 * Memori sub-screens, Space port: Edit Pengetahuan, Persona,
 * Ekspor Memori, Kontrol Data.
 *
 * All wired to real APIs: facts from GET /api/memory, edits via
 * PATCH /api/memory/facts/{id}, deletes via DELETE, persona
 * files via /api/personas, exports via GET /api/memory/export
 * and GET /api/chat/messages, reset via POST
 * /api/memory/facts/{id}/forget per fact. Impor Memori stays
 * hidden (it does not exist in the product).
 */
import { useEffect, useState } from "react";
import {
  activatePersona,
  deleteMemoryFact,
  fetchChatMessages,
  fetchMemoryFacts,
  fetchPersona,
  fetchPersonas,
  forgetMemoryFact,
  updateMemoryFact,
  updatePersona,
  type MemoryFactItem,
  type PersonaDetail,
  type PersonaSummary,
} from "../../api/client";
import { Icon } from "../../components/icons";
import { AgentAvatarPhoto, useAvatarStatus } from "../../components/AgentAvatar";
import { AgentAvatarChoices } from "../../components/AgentAvatarChoices";
import { avatarPhotoSrc } from "../../state/avatar";
import { GroupCard, PageHead, RowItem, useToast } from "./shared";

export function download(filename: string, text: string, type: string) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function EditKnowledgeScreen() {
  const [facts, setFacts] = useState<MemoryFactItem[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const { show, toast } = useToast();

  useEffect(() => {
    let cancelled = false;
    fetchMemoryFacts()
      .then((result) => {
        if (!cancelled) setFacts(result.facts);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Gagal memuat fakta.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const groups = new Map<string, MemoryFactItem[]>();
  for (const fact of facts) {
    const list = groups.get(fact.key) ?? [];
    list.push(fact);
    groups.set(fact.key, list);
  }

  async function saveEdit(fact: MemoryFactItem) {
    if (busy || !draft.trim()) return;
    setBusy(true);
    setError("");
    try {
      const updated = await updateMemoryFact(fact.id, draft.trim());
      setFacts((prev) => prev.map((f) => (f.id === fact.id ? updated : f)));
      setEditing(null);
      show("Fakta diperbarui — jawaban Puspa menyesuaikan.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan fakta.");
    } finally {
      setBusy(false);
    }
  }

  async function removeFact(fact: MemoryFactItem) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await deleteMemoryFact(fact.id);
      setFacts((prev) => prev.filter((f) => f.id !== fact.id));
      show("Fakta dihapus dari graf.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus fakta.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHead title="Edit Pengetahuan" back />
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      {facts.length === 0 && !error ? (
        <p className="foot-note">
          Belum ada fakta tersimpan. Fakta muncul di sini setelah Puspa
          mempelajarinya dari chat.
        </p>
      ) : (
        <GroupCard>
          {[...groups.entries()].map(([key, items]) => (
            <div key={key}>
              <div className="group-label">{key}</div>
              {items.map((fact) => (
                <div key={fact.id}>
                  {editing === fact.id ? (
                    <div style={{ padding: "10px 6px" }}>
                      <div className="field" style={{ marginTop: 0 }}>
                        <label htmlFor={`fact-${fact.id}`}>Isi fakta</label>
                        <textarea
                          id={`fact-${fact.id}`}
                          rows={3}
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                        />
                      </div>
                      <div style={{ display: "flex", gap: 9, marginTop: 10 }}>
                        <button
                          type="button"
                          className="btn ghost"
                          style={{ color: "var(--red)" }}
                          onClick={() => void removeFact(fact)}
                          disabled={busy}
                        >
                          Hapus
                        </button>
                        <span style={{ flex: 1 }} />
                        <button
                          type="button"
                          className="btn ghost"
                          onClick={() => setEditing(null)}
                        >
                          Batal
                        </button>
                        <button
                          type="button"
                          className="btn primary"
                          onClick={() => void saveEdit(fact)}
                          disabled={busy}
                        >
                          Simpan
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="row-item"
                      onClick={() => {
                        setEditing(fact.id);
                        setDraft(fact.content ?? "");
                      }}
                    >
                      <span className="sq-ic">
                        <Icon name="edit" size={22} />
                      </span>
                      <span className="grow">
                        <span className="t" style={{ whiteSpace: "normal" }}>
                          {fact.content ?? "(kosong)"}
                        </span>
                      </span>
                      <Icon name="chev" size={17} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </GroupCard>
      )}
      {toast}
    </div>
  );
}

export function PersonaScreen() {
  const [list, setList] = useState<PersonaSummary[]>([]);
  const [active, setActive] = useState<PersonaDetail | null>(null);
  const [soul, setSoul] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { show, toast } = useToast();
  // The agent photo (Nero, 24 Nov): the same photo the island
  // and the bubbles show.
  const avatar = useAvatarStatus();

  useEffect(() => {
    let cancelled = false;
    fetchPersonas()
      .then(async (result) => {
        if (cancelled) return;
        setList(result.personas);
        const detail = await fetchPersona(result.active);
        if (!cancelled) {
          setActive(detail);
          setSoul(detail.soul);
          setName(detail.name);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Gagal memuat persona.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    if (!active || busy) return;
    if (!name.trim()) {
      setError("Nama agent tidak boleh kosong.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = await updatePersona(active.slug, {
        name: name.trim(),
        soul,
      });
      setActive(saved);
      setName(saved.name);
      show("Persona disimpan. Puspa memakai persona baru mulai pesan berikutnya.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan persona.");
    } finally {
      setBusy(false);
    }
  }

  async function activate(slug: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await activatePersona(slug);
      setList(result.personas);
      const detail = await fetchPersona(result.active);
      setActive(detail);
      setSoul(detail.soul);
      setName(detail.name);
      show(`Persona ${detail.name} aktif.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengaktifkan persona.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHead title="Persona" back />
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: "flex", gap: 11, alignItems: "center" }}>
          <AgentAvatarPhoto
            state="idle"
            photoSrc={avatarPhotoSrc(avatar)}
            size={52}
          />
          <div>
            <b style={{ fontSize: 16.5 }}>{active?.name ?? "Puspa"}</b>
          </div>
          <span className="tag green" style={{ marginLeft: "auto" }}>
            Aktif
          </span>
        </div>
        <div className="field">
          <label htmlFor="personaName">Nama agent</label>
          <input
            id="personaName"
            className="input"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Puspa"
          />
        </div>
        <div className="field">
          <label>Foto agent</label>
          <p className="muted" style={{ margin: "4px 0 6px" }}>
            Foto yang sama dipakai di header chat dan di bubble chat.
          </p>
          <AgentAvatarChoices />
        </div>
        <div className="field">
          <label htmlFor="personaText">Isi persona</label>
          <textarea
            id="personaText"
            rows={7}
            value={soul}
            onChange={(e) => setSoul(e.target.value)}
          />
        </div>
        <div style={{ display: "flex", gap: 9, marginTop: 14 }}>
          <button
            type="button"
            className="btn primary"
            style={{ flex: 1 }}
            onClick={() => void save()}
            disabled={busy}
          >
            Simpan Persona
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              if (active) {
                setSoul(active.soul);
                setName(active.name);
              }
            }}
          >
            Reset
          </button>
        </div>
      </div>
      {list.length > 1 ? (
        <GroupCard label="Persona lain">
          {list
            .filter((p) => !p.is_active)
            .map((p) => (
              <RowItem
                key={p.slug}
                icon="profile"
                title={p.name}
                onClick={() => void activate(p.slug)}
              />
            ))}
        </GroupCard>
      ) : null}
      {toast}
    </div>
  );
}

export function ExportScreen() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { show, toast } = useToast();

  async function exportChat() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await fetchChatMessages();
      const text = result.messages
        .map((m) => `${m.sender === "users" ? "Mirza" : "Puspa"}: ${m.text}`)
        .join("\n");
      download("percakapan.md", text, "text/markdown");
      show("percakapan.md diunduh.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengunduh percakapan.");
    } finally {
      setBusy(false);
    }
  }

  async function exportAgent() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await fetchPersonas();
      download("data-agen.json", JSON.stringify(result, null, 2), "application/json");
      show("Data agen diunduh (memori, persona, dan pengaturan).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengunduh data agen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHead title="Ekspor Memori" back />
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <div className="card flat" style={{ marginTop: 16, padding: "6px 14px" }}>
        <a
          className="row-item"
          href="/api/memory/export"
          download="memory.json"
          style={{ textDecoration: "none", color: "inherit" }}
        >
          <span className="sq-ic">
            <Icon name="download" size={22} />
          </span>
          <span className="grow">
            <span className="t">Unduh Memori</span>
          </span>
          <span className="tag gray">memory.json</span>
          <Icon name="chev" size={17} />
        </a>
        <button type="button" className="row-item" onClick={() => void exportChat()} disabled={busy}>
          <span className="sq-ic">
            <Icon name="chat" size={22} />
          </span>
          <span className="grow">
            <span className="t">Unduh Percakapan</span>
          </span>
          <span className="tag gray">percakapan.md</span>
          <Icon name="chev" size={17} />
        </button>
        <button type="button" className="row-item" onClick={() => void exportAgent()} disabled={busy}>
          <span className="sq-ic">
            <Icon name="box" size={22} />
          </span>
          <span className="grow">
            <span className="t">Unduh Data Agen</span>
          </span>
          <span className="tag gray">data-agen.json</span>
          <Icon name="chev" size={17} />
        </button>
      </div>
      {toast}
    </div>
  );
}

export function DataControlScreen() {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { show, toast } = useToast();

  async function resetAll() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await fetchMemoryFacts();
      let forgotten = 0;
      for (const fact of result.facts) {
        const done = await forgetMemoryFact(fact.id);
        forgotten += done.forgotten;
      }
      setConfirming(false);
      show(`Reset selesai: ${forgotten} fakta dilupakan.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mereset Puspa.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHead title="Kontrol Data" back />
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <div className="card flat" style={{ marginTop: 16, padding: "6px 14px" }}>
        <RowItem href="/chat" icon="chat" title="Chat Diarsipkan" />
      </div>
      <div className="card flat" style={{ marginTop: 14, padding: "6px 14px" }}>
        {confirming ? (
          <div style={{ padding: "12px 6px" }}>
            <p style={{ margin: "0 0 12px" }}>
              Reset menghapus semua fakta memori Puspa (tidak bisa dibatalkan).
              Percakapan tetap ada sebagai riwayat.
            </p>
            <div style={{ display: "flex", gap: 9 }}>
              <button
                type="button"
                className="btn ghost"
                style={{ flex: 1, color: "var(--red)" }}
                onClick={() => void resetAll()}
                disabled={busy}
              >
                {busy ? "Mereset…" : "Ya, reset Puspa"}
              </button>
              <button
                type="button"
                className="btn primary"
                style={{ flex: 1 }}
                onClick={() => setConfirming(false)}
              >
                Batal
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className="row-item" onClick={() => setConfirming(true)}>
            <span className="sq-ic">
              <Icon name="trash" size={22} />
            </span>
            <span className="grow">
              <span className="t" style={{ color: "var(--red)" }}>
                Reset Puspa
              </span>
            </span>
            <Icon name="chev" size={17} />
          </button>
        )}
      </div>
      {toast}
    </div>
  );
}
