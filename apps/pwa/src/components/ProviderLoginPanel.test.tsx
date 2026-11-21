import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProviderLoginPanel } from "./ProviderLoginPanel";
import type { ProviderStatus } from "../api/client";

function providers(overrides: Partial<ProviderStatus>[] = []): ProviderStatus[] {
  const base: ProviderStatus[] = [
    { provider: "xai", flow: "pkce", state: "not_logged_in", expires_at: null, login_ready: true },
    { provider: "github_copilot", flow: "device", state: "not_logged_in", expires_at: null, login_ready: true },
    { provider: "openai_codex", flow: "pkce", state: "not_logged_in", expires_at: null, login_ready: false },
  ];
  return base.map((entry, index) => ({ ...entry, ...(overrides[index] ?? {}) }));
}

describe("ProviderLoginPanel", () => {
  it("renders one login button per provider with its login state", () => {
    render(
      <ProviderLoginPanel
        providers={providers([{ state: "logged_in", expires_at: "2026-11-22T13:00:00Z" }])}
        loading={false}
        error={null}
        onStartLogin={vi.fn()}
        onPollDevice={vi.fn()}
        onLogout={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText("Login Langganan")).toBeTruthy();
    expect(screen.getByText(/xAI \(Grok\): Masuk/)).toBeTruthy();
    expect(screen.getByText(/GitHub Copilot: Belum masuk/)).toBeTruthy();
    expect(screen.getByText(/Codex: Belum masuk/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Logout xAI (Grok)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Login GitHub Copilot" })).toBeTruthy();
    // Codex login is not wired yet: its button is disabled and says so.
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
        onLogout={onLogout}
        onRefresh={onRefresh}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Logout xAI (Grok)" }));
    expect(onLogout).toHaveBeenCalledWith("xai");
    expect(onRefresh).toHaveBeenCalled();
  });
});
