import { useEffect, useState } from "react";
import {
  deleteByokKey,
  deleteRoutingKey,
  deleteVoiceKey,
  finishProviderTokenLogin,
  getCapabilityRouting,
  getLanguage,
  getModelSettings,
  getPoolQuota,
  getProviders,
  getTelemetrySettings,
  getVoiceSettings,
  logoutProvider,
  pollProviderDeviceLogin,
  resetCapabilityRoute,
  saveByokKey,
  saveCapabilityRoute,
  saveLanguage,
  saveModelSettings,
  saveRoutingKey,
  saveTelemetrySettings,
  saveVoiceKey,
  saveVoiceSettings,
  startProviderLogin,
  subscribePush,
  type CapabilityName,
  type CapabilityRoute,
  type CapabilityRouting,
  type ModelSettings,
  type PoolQuotaResponse,
  type ProviderStatus,
  type TelemetrySettings,
  type VoiceSettings,
} from "../api/client";
import { AutonomyDialPanel } from "../components/AutonomyDialPanel";
import { ComputerPanel } from "../components/ComputerPanel";
import { ProviderLoginPanel } from "../components/ProviderLoginPanel";
import { AgentPhotoPicker } from "../components/AgentPhotoPicker";
import {
  DEFAULT_LOCAL_BASE_URL,
  DEFAULT_LOCAL_MODEL,
  MODEL_SOURCES,
  defaultModelSettings,
  describeSource,
  validateBaseUrl,
  withDefaults,
} from "../state/modelSettings";
import {
  ASR_PROVIDERS,
  EDGE_TTS_VOICES,
  TTS_PROVIDERS,
  defaultVoiceSettings,
  describeAsrProvider,
  describeTtsProvider,
  ttsProviderNeedsKey,
  withVoiceDefaults,
} from "../state/voiceSettings";
import {
  defaultTelemetrySettings,
  describeTelemetry,
  withTelemetryDefaults,
} from "../state/telemetrySettings";
import {
  CAPABILITIES,
  CAPABILITY_PROVIDERS,
  defaultCapabilityRouting,
  describeCapability,
} from "../state/capabilityRouting";
import { LANGUAGE_OPTIONS, loadCatalog, useLanguage } from "../state/language";

/** Base64url -> Uint8Array for the VAPID applicationServerKey. */
function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const decoded = atob(base64.replace(/-/g, "+").replace(/_/g, "/") + padding);
  const bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
  return bytes;
}

/** V2-40: short human reading of a quota reset countdown in seconds. */
function formatQuotaReset(seconds: number): string {
  if (seconds >= 3600) {
    const hours = Math.round(seconds / 3600);
    return `${hours} jam`;
  }
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `${minutes} menit`;
}

export function SettingsScreen() {
  const { t } = useLanguage();
  const [pushStatus, setPushStatus] = useState("");
  const [pushBusy, setPushBusy] = useState(false);
  const [language, setLanguage] = useState("id");
  const [languageLoaded, setLanguageLoaded] = useState(false);
  const [languageStatus, setLanguageStatus] = useState("");
  const [languageBusy, setLanguageBusy] = useState(false);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings());
  const [modelLoaded, setModelLoaded] = useState(false);
  const [modelStatus, setModelStatus] = useState("");
  const [modelBusy, setModelBusy] = useState(false);
  const [byokKey, setByokKey] = useState("");
  const [byokBusy, setByokBusy] = useState(false);
  const [poolQuota, setPoolQuota] = useState<PoolQuotaResponse | null>(null);
  const [poolQuotaLoaded, setPoolQuotaLoaded] = useState(false);
  const [poolQuotaError, setPoolQuotaError] = useState<string | null>(null);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [providersLoaded, setProvidersLoaded] = useState(false);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings>(defaultVoiceSettings());
  const [voiceLoaded, setVoiceLoaded] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState("");
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceKey, setVoiceKey] = useState("");
  const [voiceKeyKind, setVoiceKeyKind] = useState<"asr" | "tts">("tts");
  const [voiceKeyBusy, setVoiceKeyBusy] = useState(false);
  const [routing, setRouting] = useState<CapabilityRouting>(defaultCapabilityRouting());
  const [routingLoaded, setRoutingLoaded] = useState(false);
  const [routingStatus, setRoutingStatus] = useState("");
  const [routingBusy, setRoutingBusy] = useState<CapabilityName | null>(null);
  const [routingKey, setRoutingKey] = useState("");
  const [routingKeyProvider, setRoutingKeyProvider] = useState("openrouter");
  const [routingKeyBusy, setRoutingKeyBusy] = useState(false);
  const [telemetrySettings, setTelemetrySettings] = useState<TelemetrySettings>(
    defaultTelemetrySettings(),
  );
  const [telemetryLoaded, setTelemetryLoaded] = useState(false);
  const [telemetryStatus, setTelemetryStatus] = useState("");
  const [telemetryBusy, setTelemetryBusy] = useState(false);

  async function loadProviders() {
    try {
      const data = await getProviders();
      setProviders(Array.isArray(data.providers) ? data.providers : []);
      setProvidersLoaded(true);
      setProvidersError(null);
    } catch (err) {
      setProvidersError(err instanceof Error ? err.message : "error tak dikenal");
    }
  }

  useEffect(() => {
    let cancelled = false;
    getLanguage()
      .then((settings) => {
        if (!cancelled) {
          setLanguage(settings.locale);
          setLanguageLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled)
          setLanguageStatus(
            t(
              "ui.settings.language.load_failed",
              "Bahasa belum bisa dimuat; bahasa Indonesia dipakai.",
            ),
          );
      });
    getModelSettings()
      .then((settings) => {
        if (!cancelled) {
          setModelSettings(settings);
          setModelLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setModelStatus("Pilihan model belum bisa dimuat; default hosted dipakai.");
      });
    getVoiceSettings()
      .then((settings) => {
        if (!cancelled) {
          setVoiceSettings(settings);
          setVoiceLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled)
          setVoiceStatus("Pilihan suara belum bisa dimuat; default Edge TTS dipakai.");
      });
    getTelemetrySettings()
      .then((settings) => {
        if (!cancelled) {
          setTelemetrySettings(withTelemetryDefaults(settings));
          setTelemetryLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled)
          setTelemetryStatus("Pilihan telemetri belum bisa dimuat; default mati dipakai.");
      });
    getCapabilityRouting()
      .then((table) => {
        if (!cancelled) {
          // The table is only trusted when it carries the
          // capabilities map; anything else (a stubbed fetch in
          // tests, a proxy error page) leaves the defaults in
          // place instead of crashing the render.
          if (table && typeof table === "object" && table.capabilities) {
            setRouting({
              capabilities: {
                ...defaultCapabilityRouting().capabilities,
                ...table.capabilities,
              },
              providers: table.providers ?? CAPABILITY_PROVIDERS,
            });
            setRoutingLoaded(true);
          }
        }
      })
      .catch(() => {
        if (!cancelled)
          setRoutingStatus("Tabel routing belum bisa dimuat; default tiap kemampuan dipakai.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadProviders();
  }, []);

  // V2-40: quota bars next to the model choice. Loaded whenever
  // the BYOK provider changes (and after a key is saved or
  // removed, through the same provider value), cleared otherwise.
  async function loadPoolQuota(provider: string) {
    setPoolQuotaLoaded(false);
    setPoolQuotaError(null);
    try {
      const data = await getPoolQuota(provider);
      setPoolQuota(data);
      setPoolQuotaLoaded(true);
    } catch (err) {
      setPoolQuotaError(err instanceof Error ? err.message : "error tak dikenal");
    }
  }

  useEffect(() => {
    if (modelSettings.source !== "byok") {
      setPoolQuota(null);
      setPoolQuotaLoaded(false);
      return;
    }
    const provider = modelSettings.provider?.trim().toLowerCase() || "openrouter";
    void loadPoolQuota(provider);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelSettings.source, modelSettings.provider, modelSettings.has_byok_key]);

  async function saveLanguageChoice(next: string) {
    setLanguageBusy(true);
    setLanguageStatus("");
    try {
      const saved = await saveLanguage(next);
      setLanguage(saved.locale);
      // Reload the catalog so the nav and the translated screens
      // flip to the new language without a page reload.
      await loadCatalog(saved.locale);
      setLanguageStatus(t("ui.settings.language.saved", "Bahasa tersimpan."));
    } catch (err) {
      setLanguageStatus(
        `Gagal menyimpan bahasa: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setLanguageBusy(false);
    }
  }

  async function saveModelChoice() {
    setModelBusy(true);
    setModelStatus("");
    try {
      const draft = withDefaults(modelSettings);
      if (draft.source === "local" && draft.base_url) {
        const error = validateBaseUrl(draft.base_url);
        if (error) {
          setModelStatus(error);
          return;
        }
      }
      const saved = await saveModelSettings({
        source: draft.source,
        base_url: draft.base_url,
        model: draft.model,
        provider: draft.provider,
      });
      setModelSettings(saved);
      setModelStatus("Pilihan model tersimpan.");
    } catch (err) {
      setModelStatus(
        `Gagal menyimpan pilihan model: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setModelBusy(false);
    }
  }

  async function saveKey() {
    setByokBusy(true);
    setModelStatus("");
    try {
      const provider = modelSettings.provider?.trim().toLowerCase() || "openrouter";
      if (!byokKey.trim()) {
        setModelStatus("Kunci BYOK tidak boleh kosong.");
        return;
      }
      await saveByokKey(provider, byokKey.trim());
      setByokKey("");
      setModelSettings({ ...modelSettings, provider, has_byok_key: true });
      setModelStatus(`Kunci ${provider} tersimpan di vault.`);
    } catch (err) {
      setModelStatus(
        `Gagal menyimpan kunci: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setByokBusy(false);
    }
  }

  async function removeKey() {
    setByokBusy(true);
    setModelStatus("");
    try {
      const provider = modelSettings.provider?.trim().toLowerCase() || "openrouter";
      await deleteByokKey(provider);
      setModelSettings({ ...modelSettings, has_byok_key: false });
      setModelStatus(`Kunci ${provider} dihapus dari vault.`);
    } catch (err) {
      setModelStatus(
        `Gagal menghapus kunci: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setByokBusy(false);
    }
  }

  async function saveVoiceChoice() {
    setVoiceBusy(true);
    setVoiceStatus("");
    try {
      const draft = withVoiceDefaults(voiceSettings);
      if (draft.asr_base_url) {
        const error = validateBaseUrl(draft.asr_base_url);
        if (error) {
          setVoiceStatus(error);
          return;
        }
      }
      if (draft.tts_base_url) {
        const error = validateBaseUrl(draft.tts_base_url);
        if (error) {
          setVoiceStatus(error);
          return;
        }
      }
      const saved = await saveVoiceSettings({
        asr_provider: draft.asr_provider,
        asr_base_url: draft.asr_base_url,
        asr_model: draft.asr_model,
        tts_provider: draft.tts_provider,
        tts_voice: draft.tts_voice,
        tts_model: draft.tts_model,
        tts_base_url: draft.tts_base_url,
        tts_region: draft.tts_region,
      });
      setVoiceSettings(saved);
      setVoiceStatus("Pilihan suara tersimpan.");
    } catch (err) {
      setVoiceStatus(
        `Gagal menyimpan pilihan suara: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setVoiceBusy(false);
    }
  }

  function voiceKeyProvider(kind: "asr" | "tts"): string {
    return kind === "asr" ? voiceSettings.asr_provider : voiceSettings.tts_provider;
  }

  function setRoutingDraft(capability: CapabilityName, patch: Partial<CapabilityRoute>) {
    setRouting((current) => ({
      ...current,
      capabilities: {
        ...current.capabilities,
        [capability]: { ...current.capabilities[capability], ...patch },
      },
    }));
  }

  async function saveRoutingChoice(capability: CapabilityName) {
    const draft = routing.capabilities[capability];
    if (!draft.provider) {
      setRoutingStatus(`Pilih dulu provider untuk ${capability}.`);
      return;
    }
    if (draft.base_url) {
      const error = validateBaseUrl(draft.base_url);
      if (error) {
        setRoutingStatus(error);
        return;
      }
    }
    setRoutingBusy(capability);
    setRoutingStatus("");
    try {
      const saved = await saveCapabilityRoute(capability, {
        provider: draft.provider,
        model: draft.model?.trim() ? draft.model.trim() : null,
        base_url: draft.base_url?.trim() ? draft.base_url.trim() : null,
      });
      setRouting((current) => ({
        ...current,
        capabilities: { ...current.capabilities, [capability]: saved },
      }));
      setRoutingStatus(`Routing ${capability} tersimpan: ${saved.provider}.`);
    } catch (err) {
      setRoutingStatus(
        `Gagal menyimpan routing ${capability}: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setRoutingBusy(null);
    }
  }

  async function resetRoutingChoice(capability: CapabilityName) {
    setRoutingBusy(capability);
    setRoutingStatus("");
    try {
      const reset = await resetCapabilityRoute(capability);
      setRouting((current) => ({
        ...current,
        capabilities: { ...current.capabilities, [capability]: reset },
      }));
      setRoutingStatus(`Routing ${capability} kembali ke default.`);
    } catch (err) {
      setRoutingStatus(
        `Gagal mereset routing ${capability}: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setRoutingBusy(null);
    }
  }

  async function saveRoutingKeyChoice() {
    setRoutingKeyBusy(true);
    setRoutingStatus("");
    try {
      const provider = routingKeyProvider.trim().toLowerCase();
      if (!routingKey.trim()) {
        setRoutingStatus("Kunci routing tidak boleh kosong.");
        return;
      }
      await saveRoutingKey(provider, routingKey.trim());
      setRoutingKey("");
      setRouting((current) => ({
        ...current,
        capabilities: Object.fromEntries(
          Object.entries(current.capabilities).map(([name, route]) => [
            name,
            route.provider === provider ? { ...route, has_key: true } : route,
          ]),
        ) as Record<CapabilityName, CapabilityRoute>,
      }));
      setRoutingStatus(`Kunci ${provider} tersimpan di vault.`);
    } catch (err) {
      setRoutingStatus(
        `Gagal menyimpan kunci: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setRoutingKeyBusy(false);
    }
  }

  async function removeRoutingKeyChoice() {
    setRoutingKeyBusy(true);
    setRoutingStatus("");
    try {
      const provider = routingKeyProvider.trim().toLowerCase();
      await deleteRoutingKey(provider);
      setRouting((current) => ({
        ...current,
        capabilities: Object.fromEntries(
          Object.entries(current.capabilities).map(([name, route]) => [
            name,
            route.provider === provider ? { ...route, has_key: false } : route,
          ]),
        ) as Record<CapabilityName, CapabilityRoute>,
      }));
      setRoutingStatus(`Kunci ${provider} dihapus dari vault.`);
    } catch (err) {
      setRoutingStatus(
        `Gagal menghapus kunci: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setRoutingKeyBusy(false);
    }
  }

  async function saveVoiceKeyForKind() {
    setVoiceKeyBusy(true);
    setVoiceStatus("");
    try {
      const provider = voiceKeyProvider(voiceKeyKind);
      if (!voiceKey.trim()) {
        setVoiceStatus("Kunci suara tidak boleh kosong.");
        return;
      }
      await saveVoiceKey(voiceKeyKind, provider, voiceKey.trim());
      setVoiceKey("");
      setVoiceSettings({
        ...voiceSettings,
        has_asr_key: voiceKeyKind === "asr" ? true : voiceSettings.has_asr_key,
        has_tts_key: voiceKeyKind === "tts" ? true : voiceSettings.has_tts_key,
      });
      setVoiceStatus(`Kunci suara ${provider} tersimpan di vault.`);
    } catch (err) {
      setVoiceStatus(
        `Gagal menyimpan kunci suara: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setVoiceKeyBusy(false);
    }
  }

  async function removeVoiceKeyForKind() {
    setVoiceKeyBusy(true);
    setVoiceStatus("");
    try {
      const provider = voiceKeyProvider(voiceKeyKind);
      await deleteVoiceKey(voiceKeyKind, provider);
      setVoiceSettings({
        ...voiceSettings,
        has_asr_key: voiceKeyKind === "asr" ? false : voiceSettings.has_asr_key,
        has_tts_key: voiceKeyKind === "tts" ? false : voiceSettings.has_tts_key,
      });
      setVoiceStatus(`Kunci suara ${provider} dihapus dari vault.`);
    } catch (err) {
      setVoiceStatus(
        `Gagal menghapus kunci suara: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setVoiceKeyBusy(false);
    }
  }

  async function saveTelemetryChoice() {
    setTelemetryBusy(true);
    setTelemetryStatus("");
    try {
      const saved = await saveTelemetrySettings({ enabled: telemetrySettings.enabled });
      setTelemetrySettings(withTelemetryDefaults(saved));
      setTelemetryStatus("Pilihan telemetri tersimpan.");
    } catch (err) {
      setTelemetryStatus(
        `Gagal menyimpan pilihan telemetri: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setTelemetryBusy(false);
    }
  }

  async function enableNotifications() {
    setPushBusy(true);
    setPushStatus("");
    try {
      if (!("Notification" in window) || !("serviceWorker" in navigator)) {
        setPushStatus(
          "Peringatan: notifikasi tidak bisa menjangkau kamu di perangkat ini.",
        );
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setPushStatus(
          "Peringatan: izin notifikasi tidak diberikan; pengingat hanya tampil di aplikasi.",
        );
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const vapidKey = import.meta.env.VITE_VAPID_PUBLIC_KEY as string | undefined;
      if (!vapidKey) {
        setPushStatus(
          "Notifikasi diizinkan di perangkat ini, tapi kunci VAPID belum dikonfigurasi di server (VITE_VAPID_PUBLIC_KEY kosong), jadi langganan push belum disimpan.",
        );
        return;
      }
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      });
      const payload = subscription.toJSON();
      await subscribePush({
        endpoint: payload.endpoint ?? "",
        keys: {
          p256dh: payload.keys?.p256dh ?? "",
          auth: payload.keys?.auth ?? "",
        },
      });
      setPushStatus("Notifikasi diizinkan dan langganan push tersimpan di server.");
    } catch (err) {
      setPushStatus(
        `Gagal mengaktifkan notifikasi: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setPushBusy(false);
    }
  }

  return (
    <div>
      <h1>Pengaturan</h1>
      <div className="card">
        <strong>Foto agen</strong>
        <p>Ganti foto profil agen. Tanpa foto kustom, logo Puspa dipakai.</p>
        <AgentPhotoPicker />
      </div>
      <div className="card">
        <strong>Batas belanja</strong>
        <p>Default Rp0 tanpa persetujuan. Agen tidak bisa menaikkannya sendiri.</p>
      </div>
      <div className="card">
        <strong>Model</strong>
        <p>Pilih model yang dipakai Puspa. Default hosted tidak perlu pengaturan apa pun.</p>
        {MODEL_SOURCES.map((source) => (
          <label key={source} style={{ display: "block", marginBottom: "0.5rem" }}>
            <input
              type="radio"
              name="model-source"
              value={source}
              checked={modelSettings.source === source}
              onChange={() => setModelSettings({ ...modelSettings, source })}
            />{" "}
            {source === "hosted" ? "Hosted (default)" : source === "byok" ? "Kunci sendiri (BYOK)" : "Model lokal (Ollama)"}
            <span className="muted"> — {describeSource(source)}</span>
          </label>
        ))}
        {modelSettings.source === "byok" ? (
          <div>
            <label>
              Provider{" "}
              <input
                type="text"
                value={modelSettings.provider ?? ""}
                placeholder="openrouter"
                onChange={(event) =>
                  setModelSettings({ ...modelSettings, provider: event.target.value })
                }
              />
            </label>
            <label>
              Model (opsional){" "}
              <input
                type="text"
                value={modelSettings.model ?? ""}
                placeholder="nama model, kosongkan untuk default"
                onChange={(event) =>
                  setModelSettings({ ...modelSettings, model: event.target.value })
                }
              />
            </label>
            <label>
              Base URL (opsional){" "}
              <input
                type="text"
                value={modelSettings.base_url ?? ""}
                placeholder="https://api.provider.example/v1"
                onChange={(event) =>
                  setModelSettings({ ...modelSettings, base_url: event.target.value })
                }
              />
            </label>
            <p className="muted">
              {modelSettings.has_byok_key
                ? "Kunci BYOK sudah tersimpan di vault."
                : "Belum ada kunci BYOK tersimpan di vault."}
            </p>
            <label>
              Kunci API{" "}
              <input
                type="password"
                value={byokKey}
                autoComplete="off"
                placeholder="tempel kunci, lalu simpan"
                onChange={(event) => setByokKey(event.target.value)}
              />
            </label>
            <button type="button" onClick={saveKey} disabled={byokBusy}>
              {byokBusy ? "Menyimpan…" : "Simpan kunci ke vault"}
            </button>{" "}
            {modelSettings.has_byok_key ? (
              <button type="button" onClick={removeKey} disabled={byokBusy}>
                Hapus kunci
              </button>
            ) : null}
            {/* V2-40: quota bars next to the model choice (gsb /ui pattern). */}
            {poolQuota !== null && poolQuota.accounts.length > 0 ? (
              <div style={{ marginTop: "0.5rem" }}>
                <strong>Kuota akun ({poolQuota.strategy})</strong>
                {poolQuota.accounts.map((account) => (
                  <div key={account.account_id} style={{ marginTop: "0.5rem" }}>
                    <span>{account.label}</span>{" "}
                    <span className="muted">
                      {account.available
                        ? "siap dipakai"
                        : `tidak tersedia (${account.skip_reason ?? "?"})`}
                    </span>
                    {account.windows.map((window) => (
                      <div key={window.window}>
                        <span className="muted">{window.window === "5h" ? "5 jam" : "7 hari"}: </span>
                        {window.used_fraction === null || window.used_fraction === undefined ? (
                          <span className="muted">belum terlacak (limit belum didaftarkan)</span>
                        ) : (
                          <>
                            <span className="quota-bar">
                              <span style={{ width: `${Math.round(window.used_fraction * 100)}%` }} />
                            </span>{" "}
                            <span className="muted">
                              {Math.round(window.used_fraction * 100)}% terpakai
                              {window.tokens_remaining !== null &&
                              window.tokens_remaining !== undefined
                                ? `, sisa ${window.tokens_remaining} token`
                                : ""}
                              {window.reset_in_seconds !== null &&
                              window.reset_in_seconds !== undefined
                                ? `, reset dalam ${formatQuotaReset(window.reset_in_seconds)}`
                                : ""}
                            </span>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : null}
            {poolQuotaLoaded && poolQuota !== null && poolQuota.accounts.length === 0 ? (
              <p className="muted">Belum ada akun di pool untuk provider ini.</p>
            ) : null}
            {poolQuotaError ? (
              <p className="muted">Kuota belum bisa dimuat: {poolQuotaError}</p>
            ) : null}
          </div>
        ) : null}
        {modelSettings.source === "local" ? (
          <div>
            <label>
              Base URL{" "}
              <input
                type="text"
                value={modelSettings.base_url ?? ""}
                placeholder={DEFAULT_LOCAL_BASE_URL}
                onChange={(event) =>
                  setModelSettings({ ...modelSettings, base_url: event.target.value })
                }
              />
            </label>
            <label>
              Model{" "}
              <input
                type="text"
                value={modelSettings.model ?? ""}
                placeholder={DEFAULT_LOCAL_MODEL}
                onChange={(event) =>
                  setModelSettings({ ...modelSettings, model: event.target.value })
                }
              />
            </label>
            <p className="muted">
              Model lokal butuh hardware beneran; untuk kebanyakan pengguna, BYOK adalah jalur
              yang realistis, lokal buat yang punya GPU atau sabar dengan CPU.
            </p>
          </div>
        ) : null}
        <button type="button" onClick={saveModelChoice} disabled={modelBusy || !modelLoaded}>
          {modelBusy ? "Menyimpan…" : "Simpan pilihan model"}
        </button>
        {modelStatus ? <p className="muted">{modelStatus}</p> : null}
      </div>
      <div className="card">
        <strong>Routing per kemampuan</strong>
        <p>
          Tiap kemampuan bisa memakai provider berbeda: chat, vision, gambar, suara, dan
          embeddings. Satu klik menyimpan satu kemampuan; kemampuan lain tidak ikut berubah.
          Provider yang tidak bisa melayani suatu kemampuan ditolak dengan pesan yang jelas.
        </p>
        {CAPABILITIES.map((capability) => {
          const route =
            routing.capabilities[capability] ??
            defaultCapabilityRouting().capabilities[capability];
          const options =
            routing.providers?.[capability] ?? CAPABILITY_PROVIDERS[capability];
          return (
            <div key={capability} style={{ marginBottom: "1rem" }}>
              <strong>{capability}</strong>
              <span className="muted"> — {describeCapability(capability)}</span>
              <label>
                Provider{" "}
                <select
                  value={route.provider ?? ""}
                  disabled={!routingLoaded}
                  onChange={(event) =>
                    setRoutingDraft(capability, {
                      provider: event.target.value || null,
                    })
                  }
                >
                  <option value="">— belum dipilih —</option>
                  {options.map((provider) => (
                    <option key={provider} value={provider}>
                      {provider}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Nama model{" "}
                <input
                  type="text"
                  value={route.model ?? ""}
                  placeholder={
                    route.provider === "hosted"
                      ? "default router"
                      : "nama model di provider itu"
                  }
                  disabled={!routingLoaded}
                  onChange={(event) =>
                    setRoutingDraft(capability, { model: event.target.value })
                  }
                />
              </label>
              <label>
                Base URL (opsional){" "}
                <input
                  type="text"
                  value={route.base_url ?? ""}
                  placeholder="https://api.provider.example/v1"
                  disabled={!routingLoaded}
                  onChange={(event) =>
                    setRoutingDraft(capability, { base_url: event.target.value })
                  }
                />
              </label>
              <p className="muted">
                {route.is_default
                  ? "Memakai default."
                  : `Tersimpan: ${route.provider ?? "—"}${route.model ? ` / ${route.model}` : ""}.`}
                {route.has_key ? " Kunci sudah tersimpan di vault." : ""}
              </p>
              <button
                type="button"
                onClick={() => void saveRoutingChoice(capability)}
                disabled={!routingLoaded || routingBusy === capability}
              >
                {routingBusy === capability ? "Menyimpan…" : `Simpan ${capability}`}
              </button>{" "}
              {!route.is_default ? (
                <button
                  type="button"
                  onClick={() => void resetRoutingChoice(capability)}
                  disabled={routingBusy === capability}
                >
                  Reset ke default
                </button>
              ) : null}
            </div>
          );
        })}
        <label>
          Kunci provider untuk routing{" "}
          <input
            type="text"
            value={routingKeyProvider}
            placeholder="openrouter"
            onChange={(event) => setRoutingKeyProvider(event.target.value)}
          />
        </label>
        <label>
          Kunci API{" "}
          <input
            type="password"
            value={routingKey}
            autoComplete="off"
            placeholder="tempel kunci routing, lalu simpan"
            onChange={(event) => setRoutingKey(event.target.value)}
          />
        </label>
        <button type="button" onClick={() => void saveRoutingKeyChoice()} disabled={routingKeyBusy}>
          {routingKeyBusy ? "Menyimpan…" : "Simpan kunci ke vault"}
        </button>{" "}
        <button
          type="button"
          onClick={() => void removeRoutingKeyChoice()}
          disabled={routingKeyBusy}
        >
          Hapus kunci
        </button>
        <p className="muted">
          Satu kunci per provider dipakai semua kemampuan yang di-routing ke provider itu.
        </p>
        {routingStatus ? <p className="muted">{routingStatus}</p> : null}
      </div>
      <div className="card">
        <strong>Suara</strong>
        <p>
          Transkripsi pesan suara dan suara balasan. Default: Edge TTS gratis (suara neural
          Microsoft, termasuk bahasa Indonesia) tanpa kunci apa pun; kalau Edge diblokir, browser
          yang membacakan.
        </p>
        <label htmlFor="voice-asr-provider">Transkripsi (suara ke teks)</label>
        <select
          id="voice-asr-provider"
          value={voiceSettings.asr_provider}
          aria-describedby="voice-asr-hint"
          onChange={(event) =>
            setVoiceSettings({
              ...voiceSettings,
              asr_provider: event.target.value as VoiceSettings["asr_provider"],
            })
          }
        >
          {ASR_PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {provider === "none" ? "Tidak ada (stub)" : "Endpoint speech sendiri (BYOK)"}
            </option>
          ))}
        </select>
        <p id="voice-asr-hint" className="muted">
          {describeAsrProvider(voiceSettings.asr_provider)}
        </p>
        {voiceSettings.asr_provider === "openai-compatible" ? (
          <div>
            <label htmlFor="voice-asr-base-url">Base URL endpoint speech</label>
            <input
              id="voice-asr-base-url"
              type="text"
              value={voiceSettings.asr_base_url ?? ""}
              placeholder="https://api.openai.com/v1"
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, asr_base_url: event.target.value })
              }
            />
            <label htmlFor="voice-asr-model">Model (opsional)</label>
            <input
              id="voice-asr-model"
              type="text"
              value={voiceSettings.asr_model ?? ""}
              placeholder="whisper-1"
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, asr_model: event.target.value })
              }
            />
            <p className="muted">
              {voiceSettings.has_asr_key
                ? "Kunci ASR sudah tersimpan di vault."
                : "Belum ada kunci ASR tersimpan di vault."}
            </p>
          </div>
        ) : null}
        <label htmlFor="voice-tts-provider">Suara balasan (teks ke suara)</label>
        <select
          id="voice-tts-provider"
          value={voiceSettings.tts_provider}
          aria-describedby="voice-tts-hint"
          onChange={(event) =>
            setVoiceSettings({
              ...voiceSettings,
              tts_provider: event.target.value as VoiceSettings["tts_provider"],
            })
          }
        >
          {TTS_PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {provider === "edge"
                ? "Edge TTS (default, gratis)"
                : provider === "browser"
                  ? "Suara browser"
                  : provider === "elevenlabs"
                    ? "ElevenLabs (BYOK)"
                    : provider === "openai-compatible"
                      ? "Endpoint TTS sendiri (BYOK)"
                      : "Azure Speech F0 (BYOK)"}
            </option>
          ))}
        </select>
        <p id="voice-tts-hint" className="muted">
          {describeTtsProvider(voiceSettings.tts_provider)}
        </p>
        {voiceSettings.tts_provider === "edge" ? (
          <div>
            <label htmlFor="voice-edge-voice">Suara Edge</label>
            <select
              id="voice-edge-voice"
              value={voiceSettings.tts_voice ?? ""}
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, tts_voice: event.target.value })
              }
            >
              {EDGE_TTS_VOICES.map((voice) => (
                <option key={voice.id} value={voice.id}>
                  {voice.label}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {voiceSettings.tts_provider === "elevenlabs" ? (
          <div>
            <label htmlFor="voice-elevenlabs-id">Voice ID ElevenLabs</label>
            <input
              id="voice-elevenlabs-id"
              type="text"
              value={voiceSettings.tts_voice ?? ""}
              placeholder="id suara ElevenLabs"
              aria-describedby="voice-elevenlabs-hint"
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, tts_voice: event.target.value })
              }
            />
            <p id="voice-elevenlabs-hint" className="muted">
              {voiceSettings.has_tts_key
                ? "Kunci ElevenLabs sudah tersimpan di vault."
                : "Belum ada kunci ElevenLabs tersimpan di vault."}
            </p>
          </div>
        ) : null}
        {voiceSettings.tts_provider === "openai-compatible" ? (
          <div>
            <label htmlFor="voice-tts-base-url">Base URL endpoint TTS</label>
            <input
              id="voice-tts-base-url"
              type="text"
              value={voiceSettings.tts_base_url ?? ""}
              placeholder="https://api.openai.com/v1"
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, tts_base_url: event.target.value })
              }
            />
            <label htmlFor="voice-tts-model">Model (opsional)</label>
            <input
              id="voice-tts-model"
              type="text"
              value={voiceSettings.tts_model ?? ""}
              placeholder="tts-1"
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, tts_model: event.target.value })
              }
            />
            <label htmlFor="voice-tts-voice">Suara (opsional)</label>
            <input
              id="voice-tts-voice"
              type="text"
              value={voiceSettings.tts_voice ?? ""}
              placeholder="alloy"
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, tts_voice: event.target.value })
              }
            />
            <p className="muted">
              {voiceSettings.has_tts_key
                ? "Kunci TTS sudah tersimpan di vault."
                : "Belum ada kunci TTS tersimpan di vault."}
            </p>
          </div>
        ) : null}
        {voiceSettings.tts_provider === "azure" ? (
          <div>
            <label htmlFor="voice-azure-region">Region Azure</label>
            <input
              id="voice-azure-region"
              type="text"
              value={voiceSettings.tts_region ?? ""}
              placeholder="southeastasia"
              aria-describedby="voice-azure-hint"
              onChange={(event) =>
                setVoiceSettings({ ...voiceSettings, tts_region: event.target.value })
              }
            />
            <p id="voice-azure-hint" className="muted">
              Tier F0 gratis selamanya, tapi butuh akun Azure.{" "}
              {voiceSettings.has_tts_key
                ? "Kunci Azure sudah tersimpan di vault."
                : "Belum ada kunci Azure tersimpan di vault."}
            </p>
          </div>
        ) : null}
        <button type="button" onClick={saveVoiceChoice} disabled={voiceBusy || !voiceLoaded}>
          {voiceBusy ? "Menyimpan…" : "Simpan pilihan suara"}
        </button>
        {(voiceSettings.asr_provider === "openai-compatible" ||
          ttsProviderNeedsKey(voiceSettings.tts_provider)) && (
          <div>
            <label htmlFor="voice-key-kind">Kunci untuk</label>
            <select
              id="voice-key-kind"
              value={voiceKeyKind}
              onChange={(event) =>
                setVoiceKeyKind(event.target.value as "asr" | "tts")
              }
            >
              {voiceSettings.asr_provider === "openai-compatible" ? (
                <option value="asr">Transkripsi (ASR)</option>
              ) : null}
              {ttsProviderNeedsKey(voiceSettings.tts_provider) ? (
                <option value="tts">Suara balasan (TTS)</option>
              ) : null}
            </select>
            <label htmlFor="voice-key">Kunci API</label>
            <input
              id="voice-key"
              type="password"
              value={voiceKey}
              autoComplete="off"
              placeholder="tempel kunci, lalu simpan"
              onChange={(event) => setVoiceKey(event.target.value)}
            />
            <button type="button" onClick={saveVoiceKeyForKind} disabled={voiceKeyBusy}>
              {voiceKeyBusy ? "Menyimpan…" : "Simpan kunci suara ke vault"}
            </button>{" "}
            {(voiceKeyKind === "asr" ? voiceSettings.has_asr_key : voiceSettings.has_tts_key) ? (
              <button type="button" onClick={removeVoiceKeyForKind} disabled={voiceKeyBusy}>
                Hapus kunci suara
              </button>
            ) : null}
          </div>
        )}
        {voiceStatus ? (
          <p role="status" className="muted">
            {voiceStatus}
          </p>
        ) : null}
      </div>
      <div className="card">
        <strong>Statistik pemakaian (telemetri)</strong>
        <p>
          Statistik pemakaian anonim untuk memandu perbaikan Puspa: jumlah pemakaian fitur dan
          durasi, tanpa isi pesan, tanpa identitas, tanpa token. Mati secara default untuk
          instalasi self-host.
        </p>
        <label>
          <input
            type="checkbox"
            checked={telemetrySettings.env_override ?? telemetrySettings.enabled}
            disabled={telemetrySettings.env_override !== null}
            onChange={(event) =>
              setTelemetrySettings({ ...telemetrySettings, enabled: event.target.checked })
            }
          />{" "}
          Kirim statistik pemakaian anonim
        </label>
        <p className="muted">{describeTelemetry(telemetrySettings)}</p>
        <button
          type="button"
          onClick={saveTelemetryChoice}
          disabled={
            telemetryBusy || !telemetryLoaded || telemetrySettings.env_override !== null
          }
        >
          {telemetryBusy ? "Menyimpan…" : "Simpan pilihan telemetri"}
        </button>
        {telemetryStatus ? (
          <p role="status" className="muted">
            {telemetryStatus}
          </p>
        ) : null}
      </div>
      <div className="card">
        <ProviderLoginPanel
          providers={providers}
          loading={!providersLoaded}
          error={providersError}
          onStartLogin={(provider) => startProviderLogin(provider)}
          onPollDevice={(provider, pollKey) => pollProviderDeviceLogin(provider, pollKey)}
          onFinishToken={async (provider, token) => {
            await finishProviderTokenLogin(provider, token);
          }}
          onLogout={async (provider) => {
            await logoutProvider(provider);
          }}
          onRefresh={() => void loadProviders()}
        />
      </div>
      <div className="card">
        <strong>{t("ui.settings.language.title", "Bahasa")}</strong>
        <p>
          {t(
            "ui.settings.language.description",
            "Bahasa tampilan Puspa. Pilihan tersimpan untuk akun ini dan dipakai di semua layar.",
          )}
        </p>
        <label htmlFor="language-select">
          {t("ui.settings.language.title", "Bahasa")}
        </label>
        <select
          id="language-select"
          value={language}
          disabled={languageBusy || !languageLoaded}
          onChange={(event) => void saveLanguageChoice(event.target.value)}
        >
          {LANGUAGE_OPTIONS.map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </select>
        {languageStatus ? (
          <p role="status" className="muted">
            {languageStatus}
          </p>
        ) : null}
      </div>
      <div className="card">
        <strong>Notifikasi</strong>
        <p>
          Web Push (VAPID). Di iOS, push hanya jalan kalau PWA dipasang ke layar utama; onboarding
          menguji kirim dan menampilkan peringatan kalau notif tidak bisa menjangkau kamu.
        </p>
        <button type="button" onClick={enableNotifications} disabled={pushBusy}>
          {pushBusy ? "Mengaktifkan…" : "Izinkan notifikasi + uji kirim"}
        </button>
        {pushStatus ? <p className="muted">{pushStatus}</p> : null}
      </div>
      <div className="card">
        <strong>Akun dan perangkat</strong>
        <p>Daftar sesi dan perangkat bisa dilihat dan dicabut (sesi Fase 0).</p>
      </div>
      <div className="card">
        <strong>Persetujuan data</strong>
        <p>
          Teks v1-2026-11-17 (Fase 1). Persetujuan dicatat di Buku Kerja. Sebelum akses apa pun,
          kamu membaca dan menyetujui satu layar pendek: data apa yang disimpan, di mana, berapa
          lama, siapa yang bisa melihat, cara ekspor dan hapus.
        </p>
      </div>
      <div className="card">
        <strong>Rekor Otonomi</strong>
        <p>
          Rekor dikumpulkan dari persetujuan yang kamu beri; setelah 10 draf diterima
          berturut-turut di satu area, panel dial di bawah menawarkan naik satu tingkat. Uang
          tetap tanya dulu.
        </p>
      </div>
      <AutonomyDialPanel />
      <ComputerPanel />
    </div>
  );
}
