import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createTask,
  decideApproval,
  deleteAgent,
  formatRupiah,
  parseSseEvent,
  sendChat,
  setConnector,
  stopTask,
} from "./client";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

describe("sendChat", () => {
  it("posts form-encoded text to /api/chat and returns the message", async () => {
    const fetchMock = mockFetch({
      ok: true,
      message: { sender: "users", text: "halo Puspa", created_at: 1 },
    });
    const result = await sendChat("halo Puspa", fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/chat");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/x-www-form-urlencoded" });
    expect(String(init.body)).toContain("text=halo+Puspa");
    expect(result.message.text).toBe("halo Puspa");
  });

  it("throws ApiError with the server message on 401", async () => {
    const fetchMock = mockFetch({ error: "belum masuk" }, 401);
    await expect(
      sendChat("halo", fetchMock as unknown as typeof fetch),
    ).rejects.toBeInstanceOf(ApiError);
    await expect(sendChat("halo", fetchMock as unknown as typeof fetch)).rejects.toThrow(
      "belum masuk",
    );
  });
});

describe("decideApproval", () => {
  it("sends approve=ya for approval and approve=tidak for rejection", async () => {
    const fetchMock = mockFetch({ ok: true, status: "approved" });
    await decideApproval("req-1", true, fetchMock as unknown as typeof fetch);
    expect(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body)).toContain(
      "approve=ya",
    );
    const fetchMock2 = mockFetch({ ok: true, status: "rejected" });
    const result = await decideApproval("req-1", false, fetchMock2 as unknown as typeof fetch);
    expect(String((fetchMock2.mock.calls[0] as [string, RequestInit])[1].body)).toContain(
      "approve=tidak",
    );
    expect(result.status).toBe("rejected");
  });

  it("throws ApiError on unknown request id (404)", async () => {
    const fetchMock = mockFetch({ error: "permintaan tidak dikenal" }, 404);
    await expect(
      decideApproval("nope", true, fetchMock as unknown as typeof fetch),
    ).rejects.toThrow("permintaan tidak dikenal");
  });
});

describe("createTask and stopTask", () => {
  it("creates a task with the purpose field", async () => {
    const fetchMock = mockFetch({ task_id: "t-1", status: "completed", stage: "done" });
    const outcome = await createTask("riset", fetchMock as unknown as typeof fetch);
    expect(outcome.task_id).toBe("t-1");
    expect(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body)).toContain(
      "purpose=riset",
    );
  });

  it("stops a task and returns the stop result", async () => {
    const fetchMock = mockFetch({ stopped: 0, revoked_tokens: [], seconds: 0.1 });
    const result = await stopTask("t-1", fetchMock as unknown as typeof fetch);
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[0]).toBe(
      "/api/tugas/t-1/hentikan",
    );
    expect(result.stopped).toBe(0);
  });
});

describe("deleteAgent and setConnector", () => {
  it("deletes with the DELETE method", async () => {
    const fetchMock = mockFetch({ ok: true });
    await deleteAgent("agen-1", fetchMock as unknown as typeof fetch);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agen/agen-1");
    expect(init.method).toBe("DELETE");
  });

  it("revokes with action=cabut and grants otherwise", async () => {
    const fetchMock = mockFetch({ ok: true, connected: false });
    await setConnector("gmail", "agen-1", "cabut", fetchMock as unknown as typeof fetch);
    expect(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body)).toContain(
      "action=cabut",
    );
    const fetchMock2 = mockFetch({ ok: true, connected: true });
    const result = await setConnector(
      "gmail",
      "agen-1",
      "grant",
      fetchMock2 as unknown as typeof fetch,
    );
    expect(result.connected).toBe(true);
  });
});

describe("parseSseEvent", () => {
  it("parses office and heartbeat frames", () => {
    expect(parseSseEvent('{"type":"heartbeat","created_at":1}')).toEqual({
      type: "heartbeat",
      created_at: 1,
    });
    expect(
      parseSseEvent('{"type":"office","content":{"agent":{}}}'),
    ).toMatchObject({ type: "office" });
  });

  it("returns null for invalid JSON and non-events", () => {
    expect(parseSseEvent("not json")).toBeNull();
    expect(parseSseEvent('{"nope":true}')).toBeNull();
  });
});

describe("formatRupiah", () => {
  it("groups thousands with dots", () => {
    expect(formatRupiah(2000)).toBe("Rp2.000");
    expect(formatRupiah(1500000)).toBe("Rp1.500.000");
    expect(formatRupiah(0)).toBe("Rp0");
  });
});
