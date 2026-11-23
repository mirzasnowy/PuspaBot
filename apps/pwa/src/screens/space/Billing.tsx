/**
 * Billing screens, Space port: Cek Harga, Bayar Tagihan (PPOB),
 * the four billing detail screens, Persetujuan, Foto Struk,
 * Keuangan.
 *
 * Wired to real APIs only: price search creates a real
 * price-compare task (POST /api/tugas); billing details render
 * the matching pending approval (amount, target, summary) and
 * pay through POST /api/persetujuan/{id}; receipts use the real
 * OCR upload + GET /api/receipts/expenses; Keuangan totals the
 * real confirmed expenses. There is no balance API, so no saldo
 * number is shown. Autopilot Tagihan is NOT ported: it has no
 * backend API, and a toggle that only pretends would lie.
 */
import { useEffect, useState } from "react";
import {
  createTask,
  decideApproval,
  fetchReceiptExpenses,
  formatRupiah,
  type ApprovalRequest,
  type Bootstrap,
  type ReceiptExpense,
} from "../../api/client";
import { ReceiptUpload } from "../../components/ReceiptUpload";
import { Icon } from "../../components/icons";
import { GroupCard, PageHead, RowItem, useToast } from "./shared";

export function PriceCheckScreen() {
  const [query, setQuery] = useState("iPhone 15 128GB");
  const [sent, setSent] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { show, toast } = useToast();

  async function onSearch(event: React.FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q || busy) return;
    setBusy(true);
    setError("");
    try {
      await createTask(`Bandingkan harga ${q} di Tokopedia, Shopee, dan Blibli`);
      setSent(q);
      show("Scout memverifikasi harga di 3 marketplace — pantau di Tugas.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengirim pencarian.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHead title="Cek Harga" back />
      <form onSubmit={onSearch}>
        <div className="searchbar" style={{ marginTop: 16 }}>
          <Icon name="search" size={20} />
          <input
            type="search"
            value={query}
            aria-label="Produk yang dicari"
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="btn primary sm" disabled={busy}>
            Cari
          </button>
        </div>
      </form>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      {sent ? (
        <div className="card" style={{ marginTop: 16 }}>
          <b>Pencarian dikirim: {sent}</b>
          <p className="small" style={{ color: "var(--ink-2)" }}>
            Scout memeriksa Tokopedia, Shopee, dan Blibli, lalu menyaring penjual
            terpercaya. Hasilnya masuk sebagai tugas.
          </p>
          <a className="btn primary sm" href="/tugas">
            Lihat di Tugas
          </a>
        </div>
      ) : null}
      <div className="warnbox" style={{ marginTop: 14 }}>
        <span style={{ color: "#C47A1E" }}>
          <Icon name="alert" size={20} />
        </span>
        <span>
          <b>Harga anomali ditandai.</b> Penjual dengan harga &gt;30% di bawah
          pasar akan ditandai sebelum kamu checkout, supaya kamu bisa cek ulang
          dulu.
        </span>
      </div>
      {toast}
    </div>
  );
}

const PPOB_TILES = [
  { icon: "bolt", cls: "peach", label: "PLN", href: "/pln" },
  { icon: "drop", cls: "blue", label: "PDAM", href: "/pdam" },
  { icon: "phone", cls: "", label: "Pulsa", href: "/pulsa" },
  { icon: "globe", cls: "green", label: "Paket Data", href: "/paketdata" },
] as const;

export function PpobScreen() {
  return (
    <div className="page">
      <PageHead title="Bayar Tagihan" back />
      <div className="grid3" style={{ marginTop: 18 }}>
        {PPOB_TILES.map((tile) => (
          <a
            key={tile.label}
            className="card flat"
            style={{ textAlign: "left", padding: 16, textDecoration: "none", color: "inherit" }}
            href={tile.href}
          >
            <span className={`sq-ic ${tile.cls}`}>
              <Icon name={tile.icon} size={22} />
            </span>
            <div style={{ marginTop: 11 }}>
              <b style={{ fontSize: 16 }}>{tile.label}</b>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

function matchApproval(
  approvals: ApprovalRequest[],
  keyword: string,
): ApprovalRequest | undefined {
  const key = keyword.toLowerCase();
  return approvals.find((a) =>
    `${a.summary ?? ""} ${a.target ?? ""} ${a.kind ?? ""}`.toLowerCase().includes(key),
  );
}

export function BillDetailScreen({
  bootstrap,
  service,
  icon,
  cls,
  fields,
  warning,
  payLabel,
}: {
  bootstrap: Bootstrap;
  service: string;
  icon: string;
  cls: string;
  fields: [string, string][];
  warning: string;
  payLabel: string;
}) {
  const approval = matchApproval(bootstrap.approvals ?? [], service);
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState("");
  const { show, toast } = useToast();

  async function askCheck() {
    if (checking) return;
    setChecking(true);
    setError("");
    try {
      await createTask(`Cek tagihan ${service}`);
      setChecked(true);
      show(`Puspa mengecek tagihan ${service} — pantau di Tugas.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal meminta pengecekan.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="page">
      <PageHead title={`Pembayaran ${service}`} back />
      <div className="card" style={{ marginTop: 18 }}>
        <div style={{ textAlign: "center" }}>
          <span className={`sq-ic ${cls}`} style={{ margin: "0 auto" }}>
            <Icon name={icon} size={26} />
          </span>
          <div className="amount-hero">
            <div className="amt">
              {approval && typeof approval.amount_rp === "number"
                ? formatRupiah(approval.amount_rp)
                : "—"}
            </div>
          </div>
        </div>
        <div className="divider" />
        {approval ? (
          <>
            {approval.summary ? (
              <div className="kv">
                <span>Tagihan</span>
                <b>{approval.summary}</b>
              </div>
            ) : null}
            {approval.target ? (
              <div className="kv">
                <span>Tujuan</span>
                <b>{approval.target}</b>
              </div>
            ) : null}
            <div className="kv">
              <span>Sumber dana</span>
              <b>Saldo Puspa</b>
            </div>
          </>
        ) : (
          fields.map(([label, value]) => (
            <div className="kv" key={label}>
              <span>{label}</span>
              <b>{value}</b>
            </div>
          ))
        )}
        <div className="warnbox" style={{ marginTop: 12 }}>
          <span style={{ color: "#C47A1E" }}>
            <Icon name="alert" size={20} />
          </span>
          <span>{warning}</span>
        </div>
        {approval ? (
          <a
            className="btn primary block"
            style={{ marginTop: 16, textDecoration: "none" }}
            href={`/persetujuan?id=${encodeURIComponent(approval.request_id)}`}
          >
            {payLabel}
          </a>
        ) : (
          <button
            type="button"
            className="btn primary block"
            style={{ marginTop: 16 }}
            onClick={() => void askCheck()}
            disabled={checking}
          >
            {checked ? "Pengecekan diminta — lihat di Tugas" : "Minta Puspa Cek Tagihan"}
          </button>
        )}
        <button
          type="button"
          className="btn ghost block"
          style={{ marginTop: 9 }}
          onClick={() => window.history.back()}
        >
          Nanti Dulu
        </button>
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : null}
      </div>
      {toast}
    </div>
  );
}

export function ApprovalScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const id = new URLSearchParams(window.location.search).get("id") ?? "";
  const pending = (bootstrap.approvals ?? []).filter((a) => a.status === "pending");
  const approval = pending.find((a) => a.request_id === id) ?? pending[0];
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function decide(approve: boolean) {
    if (!approval || busy) return;
    setBusy(true);
    setError("");
    try {
      await decideApproval(approval.request_id, approve);
      setDone(
        approve
          ? "Disetujui & dibayar. Terima kasih!"
          : "Pembayaran ditolak. Puspa tidak akan membayar tanpa izinmu.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memutuskan persetujuan.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div style={{ maxWidth: 480, margin: "8px auto 0" }}>
        <div className="card" style={{ textAlign: "center", padding: "28px 22px" }}>
          <span
            className="sq-ic green"
            style={{ width: 74, height: 74, borderRadius: 22, margin: "0 auto" }}
          >
            <Icon name="shield" size={32} />
          </span>
          <h1 style={{ fontSize: 23, fontWeight: 800, letterSpacing: "-.02em", margin: "16px 0 6px" }}>
            Persetujuan Diperlukan
          </h1>
          {done ? (
            <p>{done}</p>
          ) : approval ? (
            <>
              <div className="divider" />
              {approval.summary ? (
                <div className="kv">
                  <span>Tagihan</span>
                  <b>{approval.summary}</b>
                </div>
              ) : null}
              <div className="kv">
                <span>Dari</span>
                <b>Saldo Puspa</b>
              </div>
              <div className="kv">
                <span>Jumlah</span>
                <b>
                  {typeof approval.amount_rp === "number"
                    ? formatRupiah(approval.amount_rp)
                    : "—"}
                </b>
              </div>
              <div className="kv">
                <span>Kredit terpakai</span>
                <b>1 kredit</b>
              </div>
              <div className="kv">
                <span>Otonomi agen</span>
                <b>pembayaran selalu manual</b>
              </div>
              <button
                type="button"
                className="btn primary block"
                style={{ marginTop: 16 }}
                onClick={() => void decide(true)}
                disabled={busy}
              >
                Setuju, Bayar
              </button>
              <button
                type="button"
                className="btn ghost block"
                style={{ marginTop: 9 }}
                onClick={() => void decide(false)}
                disabled={busy}
              >
                Tolak
              </button>
            </>
          ) : (
            <p>Semua beres! Tidak ada yang menunggu persetujuanmu.</p>
          )}
          {error ? (
            <p role="alert" className="error">
              {error}
            </p>
          ) : null}
        </div>
        <p className="tiny muted" style={{ textAlign: "center", marginTop: 12 }}>
          Setiap pembayaran tercatat di log audit dan bisa kamu tinjau di Antrean
          Persetujuan.
        </p>
      </div>
    </div>
  );
}

export function ReceiptScreen() {
  const [expenses, setExpenses] = useState<ReceiptExpense[]>([]);
  const [error, setError] = useState("");
  const { show, toast } = useToast();

  async function refresh() {
    try {
      const result = await fetchReceiptExpenses();
      setExpenses(result.expenses);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat struk.");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const last = expenses[0];

  return (
    <div className="page">
      <PageHead title="Foto Struk" back />
      <div className="grid2" style={{ marginTop: 16 }}>
        <div className="card" style={{ textAlign: "center" }}>
          <span
            className="sq-ic peach"
            style={{ width: 70, height: 70, borderRadius: 20, margin: "4px auto 0" }}
          >
            <Icon name="cam" size={30} />
          </span>
          <div style={{ marginTop: 14 }}>
            <ReceiptUpload
              onRecorded={() => {
                void refresh();
                show("Struk terbaca dan tercatat ke Keuangan.");
              }}
            />
          </div>
        </div>
        <div className="card">
          <b>Hasil baca terakhir</b>
          {last ? (
            <>
              <span className="tag green" style={{ marginLeft: 8 }}>
                OCR selesai
              </span>
              <div className="divider" />
              <div className="kv">
                <span>{last.merchant}</span>
                <b>{last.expense_date ?? ""}</b>
              </div>
              {last.lines.map((line, i) => (
                <div className="kv" key={`${last.expense_id}-${i}`}>
                  <span>{line.name}</span>
                  <b>{formatRupiah(line.amount)}</b>
                </div>
              ))}
              <div className="divider" />
              <div className="kv">
                <span>
                  <b>Total</b>
                </span>
                <b style={{ color: "var(--red)" }}>{formatRupiah(last.total)}</b>
              </div>
            </>
          ) : (
            <p className="muted">Belum ada struk yang dibaca.</p>
          )}
          <a className="btn ghost sm" style={{ marginTop: 8 }} href="/keuangan">
            Lihat di Keuangan
          </a>
        </div>
      </div>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      {toast}
    </div>
  );
}

export function FinanceScreen() {
  const [expenses, setExpenses] = useState<ReceiptExpense[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchReceiptExpenses()
      .then((result) => {
        if (!cancelled) setExpenses(result.expenses);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Gagal memuat keuangan.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const total = expenses.reduce((sum, e) => sum + e.total, 0);

  return (
    <div className="page">
      <PageHead title="Keuangan" back />
      <div className="grid2" style={{ marginTop: 16 }}>
        <div className="card">
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <span className="sq-ic">
              <Icon name="wallet" size={22} />
            </span>
            <b style={{ fontSize: 16.5, flex: 1, minWidth: 0 }}>
              Pengeluaran Tercatat
            </b>
            <b style={{ fontSize: 19, fontWeight: 800 }}>{formatRupiah(total)}</b>
          </div>
          <div className="divider" />
          <p className="small" style={{ color: "var(--ink-2)" }}>
            Total dari {expenses.length} struk yang sudah dikonfirmasi. Saldo tidak
            ditampilkan: belum ada API saldo.
          </p>
          <a className="btn primary sm" href="/paket">
            Top Up
          </a>
        </div>
        <div className="card">
          <b>Transaksi Terakhir</b>
          {expenses.length === 0 && !error ? (
            <p className="muted">Belum ada transaksi tercatat.</p>
          ) : (
            expenses.slice(0, 8).map((e) => (
              <div className="kv" key={e.expense_id}>
                <span>
                  {e.merchant} · {e.expense_date ?? ""}
                </span>
                <b>-{formatRupiah(e.total)}</b>
              </div>
            ))
          )}
        </div>
      </div>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <GroupCard label="Catat Pengeluaran">
        <RowItem href="/struk" icon="cam" title="Foto Struk" />
      </GroupCard>
    </div>
  );
}
