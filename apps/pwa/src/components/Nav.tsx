/**
 * App shell (Space port): charcoal desktop sidebar + phone bottom
 * tab bar, four tabs only (Chat, Tugas, Memori, Saya). Same
 * structure as the Space puspabot-app export: brand, tab nav,
 * credit card, profile row.
 *
 * NO header at all (Nero, 23 Nov): each screen carries its
 * own one big title, and there is no bell and no "..." menu
 * anywhere — notifications reach the user through chat (the
 * approval pill in Chat, the queue at /antrean). The canvas
 * (studio) and the pre-auth onboarding render without the shell.
 */
import type { ReactNode } from "react";
import { MAIN_TABS, tabOf, type SpaceScreenId } from "../api/space";
import type { ScreenName } from "../api/bootstrap";
import { useLanguage } from "../state/language";
import { Icon } from "./icons";

function tabPath(tab: string): string {
  return tab === "percakapan" ? "/chat" : `/${tab}`;
}

/** Catalog key per tab id (the Indonesian label is the fallback). */
const TAB_KEYS: Record<string, string> = {
  percakapan: "ui.nav.chat",
  tugas: "ui.nav.tasks",
  memori: "ui.nav.memory",
  saya: "ui.nav.profile",
};

export function Nav({
  current,
  botName,
  userName,
  children,
}: {
  current: ScreenName;
  /** The bot's own name (V2-27): the required agent from the bootstrap, "Puspa" when unknown. */
  botName?: string;
  userName?: string;
  children?: ReactNode;
}) {
  const { t } = useLanguage();
  const activeTab = tabOf(current as SpaceScreenId);
  const name = botName?.trim() || "Puspa";
  const showShell = current !== "onboarding" && current !== "login" && current !== "studio";
  if (!showShell) {
    return <>{children}</>;
  }
  return (
    <>
      <div className="bgwash" aria-hidden="true" />
      <div className="app">
        <aside className="sidebar" aria-label={t("ui.nav.main", "Navigasi utama")}>
          <div className="brand">
            <img src="/static/puspa-mascot-mark.png" alt={t("ui.nav.mascot_alt", "Maskot bunga Puspa")} />
            <b>PuspaBot</b>
            <span className="dot" aria-hidden="true">
              ●
            </span>
          </div>
          <nav className="snav" aria-label={t("ui.nav.tabs", "Tab utama")}>
            {MAIN_TABS.map((tab) => (
              <a key={tab.id} href={tabPath(tab.id)} className={activeTab === tab.id ? "on" : undefined} aria-current={activeTab === tab.id ? "page" : undefined}>
                <Icon name={tab.icon} size={22} />
                <span>{t(TAB_KEYS[tab.id] ?? "ui.nav.chat", tab.label)}</span>
              </a>
            ))}
          </nav>
          <div className="side-spacer" />
          <div className="credit-card">
            <div className="lbl">{t("ui.nav.credit.plan", "Paket Free")}</div>
            <div className="row">
              <span className="gem">
                <Icon name="gem" size={20} />
              </span>
              <span className="num">15</span>
              <a className="btn-topup" href="/paket">
                {t("ui.nav.credit.topup", "Top Up")}
              </a>
            </div>
            <div className="plan-line">
              <b>{t("ui.nav.credit.amount", "{credits} kredit", { credits: 15 })}</b>{" "}
              {t("ui.nav.credit.per_month_suffix", "/ bulan · Free")}
            </div>
          </div>
          <a className="profile-row" href="/saya">
            <span className="avatar">{(userName ?? "M").slice(0, 1)}</span>
            <span className="nm">{userName ?? name}</span>
            <span className="chev">
              <Icon name="chev" size={17} />
            </span>
          </a>
        </aside>
        <div className="main">
          {/* No universal header (Nero, 23 Nov): no bell, no "..."
              menu anywhere. Notifications reach the user through
              chat instead (the approval pill in Chat, the queue
              at /antrean). Content starts right at the top — the
              screen's own big title is the first row. */}
          <main className="view">
            <section className="screen active">{children}</section>
          </main>
          <nav className="tabbar" aria-label={t("ui.nav.bottom", "Navigasi bawah")}>
            {MAIN_TABS.map((tab) => (
              <a
                key={tab.id}
                href={tabPath(tab.id)}
                className={activeTab === tab.id ? "on" : undefined}
                aria-current={activeTab === tab.id ? "page" : undefined}
              >
                <Icon name={tab.icon} size={23} />
                <span>{t(TAB_KEYS[tab.id] ?? "ui.nav.chat", tab.label)}</span>
              </a>
            ))}
          </nav>
        </div>
      </div>
    </>
  );
}
