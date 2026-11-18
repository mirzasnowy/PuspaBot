/**
 * ApprovalCard (DuskBot ApprovalCard/AskCard pattern, rewritten).
 * One tap decides: the full content of the request is visible on
 * the card itself — kind, summary, amount, target, and the content
 * hash the decision binds to. After a decision the card locks:
 * the buttons disable and the status badge shows the outcome.
 */
import { formatRupiah, type ApprovalRequest } from "../api/client";
import { shortHash } from "../state/approvals";

export interface ApprovalCardProps {
  item: ApprovalRequest;
  deciding: boolean;
  onDecide: (requestId: string, approve: boolean) => void;
}

export function ApprovalCard({ item, deciding, onDecide }: ApprovalCardProps) {
  const decided = item.status !== "pending";
  return (
    <article className={`card approval-card approval-${item.status}`} data-testid="approval-card">
      <div className="approval-head">
        <strong>{item.kind ?? "Approval"}</strong>
        <span className="badge" data-testid="approval-status">
          {item.status === "pending"
            ? "Menunggu"
            : item.status === "approved"
              ? "Disetujui"
              : "Ditolak"}
        </span>
      </div>
      {item.summary ? <p>{item.summary}</p> : null}
      <dl className="approval-facts">
        {item.target ? (
          <>
            <dt>Tujuan</dt>
            <dd>{item.target}</dd>
          </>
        ) : null}
        {typeof item.amount_rp === "number" ? (
          <>
            <dt>Jumlah</dt>
            <dd>{formatRupiah(item.amount_rp)}</dd>
          </>
        ) : null}
        {item.task_id ? (
          <>
            <dt>Tugas</dt>
            <dd>{item.task_id}</dd>
          </>
        ) : null}
        <dt>Hash isi</dt>
        <dd data-testid="approval-hash">{shortHash(item.content_hash)}</dd>
      </dl>
      <p className="muted">
        Persetujuan terikat isi: kalau isi berubah setelah disetujui, persetujuan batal dan diminta
        ulang.
      </p>
      <div className="approval-actions">
        <button
          type="button"
          disabled={decided || deciding}
          onClick={() => onDecide(item.request_id, true)}
        >
          {deciding ? "Menyimpan…" : "Setuju"}
        </button>
        <button
          type="button"
          className="button-secondary"
          disabled={decided || deciding}
          onClick={() => onDecide(item.request_id, false)}
        >
          Tolak
        </button>
      </div>
    </article>
  );
}
