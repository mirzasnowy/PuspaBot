import { useEffect, useState, type FormEvent } from "react";
import {
  deleteByokKey,
  fetchAgents,
  fetchAutonomyDial,
  fetchRooms,
  getModelSettings,
  getPoolQuota,
  getVoiceSettings,
  saveByokKey,
  saveModelSettings,
  saveVoiceSettings,
  setDialCeiling,
  type Agent,
  type CeilingName,
  type DialState,
  type ModelSettings,
  type PoolQuotaResponse,
  type VoiceSettings,
} from "../../api/client";
import { SPACE_AGENTS } from "../../components/Mascot";
import { AgentMascot } from "../../components/Mascot";
import { builtinAvatarSrc, DEFAULT_AVATAR_SRC } from "../../state/avatar";
import { useLanguage } from "../../state/language";
import type { Bootstrap } from "../../api/client";
import { ConnectorsScreen } from "../ConnectorsScreen";
import { InsightsScreen } from "../InsightsScreen";
import { LiveViewScreen } from "../LiveViewScreen";
import { MorningScreen } from "../MorningScreen";
import { ReplayScreen } from "../ReplayScreen";
import { RoomsScreen } from "../RoomsScreen";
import { PageHead, useLiveOffice, useToast } from "./shared";
import { officeAgents } from "../../state/office";

/**
 * Tim Agen: the six agents with their animated mascots. The
 * working state is live: it comes from the SSE office
 * projection, so a mascot only dances when its agent is
 * actually working right now.
 *
 * Custom agents (Nero, 24 Nov) list below the six built-ins,
 * with the flower icon picked in the Tambah Agent form — a new
 * agent shows up here as well as in the chat list, so Tim Agen
 * is the real roster, not a static poster.
 */
export function AgentsScreen() {
  const { t } = useLanguage();
  const { office } = useLiveOffice();
  const live = officeAgents(office);
  const [custom, setCustom] = useState<Agent[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchAgents()
      .then((result) => {
        if (!cancelled) setCustom(result.agents.filter((agent) => !agent.required));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  const statusOf = (id: string) =>
    live.find((a) => a.agent_id === id)?.status === "working"
      ? "working"
      : "idle";

  return (
    <div className="page">
      <PageHead title="Tim Agen" back />
      {Object.entries(SPACE_AGENTS).map(([id, agent]) => (
        <div className="card" key={id} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <AgentMascot
              agent={id as keyof typeof SPACE_AGENTS}
              size={52}
              state={statusOf(id)}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <b style={{ textTransform: "capitalize" }}>{agent.name}</b>
              <div style={{ color: "var(--ink-2)", fontSize: 13 }}>
                {agent.role}
              </div>
              <span
                className="tag"
                style={{
                  marginTop: 6,
                  background:
                    statusOf(id) === "working"
                      ? "#FDE8E9"
                      : undefined,
                  color:
                    statusOf(id) === "working"
                      ? "var(--red-dark)"
                      : undefined,
                }}
              >
                {statusOf(id) === "working" ? "Lagi bekerja" : "Idle"}
              </span>
            </div>
            <a className="btn ghost sm" href="/chat/puspa">
              Chat
            </a>
          </div>
        </div>
      ))}
      <a className="btn dark block" href="/chat/ruang">
        Chat Grup (Ruang Bersama)
      </a>
      {custom.length > 0 ? (
        <>
          <h2 style={{ marginTop: 20 }}>{t("ui.agents.custom_title", "Agen buatanmu")}</h2>
          {custom.map((agent) => (
            <div className="card" key={agent.agent_id} style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <img
                  src={builtinAvatarSrc(agent.avatar) ?? DEFAULT_AVATAR_SRC}
                  alt=""
                  width={52}
                  height={52}
                  style={{ borderRadius: "50%" }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b>{agent.name}</b>
                  <div style={{ color: "var(--ink-2)", fontSize: 13 }}>
                    {agent.role || agent.purpose || ""}
                  </div>
                </div>
                <a className="btn ghost sm" href="/konektor">
                  {t("ui.agents.manage", "Kelola")}
                </a>
              </div>
            </div>
          ))}
        </>
      ) : null}
    </div>
  );
}

/** Suara: voice provider + voice choice, saved to the real API. */
export function VoiceScreen() {
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [error, setError] = useState("");
  const { toast, show } = useToast();

  useEffect(() => {
    getVoiceSettings()
      .then(setSettings)
      .catch(() => setError("Gagal memuat pengaturan suara."));
  }, []);

  async function save(next: VoiceSettings) {
    try {
      const saved = await saveVoiceSettings(next);
      setSettings(saved);
      show("Pengaturan suara tersimpan.");
    } catch {
      show("Gagal menyimpan. Coba lagi.");
    }
  }

  return (
    <div className="page">
      <PageHead title="Suara" back />
      {error && <div className="warnbox">{error}</div>}
      {settings && (
        <div className="card stacked-form">
          <label>
            Suara balasan (TTS)
            <select
              className="input"
              value={settings.tts_provider}
              onChange={(e) =>
                void save({
                  ...settings,
                  tts_provider: e.target
                    .value as VoiceSettings["tts_provider"],
                })
              }
            >
              <option value="edge">Edge TTS (gratis)</option>
              <option value="browser">Suara browser</option>
              <option value="elevenlabs">ElevenLabs</option>
              <option value="openai-compatible">OpenAI-compatible</option>
              <option value="azure">Azure</option>
            </select>
          </label>
          <label>
            Nama suara
            <input
              className="input"
              value={settings.tts_voice ?? ""}
              onChange={(e) =>
                setSettings({ ...settings, tts_voice: e.target.value })
              }
              onBlur={() => void save(settings)}
              placeholder="Contoh: id-ID-ArdiNeural"
            />
          </label>
          <p className="muted" style={{ margin: 0 }}>
            Suara gratis default: Edge TTS. Kunci API untuk provider
            berbayar disimpan di vault, tidak pernah di layar ini.
          </p>
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

/**
 * Tema & Otonomi: the two themes (Petal Light / Dark Bloom,
 * local, applied at once) + the autonomy dial (real API: how
 * much Puspa may do on its own per area).
 */
export function ThemeScreen() {
  const [theme, setTheme] = useState(
    () =>
      document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  const [dial, setDial] = useState<DialState | null>(null);
  const [error, setError] = useState("");
  const { toast, show } = useToast();

  useEffect(() => {
    fetchAutonomyDial()
      .then(setDial)
      .catch(() => setError("Gagal memuat otonomi."));
  }, []);

  function applyTheme(next: string) {
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("puspa-theme", next);
    } catch {
      // Private mode: the theme still applies for this session.
    }
  }

  async function setCeiling(area: string, ceiling: CeilingName) {
    try {
      const updated = await setDialCeiling(area, ceiling);
      setDial((prev) =>
        prev
          ? {
              ...prev,
              areas: prev.areas.map((a) =>
                a.area === updated.area ? updated : a,
              ),
            }
          : prev,
      );
      show("Otonomi diperbarui.");
    } catch {
      show("Gagal memperbarui. Coba lagi.");
    }
  }

  return (
    <div className="page">
      <PageHead title="Tema & Otonomi" back />
      {error && <div className="warnbox">{error}</div>}
      <div className="card stacked-form">
        <label>
          Tema
          <select
            className="input"
            value={theme}
            onChange={(e) => applyTheme(e.target.value)}
          >
            <option value="light">Petal Light</option>
            <option value="dark">Dark Bloom</option>
          </select>
        </label>
      </div>
      {dial && (
        <div className="card" style={{ marginTop: 12 }}>
          <b>Otonomi per area</b>
          <p className="muted" style={{ margin: "6px 0 10px" }}>
            {dial.threshold_text}
          </p>
          {dial.areas.map((area) => (
            <div className="kv" key={area.area}>
              <span>
                {area.label}
                <br />
                <small style={{ color: "var(--muted)" }}>
                  {area.description}
                </small>
              </span>
              <select
                className="input"
                style={{ width: 150 }}
                value={area.ceiling}
                onChange={(e) =>
                  void setCeiling(area.area, e.target.value as CeilingName)
                }
              >
                <option value="ask_first">Tanya dulu</option>
                <option value="balanced">Seimbang</option>
                <option value="autonomous">Otomatis</option>
              </select>
            </div>
          ))}
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

/**
 * Riset & Belanja Grup: the rooms with their live jobs, plus a
 * price check that becomes a real task.
 */
export function GroupResearchScreen() {
  const [rooms, setRooms] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchRooms()
      .then((body) =>
        setRooms(
          body.rooms.map((room) => ({ id: room.id, name: room.name })),
        ),
      )
      .catch(() => setError("Gagal memuat ruang."));
  }, []);

  return (
    <div className="page">
      <PageHead title="Riset & Belanja Grup" back />
      {error && <div className="warnbox">{error}</div>}
      <div className="card">
        {rooms.length === 0 && !error && (
          <p className="muted" style={{ margin: 0 }}>
            Belum ada ruang. Buat ruang di Ruang Bersama, lalu riset
            dan belanja bareng di sini.
          </p>
        )}
        {rooms.map((room) => (
          <div className="kv" key={room.id}>
            <span>{room.name}</span>
            <a className="btn ghost sm" href={`/chat/ruang?room=${room.id}`}>
              Buka
            </a>
          </div>
        ))}
      </div>
      <a className="btn dark block" style={{ marginTop: 12 }} href="/cekharga">
        Cek Harga Grup
      </a>
    </div>
  );
}

/** Provider & Model: model source + BYOK key, saved to the real API. */
export function ProviderScreen() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [quota, setQuota] = useState<PoolQuotaResponse | null>(null);
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const { toast, show } = useToast();

  useEffect(() => {
    getModelSettings()
      .then((s) => {
        setSettings(s);
        if (s.source === "byok") {
          const provider = s.provider?.trim().toLowerCase() || "openrouter";
          return getPoolQuota(provider).then(setQuota);
        }
        return undefined;
      })
      .catch(() => setError("Gagal memuat provider."));
  }, []);

  async function save(next: ModelSettings) {
    try {
      const saved = await saveModelSettings(next);
      setSettings(saved);
      show("Provider tersimpan.");
    } catch {
      show("Gagal menyimpan. Coba lagi.");
    }
  }

  async function saveKey(event: FormEvent) {
    event.preventDefault();
    if (!key.trim() || !settings) return;
    try {
      const provider = settings.provider?.trim().toLowerCase() || "openrouter";
      await saveByokKey(provider, key.trim());
      setKey("");
      setSettings({ ...settings, provider, has_byok_key: true });
      show(`Kunci ${provider} tersimpan di vault.`);
    } catch {
      show("Gagal menyimpan kunci.");
    }
  }

  async function removeKey() {
    if (!settings) return;
    try {
      const provider = settings.provider?.trim().toLowerCase() || "openrouter";
      await deleteByokKey(provider);
      setSettings({ ...settings, has_byok_key: false });
      show(`Kunci ${provider} dihapus dari vault.`);
    } catch {
      show("Gagal menghapus kunci.");
    }
  }

  return (
    <div className="page">
      <PageHead title="Provider & Model" back />
      {error && <div className="warnbox">{error}</div>}
      {settings && (
        <div className="card stacked-form">
          <label>
            Sumber model
            <select
              className="input"
              value={settings.source}
              onChange={(e) =>
                void save({
                  ...settings,
                  source: e.target.value as ModelSettings["source"],
                })
              }
            >
              <option value="hosted">Hosted (default Puspa)</option>
              <option value="byok">Kunci sendiri (BYOK)</option>
              <option value="local">Lokal (Ollama)</option>
            </select>
          </label>
          <label>
            Model
            <input
              className="input"
              value={settings.model ?? ""}
              onChange={(e) =>
                setSettings({ ...settings, model: e.target.value })
              }
              onBlur={() => void save(settings)}
              placeholder="Contoh: llama3.1"
            />
          </label>
          {quota && quota.accounts.length > 0 && (
            <div>
              <b>Kuota akun ({quota.strategy})</b>
              {quota.accounts.map((account) => {
                const fiveHour = account.windows.find((w) => w.window === "5h");
                return (
                  <div className="kv" key={account.account_id}>
                    <span>{account.label}</span>
                    <span>
                      {fiveHour?.tokens_remaining != null
                        ? `${fiveHour.tokens_remaining.toLocaleString("id-ID")} token tersisa / 5 jam`
                        : account.available
                          ? "Siap dipakai"
                          : (account.skip_reason ?? "Tidak tersedia")}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {settings.source === "byok" && (
            <form onSubmit={saveKey} className="stacked-form">
              <label>
                Kunci API (disimpan di vault)
                <input
                  className="input"
                  type="password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder="sk-…"
                  autoComplete="off"
                />
              </label>
              <button className="btn dark block" type="submit">
                Simpan Kunci
              </button>
              <button
                className="btn ghost block"
                type="button"
                onClick={() => void removeKey()}
              >
                Hapus Kunci
              </button>
            </form>
          )}
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

/** Thin Space wrappers: one big title each, same component underneath. */
export function ConnectorsSpaceScreen({
  bootstrap,
}: {
  bootstrap: Bootstrap;
}) {
  return (
    <div className="page">
      <PageHead title="Konektor" back />
      <ConnectorsScreen bootstrap={bootstrap} />
    </div>
  );
}

export function MorningSpaceScreen() {
  return (
    <div className="page">
      <PageHead title="Ringkasan Pagi" back />
      <MorningScreen />
    </div>
  );
}

export function RoomsSpaceScreen() {
  return (
    <div className="page">
      <PageHead title="Ruang Bersama" back />
      <RoomsScreen />
    </div>
  );
}

export function LiveSpaceScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  return (
    <div className="page">
      <PageHead title="Live View" back />
      <LiveViewScreen bootstrap={bootstrap} />
    </div>
  );
}

export function ReplaySpaceScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  return (
    <div className="page">
      <PageHead title="Replay" back />
      <ReplayScreen bootstrap={bootstrap} />
    </div>
  );
}

export function InsightsSpaceScreen() {
  return (
    <div className="page">
      <PageHead title="Insights" back />
      <InsightsScreen />
    </div>
  );
}
