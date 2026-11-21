import { describe, expect, it } from "vitest";
import {
  displayNameForMember,
  fallbackMemberName,
  isUuid,
  memberDisplayNames,
  renderDecisionText,
  requiredAgentName,
  resolveSenderName,
} from "./names";

const UUID_A = "11111111-1111-1111-1111-111111111111";
const UUID_B = "22222222-2222-2222-2222-222222222222";
const UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/;

describe("group chat names (V2-27)", () => {
  it("detects UUID-shaped ids", () => {
    expect(isUuid(UUID_A)).toBe(true);
    expect(isUuid("member-1")).toBe(false);
    expect(isUuid("")).toBe(false);
    expect(isUuid(undefined)).toBe(false);
  });

  it("numbers unnamed members by list position", () => {
    expect(fallbackMemberName(0)).toBe("Anggota 1");
    expect(fallbackMemberName(2)).toBe("Anggota 3");
  });

  it("shows the display name when set, else the placeholder, never the UUID", () => {
    expect(
      displayNameForMember({ user_id: UUID_A, display_name: "Rani" }, 0),
    ).toBe("Rani");
    expect(displayNameForMember({ user_id: UUID_A }, 1)).toBe("Anggota 2");
    expect(displayNameForMember({ user_id: UUID_A, display_name: "  " }, 1)).toBe(
      "Anggota 2",
    );
    const rendered = displayNameForMember({ user_id: UUID_A }, 0);
    expect(UUID_RE.test(rendered)).toBe(false);
    expect(rendered).not.toContain(UUID_A);
  });

  it("maps a whole member list to display names", () => {
    expect(
      memberDisplayNames([
        { user_id: UUID_A, display_name: "Rani" },
        { user_id: UUID_B },
      ]),
    ).toEqual({ [UUID_A]: "Rani", [UUID_B]: "Anggota 2" });
  });

  it("renders agreed-by ids as names in decision text", () => {
    const names = { [UUID_A]: "Rani", [UUID_B]: "Dimas" };
    expect(
      renderDecisionText(`Piknik hari Minggu.\nAgreed by: ${UUID_A}, ${UUID_B}`, names),
    ).toBe("Piknik hari Minggu.\nAgreed by: Rani, Dimas");
    expect(
      renderDecisionText(`Agreed by: ${UUID_A}, ${UUID_B}`, {}),
    ).toBe("Agreed by: Anggota, Anggota");
    expect(renderDecisionText("No ids here.", names)).toBe("No ids here.");
  });

  it("picks the required agent's name, falling back to Puspa", () => {
    expect(
      requiredAgentName([
        { agent_id: "puspa", name: "Bimo", required: true },
        { agent_id: "other", name: "Lain" },
      ]),
    ).toBe("Bimo");
    expect(requiredAgentName([])).toBe("Puspa");
    expect(requiredAgentName(undefined)).toBe("Puspa");
  });

  it("resolves chat senders to names, never a UUID or hardcoded Puspa", () => {
    expect(resolveSenderName("users", { botName: "Bimo" })).toBe("Saya");
    expect(
      resolveSenderName("users", { botName: "Bimo", userDisplayName: "Rani" }),
    ).toBe("Rani");
    expect(resolveSenderName("puspa", { botName: "Bimo" })).toBe("Bimo");
    expect(resolveSenderName("Puspa", { botName: "Bimo" })).toBe("Bimo");
    expect(resolveSenderName(UUID_A, { botName: "Sari" })).toBe("Sari");
    expect(resolveSenderName(UUID_A, {})).toBe("Puspa");
    expect(resolveSenderName("bimo", { botName: "Bimo" })).toBe("bimo");
  });
});
