import { useEffect, useState } from "react";
import type { Bootstrap } from "../api/client";
import type { ScreenName } from "../api/bootstrap";
import {
  fetchDueHints,
  markHintSeen,
  type HintContext,
  type OnboardingHint,
} from "../state/onboarding";

/**
 * One-time contextual hint banner (G28).
 *
 * Asks `GET /api/onboarding/hints` with the branch facts the
 * shell can know — which screen just opened, whether it is a
 * first visit on this install, whether an approval is waiting,
 * whether memory has been used — and shows at most the first
 * due hint. Dismissing posts the hint as seen, so the server
 * never shows it again. A failed fetch shows nothing: hints
 * are a nudge, never a blocker.
 *
 * The banner is a polite live region with an accessible name
 * (per the persistent-toast guidance: a standard, non-critical
 * message announces at the next graceful break, the dismiss
 * button stays keyboard-focusable without stealing focus, and
 * the banner persists until the user dismisses it). The
 * `billingRows` / `taskCostIdr` props are only passed by the
 * Insights screen, whose report supplies them; the shell-level
 * banner in `App.tsx` never passes them, so the two render
 * sites can only surface different hints.
 */

const VISITED_KEY = "puspa:visited-screens";

function readVisitedScreens(): Set<string> {
  try {
    const raw = window.localStorage.getItem(VISITED_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((entry): entry is string => typeof entry === "string"));
  } catch {
    return new Set();
  }
}

function markScreenVisited(screen: string): void {
  try {
    const visited = readVisitedScreens();
    visited.add(screen);
    window.localStorage.setItem(VISITED_KEY, JSON.stringify([...visited]));
  } catch {
    // A missing or blocked localStorage only loses first-visit
    // detection; the banner still works without it.
  }
}

export function HintBanner({
  screen,
  bootstrap,
  billingRows,
  taskCostIdr,
}: {
  screen: ScreenName;
  bootstrap?: Bootstrap;
  billingRows?: number;
  taskCostIdr?: number;
}) {
  const [hint, setHint] = useState<OnboardingHint | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const visited = readVisitedScreens();
    const firstVisit = !visited.has(screen);
    markScreenVisited(screen);
    const context: HintContext = {
      screen,
      first_visit: firstVisit,
    };
    if (bootstrap) {
      context.approval_requested = (bootstrap.approvals ?? []).length > 0;
      context.memory_used = (bootstrap.profile ?? []).length > 0;
    }
    if (billingRows !== undefined) context.billing_rows = billingRows;
    if (taskCostIdr !== undefined) context.task_cost_idr = taskCostIdr;
    fetchDueHints(context)
      .then((result) => {
        if (!cancelled) setHint(result.hints[0] ?? null);
      })
      .catch(() => {
        // Hints are best-effort; a failed fetch shows no banner.
      });
    return () => {
      cancelled = true;
    };
  }, [screen, bootstrap, billingRows, taskCostIdr]);

  if (dismissed || !hint) return null;

  // Captured before `dismiss` is defined: narrowing of `hint`
  // does not reach inside a hoisted function declaration.
  const flag = hint.flag;

  function dismiss() {
    setDismissed(true);
    markHintSeen(flag).catch(() => {
      // The banner is already gone; a failed seen-post only
      // means the hint may be offered again later.
    });
  }

  return (
    <div role="region" aria-label="Petunjuk" aria-live="polite" className="card">
      <strong>{hint.title}</strong>
      <p>{hint.body}</p>
      <p>
        {hint.screen !== screen ? (
          <>
            <a href={`/${hint.screen}`}>{hint.action_label}</a> ·{" "}
          </>
        ) : null}
        <button type="button" onClick={dismiss} aria-label={`Tutup petunjuk: ${hint.title}`}>
          Tutup
        </button>
      </p>
    </div>
  );
}
