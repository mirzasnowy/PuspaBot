/**
 * Insights state for the Insights screen (G28).
 *
 * Wire types come from `../api/client` (generated from
 * `docs/openapi-pwa.yaml`); they mirror the report
 * `puspa/billing/insights.py` returns from `GET
 * /api/insights?days=N`. Costs arrive as exact Rupiah integers
 * plus server-rendered labels -- the client never re-derives a
 * cost, so a small amount can never be displayed as an
 * approximate "~$0.00".
 */
import { getJson, type FetchFn } from "../api/client";
import type {
  CostlyTask,
  CostSummary,
  DayActivity,
  DriverBreakdown,
  InsightsReport,
  ModelBreakdown,
  ModuleUsage,
  TokenTotals,
  ToolUsage,
} from "../api/client";

export type {
  CostlyTask,
  CostSummary,
  DayActivity,
  DriverBreakdown,
  InsightsReport,
  ModelBreakdown,
  ModuleUsage,
  TokenTotals,
  ToolUsage,
};

/** Window lengths the Insights screen offers, in display order. */
export const INSIGHT_DAY_OPTIONS = [7, 30, 90] as const;

export type InsightDays = (typeof INSIGHT_DAY_OPTIONS)[number];

/** The report for a user with no recorded turns: honest zeros. */
export function emptyInsights(days: number): InsightsReport {
  const now = new Date().toISOString();
  return {
    window_days: days,
    since: now,
    until: now,
    turns: 0,
    tokens: { input: 0, output: 0, cached: 0, total: 0 },
    cost: {
      total_idr: 0,
      label: "Rp0",
      avg_per_turn_idr: 0,
      avg_per_turn_label: "Rp0",
      per_1k_tokens_idr: null,
      per_1k_tokens_label: null,
    },
    per_model: [],
    per_driver: [],
    tool_usage: [],
    module_usage: [],
    activity_per_day: [],
    top_costly_tasks: [],
    tool_errors: 0,
    task_cost_ceiling_idr: 2000,
  };
}

/** True when the report holds at least one recorded turn. */
export function hasUsage(report: InsightsReport): boolean {
  return report.turns > 0;
}

/** Validate a window length the way the server does (1..90 days). */
export function validateDays(days: number): string | null {
  if (!Number.isInteger(days) || days < 1 || days > 90) {
    return "Rentang harus 1 sampai 90 hari.";
  }
  return null;
}

/** Fetch the insights report for the logged-in user. */
export function fetchInsights(
  days: number,
  fetchFn: FetchFn = fetch,
): Promise<InsightsReport> {
  const error = validateDays(days);
  if (error) return Promise.reject(new Error(error));
  return getJson<InsightsReport>(`/api/insights?days=${days}`, fetchFn);
}
