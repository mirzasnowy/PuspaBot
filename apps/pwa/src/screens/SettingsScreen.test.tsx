import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsScreen } from "./SettingsScreen";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

const hostedSettings = {
  source: "hosted",
  base_url: null,
  model: null,
  provider: null,
  has_byok_key: false,
};

describe("SettingsScreen model picker", () => {
  it("offers hosted, BYOK, and local choices", async () => {
    const fetchMock = mockFetch(hostedSettings);
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      expect(await screen.findByText("Model")).toBeTruthy();
      expect(screen.getByText(/Hosted \(default\)/)).toBeTruthy();
      expect(screen.getByText(/Kunci sendiri \(BYOK\)/)).toBeTruthy();
      expect(screen.getByText(/Model lokal \(Ollama\)/)).toBeTruthy();
      expect(fetchMock).toHaveBeenCalledWith("/api/model-settings", {
        credentials: "same-origin",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows the local base URL and model fields when local is picked", async () => {
    vi.stubGlobal("fetch", mockFetch(hostedSettings));
    try {
      render(<SettingsScreen />);
      await screen.findByText("Model");
      const user = userEvent.setup();
      await user.click(screen.getByRole("radio", { name: /Model lokal/ }));
      expect(screen.getByPlaceholderText("http://localhost:11434/v1")).toBeTruthy();
      expect(screen.getByPlaceholderText("llama3.1")).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("refuses a non-http base URL without calling the server to save", async () => {
    const fetchMock = mockFetch(hostedSettings);
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      await screen.findByText("Model");
      const user = userEvent.setup();
      await user.click(screen.getByRole("radio", { name: /Model lokal/ }));
      await user.type(screen.getByPlaceholderText("http://localhost:11434/v1"), "ftp://x/v1");
      const callsBefore = fetchMock.mock.calls.length;
      await user.click(screen.getByRole("button", { name: "Simpan pilihan model" }));
      expect(await screen.findByText(/http atau https/)).toBeTruthy();
      expect(fetchMock.mock.calls.length).toBe(callsBefore);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows the BYOK key field when BYOK is picked, never a plain key display", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ ...hostedSettings, source: "byok", provider: "openrouter" }),
    );
    try {
      render(<SettingsScreen />);
      await screen.findByText("Model");
      expect(screen.getByPlaceholderText("tempel kunci, lalu simpan")).toBeTruthy();
      expect(screen.queryByText(/sk-/)).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows the subscription login panel with one button per provider", async () => {
    const providersBody = {
      providers: [
        { provider: "xai", flow: "pkce", state: "logged_in", expires_at: null, login_ready: true },
        { provider: "github_copilot", flow: "device", state: "not_logged_in", expires_at: null, login_ready: true },
        { provider: "openai_codex", flow: "pkce", state: "not_logged_in", expires_at: null, login_ready: false },
      ],
    };
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const body = url === "/api/providers" ? providersBody : hostedSettings;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      expect(await screen.findByText("Login Langganan")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Logout xAI (Grok)" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Login GitHub Copilot" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Login Codex" })).toHaveProperty("disabled", true);
      expect(fetchMock).toHaveBeenCalledWith("/api/providers", {
        credentials: "same-origin",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
