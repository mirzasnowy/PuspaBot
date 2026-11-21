import { useCallback, useEffect, useState } from "react";
import {
  activatePersona,
  ApiError,
  createPersona,
  deletePersona,
  fetchPersona,
  fetchPersonas,
  updatePersona,
  type PersonaDetail,
  type PersonaList,
} from "../api/client";
import { SPEAKING_STYLES } from "../state/onboarding";

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Gagal memuat persona.";
}

function styleName(style: string | null): string {
  if (!style) return "";
  return SPEAKING_STYLES.find((s) => s.id === style)?.name ?? style;
}

/**
 * Switchable personas: one agent, several roles (work vs personal).
 * The list is a native radio group inside a fieldset — switching
 * only happens on the explicit "Aktifkan persona" submit, never on
 * radio change alone — and the create/edit forms use labeled
 * controls with an aria-live status line for the result.
 */
export function PersonasScreen() {
  const [list, setList] = useState<PersonaList | null>(null);
  const [loadError, setLoadError] = useState("");
  const [status, setStatus] = useState("");
  const [selectedSlug, setSelectedSlug] = useState("");
  const [newName, setNewName] = useState("");
  const [newSoul, setNewSoul] = useState("");
  const [newUser, setNewUser] = useState("");
  const [newStyle, setNewStyle] = useState("santai");
  const [editing, setEditing] = useState<PersonaDetail | null>(null);
  const [editSoul, setEditSoul] = useState("");
  const [editStyle, setEditStyle] = useState("santai");

  const refresh = useCallback(async () => {
    try {
      const data = await fetchPersonas();
      setList(data);
      setSelectedSlug(data.active);
      setLoadError("");
    } catch (error) {
      setLoadError(errorText(error));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleActivate(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedSlug) {
      setStatus("Pilih dulu persona yang mau diaktifkan.");
      return;
    }
    try {
      const data = await activatePersona(selectedSlug);
      setList(data);
      setStatus(`Persona aktif sekarang: ${selectedSlug}. Berlaku mulai giliran berikutnya.`);
    } catch (error) {
      setStatus(errorText(error));
    }
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    try {
      const created = await createPersona({
        name: newName,
        soul: newSoul || undefined,
        user: newUser || undefined,
        style: newStyle,
      });
      setNewName("");
      setNewSoul("");
      setNewUser("");
      setNewStyle("santai");
      await refresh();
      setStatus(`Persona ${created.name} dibuat.`);
    } catch (error) {
      setStatus(errorText(error));
    }
  }

  async function startEdit(slug: string) {
    try {
      const detail = await fetchPersona(slug);
      setEditing(detail);
      setEditSoul(detail.soul);
      setEditStyle(detail.style ?? "santai");
    } catch (error) {
      setStatus(errorText(error));
    }
  }

  async function handleEdit(event: React.FormEvent) {
    event.preventDefault();
    if (!editing) return;
    try {
      const updated = await updatePersona(editing.slug, {
        soul: editSoul,
        style: editStyle,
      });
      setEditing(null);
      await refresh();
      setStatus(`Persona ${updated.name} disimpan.`);
    } catch (error) {
      setStatus(errorText(error));
    }
  }

  async function handleDelete(slug: string, name: string) {
    if (
      !window.confirm(
        `Hapus persona ${name}? File persona ini dihapus dan tidak bisa dikembalikan.`,
      )
    ) {
      return;
    }
    try {
      await deletePersona(slug);
      if (editing?.slug === slug) setEditing(null);
      await refresh();
      setStatus(`Persona ${name} dihapus.`);
    } catch (error) {
      setStatus(errorText(error));
    }
  }

  return (
    <div>
      <h1>Persona</h1>
      <p className="muted">
        Satu agen, beberapa peran: misalnya satu persona untuk kerja dan satu untuk personal.
        Persona yang aktif dipakai agen di giliran berikutnya. Persona lama tetap tersimpan dan
        bisa diaktifkan lagi kapan saja.
      </p>
      {loadError ? (
        <p role="alert" className="error">
          {loadError}
        </p>
      ) : null}
      <p role="status" className="muted">
        {status}
      </p>

      <h2>Persona aktif</h2>
      {list === null ? (
        <p className="muted">Memuat daftar persona...</p>
      ) : (
        <form onSubmit={(event) => void handleActivate(event)} aria-labelledby="judul-aktif">
          <fieldset>
            <legend id="judul-aktif">Pilih persona yang aktif</legend>
            {list.personas.map((persona) => (
              <div key={persona.slug}>
                <input
                  type="radio"
                  id={`aktif-${persona.slug}`}
                  name="active"
                  value={persona.slug}
                  checked={selectedSlug === persona.slug}
                  onChange={() => setSelectedSlug(persona.slug)}
                />
                <label htmlFor={`aktif-${persona.slug}`}>
                  {persona.name}
                  {persona.is_default ? " (bawaan)" : ""}
                  {persona.style ? ` — ${styleName(persona.style)}` : ""}
                </label>
              </div>
            ))}
            <button type="submit">Aktifkan persona</button>
          </fieldset>
        </form>
      )}

      <h2>Buat persona baru</h2>
      <form onSubmit={(event) => void handleCreate(event)} aria-labelledby="judul-buat">
        <fieldset>
          <legend id="judul-buat">Persona baru</legend>
          <div>
            <label htmlFor="nama-baru">
              Nama persona <span aria-hidden="true">*</span>
            </label>
            <input
              id="nama-baru"
              name="name"
              type="text"
              required
              minLength={1}
              maxLength={64}
              autoComplete="off"
              autoCapitalize="words"
              spellCheck={false}
              aria-describedby="bantuan-nama"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
            />
            <p id="bantuan-nama" className="muted">
              Contoh: Kerja, Personal. Nama jadi pengenal folder persona.
            </p>
          </div>
          <div>
            <label htmlFor="prompt-baru">Prompt: cara agen bicara dan bersikap</label>
            <textarea
              id="prompt-baru"
              name="soul"
              rows={4}
              aria-describedby="bantuan-prompt"
              value={newSoul}
              onChange={(event) => setNewSoul(event.target.value)}
            />
            <p id="bantuan-prompt" className="muted">
              Kosongkan untuk memakai prompt bawaan. Contoh: jawab singkat, langsung ke
              intinya.
            </p>
          </div>
          <div>
            <label htmlFor="gaya-baru">Gaya balasan</label>
            <select
              id="gaya-baru"
              name="style"
              value={newStyle}
              onChange={(event) => setNewStyle(event.target.value)}
            >
              {SPEAKING_STYLES.map((style) => (
                <option key={style.id} value={style.id}>
                  {style.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="tentang-baru">Tentang kamu (untuk persona ini)</label>
            <textarea
              id="tentang-baru"
              name="user"
              rows={3}
              value={newUser}
              onChange={(event) => setNewUser(event.target.value)}
            />
          </div>
          <button type="submit">Buat persona</button>
        </fieldset>
      </form>

      <h2>Ubah persona</h2>
      {list === null ? (
        <p className="muted">Memuat daftar persona...</p>
      ) : (
        list.personas.map((persona) => (
          <div key={persona.slug}>
            <strong>{persona.name}</strong>
            {persona.is_default ? (
              <p className="muted">Persona bawaan (file di folder utama Puspa).</p>
            ) : null}
            {editing?.slug === persona.slug ? (
              <form
                onSubmit={(event) => void handleEdit(event)}
                aria-label={`Ubah persona ${persona.name}`}
              >
                <div>
                  <label htmlFor={`prompt-${persona.slug}`}>Prompt</label>
                  <textarea
                    id={`prompt-${persona.slug}`}
                    name="soul"
                    rows={3}
                    value={editSoul}
                    onChange={(event) => setEditSoul(event.target.value)}
                  />
                </div>
                <div>
                  <label htmlFor={`gaya-${persona.slug}`}>Gaya balasan</label>
                  <select
                    id={`gaya-${persona.slug}`}
                    name="style"
                    value={editStyle}
                    onChange={(event) => setEditStyle(event.target.value)}
                  >
                    {SPEAKING_STYLES.map((style) => (
                      <option key={style.id} value={style.id}>
                        {style.name}
                      </option>
                    ))}
                  </select>
                </div>
                <button type="submit">Simpan perubahan</button>
                <button type="button" onClick={() => setEditing(null)}>
                  Batal
                </button>
              </form>
            ) : (
              <button type="button" onClick={() => void startEdit(persona.slug)}>
                Ubah {persona.name}
              </button>
            )}
            {persona.is_default ? null : (
              <button type="button" onClick={() => void handleDelete(persona.slug, persona.name)}>
                Hapus persona {persona.name}
              </button>
            )}
          </div>
        ))
      )}
    </div>
  );
}
