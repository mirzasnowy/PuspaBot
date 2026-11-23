import { useEffect, useState } from "react";
import {
  addGraphNode,
  createTask,
  fetchBillingPlan,
  fetchTrialStatus,
  type BillingTier,
  type TrialStatus,
} from "../../api/client";
import { SAYA_GROUPS } from "../../api/space";

type SayaGroup = (typeof SAYA_GROUPS)[number];
import { fetchOnboardingStatus } from "../../state/onboarding";
import { AgentMascot } from "../../components/Mascot";
import { GroupCard, PageHead, RowItem, useToast } from "./shared";
import { useLanguage } from "../../state/language";

/** Catalog key per Saya group id / row slug (the space.ts label is the fallback). */
const GROUP_KEYS: Record<string, string> = {
  grupkeuangan: "ui.saya.group.grupkeuangan",
  grupstudio: "ui.saya.group.grupstudio",
  grupruang: "ui.saya.group.grupruang",
  grupakun: "ui.saya.group.grupakun",
};
const ROW_KEYS: Record<string, string> = {
  keuangan: "ui.saya.row.keuangan",
  ppob: "ui.saya.row.ppob",
  cekharga: "ui.saya.row.cekharga",
  agen: "ui.saya.row.agen",
  studio: "ui.saya.row.studio",
  galeri: "ui.saya.row.galeri",
  marketplace: "ui.saya.row.marketplace",
  ruang: "ui.saya.row.ruang",
  pagi: "ui.saya.row.pagi",
  konektor: "ui.saya.row.konektor",
  provider: "ui.saya.row.provider",
  tema: "ui.saya.row.tema",
  suara: "ui.saya.row.suara",
  insights: "ui.nav.insights",
  share: "ui.saya.row.share",
};

const FREE_CREDITS_PER_MONTH = 15;

/**
 * Saya: one merged profile+plan card + 4 group rows + Keluar.
 * The profile (user name) and the Plus plan share a single
 * compact card (Nero, 24 Nov): name, plan, and the upgrade
 * button together, not two separate cards. Every row is one
 * group, one domain; details live one level down on their own
 * sub-page.
 */
export function ProfileScreen() {
  const { t } = useLanguage();
  const [userName, setUserName] = useState(t("ui.profile.you", "Kamu"));
  const [trial, setTrial] = useState<TrialStatus | null>(null);

  useEffect(() => {
    fetchOnboardingStatus()
      .then((s) => {
        if (s.name) setUserName(s.name);
      })
      .catch(() => undefined);
    fetchTrialStatus()
      .then(setTrial)
      .catch(() => undefined);
  }, []);

  const planLine =
    trial && (trial.status === "active" || trial.status === "grace")
      ? t("ui.profile.trial_line", "Trial {tier} · {days} hari lagi", { tier: trial.tier || "Pro", days: trial.days_left ?? "?" })
      : t("ui.profile.free_line", "Paket Free · {credits} kredit per bulan", { credits: FREE_CREDITS_PER_MONTH });

  return (
    <div className="page">
      <PageHead title={t("ui.nav.profile", "Saya")} />
      <div className="card" style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <AgentMascot agent="puspa" size={56} state="idle" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 800, fontSize: 17 }}>{userName}</div>
          <div style={{ color: "var(--ink-2)", fontSize: 13 }}>
            {planLine}
          </div>
        </div>
        <span className="tag green">Free</span>
        <a className="btn ghost sm" href="/persona">
          {t("ui.profile.edit", "Ubah")}
        </a>
      </div>
      <a className="btn dark block" style={{ marginTop: 12 }} href="/paket">
        {t("ui.profile.upgrade", "Upgrade ke Plus")}
      </a>

      {SAYA_GROUPS.map((group) => (
        <GroupCard key={group.id} label={t(GROUP_KEYS[group.id] ?? "ui.saya.group.grupakun", group.label)}>
          {group.rows.map(([icon, tint, label, to]) => (
            <RowItem key={to} icon={icon} cls={tint} title={t(ROW_KEYS[to] ?? "ui.saya.row.keuangan", label)} href={`/${to}`} />
          ))}
        </GroupCard>
      ))}

      <a className="btn ghost block" style={{ marginTop: 12 }} href="/onboarding">
        {t("ui.profile.logout", "Keluar")}
      </a>
      <p className="foot-note">
        {t("ui.profile.footnote", "PuspaBot — asisten pribadi yang kamu jalankan sendiri.")}
      </p>
    </div>
  );
}

/** One Saya group sub-page: its rows, nothing else. */
export function GroupScreen({ group }: { group: SayaGroup }) {
  const { t } = useLanguage();
  return (
    <div className="page">
      <PageHead title={t(GROUP_KEYS[group.id] ?? "ui.saya.group.grupakun", group.label)} back />
      <div className="card">
        {group.rows.map(([icon, tint, label, to]) => (
          <RowItem key={to} icon={icon} cls={tint} title={t(ROW_KEYS[to] ?? "ui.saya.row.keuangan", label)} href={`/${to}`} />
        ))}
      </div>
    </div>
  );
}

/**
 * Paket & Harga: real tiers from GET /api/billing/plan.
 * There is no checkout API, so credit-pack "Beli" buttons are
 * not shown; the real action is starting a Pro trial.
 */
export function PlansScreen() {
  const { t, locale } = useLanguage();
  const [tiers, setTiers] = useState<BillingTier[] | null>(null);
  const [error, setError] = useState("");
  const [trial, setTrial] = useState<TrialStatus | null>(null);

  useEffect(() => {
    fetchBillingPlan()
      .then((plan) => setTiers(plan.tiers))
      .catch(() => setError(t("ui.plans.load_failed", "Gagal memuat paket. Coba lagi.")));
    fetchTrialStatus()
      .then(setTrial)
      .catch(() => undefined);
  }, []);

  const trialActive =
    trial && (trial.status === "active" || trial.status === "grace");

  return (
    <div className="page">
      <PageHead title={t("ui.plans.title", "Paket & Harga")} back />
      {error && <div className="warnbox">{error}</div>}
      {trialActive && (
        <div className="okbox">
          {t("ui.plans.trial_active", "Trial {tier} aktif — {days} hari tersisa.", { tier: trial.tier || "Pro", days: trial.days_left ?? "?" })}
        </div>
      )}
      {!tiers && !error && <p className="muted">{t("ui.plans.loading", "Memuat paket…")}</p>}
      {tiers?.map((tier) => (
        <div className="card" key={tier.id} style={{ marginBottom: 12 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
            }}
          >
            <b style={{ fontSize: 16, textTransform: "capitalize" }}>
              {tier.id}
            </b>
            <b>
              {tier.price_idr_per_month === 0
                ? t("ui.plans.free", "Gratis")
                : t("ui.plans.price", "Rp{price}/bln", { price: tier.price_idr_per_month.toLocaleString(locale === "en" ? "en-US" : "id-ID") })}
            </b>
          </div>
          <div style={{ color: "var(--ink-2)", fontSize: 13, marginTop: 4 }}>
            {t("ui.plans.credits_per_month", "{credits} kredit per bulan", { credits: tier.monthly_credits })}
          </div>
          {tier.id === "free" ? (
            <span className="tag" style={{ marginTop: 10 }}>
              {t("ui.plans.active_plan", "Paket aktif")}
            </span>
          ) : trialActive ? (
            <span className="tag" style={{ marginTop: 10 }}>
              {t("ui.plans.trial_tag", "Trial aktif")}
            </span>
          ) : (
            <form method="post" action="/trial/start" style={{ marginTop: 10 }}>
              <button className="btn primary block" type="submit">
                {t("ui.plans.start_trial", "Mulai Trial Pro")}
              </button>
            </form>
          )}
        </div>
      ))}
      <p className="foot-note">
        {t("ui.plans.note", "Kredit dipakai untuk tugas agent. Pembelian kredit menyusul — untuk sekarang mulai Trial Pro.")}
      </p>
    </div>
  );
}

/** Trial Pro: real state from GET /api/trial/status. */
export function TrialScreen() {
  const { t } = useLanguage();
  const [trial, setTrial] = useState<TrialStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchTrialStatus()
      .then(setTrial)
      .catch(() => setError(t("ui.trial.load_failed", "Gagal memuat status trial. Coba lagi.")));
  }, []);

  const active =
    trial && (trial.status === "active" || trial.status === "grace");

  return (
    <div className="page">
      <PageHead title="Trial Pro" back />
      {error && <div className="warnbox">{error}</div>}
      {!trial && !error && <p className="muted">{t("ui.trial.loading", "Memuat…")}</p>}
      {trial && !active && (
        <>
          <div className="card">
            <b>{t("ui.trial.pitch", "Coba Pro selama 14 hari, gratis.")}</b>
            <p style={{ color: "var(--ink-2)", margin: "8px 0 0" }}>
              {t("ui.trial.pitch_note", "Semua fitur Pro terbuka. Setelah trial berakhir kamu kembali ke paket Free dan datamu tetap ada.")}
            </p>
          </div>
          <form method="post" action="/trial/start" style={{ marginTop: 12 }}>
            <button className="btn primary block" type="submit">
              {t("ui.plans.start_trial", "Mulai Trial Pro")}
            </button>
          </form>
          <a className="btn ghost block" style={{ marginTop: 8 }} href="/paket">
            {t("ui.trial.later", "Nanti Saja")}
          </a>
        </>
      )}
      {trial && active && (
        <div className="okbox">
          {t("ui.trial.active_line", "Trial {tier} aktif — {days} hari tersisa", { tier: trial.tier || "Pro", days: trial.days_left ?? "?" })}
          {trial.expires_at ? ` ${t("ui.trial.active_until", "(sampai {expires})", { expires: trial.expires_at })}` : ""}.
        </div>
      )}
    </div>
  );
}

/**
 * Bagikan ke Puspa: every action does something real — a task,
 * a memory node, or a price check. Nothing here is a dead share
 * sheet.
 */
export function ShareScreen() {
  const { t } = useLanguage();
  const { toast, show } = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  async function shareAsTask() {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await createTask(text.trim());
      show(t("ui.share.sent_task", "Terkirim ke Puspa sebagai tugas."));
      setText("");
    } catch {
      show(t("ui.share.send_failed", "Gagal mengirim. Coba lagi."));
    } finally {
      setBusy(false);
    }
  }

  async function saveAsMemory() {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await addGraphNode({ name: text.trim(), kind: "note" });
      show(t("ui.share.saved_memory", "Tersimpan ke Memori."));
      setText("");
    } catch {
      show(t("ui.share.save_failed", "Gagal menyimpan. Coba lagi."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHead title={t("ui.island.menu_share", "Bagikan ke Puspa")} back />
      <div className="card">
        <label htmlFor="share-text" style={{ fontWeight: 700 }}>
          {t("ui.share.label", "Tempel teks, tautan, atau catatan")}
        </label>
        <textarea
          id="share-text"
          className="input"
          rows={5}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={t("ui.share.placeholder", "Contoh: https://tokopedia.com/… atau catatan untuk Puspa")}
          style={{ marginTop: 8 }}
        />
      </div>
      <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
        <button
          className="btn primary block"
          type="button"
          onClick={shareAsTask}
          disabled={busy || !text.trim()}
        >
          {t("ui.share.make_task", "Jadikan Tugas")}
        </button>
        <button
          className="btn dark block"
          type="button"
          onClick={saveAsMemory}
          disabled={busy || !text.trim()}
        >
          {t("ui.share.save_memory", "Simpan ke Memori")}
        </button>
        <a className="btn ghost block" href="/cekharga">
          {t("ui.saya.row.cekharga", "Cek Harga")}
        </a>
      </div>
      {toast && <div className="toast">{toast}</div>}
      <p className="foot-note">
        {t("ui.share.note", "Tempel dari aplikasi lain, lalu pilih mau dijadikan tugas atau disimpan sebagai memori.")}
      </p>
    </div>
  );
}
