import { describe, expect, it } from "vitest";
import {
  HIDDEN_FEATURES,
  MAIN_TABS,
  SAYA_GROUPS,
  SPACE_SCREEN_COUNT,
  SPACE_SCREEN_IDS,
  SPACE_TITLES,
  TAB_OF,
  tabOf,
} from "./space";

/**
 * Space registry contract (Nero, 23 Nov): 46 of the Space's 48
 * screens ported; Beranda removed (tab deleted) and Autopilot
 * hidden (no backend API); four tabs only; every screen inside
 * exactly one tab; the ten hidden features appear nowhere in
 * the nav, groups, or PPOB.
 */
describe("Space screen registry", () => {
  it("ports 46 of the Space's 48 screens", () => {
    expect(SPACE_SCREEN_IDS).toHaveLength(46);
    expect(SPACE_SCREEN_COUNT).toBe(46);
    expect(new Set(SPACE_SCREEN_IDS).size).toBe(46);
  });

  it("excludes Beranda and Autopilot", () => {
    expect(SPACE_SCREEN_IDS).not.toContain("beranda");
    expect(SPACE_SCREEN_IDS).not.toContain("autopilot");
    expect(SPACE_TITLES).not.toHaveProperty("beranda");
  });

  it("has exactly four tabs: Chat, Tugas, Memori, Saya", () => {
    expect(MAIN_TABS.map((t) => t.id)).toEqual([
      "percakapan",
      "tugas",
      "memori",
      "saya",
    ]);
    expect(MAIN_TABS.map((t) => t.label)).toEqual([
      "Chat",
      "Tugas",
      "Memori",
      "Saya",
    ]);
  });

  it("maps every screen to exactly one tab", () => {
    for (const id of SPACE_SCREEN_IDS) {
      if (id === "onboarding") {
        // Onboarding is pre-auth: no tab.
        expect(tabOf(id)).toBeNull();
        continue;
      }
      expect(tabOf(id)).not.toBeNull();
    }
    // TAB_OF covers every non-tab screen exactly once.
    const tabbed = SPACE_SCREEN_IDS.filter(
      (id) => id !== "onboarding" && !MAIN_TABS.some((t) => t.id === id),
    );
    for (const id of tabbed) {
      expect(TAB_OF[id]).toBeDefined();
    }
  });

  it("keeps the ten hidden features out of the nav and groups", () => {
    const navText = [
      ...SPACE_SCREEN_IDS,
      ...MAIN_TABS.map((t) => t.label),
      ...SAYA_GROUPS.flatMap((g) => g.rows.map((r) => r[2])),
      ...Object.values(SPACE_TITLES),
    ]
      .join(" ")
      .toLowerCase();
    for (const hidden of HIDDEN_FEATURES) {
      expect(navText).not.toContain(hidden.toLowerCase());
    }
    expect(HIDDEN_FEATURES).toHaveLength(13);
  });

  it("keeps the Saya groups to four, max six rows each", () => {
    expect(SAYA_GROUPS.map((g) => g.id)).toEqual([
      "grupkeuangan",
      "grupstudio",
      "grupruang",
      "grupakun",
    ]);
    for (const group of SAYA_GROUPS) {
      expect(group.rows.length).toBeLessThanOrEqual(6);
      for (const [, , , target] of group.rows) {
        expect(SPACE_SCREEN_IDS).toContain(target);
      }
    }
  });

  it("has no Autopilot row in the Keuangan group", () => {
    const keuangan = SAYA_GROUPS.find((g) => g.id === "grupkeuangan");
    expect(keuangan?.rows.map((r) => r[2])).not.toContain("Autopilot Tagihan");
  });
});
