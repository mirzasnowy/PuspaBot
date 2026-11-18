/**
 * Memory screen store: profile facts seeded from the bootstrap.
 * The server returns rows from `puspa.memory.profile.list_facts`;
 * rows may be full fact objects or plain strings (older shapes), so
 * the normalizer accepts both and never crashes the screen.
 */

export type FactOrigin = "user_stated" | "user_confirmed" | "inferred" | "unknown";
export type FactStatus = "proposal" | "active" | "rejected" | "replaced" | "unknown";

export interface NormalizedFact {
  key: string;
  value: string;
  origin: FactOrigin;
  status: FactStatus;
}

export function normalizeFact(raw: unknown): NormalizedFact | null {
  if (typeof raw === "string") {
    return { key: raw, value: "", origin: "unknown", status: "unknown" };
  }
  if (raw === null || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const key = typeof row.key === "string" ? row.key : "";
  const value =
    typeof row.value === "string"
      ? row.value
      : typeof row.fact === "string"
        ? row.fact
        : "";
  if (!key && !value) return null;
  const origin = row.origin;
  const status = row.status;
  return {
    key: key || value,
    value: key ? value : "",
    origin:
      origin === "user_stated" || origin === "user_confirmed" || origin === "inferred"
        ? origin
        : "unknown",
    status:
      status === "proposal" || status === "active" || status === "rejected" || status === "replaced"
        ? status
        : "unknown",
  };
}

export function normalizeFacts(raw: unknown): NormalizedFact[] {
  if (!Array.isArray(raw)) return [];
  const out: NormalizedFact[] = [];
  for (const item of raw) {
    const fact = normalizeFact(item);
    if (fact) out.push(fact);
  }
  return out;
}

/** Facts waiting for user confirmation (inferred proposals). */
export function proposals(facts: NormalizedFact[]): NormalizedFact[] {
  return facts.filter((f) => f.status === "proposal");
}

export function activeFacts(facts: NormalizedFact[]): NormalizedFact[] {
  return facts.filter((f) => f.status === "active" || f.status === "unknown");
}

export const ORIGIN_LABELS: Record<FactOrigin, string> = {
  user_stated: "User stated",
  user_confirmed: "User confirmed",
  inferred: "Inferred (proposal)",
  unknown: "Unknown origin",
};
