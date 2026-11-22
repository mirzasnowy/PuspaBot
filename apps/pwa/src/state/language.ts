/**
 * Client-side UI strings (COW-18).
 *
 * The server owns the strings: one catalog file per language in
 * `puspa/i18n/locales/` (`ui.*` keys), served merged by
 * `GET /api/i18n/catalog`. This module holds the loaded catalog
 * and the `t()` lookup every screen uses. It is deliberately
 * small and honest about what it does not do: it translates the
 * core screens (nav, Settings language card, Chat composer,
 * Hub/Login titles) — the rest of the screens still render their
 * hardcoded Indonesian copy until their own pass.
 *
 * Fallback chain, never blank: the loaded catalog's string for
 * the key, else the `fallback` the call site passes (the
 * hardcoded Indonesian string the screen rendered before this
 * change), else the key itself. A half-translated catalog can
 * therefore never render an empty label.
 */
import { useSyncExternalStore } from "react";
import { getCatalog, type CatalogResponse } from "../api/client";

/** Locale codes the Settings switch offers, in display order. */
export const LANGUAGE_OPTIONS = [
  { code: "id", label: "Bahasa Indonesia" },
  { code: "en", label: "English" },
] as const;

export type UICatalog = Record<string, string>;

let catalog: UICatalog = {};
let locale = "id";
let version = 0;
const listeners = new Set<() => void>();

function notify(): void {
  version += 1;
  for (const listener of listeners) listener();
}

/** The locale the loaded catalog was served for ("id" before any load). */
export function currentLocale(): string {
  return locale;
}

/**
 * Translate `key`, filling `{name}` placeholders from `vars`.
 * Falls back to `fallback` (the screen's hardcoded Indonesian
 * string), then to the key itself — never an empty string.
 */
export function translate(
  key: string,
  fallback: string,
  vars?: Record<string, string | number>,
): string {
  const template = catalog[key] ?? fallback ?? key;
  const filled = template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    vars && name in vars ? String(vars[name]) : whole,
  );
  return filled || key;
}

/** Short alias the screens use: `t("ui.nav.chat", "Chat")`. */
export const t = translate;

/**
 * Load the catalog for the user's stored language (or `locale`
 * when given) and re-render every screen using `useLanguage`.
 * Never throws: a failed load (logged out, server down) simply
 * leaves the Indonesian defaults in place.
 */
export async function loadCatalog(localeOverride?: string): Promise<void> {
  try {
    const response: CatalogResponse = await getCatalog(localeOverride);
    if (response && typeof response.strings === "object" && response.strings !== null) {
      catalog = { ...response.strings };
      locale = response.locale || localeOverride || "id";
      notify();
    }
  } catch {
    // Keep the defaults: the screens render Indonesian copy.
  }
}

/** Test-only reset: forget the loaded catalog. */
export function resetCatalogForTests(): void {
  catalog = {};
  locale = "id";
  notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getVersion(): number {
  return version;
}

/**
 * React hook: the current locale plus `t()`, re-rendering the
 * component when a catalog loads. Components that never call
 * this hook never re-render on a language change.
 */
export function useLanguage(): { locale: string; t: typeof translate } {
  useSyncExternalStore(subscribe, getVersion);
  return { locale, t: translate };
}
