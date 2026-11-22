import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

describe("SettingsScreen telemetry toggle", () => {
  const voiceDefaults = {
    asr_provider: "none",
    asr_base_url: null,
    asr_model: null,
    tts_provider: "edge",
    tts_voice: "id-ID-GadisNeural",
    tts_model: null,
    tts_base_url: null,
    tts_region: null,
    has_asr_key: false,
    has_tts_key: false,
  };
  const telemetryOff = { enabled: false, env_override: null, effective_enabled: false };

  function mockFetchByUrl(telemetryBody: unknown) {
    let saved: unknown = telemetryBody;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      let body: unknown = hostedSettings;
      if (url === "/api/telemetry/settings") {
        if (init?.method === "POST") {
          const posted = JSON.parse(String(init.body));
          saved = { enabled: posted.enabled, env_override: null, effective_enabled: posted.enabled };
          body = saved;
        } else {
          body = saved;
        }
      } else if (url === "/api/voice/settings") {
        body = voiceDefaults;
      } else if (url === "/api/providers") {
        body = { providers: [] };
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      } as Response);
    });
    return fetchMock;
  }

  it("shows the toggle off by default and saves turning it on", async () => {
    const fetchMock = mockFetchByUrl(telemetryOff);
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      expect(await screen.findByText("Statistik pemakaian (telemetri)")).toBeTruthy();
      const toggle = screen.getByRole("checkbox", { name: /Kirim statistik pemakaian anonim/ });
      const saveButton = screen.getByRole("button", { name: "Simpan pilihan telemetri" });
      await waitFor(() => expect(saveButton).toHaveProperty("disabled", false));
      expect(toggle).toHaveProperty("checked", false);
      expect(screen.getByText(/Mati \(default untuk self-host\)/)).toBeTruthy();
      const user = userEvent.setup();
      await user.click(toggle);
      expect(toggle).toHaveProperty("checked", true);
      await user.click(saveButton);
      expect(await screen.findByText("Pilihan telemetri tersimpan.")).toBeTruthy();
      expect(toggle).toHaveProperty("checked", true);
      const postCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          url === "/api/telemetry/settings" &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(String((postCall?.[1] as RequestInit).body))).toEqual({
        enabled: true,
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("disables the toggle when the operator flag decides", async () => {
    const fetchMock = mockFetchByUrl({
      enabled: false,
      env_override: false,
      effective_enabled: false,
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      expect(await screen.findByText("Statistik pemakaian (telemetri)")).toBeTruthy();
      const toggle = screen.getByRole("checkbox", { name: /Kirim statistik pemakaian anonim/ });
      await waitFor(() => expect(toggle).toHaveProperty("disabled", true));
      expect(screen.getByRole("button", { name: "Simpan pilihan telemetri" })).toHaveProperty(
        "disabled",
        true,
      );
      expect(screen.getByText(/Dimatikan oleh operator lewat PUSPA_TELEMETRY=0/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows the operator-enabled note when the flag forces it on", async () => {
    const fetchMock = mockFetchByUrl({
      enabled: false,
      env_override: true,
      effective_enabled: true,
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      expect(await screen.findByText("Statistik pemakaian (telemetri)")).toBeTruthy();
      const toggle = screen.getByRole("checkbox", { name: /Kirim statistik pemakaian anonim/ });
      await waitFor(() => expect(toggle).toHaveProperty("disabled", true));
      expect(toggle).toHaveProperty("checked", true);
      expect(screen.getByText(/Diaktifkan oleh operator lewat PUSPA_TELEMETRY=1/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("SettingsScreen per-capability routing", () => {
  const routingTable = {
    capabilities: {
      chat: { capability: "chat", provider: "hosted", model: null, base_url: null, has_key: false, is_default: true },
      vision: { capability: "vision", provider: "hosted", model: null, base_url: null, has_key: false, is_default: true },
      image: { capability: "image", provider: null, model: null, base_url: null, has_key: false, is_default: true },
      speech: { capability: "speech", provider: null, model: null, base_url: null, has_key: false, is_default: true },
      embeddings: { capability: "embeddings", provider: "hosted", model: null, base_url: null, has_key: false, is_default: true },
    },
    providers: {
      chat: ["hosted", "ollama", "openrouter", "openai", "deepseek", "groq", "together", "mistral", "xai", "gemini"],
      vision: ["hosted", "ollama", "openrouter", "openai", "groq", "together", "mistral", "xai", "gemini"],
      image: ["openai", "openrouter", "together", "xai", "gemini"],
      speech: ["openai", "openrouter", "groq", "deepseek", "together"],
      embeddings: ["hosted", "ollama", "openai", "openrouter", "together", "mistral", "gemini"],
    },
  };

  function mockFetchByUrl() {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      let body: unknown = hostedSettings;
      if (url === "/api/routing") {
        body = routingTable;
      } else if (url === "/api/providers") {
        body = { providers: [] };
      } else if (url.startsWith("/api/routing/") && init?.method === "POST") {
        const capability = url.split("/").pop();
        const posted = JSON.parse(String(init.body));
        body = {
          capability,
          provider: posted.provider,
          model: posted.model,
          base_url: posted.base_url,
          has_key: false,
          is_default: false,
        };
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      } as Response);
    });
    return fetchMock;
  }

  it("lists all five capabilities with their providers", async () => {
    const fetchMock = mockFetchByUrl();
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      expect(await screen.findByText("Routing per kemampuan")).toBeTruthy();
      for (const capability of ["chat", "vision", "image", "speech", "embeddings"]) {
        expect(screen.getByText(capability, { exact: true })).toBeTruthy();
      }
      expect(fetchMock).toHaveBeenCalledWith("/api/routing", {
        credentials: "same-origin",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("saves one capability with one click and leaves the others alone", async () => {
    const fetchMock = mockFetchByUrl();
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SettingsScreen />);
      await screen.findByText("Routing per kemampuan");
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Simpan chat" })).toHaveProperty(
          "disabled",
          false,
        ),
      );
      const user = userEvent.setup();
      const selects = screen.getAllByRole("combobox");
      const chatSelect = selects[0];
      await user.selectOptions(chatSelect, "groq");
      const modelInputs = screen.getAllByPlaceholderText("nama model di provider itu");
      await user.type(modelInputs[0], "llama-3.3-70b-versatile");
      await user.click(screen.getByRole("button", { name: "Simpan chat" }));
      expect(await screen.findByText("Routing chat tersimpan: groq.")).toBeTruthy();
      const postCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          url === "/api/routing/chat" &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(String((postCall?.[1] as RequestInit).body))).toEqual({
        provider: "groq",
        model: "llama-3.3-70b-versatile",
        base_url: null,
      });
      expect(
        fetchMock.mock.calls.filter(
          ([url, init]) =>
            typeof url === "string" &&
            url.startsWith("/api/routing/") &&
            (init as RequestInit | undefined)?.method === "POST",
        ),
      ).toHaveLength(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
