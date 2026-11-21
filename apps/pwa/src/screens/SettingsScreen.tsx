import { useEffect, useState } from "react";
import {
  deleteByokKey,
  deleteVoiceKey,
  finishProviderTokenLogin,
  getModelSettings,
  getProviders,
  getVoiceSettings,
  logoutProvider,
  pollProviderDeviceLogin,
  saveByokKey,
  saveModelSettings,
  saveVoiceKey,
  saveVoiceSettings,
  startProviderLogin,
  subscribePush,
  type ModelSettings,
  type ProviderStatus,
  type VoiceSettings,
} from "../api/client";
import { AutonomyDialPanel } from "../components/AutonomyDialPanel";
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

/** Base64url -> Uint8Array for the VAPID applicationServerKey. */
function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const decoded = atob(base64.replace(/-/g, "+").replace(/_/g, "/") + padding);
  const bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
  return bytes;
}

export function SettingsScreen() {
  const [pushStatus, setPushStatus] = useState("");
  const [pushBusy, setPushBusy] = useState(false);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings());
  const [modelLoaded, setModelLoaded] = useState(false);
  const [modelStatus, setModelStatus] = useState("");
  const [modelBusy, setModelBusy] = useState(false);
  const [byokKey, setByokKey] = useState("");
  const [byokBusy, setByokBusy] = useState(false);
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
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadProviders();
  }, []);

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
        <strong>Bahasa</strong>
        <p>Indonesia.</p>
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
    </div>
  );
}
