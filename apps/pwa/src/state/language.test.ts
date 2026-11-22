/**
 * Tests: client-side UI strings (COW-18).
 *
 * `translate()` never renders a blank: the loaded catalog wins,
 * else the call site's hardcoded Indonesian fallback, else the
 * key itself. `loadCatalog()` flips the rendered strings to the
 * served locale, and a failed load keeps the Indonesian defaults.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LANGUAGE_OPTIONS,
  currentLocale,
  loadCatalog,
  resetCatalogForTests,
  translate,
} from "./language";

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

beforeEach(() => {
  resetCatalogForTests();
  vi.unstubAllGlobals();
});

afterEach(() => {
  resetCatalogForTests();
  vi.unstubAllGlobals();
});

describe("translate without a loaded catalog", () => {
  it("renders the hardcoded Indonesian fallback", () => {
    expect(translate("ui.nav.tasks", "Tugas")).toBe("Tugas");
    expect(translate("ui.nav.settings", "Pengaturan")).toBe("Pengaturan");
  });

  it("never renders a blank: an empty fallback returns the key itself", () => {
    expect(translate("ui.nav.nope", "")).toBe("ui.nav.nope");
  });

  it("fills {name} placeholders and leaves unknown ones untouched", () => {
    expect(translate("ui.chat.input_placeholder", "Tulis pesan ke {bot}...", { bot: "Puspa" })).toBe(
      "Tulis pesan ke Puspa...",
    );
    expect(translate("ui.chat.input_placeholder", "Tulis pesan ke {bot}...")).toBe(
      "Tulis pesan ke {bot}...",
    );
  });
});

describe("loadCatalog flips the rendered strings", () => {
  it("serves English strings after the English catalog loads", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        locale: "en",
        strings: {
          "ui.nav.tasks": "Tasks",
          "ui.nav.settings": "Settings",
          "ui.chat.input_placeholder": "Write a message to {bot}...",
        },
      }),
    );
    await loadCatalog("en");
    expect(currentLocale()).toBe("en");
    expect(translate("ui.nav.tasks", "Tugas")).toBe("Tasks");
    expect(translate("ui.nav.settings", "Pengaturan")).toBe("Settings");
    expect(translate("ui.chat.input_placeholder", "Tulis pesan ke {bot}...", { bot: "Puspa" })).toBe(
      "Write a message to Puspa...",
    );
  });

  it("a key the served catalog is missing falls back honestly, never blank", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ locale: "en", strings: { "ui.nav.tasks": "Tasks" } }),
    );
    await loadCatalog("en");
    expect(translate("ui.nav.tasks", "Tugas")).toBe("Tasks");
    expect(translate("ui.nav.settings", "Pengaturan")).toBe("Pengaturan");
    expect(translate("ui.nav.rooms", "Ruang")).toBe("Ruang");
  });

  it("a failed load keeps the Indonesian defaults and never throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("server down")));
    await loadCatalog("en");
    expect(currentLocale()).toBe("id");
    expect(translate("ui.nav.tasks", "Tugas")).toBe("Tugas");
  });
});

describe("LANGUAGE_OPTIONS", () => {
  it("offers Indonesian first, then English", () => {
    expect(LANGUAGE_OPTIONS.map((option) => option.code)).toEqual(["id", "en"]);
  });
});
