import { describe, expect, it } from "vitest";
import type { AvatarStatus, OfficeProjection } from "../api/client";
import { activeBuiltinId, avatarPhotoSrc, avatarStatus, BUILTIN_AVATARS, DEFAULT_AVATAR_SRC } from "./avatar";

function officeWith(statuses: string[]): OfficeProjection {
  return {
    agents: statuses.map((status, i) => ({
      agent_id: `agent-${i}`,
      status,
      last_task: "",
      subagents: [],
      cost_rp: 0,
    })),
  } as OfficeProjection;
}

function avatarWith(hasPhoto: boolean, updatedAt: number | null): AvatarStatus {
  return {
    ok: true,
    has_photo: hasPhoto,
    photo_url: hasPhoto ? "/api/agent/avatar/photo" : null,
    updated_at: updatedAt,
    mime_type: hasPhoto ? "image/png" : null,
  };
}

describe("avatarStatus", () => {
  it("is idle when nothing is sending and no agent is working", () => {
    expect(avatarStatus(false, null)).toBe("idle");
    expect(avatarStatus(false, officeWith(["idle"]))).toBe("idle");
    expect(avatarStatus(false, officeWith([]))).toBe("idle");
  });

  it("is working while a chat turn is in flight", () => {
    expect(avatarStatus(true, null)).toBe("working");
    expect(avatarStatus(true, officeWith(["idle"]))).toBe("working");
  });

  it("is working while any office agent is working", () => {
    expect(avatarStatus(false, officeWith(["idle", "working"]))).toBe("working");
    expect(avatarStatus(false, officeWith(["working"]))).toBe("working");
  });

  it("goes back to idle when the turn lands and the agents stop", () => {
    // idle -> working -> idle follows the signal, both directions.
    expect(avatarStatus(false, officeWith(["idle"]))).toBe("idle");
    expect(avatarStatus(true, officeWith(["idle"]))).toBe("working");
    expect(avatarStatus(false, officeWith(["working"]))).toBe("working");
    expect(avatarStatus(false, officeWith(["idle"]))).toBe("idle");
  });
});

describe("avatarPhotoSrc", () => {
  it("uses the default logo when no photo is set", () => {
    expect(avatarPhotoSrc(null)).toBe(DEFAULT_AVATAR_SRC);
    expect(avatarPhotoSrc(avatarWith(false, null))).toBe(DEFAULT_AVATAR_SRC);
  });

  it("uses the uploaded photo with a cache-busting version", () => {
    expect(avatarPhotoSrc(avatarWith(true, 1780000000))).toBe(
      "/api/agent/avatar/photo?v=1780000000",
    );
  });

  it("uses the photo URL without a version when updated_at is missing", () => {
    expect(avatarPhotoSrc(avatarWith(true, null))).toBe("/api/agent/avatar/photo");
  });
});

describe("built-in flower avatars (Nero, 24 Nov)", () => {
  it("lists four flowers and no animals", () => {
    expect(BUILTIN_AVATARS.map((option) => option.id)).toEqual([
      "tulip",
      "sunflower",
      "rose",
      "sakura",
    ]);
    expect(BUILTIN_AVATARS.every((option) => option.src.endsWith(".png"))).toBe(true);
  });

  it("activeBuiltinId reads the source marker", () => {
    expect(activeBuiltinId(null)).toBeNull();
    expect(activeBuiltinId({ ...avatarWith(false, null), source: "default" })).toBeNull();
    expect(activeBuiltinId({ ...avatarWith(true, 1), source: "upload" })).toBeNull();
    expect(activeBuiltinId({ ...avatarWith(true, 1), source: "builtin:rose" })).toBe(
      "rose",
    );
  });
});
