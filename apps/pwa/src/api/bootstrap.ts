/**
 * Server bootstrap: the FastAPI app embeds the screen's real data as
 * `window.__PUSPA_BOOTSTRAP__` JSON in the served HTML (see
 * `docs/openapi-pwa.yaml`, schema `Bootstrap`). The SPA hydrates its
 * per-screen stores from it, so the first paint already has real
 * data and no extra fetch is needed. When the page is served without
 * a bootstrap (dev server, or the Jinja fallback), every field
 * defaults to empty.
 */
import type { Bootstrap } from "./client";

declare global {
  interface Window {
    __PUSPA_BOOTSTRAP__?: Bootstrap;
  }
}

export type ScreenName =
  | "chat"
  | "tasks"
  | "modules"
  | "connectors"
  | "memory"
  | "settings"
  | "office"
  | "live"
  | "insights"
  | "onboarding"
  | "mockup"
  | "rooms"
  | "morning"
  | "login";

export const SCREENS: { name: ScreenName; path: string; label: string }[] = [
  { name: "chat", path: "/chat", label: "Chat" },
  { name: "tasks", path: "/tasks", label: "Tugas" },
  { name: "modules", path: "/modules", label: "Modul" },
  { name: "connectors", path: "/connectors", label: "Konektor" },
  { name: "memory", path: "/memory", label: "Memori" },
  { name: "settings", path: "/settings", label: "Pengaturan" },
  { name: "office", path: "/office", label: "Kantor" },
  { name: "live", path: "/live", label: "Live" },
  { name: "insights", path: "/insights", label: "Insights" },
  { name: "onboarding", path: "/onboarding", label: "Onboarding" },
  { name: "mockup", path: "/mockup", label: "Mockup" },
  { name: "rooms", path: "/rooms", label: "Ruang" },
  { name: "morning", path: "/morning", label: "Pagi" },
];

export function readBootstrap(): Bootstrap {
  if (typeof window === "undefined") return {};
  return window.__PUSPA_BOOTSTRAP__ ?? {};
}

/** Current screen from the bootstrap, falling back to the URL path. */
export function currentScreen(bootstrap: Bootstrap): ScreenName {
  if (bootstrap.screen) return bootstrap.screen;
  const path = window.location.pathname.replace(/\/$/, "") || "/chat";
  const found = SCREENS.find((s) => s.path === path);
  if (found) return found.name;
  if (path === "/masuk") return "login";
  return "chat";
}
