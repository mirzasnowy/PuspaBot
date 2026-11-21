import { useEffect, useState } from "react";
import {
  deleteByokKey,
  getModelSettings,
  getProviders,
  logoutProvider,
  pollProviderDeviceLogin,
  saveByokKey,
  saveModelSettings,
  startProviderLogin,
  subscribePush,
  type ModelSettings,
  type ProviderStatus,
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
        <ProviderLoginPanel
          providers={providers}
          loading={!providersLoaded}
          error={providersError}
          onStartLogin={(provider) => startProviderLogin(provider)}
          onPollDevice={(provider, pollKey) => pollProviderDeviceLogin(provider, pollKey)}
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
