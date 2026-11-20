/**
 * Receipt photo uploader for the Chat screen (plan 12.5).
 *
 * Flow, always in this order: pick a photo -> upload -> read ->
 * show the parsed draft for confirmation (editable) -> record on
 * confirm, or discard. Nothing is ever recorded from a photo
 * alone: the draft is the expense, the user may edit it first,
 * and the server keeps the photo as the evidence for every
 * number in it.
 *
 * Identity cards, family cards, and official letters are refused
 * by the server (422): this component shows that refusal as an
 * error instead of a draft.
 */
import { useRef, useState } from "react";
import {
  confirmReceipt,
  discardReceipt,
  uploadReceipt,
  type ChatMessage,
  type ReceiptDraft,
  type ReceiptUpload,
} from "../api/client";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading" }
  | {
      kind: "confirming";
      pendingId: string;
      draft: ReceiptDraft;
      documentKind: string;
    }
  | { kind: "error"; message: string };

function formatAmount(amount: number): string {
  return `Rp ${amount.toLocaleString("id-ID")}`;
}

export function ReceiptUpload({ onRecorded }: { onRecorded: (message: ChatMessage) => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [merchant, setMerchant] = useState("");
  const [total, setTotal] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function onPick(file: File | undefined) {
    if (!file) return;
    setPhase({ kind: "uploading" });
    try {
      const result: ReceiptUpload = await uploadReceipt(file);
      setMerchant(result.draft.merchant ?? "");
      setTotal(result.draft.total == null ? "" : String(result.draft.total));
      setPhase({
        kind: "confirming",
        pendingId: result.pending_id,
        draft: result.draft,
        documentKind: result.document_kind,
      });
    } catch (err) {
      setPhase({
        kind: "error",
        message: err instanceof Error ? err.message : "Gagal membaca foto struk.",
      });
    }
  }

  async function onConfirm() {
    if (phase.kind !== "confirming") return;
    const editedTotal = Number(total);
    if (!merchant.trim() || !Number.isFinite(editedTotal) || editedTotal <= 0) {
      setPhase({ kind: "error", message: "Isi nama tempat dan total yang benar dulu." });
      return;
    }
    try {
      const { expense } = await confirmReceipt(phase.pendingId, {
        merchant: merchant.trim(),
        total: editedTotal,
      });
      onRecorded({
        sender: "users",
        text:
          `Pengeluaran dicatat: ${expense.merchant} — ${formatAmount(expense.total)}` +
          (expense.expense_date ? ` (${expense.expense_date})` : ""),
        created_at: Date.now() / 1000,
      } as ChatMessage);
      setPhase({ kind: "idle" });
    } catch (err) {
      setPhase({
        kind: "error",
        message: err instanceof Error ? err.message : "Gagal menyimpan pengeluaran.",
      });
    }
  }

  async function onDiscard() {
    if (phase.kind !== "confirming") return;
    try {
      await discardReceipt(phase.pendingId);
    } catch {
      // Dropping the local draft is enough; the server-side pending
      // record expires with the session either way.
    }
    setPhase({ kind: "idle" });
  }

  if (phase.kind === "confirming") {
    const { draft, documentKind } = phase;
    return (
      <div className="card">
        <p>
          <strong>Foto struk dibaca</strong> ({documentKind}). Periksa dulu sebelum disimpan:
        </p>
        <label>
          Tempat
          <input
            aria-label="Nama tempat"
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
          />
        </label>
        <label>
          Total (Rp)
          <input
            aria-label="Total"
            inputMode="numeric"
            value={total}
            onChange={(e) => setTotal(e.target.value.replace(/[^0-9]/g, ""))}
          />
        </label>
        <ul>
          {draft.lines.map((line) => (
            <li key={line.evidence_id}>
              {line.name} — {formatAmount(line.amount)}
            </li>
          ))}
        </ul>
        {draft.total_is_sum ? <p className="muted">Total dihitung dari item (tidak tertulis di struk).</p> : null}
        {draft.held_amounts > 0 ? (
          <p className="muted">{draft.held_amounts} angka di foto tidak bisa dipasangkan ke baris, jadi tidak ikut disimpan.</p>
        ) : null}
        <button type="button" onClick={onConfirm}>
          Simpan pengeluaran
        </button>{" "}
        <button type="button" onClick={onDiscard}>
          Buang
        </button>
      </div>
    );
  }

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        aria-label="Foto struk"
        style={{ display: "none" }}
        onChange={(e) => {
          void onPick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <button type="button" disabled={phase.kind === "uploading"} onClick={() => inputRef.current?.click()}>
        {phase.kind === "uploading" ? "Membaca foto…" : "Foto struk"}
      </button>
      {phase.kind === "error" ? <p role="alert" className="error">{phase.message}</p> : null}
    </div>
  );
}
