import { useEffect, useRef, useState } from "react";
import type {
  ProviderLoginStart,
  ProviderStatus,
} from "../api/client";

/**
 * Subscription-provider login panel (V2-28).
 *
 * One login button per provider, running the same OAuth flow the
 * terminal runs (`puspa provider login`): PKCE providers open an
 * authorize URL in a new tab, device-flow providers show a user
 * code and a verification link and poll until approved. Tokens
 * live only in the server-side vault; this panel never sees one.
 * Per-provider state (logged in / expiring / expired / not logged
 * in) comes from the parent, which loads it from /api/providers.
 *
 * Accessibility (modern-web-guidance: accessibility, forms):
 * native <button> elements with a distinct accessible name per
 * provider, status messages in an aria-live region, and the login
 * button disabled while its own login is in flight (to prevent
 * double submits) rather than to block validation.
 */

const PROVIDER_LABELS: Record<string, string> = {
  xai: "xAI (Grok)",
  github_copilot: "GitHub Copilot",
  openai_codex: "Codex",
};

const STATE_LABELS: Record<string, string> = {
  logged_in: "Masuk",
  expiring: "Segera kedaluwarsa",
  expired: "Kedaluwarsa",
  not_logged_in: "Belum masuk",
};

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

interface DevicePrompt {
  userCode: string;
  verificationUri: string;
  pollKey: string;
  expiresIn: number;
}

interface ProviderLoginPanelProps {
  providers: ProviderStatus[];
  loading: boolean;
  error: string | null;
  onStartLogin: (provider: string) => Promise<ProviderLoginStart>;
  onPollDevice: (provider: string, pollKey: string) => Promise<{ status: string }>;
  onLogout: (provider: string) => Promise<void>;
  onRefresh: () => void;
}

export function ProviderLoginPanel({
  providers,
  loading,
  error,
  onStartLogin,
  onPollDevice,
  onLogout,
  onRefresh,
}: ProviderLoginPanelProps) {
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [device, setDevice] = useState<{ provider: string; prompt: DevicePrompt } | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current !== null) clearTimeout(pollTimer.current);
    };
  }, []);

  async function attemptPoll(provider: string, prompt: DevicePrompt, intervalSeconds: number) {
    try {
      const result = await onPollDevice(provider, prompt.pollKey);
      if (result.status === "complete") {
        setDevice(null);
        setBusyProvider(null);
        setMessage(`Login ${providerLabel(provider)} berhasil.`);
        onRefresh();
        return;
      }
    } catch (err) {
      setDevice(null);
      setBusyProvider(null);
      setMessage(
        `Login ${providerLabel(provider)} gagal: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
      return;
    }
    pollTimer.current = setTimeout(
      () => void attemptPoll(provider, prompt, intervalSeconds),
      intervalSeconds * 1000,
    );
  }

  async function login(provider: string) {
    setBusyProvider(provider);
    setMessage("");
    try {
      const started = await onStartLogin(provider);
      if (started.flow === "pkce") {
        window.open(started.url, "_blank", "noopener");
        setBusyProvider(null);
        setMessage(
          `Tab login ${providerLabel(provider)} dibuka. Selesaikan di tab itu, lalu kembali ke sini.`,
        );
        onRefresh();
      } else {
        const prompt: DevicePrompt = {
          userCode: started.user_code,
          verificationUri: started.verification_uri,
          pollKey: started.poll_key,
          expiresIn: started.expires_in,
        };
        setDevice({ provider, prompt });
        setMessage(
          `Buka ${started.verification_uri} dan masukkan kode ${started.user_code}.`,
        );
        void attemptPoll(provider, prompt, Math.max(1, started.interval));
      }
    } catch (err) {
      setBusyProvider(null);
      setMessage(
        `Login ${providerLabel(provider)} gagal: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    }
  }

  async function logout(provider: string) {
    setBusyProvider(provider);
    setMessage("");
    try {
      await onLogout(provider);
      setMessage(`Logout ${providerLabel(provider)} berhasil.`);
      onRefresh();
    } catch (err) {
      setMessage(
        `Logout ${providerLabel(provider)} gagal: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusyProvider(null);
    }
  }

  return (
    <section aria-labelledby="provider-login-heading">
      <h3 id="provider-login-heading">Login Langganan</h3>
      <p>
        Masuk ke langganan AI (xAI, GitHub Copilot, Codex) tanpa nanobot. Token hanya
        tersimpan di vault kamu, tidak pernah ditampilkan di sini.
      </p>
      {loading ? <p>Memuat status login...</p> : null}
      {error ? (
        <p role="alert">Status login belum bisa dimuat: {error}</p>
      ) : null}
      <p aria-live="polite">{message}</p>
      <ul>
        {providers.map((entry) => {
          const busy = busyProvider === entry.provider;
          const loggedIn = entry.state === "logged_in" || entry.state === "expiring";
          const deviceForThis =
            device !== null && device.provider === entry.provider ? device.prompt : null;
          return (
            <li key={entry.provider}>
              <span>
                {providerLabel(entry.provider)}: {STATE_LABELS[entry.state] ?? entry.state}
              </span>
              {entry.expires_at ? <span> (berlaku sampai {entry.expires_at})</span> : null}
              {!entry.login_ready && entry.state === "not_logged_in" ? (
                <span> Login belum tersedia untuk provider ini.</span>
              ) : null}
              {loggedIn ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void logout(entry.provider)}
                  aria-label={`Logout ${providerLabel(entry.provider)}`}
                >
                  Logout
                </button>
              ) : (
                <button
                  type="button"
                  disabled={busy || !entry.login_ready}
                  onClick={() => void login(entry.provider)}
                  aria-label={`Login ${providerLabel(entry.provider)}`}
                >
                  {busy ? "Menunggu..." : "Login"}
                </button>
              )}
              {deviceForThis ? (
                <p>
                  Buka{" "}
                  <a href={deviceForThis.verificationUri} target="_blank" rel="noreferrer">
                    {deviceForThis.verificationUri}
                  </a>{" "}
                  dan masukkan kode <strong>{deviceForThis.userCode}</strong>. Menunggu
                  persetujuan...
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
