import { useEffect, useState } from "react";
import {
  deleteAgentPhoto,
  fetchAgentAvatar,
  setBuiltinAvatar,
  type AvatarStatusWithSource,
} from "../api/client";
import {
  activeBuiltinId,
  BUILTIN_AVATARS,
  DEFAULT_AVATAR_SRC,
} from "../state/avatar";
import { useLanguage } from "../state/language";

/**
 * Built-in avatar choices (Nero, 24 Nov — flowers only):
 * the default Puspa flower logo plus four flower avatars
 * (tulip, sunflower, rose, sakura). Picking one copies the
 * shipped flower file into the user's avatar dir, so every
 * avatar in the app — island, bubbles, lists — shows the same
 * photo at once. Picking the default removes the photo.
 * Used in onboarding, the persona (agent edit) screen, and
 * the Settings photo picker.
 *
 * Controlled mode (Tambah Agent form, Nero 24 Nov): pass
 * `value` + `onSelect` and clicks only report the picked id —
 * nothing is applied to the Puspa avatar, because the icon
 * belongs to the agent being created, not to Puspa.
 */
export function AgentAvatarChoices({
  onStatusChange,
  value,
  onSelect,
}: {
  onStatusChange?: (status: AvatarStatusWithSource) => void;
  /** Controlled mode: the picked id ("default" or a flower id). */
  value?: string;
  /** Controlled mode: called with the picked id; nothing is applied. */
  onSelect?: (id: string) => void;
}) {
  const { t } = useLanguage();
  const [status, setStatus] = useState<AvatarStatusWithSource | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const controlled = onSelect !== undefined;
  const active = controlled
    ? value && value !== "default"
      ? value
      : null
    : activeBuiltinId(status);
  const isDefault = controlled
    ? value === "default" || value === undefined
    : status !== null && !status.has_photo;

  useEffect(() => {
    if (controlled) return;
    let cancelled = false;
    fetchAgentAvatar()
      .then((loaded) => {
        if (!cancelled) setStatus(loaded);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, [controlled]);

  async function pickBuiltin(id: string) {
    if (controlled) {
      onSelect(id);
      return;
    }
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const saved = await setBuiltinAvatar(id);
      setStatus(saved);
      onStatusChange?.(saved);
      setMessage(t("ui.avatar.changed", "Avatar diganti."));
    } catch (err) {
      setMessage(
        t("ui.avatar.change_failed", "Gagal mengganti avatar: {error}", {
          error: err instanceof Error ? err.message : t("ui.avatar.unknown_error", "error tak dikenal"),
        }),
      );
    } finally {
      setBusy(false);
    }
  }

  async function pickDefault() {
    if (controlled) {
      onSelect("default");
      return;
    }
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      await deleteAgentPhoto();
      const fresh: AvatarStatusWithSource = {
        ok: true,
        has_photo: false,
        photo_url: null,
        updated_at: null,
        mime_type: null,
        source: "default",
      };
      setStatus(fresh);
      onStatusChange?.(fresh);
      setMessage(t("ui.avatar.restored", "Avatar kembali ke logo default Puspa."));
    } catch (err) {
      setMessage(
        t("ui.avatar.restore_failed", "Gagal mengembalikan avatar: {error}", {
          error: err instanceof Error ? err.message : t("ui.avatar.unknown_error", "error tak dikenal"),
        }),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="avatar-choices" role="group" aria-label={t("ui.avatar.choose", "Pilih avatar agent")}>
        <button
          type="button"
          className={`avatar-choice${isDefault ? " on" : ""}`}
          aria-pressed={isDefault}
          disabled={busy}
          onClick={() => void pickDefault()}
        >
          <img src={DEFAULT_AVATAR_SRC} alt="" width={56} height={56} />
          <span>Puspa (default)</span>
        </button>
        {BUILTIN_AVATARS.map((option) => (
          <button
            key={option.id}
            type="button"
            className={`avatar-choice${active === option.id ? " on" : ""}`}
            aria-pressed={active === option.id}
            disabled={busy}
            onClick={() => void pickBuiltin(option.id)}
          >
            <img src={option.src} alt="" width={56} height={56} />
            <span>{t(option.labelKey, option.label)}</span>
          </button>
        ))}
      </div>
      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
