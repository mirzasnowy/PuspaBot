import { useState } from "react";
import { Icon } from "./icons";
import { AgentAvatarPhoto } from "./AgentAvatar";
import { avatarPhotoSrc, avatarStatus } from "../state/avatar";
import { useLanguage } from "../state/language";
import type { AvatarStatusWithSource, OfficeProjection } from "../api/client";

/**
 * Dynamic Island chat header (Nero, 23 Nov, latest rules):
 * back "<" left, island center, "..." right — nothing else.
 * The back and "..." buttons are the app's standard icon
 * buttons (Nero, 24 Nov): light, bordered, same as the other
 * standard buttons — no special dark circles in Chat.
 * No bell anywhere (Nero, 23 Nov): notifications reach the
 * user through chat (the approval pill below the island, the
 * queue at /antrean and from Tugas), never through a header bell.
 *
 * Buttons: back and "..." are the SAME 44px dark circle with a
 * thick fat white chevron/dots (Nero, 23 Nov) — 44px sits
 * between the original 54px and the too-small 36px. The same
 * circle is used by every PageHead back button, so back looks
 * identical on all screens.
 *
 * Idle: ONE line, agent name only, CENTERED in the island —
 * no "Puspa >", no "Online", no second line, compact pill.
 * Working: the pill expands and only then shows the live
 * task/activity (e.g. "Mencari harga…"); idle shows no task.
 * The mascot is a flex child in normal flow, so the pill can
 * never overlap the avatar. The pill's background in both
 * states is the faint Puspa pixel-art scene (the artwork file
 * in static/), with the name and task written on top of it.
 *
 * Header: transparent, no black box, no overlay behind it —
 * it blends with the chat background. Sticky: the wrap sticks
 * to the top while the chat scrolls. NO glass/frosted effect
 * (Nero, 24 Nov — the earlier glass instruction is cancelled):
 * no translucent layer, no blur over the chat when scrolled.
 * The scroll container is `.view` (overflow-y: auto).
 *
 * The avatar in the island is the SAME photo the chat bubbles
 * show (Nero, 24 Nov): the AgentAvatar photo — the uploaded
 * photo when one is set, else the default Puspa logo — with
 * the same idle/working animation as the bubbles.
 */
export function DynamicIsland({
  name,
  working,
  activity,
  onBack,
  avatar,
  office,
}: {
  name: string;
  working: boolean;
  /** Live activity line, e.g. "Mengetik…" or "Mencari harga…". */
  activity: string | null;
  onBack?: () => void;
  /** Avatar photo status (GET /api/agent/avatar); null = default logo. */
  avatar?: AvatarStatusWithSource | null;
  /** Live office projection; the island animates working when it lists a working agent. */
  office?: OfficeProjection | null;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { t } = useLanguage();
  const state = working
    ? "working"
    : avatarStatus(false, office ?? null);

  return (
    <div className="chat-island-wrap">
      <button
        type="button"
        className="icon-btn"
        aria-label={t("ui.common.back", "Kembali")}
        onClick={() => (onBack ? onBack() : history.back())}
      >
        <Icon name="backchev" size={20} strokeWidth={3.4} />
      </button>

      <div className="island-center">
        <div
          className={`dynamic-island${working ? " dynamic-island--working" : ""}`}
          data-state={working ? "working" : "idle"}
          role="status"
          aria-label={working ? `${name}: ${activity ?? t("ui.island.working_fallback", "bekerja")}` : `${name}: idle`}
        >
          <span className="island-avatar">
            <AgentAvatarPhoto
              state={state}
              photoSrc={avatarPhotoSrc(avatar ?? null)}
              size={working ? 44 : 32}
            />
          </span>
          <span className="island-text">
            <span className="island-name">{name}</span>
            {working && (
              <span className="island-activity">
                {activity ?? t("ui.island.working", "Bekerja…")}
              </span>
            )}
          </span>
          {working && (
            <span className="typing" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
          )}
        </div>
      </div>

      <div className="app-menu" style={{ flex: "0 0 auto" }}>
        <button
          type="button"
          className="icon-btn"
          aria-label={t("ui.island.menu", "Menu chat")}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <Icon name="dots" size={20} strokeWidth={3} />
        </button>
        {menuOpen && (
          <div className="app-menu-pop" role="menu">
            <a href="/studio" target="_blank" rel="noreferrer">
              {t("ui.island.menu_canvas", "Buat Desain di Kanvas")}
            </a>
            <a href="/antrean">{t("ui.island.menu_approvals", "Antrean Persetujuan")}</a>
            <a href="/tema">{t("ui.island.menu_theme", "Tema & Otonomi")}</a>
            <a href="/suara">{t("ui.island.menu_voice", "Suara")}</a>
            <a href="/share">{t("ui.island.menu_share", "Bagikan ke Puspa")}</a>
          </div>
        )}
      </div>
    </div>
  );
}
