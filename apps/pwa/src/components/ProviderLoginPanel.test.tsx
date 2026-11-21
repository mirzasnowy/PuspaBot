import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProviderLoginPanel } from "./ProviderLoginPanel";
import type { ProviderStatus } from "../api/client";

function providers(overrides: Partial<ProviderStatus>[] = []): ProviderStatus[] {
  const base: ProviderStatus[] = [
    { provider: "xai", flow: "pkce", state: "not_logged_in", expires_at: null, login_ready: true },
    { provider: "github_copilot", flow: "device", state: "not_logged_in", expires_at: null, login_ready: true },
    // Codex is wired since V2-29 (endpoints verified from 9Router).
    { provider: "openai_codex", flow: "pkce", state: "not_logged_in", expires_at: null, login_ready: true },
    { provider: "claude", flow: "setup_token", state: "not_logged_in", expires_at: null, login_ready: true },
    { provider: "glm", flow: "polling", state: "not_logged_in", expires_at: null, login_ready: true },
    { provider: "kiro", flow: "device", state: "not_logged_in", expires_at: null, login_ready: true },
  ];
  return base.map((entry, index) => ({ ...entry, ...(overrides[index] ?? {}) }));
}

function defaultProps() {
  return {
    loading: false,
    error: null,
    onStartLogin: vi.fn(),
    onPollDevice: vi.fn(),
    onFinishToken: vi.fn(),
    onLogout: vi.fn(),
    onRefresh: vi.fn(),
  };
}

describe("ProviderLoginPanel", () => {
  it("renders one login button per provider with its login state", () => {
    render(
      <ProviderLoginPanel
        providers={providers([{ state: "logged_in", expires_at: "2026-11-22T13:00:00Z" }])}
        {...defaultProps()}
      />,
    );
    expect(screen.getByText("Login Langganan")).toBeTruthy();
    expect(screen.getByText(/xAI \(Grok\): Masuk/)).toBeTruthy();
    expect(screen.getByText(/GitHub Copilot: Belum masuk/)).toBeTruthy();
    expect(screen.getByText(/Codex: Belum masuk/)).toBeTruthy();
    expect(screen.getByText(/Claude: Belum masuk/)).toBeTruthy();
    expect(screen.getByText(/GLM \(Z\.ai\): Belum masuk/)).toBeTruthy();
    expect(screen.getByText(/Kiro: Belum masuk/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Logout xAI (Grok)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Login GitHub Copilot" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Login Codex" })).toHaveProperty("disabled", false);
  });

  it("disables the login button and says so when a provider is not wired yet", () => {
    render(
      <ProviderLoginPanel
        providers={providers([{}, {}, { login_ready: false }])}
        {...defaultProps()}
      />,
    );
    expect(screen.getByRole("button", { name: "Login Codex" })).toHaveProperty("disabled", true);
    expect(screen.getByText(/Login belum tersedia untuk provider ini/)).toBeTruthy();
  });

  it("opens the PKCE authorize URL in a new tab when the login button is clicked", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const onStartLogin = vi.fn().mockResolvedValue({
      flow: "pkce",
      url: "https://auth.example.test/authorize?client_id=fake",
      state: "state-fake",
    });
    const onRefresh = vi.fn();
    render(
      <ProviderLoginPanel
        providers={providers()}
        loading={false}
        error={null}
        onStartLogin={onStartLogin}
        onPollDevice={vi.fn()}
        onFinishToken={vi.fn()}
        onLogout={vi.fn()}
        onRefresh={onRefresh}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Login xAI (Grok)" }));
    expect(onStartLogin).toHaveBeenCalledWith("xai");
    expect(openSpy).toHaveBeenCalledWith(
      "https://auth.example.test/authorize?client_id=fake",
      "_blank",
      "noopener",
    );
    expect(onRefresh).toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it("shows the device user code and polls until the login completes", async () => {
    const onStartLogin = vi.fn().mockResolvedValue({
      flow: "device",
      user_code: "USER-CODE-FAKE",
      verification_uri: "https://auth.example.test/device",
      expires_in: 900,
      interval: 1,
      poll_key: "poll-key-fake",
    });
    const onPollDevice = vi
      .fn()
      .mockResolvedValueOnce({ status: "pending" })
      .mockResolvedValue({ status: "complete", state: "logged_in" });
    const onRefresh = vi.fn();
    render(
      <ProviderLoginPanel
        providers={providers()}
        loading={false}
        error={null}
        onStartLogin={onStartLogin}
        onPollDevice={onPollDevice}
        onFinishToken={vi.fn()}
        onLogout={vi.fn()}
        onRefresh={onRefresh}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Login GitHub Copilot" }));
    expect(await screen.findByText("USER-CODE-FAKE")).toBeTruthy();
    expect(screen.getByText("https://auth.example.test/device")).toBeTruthy();
    expect(onPollDevice).toHaveBeenCalledWith("github_copilot", "poll-key-fake");
    await waitFor(() => expect(onRefresh).toHaveBeenCalled(), { timeout: 3000 });
  });

  it("shows the polling sign-in link and polls until the login completes", async () => {
    const onStartLogin = vi.fn().mockResolvedValue({
      flow: "polling",
      verification_uri: "https://zcode.z.ai/oauth/authorize",
      expires_in: 300,
      interval: 1,
      poll_key: "poll-key-fake",
    });
    const onPollDevice = vi
      .fn()
      .mockResolvedValueOnce({ status: "pending" })
      .mockResolvedValue({ status: "complete", state: "logged_in" });
    const onRefresh = vi.fn();
    render(
      <ProviderLoginPanel
        providers={providers()}
        loading={false}
        error={null}
        onStartLogin={onStartLogin}
        onPollDevice={onPollDevice}
        onFinishToken={vi.fn()}
        onLogout={vi.fn()}
        onRefresh={onRefresh}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Login GLM (Z.ai)" }));
    expect(await screen.findByText("https://zcode.z.ai/oauth/authorize")).toBeTruthy();
    expect(onPollDevice).toHaveBeenCalledWith("glm", "poll-key-fake");
    await waitFor(() => expect(onRefresh).toHaveBeenCalled(), { timeout: 3000 });
  });

  it("shows the paste form for a setup-token login and submits the token", async () => {
    const onStartLogin = vi.fn().mockResolvedValue({
      flow: "setup_token",
      help: "Run `claude setup-token` and paste the token it prints.",
      token_prefix: "sk-ant-oat01-",
    });
    const onFinishToken = vi.fn().mockResolvedValue(undefined);
    const onRefresh = vi.fn();
    render(
      <ProviderLoginPanel
        providers={providers()}
        loading={false}
        error={null}
        onStartLogin={onStartLogin}
        onPollDevice={vi.fn()}
        onFinishToken={onFinishToken}
        onLogout={vi.fn()}
        onRefresh={onRefresh}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Login Claude" }));
    const input = await screen.findByLabelText(/Token Claude \(diawali sk-ant-oat01-\)/);
    expect(input.getAttribute("type")).toBe("password");
    expect(input.getAttribute("autocomplete")).toBe("off");
    await userEvent.setup().type(input, "sk-ant-oat01-FAKE-TOKEN");
    await userEvent.setup().click(screen.getByRole("button", { name: "Simpan token" }));
    expect(onFinishToken).toHaveBeenCalledWith("claude", "sk-ant-oat01-FAKE-TOKEN");
    await waitFor(() => expect(onRefresh).toHaveBeenCalled(), { timeout: 3000 });
    expect(screen.getByText(/Login Claude berhasil/)).toBeTruthy();
  });

  it("shows the error in the form when the pasted token is refused", async () => {
    const onStartLogin = vi.fn().mockResolvedValue({
      flow: "setup_token",
      help: "Run `claude setup-token` and paste the token it prints.",
      token_prefix: "sk-ant-oat01-",
    });
    const onFinishToken = vi.fn().mockRejectedValue(new Error("wrong token prefix"));
    render(
      <ProviderLoginPanel
        providers={providers()}
        loading={false}
        error={null}
        onStartLogin={onStartLogin}
        onPollDevice={vi.fn()}
        onFinishToken={onFinishToken}
        onLogout={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Login Claude" }));
    const input = await screen.findByLabelText(/Token Claude/);
    await userEvent.setup().type(input, "wrong-token");
    await userEvent.setup().click(screen.getByRole("button", { name: "Simpan token" }));
    expect(await screen.findByText(/Login gagal: wrong token prefix/)).toBeTruthy();
    expect(input).toBeTruthy();
  });

  it("calls logout and refreshes when the logout button is clicked", async () => {
    const onLogout = vi.fn().mockResolvedValue(undefined);
    const onRefresh = vi.fn();
    render(
      <ProviderLoginPanel
        providers={providers([{ state: "logged_in" }])}
        loading={false}
        error={null}
        onStartLogin={vi.fn()}
        onPollDevice={vi.fn()}
        onFinishToken={vi.fn()}
        onLogout={onLogout}
        onRefresh={onRefresh}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Logout xAI (Grok)" }));
    expect(onLogout).toHaveBeenCalledWith("xai");
    expect(onRefresh).toHaveBeenCalled();
  });
});
