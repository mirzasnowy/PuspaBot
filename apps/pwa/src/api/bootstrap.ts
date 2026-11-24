/**
 * Server bootstrap: the FastAPI app embeds the screen's real data as
 * `window.__PUSPA_BOOTSTRAP__` JSON in the served HTML (see
 * `docs/openapi-pwa.yaml`, schema `Bootstrap`). The SPA hydrates its
 * per-screen stores from it, so the first paint already has real
 * data and no extra fetch is needed. When the page is served without
 * a bootstrap (dev server, or the Jinja fallback), every field
 * defaults to empty.
 *
 * Screen ids are the Space puspabot-app ids (see `./space`): four
 * tabs (Chat, Tugas, Memori, Saya) plus one sub-screen per feature.
 * Beranda is deliberately absent (Nero, 23 Nov): the PWA has four
 * tabs, and the Beranda design lives only in saved-designs.
 */
import type { Bootstrap } from "./client";
import { SPACE_SCREEN_IDS, SPACE_TITLES, type SpaceScreenId } from "./space";

declare global {
  interface Window {
    __PUSPA_BOOTSTRAP__?: Bootstrap;
  }
}

export type { SpaceScreenId };

/**
 * Legacy screens that predate the Space port. They are NOT in the
 * four-tab navigation and NOT counted in the Space screens, but
 * their old paths still resolve (direct URL only) so existing
 * bookmarks and docs links keep working.
 */
export type LegacyScreenName =
  | "diagnostics"
  | "hub"
  | "wiki"
  | "artifacts"
  | "mockup"
  | "login";

export type ScreenName = SpaceScreenId | LegacyScreenName;

function spacePath(id: SpaceScreenId): string {
  if (id === "percakapan") return "/chat";
  if (id === "chat") return "/chat/puspa";
  if (id === "ruangchat") return "/chat/ruang";
  return `/${id}`;
}

export const SCREENS: { name: ScreenName; path: string; label: string }[] = [
  ...SPACE_SCREEN_IDS.map((id) => ({
    name: id as ScreenName,
    path: spacePath(id),
    label: SPACE_TITLES[id],
  })),
  { name: "diagnostics", path: "/diagnostics", label: "Diagnostik" },
  { name: "hub", path: "/hub", label: "Hub Skill" },
  { name: "wiki", path: "/wiki", label: "Wiki" },
  { name: "artifacts", path: "/artifacts", label: "Artefak" },
  { name: "mockup", path: "/mockup", label: "Mockup" },
  { name: "login", path: "/masuk", label: "Masuk" },
];

/** Old paths redirect to their Space screen (same feature, new id). */
export const LEGACY_ALIASES: Record<string, ScreenName> = {
  "/": "percakapan",
  "/tasks": "tugas",
  "/memory": "memori",
  "/modules": "modul",
  "/connectors": "konektor",
  "/settings": "saya",
  "/personas": "persona",
  "/office": "ruang",
  "/morning": "pagi",
  "/rooms": "ruang",
  "/live": "live",
  "/replay": "replay",
  "/insights": "insights",
};

export function readBootstrap(): Bootstrap {
  if (typeof window === "undefined") return {};
  return window.__PUSPA_BOOTSTRAP__ ?? {};
}

/** Current screen from the bootstrap, falling back to the URL path. */
export function currentScreen(bootstrap: Bootstrap): ScreenName {
  if (bootstrap.screen) return bootstrap.screen as ScreenName;
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  const found = SCREENS.find((s) => s.path === path);
  if (found) return found.name;
  if (LEGACY_ALIASES[path]) return LEGACY_ALIASES[path];
  return "percakapan";
}
