import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  deleteByokKey,
  getModelSettings,
  saveByokKey,
  saveModelSettings,
} from "./client";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

describe("getModelSettings", () => {
  it("fetches the stored selection from /api/model-settings", async () => {
    const fetchMock = mockFetch({
      source: "local",
      base_url: "http://localhost:11434/v1",
      model: "llama3.1",
      provider: null,
      has_byok_key: false,
    });
    const result = await getModelSettings(fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/model-settings");
    expect(result.source).toBe("local");
  });

  it("throws ApiError with the server message on 401", async () => {
    const fetchMock = mockFetch({ error: "belum masuk" }, 401);
    await expect(getModelSettings(fetchMock as unknown as typeof fetch)).rejects.toThrow(
      "belum masuk",
    );
    await expect(getModelSettings(fetchMock as unknown as typeof fetch)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});

describe("saveModelSettings", () => {
  it("posts the selection as JSON and returns the saved selection", async () => {
    const fetchMock = mockFetch({
      source: "byok",
      base_url: null,
      model: null,
      provider: "openrouter",
      has_byok_key: false,
    });
    const result = await saveModelSettings(
      { source: "byok", base_url: null, model: null, provider: "openrouter" },
      fetchMock as unknown as typeof fetch,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/model-settings");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body.source).toBe("byok");
    expect(body).not.toHaveProperty("api_key");
    expect(body).not.toHaveProperty("key");
    expect(result.provider).toBe("openrouter");
  });
});

describe("saveByokKey / deleteByokKey", () => {
  it("posts the key to the BYOK endpoint only", async () => {
    const fetchMock = mockFetch({ ok: true });
    await saveByokKey("openrouter", "sk-test", fetchMock as unknown as typeof fetch);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/model-settings/byok-key");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ provider: "openrouter", key: "sk-test" });
  });

  it("deletes by provider query", async () => {
    const fetchMock = mockFetch({ ok: true });
    await deleteByokKey("openrouter", fetchMock as unknown as typeof fetch);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/model-settings/byok-key?provider=openrouter");
    expect(init.method).toBe("DELETE");
  });
});
