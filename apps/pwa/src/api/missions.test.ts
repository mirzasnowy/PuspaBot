import { describe, expect, it, vi } from "vitest";
import {
  answerMission,
  cancelMission,
  createMission,
  fetchMissions,
  runMission,
  scheduleMission,
} from "./missions";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

describe("missions client (V2-42)", () => {
  const mission = {
    id: "misi-abc",
    user_id: "user-1",
    title: "Riset harga laptop",
    goal: "Riset harga laptop",
    status: "active",
    steps: [
      {
        title: "Cari tiga harga",
        status: "done",
        result: "3 harga ditemukan",
        question: null,
        options: [],
        answer: null,
      },
      {
        title: "Pilih toko",
        status: "active",
        result: null,
        question: "Toko mana?",
        options: ["Tokopedia", "Shopee"],
        answer: null,
      },
    ],
    webhook_token: "tok-1",
    created_at: "2026-11-22T12:00:00+00:00",
    updated_at: "2026-11-22T12:00:00+00:00",
  };

  it("fetchMissions reads /api/missions", async () => {
    const fetchMock = mockFetch({ missions: [mission] });
    const result = await fetchMissions(fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledWith("/api/missions", { credentials: "same-origin" });
    expect(result.missions).toHaveLength(1);
    expect(result.missions[0].steps[1].question).toBe("Toko mana?");
  });

  it("createMission posts goal and steps", async () => {
    const fetchMock = mockFetch(mission);
    const result = await createMission(
      { goal: "Riset harga laptop", steps: ["Cari tiga harga"] },
      fetchMock as unknown as typeof fetch,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/missions",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.id).toBe("misi-abc");
  });

  it("runMission posts to /run and answerMission posts go with the answer", async () => {
    const fetchMock = mockFetch({ queued: true, mission_id: "misi-abc", trigger_id: "pemicu-1" });
    const queued = await runMission("misi-abc", fetchMock as unknown as typeof fetch);
    expect(queued.trigger_id).toBe("pemicu-1");
    const answered = await answerMission(
      "misi-abc",
      "Tokopedia",
      true,
      mockFetch(mission) as unknown as typeof fetch,
    );
    expect(answered.steps[1].question).toBe("Toko mana?");
  });

  it("cancelMission and scheduleMission post to their endpoints", async () => {
    const fetchMock = mockFetch(mission);
    await cancelMission("misi-abc", fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/missions/misi-abc/cancel",
      expect.objectContaining({ method: "POST" }),
    );
    const scheduled = await scheduleMission(
      "misi-abc",
      { type: "daily", time: "07:00", timezone: "Asia/Jakarta" },
      mockFetch({
        scheduled: true,
        mission_id: "misi-abc",
        trigger_id: "pemicu-2",
        next_trigger_at: "2026-11-23T07:00:00+07:00",
      }) as unknown as typeof fetch,
    );
    expect(scheduled.trigger_id).toBe("pemicu-2");
  });
});

