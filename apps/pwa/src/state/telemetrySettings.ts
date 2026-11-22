/**
 * Telemetry toggle state for the Settings screen (AST-09).
 *
 * Pure helpers around the `TelemetrySettings` wire type from
 * `../api/client`: the defaults for a user who never opened the
 * toggle (off, no environment override), a normalizer for
 * server answers, and the one-line explanation the screen shows
 * for the current state.
 */
import type { TelemetrySettings } from "../api/client";

export type { TelemetrySettings };

/** The toggle for a user who never opened Settings: off. */
export function defaultTelemetrySettings(): TelemetrySettings {
  return {
    enabled: false,
    env_override: null,
    effective_enabled: false,
  };
}

/**
 * Fill in a server answer's missing fields with the defaults,
 * so the screen never reads `undefined` as a switch position.
 */
export function withTelemetryDefaults(settings: TelemetrySettings): TelemetrySettings {
  return {
    enabled: settings.enabled ?? false,
    env_override: settings.env_override ?? null,
    effective_enabled: settings.effective_enabled ?? settings.enabled ?? false,
  };
}

/** One-line explanation of what the current state means. */
export function describeTelemetry(settings: TelemetrySettings): string {
  if (settings.env_override === true) {
    return (
      "Diaktifkan oleh operator lewat PUSPA_TELEMETRY=1; saklar di sini tidak mengubahnya. " +
      "Statistik anonim dikirim ke endpoint yang dikonfigurasi operator."
    );
  }
  if (settings.env_override === false) {
    return (
      "Dimatikan oleh operator lewat PUSPA_TELEMETRY=0; saklar di sini tidak mengubahnya. " +
      "Tidak ada statistik yang dikirim."
    );
  }
  if (settings.enabled) {
    return (
      "Aktif: Puspa mengirim statistik pemakaian anonim (jumlah pemakaian fitur dan durasi, " +
      "tanpa isi pesan, tanpa identitas, tanpa token) ke endpoint yang dikonfigurasi operator."
    );
  }
  return "Mati (default untuk self-host): tidak ada statistik yang dikirim.";
}
