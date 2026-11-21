import { useEffect, useRef, useState } from "react";
import type {
  ProviderLoginStart,
  ProviderStatus,
} from "../api/client";

/**
 * Subscription-provider login panel (V2-28, V2-29).
 *
 * One login button per provider, running the same OAuth flow the
 * terminal runs (`puspa provider login`): PKCE providers open an
 * authorize URL in a new tab, device-flow providers show a user
 * code and a verification link and poll until approved, polling
 * providers (GLM, CodeBuddy) show a sign-in link and poll until
 * the token is ready, and pasted-token providers (Claude setup
 * token, Cursor import, Cline blob) show a form to paste the
 * token. Tokens live only in the server-side vault; this panel
 * never sees one. Per-provider state (logged in / expiring /
 * expired / not logged in) comes from the parent, which loads it
 * from /api/providers.
 *
 * Accessibility (modern-web-guidance: accessibility, forms):
 * native <button> elements with a distinct accessible name per
 * provider, status messages in an aria-live region, and the login
 * button disabled while its own login is in flight (to prevent
 * double submits) rather than to block validation. The paste
 * form uses a visible <label> bound to the input, helper text
 * via aria-describedby, a password input so the pasted token is
 * not shown on screen, and autocomplete off for the token field.
 */

const PROVIDER_LABELS: Record<string, string> = {
  antigravity: "Antigravity",
  claude: "Claude",
  cline: "Cline",
  codebuddy_cn: "CodeBuddy (CN)",
  codebuddy_intl: "CodeBuddy",
  cursor: "Cursor",
  gemini_cli: "Gemini CLI",
  github_copilot: "GitHub Copilot",
  gitlab: "GitLab Duo",
  glm: "GLM (Z.ai)",
  grok_cli: "Grok CLI",
  iflow: "iFlow",
  kilocode: "KiloCode",
  kimi: "Kimi",
  kiro: "Kiro",
  minimax_code: "MiniMax Code (CN)",
  minimax_code_global: "MiniMax Code",
  muse: "Muse",
  nous: "Nous",
  openai_codex: "Codex",
  qoder: "Qoder",
  qoder_cn: "Qoder (CN)",
  xai: "xAI (Grok)",
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

interface PollingPrompt {
  verificationUri: string;
  pollKey: string;
  expiresIn: number;
}

interface PastePrompt {
  flow: "setup_token" | "import_token" | "encoded_token";
  help: string;
  tokenPrefix: string | null;
}

interface ProviderLoginPanelProps {
  providers: ProviderStatus[];
  loading: boolean;
  error: string | null;
  onStartLogin: (provider: string) => Promise<ProviderLoginStart>;
  onPollDevice: (provider: string, pollKey: string) => Promise<{ status: string }>;
  onFinishToken: (provider: string, token: string) => Promise<void>;
  onLogout: (provider: string) => Promise<void>;
  onRefresh: () => void;
}

export function ProviderLoginPanel({
  providers,
  loading,
  error,
  onStartLogin,
  onPollDevice,
  onFinishToken,
  onLogout,
  onRefresh,
}: ProviderLoginPanelProps) {
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [device, setDevice] = useState<{ provider: string; prompt: DevicePrompt } | null>(null);
  const [polling, setPolling] = useState<{ provider: string; prompt: PollingPrompt } | null>(null);
  const [paste, setPaste] = useState<{ provider: string; prompt: PastePrompt } | null>(null);
  const [pasteValue, setPasteValue] = useState("");
  const [pasteBusy, setPasteBusy] = useState(false);
  const [pasteError, setPasteError] = useState("");
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current !== null) clearTimeout(pollTimer.current);
    };
  }, []);

  async function attemptPoll(provider: string, prompt: DevicePrompt | PollingPrompt, intervalSeconds: number) {
    try {
      const result = await onPollDevice(provider, prompt.pollKey);
      if (result.status === "complete") {
        setDevice(null);
        setPolling(null);
        setBusyProvider(null);
        setMessage(`Login ${providerLabel(provider)} berhasil.`);
        onRefresh();
        return;
      }
    } catch (err) {
      setDevice(null);
      setPolling(null);
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
    setPasteError("");
    try {
      const started = await onStartLogin(provider);
      if (started.flow === "pkce") {
        window.open(started.url, "_blank", "noopener");
        setBusyProvider(null);
        setMessage(
          `Tab login ${providerLabel(provider)} dibuka. Selesaikan di tab itu, lalu kembali ke sini.`,
        );
        onRefresh();
      } else if (started.flow === "device") {
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
      } else if (started.flow === "polling") {
        const prompt: PollingPrompt = {
          verificationUri: started.verification_uri,
          pollKey: started.poll_key,
          expiresIn: started.expires_in,
        };
        setPolling({ provider, prompt });
        setMessage(`Buka ${started.verification_uri} dan masuk di sana.`);
        void attemptPoll(provider, prompt, Math.max(1, started.interval));
      } else {
        setBusyProvider(null);
        setPasteValue("");
        setPaste({
          provider,
          prompt: {
            flow: started.flow,
            help: started.help,
            tokenPrefix: started.flow === "encoded_token" ? null : started.token_prefix,
          },
        });
        setMessage(started.help);
      }
    } catch (err) {
      setBusyProvider(null);
      setMessage(
        `Login ${providerLabel(provider)} gagal: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    }
  }

  async function submitPaste(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (paste === null || pasteBusy) return;
    setPasteBusy(true);
    setPasteError("");
    try {
      await onFinishToken(paste.provider, pasteValue);
      setPaste(null);
      setPasteValue("");
      setMessage(`Login ${providerLabel(paste.provider)} berhasil.`);
      onRefresh();
    } catch (err) {
      setPasteError(
        `Login gagal: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setPasteBusy(false);
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
        Masuk ke langganan AI tanpa nanobot. Token hanya tersimpan di vault kamu, tidak pernah
        ditampilkan di sini. Login langganan hanya untuk pemakaian sendiri (local): di deployment
        hosted, login langganan ditolak supaya host tidak pernah memakai paket langganan orang lain.
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
          const pollingForThis =
            polling !== null && polling.provider === entry.provider ? polling.prompt : null;
          const pasteForThis =
            paste !== null && paste.provider === entry.provider ? paste.prompt : null;
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
              {pollingForThis ? (
                <p>
                  Buka{" "}
                  <a href={pollingForThis.verificationUri} target="_blank" rel="noreferrer">
                    {pollingForThis.verificationUri}
                  </a>{" "}
                  dan masuk di sana. Menunggu sampai selesai...
                </p>
              ) : null}
              {pasteForThis ? (
                <form aria-labelledby={`paste-heading-${entry.provider}`} onSubmit={(event) => void submitPaste(event)}>
                  <h4 id={`paste-heading-${entry.provider}`}>
                    Tempel token {providerLabel(entry.provider)}
                  </h4>
                  {pasteForThis.help ? (
                    <p id={`paste-help-${entry.provider}`}>{pasteForThis.help}</p>
                  ) : null}
                  <label htmlFor={`paste-token-${entry.provider}`}>
                    Token {providerLabel(entry.provider)}
                    {pasteForThis.tokenPrefix ? ` (diawali ${pasteForThis.tokenPrefix})` : ""}
                  </label>
                  <input
                    id={`paste-token-${entry.provider}`}
                    name="token"
                    type="password"
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    aria-describedby={pasteForThis.help ? `paste-help-${entry.provider}` : undefined}
                    aria-invalid={pasteError ? true : undefined}
                    value={pasteValue}
                    onChange={(event) => {
                      setPasteValue(event.target.value);
                      if (pasteError) setPasteError("");
                    }}
                    required
                  />
                  {pasteError ? <p role="alert">{pasteError}</p> : null}
                  <button type="submit" disabled={pasteBusy}>
                    {pasteBusy ? "Menyimpan..." : "Simpan token"}
                  </button>
                </form>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
