import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  confirmVoice,
  createTask,
  decideApproval,
  deleteAgent,
  changeGraphEdge,
  deleteGraphEdge,
  discardVoice,
  fetchGraphNode,
  fetchGraphPage,
  fetchRoomBoard,
  fetchRooms,
  renameGraphNode,
  fetchAutonomyDial,
  fetchOperatorStatus,
  fetchWeeklyReport,
  formatRupiah,
  getJson,
  parseSseEvent,
  postJson,
  sendChat,
  setConnector,
  setDialCeiling,
  stopAll,
  stopTask,
  uploadVoice,
  voiceStatus,
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

describe("fetchOperatorStatus", () => {
  it("GETs /api/operator/status and returns the live payload", async () => {
    const payload = {
      mode: "local",
      operator_panels: true,
      pairing: { active_codes: 0, active_sessions: 1, sessions: [], locked_addresses: 0, locks: [] },
      gateway: { policy: { loaded: true, stale: false, allow_rules: ["true"] }, decisions: 1 },
      log: [],
    };
    const fetchMock = mockFetch(payload);
    const result = await fetchOperatorStatus(fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/operator/status");
    expect(init.method).toBeUndefined();
    expect(result.mode).toBe("local");
    expect(result.operator_panels).toBe(true);
    expect(result.pairing?.active_sessions).toBe(1);
  });

  it("throws ApiError with the server message on 401", async () => {
    const fetchMock = mockFetch({ error: "belum masuk" }, 401);
    await expect(
      fetchOperatorStatus(fetchMock as unknown as typeof fetch),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

describe("shared JSON helpers", () => {
  it("getJson returns parsed JSON on success", async () => {
    const fetchMock = mockFetch({ ok: true });
    await expect(getJson("/api/x", fetchMock as unknown as typeof fetch)).resolves.toEqual({
      ok: true,
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/x", { credentials: "same-origin" });
  });

  it("getJson throws ApiError with the server error message", async () => {
    const fetchMock = mockFetch({ error: "belum masuk" }, 401);
    const error = await getJson("/api/x", fetchMock as unknown as typeof fetch).catch(
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).message).toBe("belum masuk");
  });

  it("postJson sends a JSON body and returns parsed JSON", async () => {
    const fetchMock = mockFetch({ ok: true });
    await expect(
      postJson("/api/x", { a: 1 }, fetchMock as unknown as typeof fetch),
    ).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/x",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ a: 1 }),
      }),
    );
  });
});

describe("room boards (B9)", () => {
  it("fetchRooms reads /api/rooms", async () => {
    const fetchMock = mockFetch({
      rooms: [{ id: "room-1", name: "Keluarga", role: "owner" }],
    });
    const result = await fetchRooms(fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledWith("/api/rooms", { credentials: "same-origin" });
    expect(result.rooms).toHaveLength(1);
    expect(result.rooms[0].name).toBe("Keluarga");
  });

  it("fetchRoomBoard reads /api/rooms/{id}/tasks and returns delegation", async () => {
    const fetchMock = mockFetch({
      room_id: "room-1",
      room_name: "Keluarga",
      tasks: [
        {
          id: "task-1",
          room_id: "room-1",
          title: "Compare prices",
          owner_user_id: "user-2",
          due_at: "2026-11-21T10:00:00+00:00",
          status: "open",
          created_by: "user-1",
          created_at: "2026-11-19T10:00:00+00:00",
          completed_at: null,
          delegation: {
            delegate_kind: "agent",
            assignee_ref: "sub-abc",
            status: "in_progress",
            last_report: "checked 3 sellers",
            reported_at: null,
            started_at: null,
            blocked_reason: null,
          },
          pending_steps: 1,
        },
      ],
    });
    const result = await fetchRoomBoard("room-1", fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledWith("/api/rooms/room-1/tasks", {
      credentials: "same-origin",
    });
    expect(result.tasks).toHaveLength(1);
    expect(result.tasks[0].delegation?.status).toBe("in_progress");
    expect(result.tasks[0].pending_steps).toBe(1);
  });

  it("fetchRoomBoard throws ApiError on 403 for a non-member", async () => {
    const fetchMock = mockFetch({ error: "not a member" }, 403);
    await expect(
      fetchRoomBoard("room-1", fetchMock as unknown as typeof fetch),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

describe("graph client (B18)", () => {
  it("fetchGraphPage passes limit, offset, and kind as query params", async () => {
    const page = {
      nodes: [],
      edges: [],
      total_nodes: 0,
      total_edges: 0,
      limit: 50,
      offset: 0,
    };
    const fetchMock = mockFetch(page);
    const result = await fetchGraphPage(
      { limit: 50, offset: 0, kind: "phone" },
      fetchMock as unknown as typeof fetch,
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/graph?limit=50&offset=0&kind=phone", {
      credentials: "same-origin",
    });
    expect(result.total_nodes).toBe(0);
  });

  it("renameGraphNode PATCHes the node with the new name", async () => {
    const fetchMock = mockFetch({ name: "pixel 9 pro", sources: [], fact_count: 0, is_code_node: false });
    const result = await renameGraphNode(
      "samsung galaxy s24",
      "Pixel 9 Pro",
      fetchMock as unknown as typeof fetch,
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/graph/nodes/samsung%20galaxy%20s24");
    expect(init.method).toBe("PATCH");
    expect(String(init.body)).toBe(JSON.stringify({ name: "Pixel 9 Pro" }));
    expect(result.name).toBe("pixel 9 pro");
  });

  it("changeGraphEdge records a fact correction with operation correct_fact", async () => {
    const fetchMock = mockFetch({
      subject: "samsung galaxy s24",
      relation: "sells",
      object: "shopee",
      sources: ["user_correction"],
    });
    const result = await changeGraphEdge(
      {
        subject: "samsung galaxy s24",
        relation: "sells",
        object: "tokopedia",
        new_object: "Shopee",
        operation: "correct_fact",
      },
      fetchMock as unknown as typeof fetch,
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/graph/edges");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body)).operation).toBe("correct_fact");
    expect(result.object).toBe("shopee");
  });

  it("deleteGraphEdge sends the triple as query params", async () => {
    const fetchMock = mockFetch({
      deleted: { subject: "a", relation: "sells", object: "b" },
    });
    await deleteGraphEdge(
      { subject: "a", relation: "sells", object: "b" },
      fetchMock as unknown as typeof fetch,
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/graph/edges?subject=a&relation=sells&object=b");
    expect(init.method).toBe("DELETE");
  });

  it("fetchGraphNode throws ApiError on 404", async () => {
    const fetchMock = mockFetch({ error: "unknown node" }, 404);
    await expect(
      fetchGraphNode("nope", fetchMock as unknown as typeof fetch),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

describe("voice messages", () => {
  it("uploadVoice posts the audio as multipart form data", async () => {
    const fetchMock = mockFetch({
      ok: true,
      pending_id: "pend-1",
      transcript: "tolong cek harga beras",
      engine: "stub",
      audio_deleted: true,
      size_bytes: 5,
      mime_type: "audio/webm",
    });
    const audio = new Blob(["audio"], { type: "audio/webm" });
    const result = await uploadVoice(audio, false, fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/voice/upload");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("audio")).toBeInstanceOf(Blob);
    expect(result.pending_id).toBe("pend-1");
    expect(result.transcript).toBe("tolong cek harga beras");
  });

  it("confirmVoice posts the pending id and the edited text", async () => {
    const fetchMock = mockFetch({
      ok: true,
      message: { sender: "users", text: "cek harga gula", created_at: 1 },
    });
    const result = await confirmVoice(
      "pend-1",
      "cek harga gula",
      fetchMock as unknown as typeof fetch,
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/voice/confirm");
    expect(JSON.parse(String(init.body))).toEqual({
      pending_id: "pend-1",
      text: "cek harga gula",
    });
    expect(result.message.text).toBe("cek harga gula");
  });

  it("discardVoice posts the pending id", async () => {
    const fetchMock = mockFetch({ ok: true });
    await discardVoice("pend-1", fetchMock as unknown as typeof fetch);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/voice/discard");
    expect(JSON.parse(String(init.body))).toEqual({ pending_id: "pend-1" });
  });

  it("voiceStatus reads the transcription status", async () => {
    const fetchMock = mockFetch({
      engine: "stub",
      engine_selected: false,
      selection: "pending",
      required_recordings: 50,
    });
    const result = await voiceStatus(fetchMock as unknown as typeof fetch);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/voice/status");
    expect(result.engine_selected).toBe(false);
    expect(result.required_recordings).toBe(50);
  });
});

describe("P-02 controls", () => {
  it("stopAll posts to /api/stop-all and returns the report", async () => {
    const fetchMock = mockFetch({
      user_id: "user-1",
      agents_stopped: [],
      subagents_stopped: 0,
      audit_trail_url: "/api/proof/audit-trail",
    });
    const result = await stopAll(fetchMock as unknown as typeof fetch);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/stop-all");
    expect(init.method).toBe("POST");
    expect(result.audit_trail_url).toBe("/api/proof/audit-trail");
  });

  it("fetchAutonomyDial reads /api/autonomy-dial", async () => {
    const fetchMock = mockFetch({
      areas: [],
      threshold: 10,
      threshold_text: "10 accepted drafts in a row",
    });
    const result = await fetchAutonomyDial(fetchMock as unknown as typeof fetch);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/autonomy-dial");
    expect(result.threshold).toBe(10);
  });

  it("setDialCeiling posts the ceiling as JSON to the area path", async () => {
    const fetchMock = mockFetch({ area: "messaging", ceiling: "balanced" });
    const result = await setDialCeiling(
      "messaging",
      "balanced",
      fetchMock as unknown as typeof fetch,
    );
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/autonomy-dial/messaging/ceiling");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body)).ceiling).toBe("balanced");
    expect(result.ceiling).toBe("balanced");
  });

  it("setDialCeiling throws ApiError with the server message on 409", async () => {
    const fetchMock = mockFetch({ error: "needs 10 accepted drafts" }, 409);
    await expect(
      setDialCeiling("messaging", "balanced", fetchMock as unknown as typeof fetch),
    ).rejects.toThrow("needs 10 accepted drafts");
  });

  it("fetchWeeklyReport reads /api/weekly-report for the given days", async () => {
    const fetchMock = mockFetch({ window_days: 7, tasks_completed: 3 });
    const result = await fetchWeeklyReport(7, fetchMock as unknown as typeof fetch);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/weekly-report?days=7");
    expect(result.tasks_completed).toBe(3);
  });
});
