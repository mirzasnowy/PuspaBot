/**
 * Approval queue store (DuskBot ApprovalCard pattern, rewritten).
 * One tap decides: approve or reject directly from the card, with
 * the full content visible on the card itself. Decisions are
 * content-bound: `contentHash` identifies the exact approved
 * content, so a changed content hash voids the decision — the
 * server asks again. Locally, a decided card can never be
 * re-decided in the same session.
 */
import type { ApprovalRequest } from "../api/client";

export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ApprovalsState {
  items: ApprovalRequest[];
  deciding: Record<string, boolean>;
  error: string | null;
}

export function initialApprovalsState(items: ApprovalRequest[] = []): ApprovalsState {
  return { items, deciding: {}, error: null };
}

export function pendingApprovals(state: ApprovalsState): ApprovalRequest[] {
  return state.items.filter((item) => item.status === "pending");
}

export function decidedApprovals(state: ApprovalsState): ApprovalRequest[] {
  return state.items.filter((item) => item.status !== "pending");
}

/** Apply a one-tap decision. A non-pending item is never changed. */
export function applyDecision(
  state: ApprovalsState,
  requestId: string,
  status: ApprovalStatus,
): ApprovalsState {
  return {
    ...state,
    items: state.items.map((item) =>
      item.request_id === requestId && item.status === "pending"
        ? { ...item, status }
        : item,
    ),
    deciding: { ...state.deciding, [requestId]: false },
  };
}

export function markDeciding(state: ApprovalsState, requestId: string): ApprovalsState {
  return { ...state, deciding: { ...state.deciding, [requestId]: true } };
}

/**
 * Content binding check: the decision only stands while the content
 * hash is unchanged. A new hash for the same request id means the
 * approval is void and must be asked again.
 */
export function isDecisionVoid(
  decided: ApprovalRequest,
  currentContentHash: string | undefined,
): boolean {
  if (!decided.content_hash || !currentContentHash) return false;
  return decided.content_hash !== currentContentHash;
}

/** Short display form of a content hash (first 12 chars). */
export function shortHash(hash: string | undefined): string {
  if (!hash) return "—";
  return hash.length > 12 ? `${hash.slice(0, 12)}…` : hash;
}
