/**
 * Agent photo picker (V2-26): change the agent's profile photo
 * from Settings.
 *
 * Shows the current photo (or the default Puspa logo), a file
 * input for a new one, and a remove button that restores the
 * logo. The upload goes through `uploadAgentPhoto`; the server
 * keeps one photo per user and refuses anything that is not a
 * displayable image (jpeg, png, webp, gif; at most 2 MiB).
 * A too-large file is refused in the browser first, before any
 * upload, with the limit in the visible helper text.
 */
import { useEffect, useState } from "react";
import {
  deleteAgentPhoto,
  fetchAgentAvatar,
  uploadAgentPhoto,
  type AvatarStatusWithSource,
} from "../api/client";
import { avatarPhotoSrc, DEFAULT_AVATAR_SRC } from "../state/avatar";
import { AgentAvatar } from "./AgentAvatar";
import { AgentAvatarChoices } from "./AgentAvatarChoices";

const MAX_PHOTO_BYTES = 2 * 1024 * 1024;
const ACCEPT = "image/jpeg,image/png,image/webp,image/gif";

export function AgentPhotoPicker() {
  const [status, setStatus] = useState<AvatarStatusWithSource | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchAgentAvatar()
      .then((loaded) => {
        if (!cancelled) setStatus(loaded);
      })
      .catch(() => {
        if (!cancelled) setMessage("Foto agen belum bisa dimuat; logo default dipakai.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onPickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > MAX_PHOTO_BYTES) {
      setMessage("Foto terlalu besar; maksimal 2 MB.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const saved = await uploadAgentPhoto(file);
      setStatus(saved);
      setMessage("Foto agen tersimpan.");
    } catch (err) {
      setMessage(
        `Gagal menyimpan foto: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function onRemove() {
    setBusy(true);
    setMessage("");
    try {
      await deleteAgentPhoto();
      setStatus({
        ok: true,
        has_photo: false,
        photo_url: null,
        updated_at: null,
        mime_type: null,
        source: "default",
      });
      setMessage("Foto agen dihapus; logo default dipakai lagi.");
    } catch (err) {
      setMessage(
        `Gagal menghapus foto: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <AgentAvatar state="idle" photoSrc={avatarPhotoSrc(status)} />
      <p className="muted">
        {status?.has_photo
          ? "Foto agen kustom terpasang."
          : `Belum ada foto kustom; logo default Puspa dipakai (${DEFAULT_AVATAR_SRC}).`}
      </p>
      <label htmlFor="agent-photo-input">Foto agen baru</label>
      <input
        id="agent-photo-input"
        name="agent-photo"
        type="file"
        accept={ACCEPT}
        aria-describedby="agent-photo-help"
        onChange={(event) => void onPickFile(event)}
        disabled={busy}
      />
      <p id="agent-photo-help" className="muted">
        JPEG, PNG, WebP, atau GIF, maksimal 2 MB.
      </p>
      {status?.has_photo ? (
        <button type="button" className="button-secondary" onClick={() => void onRemove()} disabled={busy}>
          {busy ? "Menghapus…" : "Hapus foto, pakai logo default"}
        </button>
      ) : null}
      <h3 style={{ margin: "16px 0 4px" }}>Atau pilih avatar bunga</h3>
      <p className="muted" style={{ margin: "0 0 6px" }}>
        Empat bunga Puspa — tanpa unggah file.
      </p>
      <AgentAvatarChoices onStatusChange={setStatus} />
      {message ? (
        <p className="muted" role="status">
          {message}
        </p>
      ) : null}
    </div>
  );
}
