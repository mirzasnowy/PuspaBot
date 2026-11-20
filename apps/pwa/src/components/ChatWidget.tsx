/**
 * ChatWidget (P-14): the five interactive widgets that render
 * inside a chat message — calculator, form, comparison,
 * diagram, and simulation. The widget payload is declarative
 * JSON (see `puspa/pwa/chat_widgets.py`): the component only
 * renders what the payload describes, and holds the widget's
 * state (input values, the picked diagram node, the sort
 * column) locally.
 *
 * - Calculator: number/slider inputs plus a formula; the
 *   result updates live, evaluated by the safe evaluator in
 *   `state/chatWidgets.ts` (the server's
 *   `/api/chat/widgets/compute` is the authoritative check).
 * - Form: fields the user fills in; submitting validates the
 *   answers server-side and posts them as a chat message.
 * - Comparison: rows against named columns; numeric columns
 *   sort, and the cheapest row in each numeric column is
 *   highlighted.
 * - Diagram: an acyclic flow; tapping a node shows its detail.
 * - Simulation: the deterministic world simulator as a widget
 *   (prices per store, the PLN bill, a price chart, a day
 *   stepper, pay-bill and buy actions). Every step goes through
 *   the server, which re-derives the world from the widget's
 *   day and bill state; the new message replaces the widget in
 *   the thread.
 *
 * Honest limit: this is verified by vitest/jsdom interaction
 * tests, not by a tap or a slider drag on a real screen — this
 * VM has no live browser. The simulation shows only what the
 * deterministic simulator computes (fixed daily price drifts,
 * one PLN bill), not live market data.
 */
import { useState } from "react";
import {
  formatRupiah,
  simulateWidget,
  submitWidgetForm,
  type ChatMessage,
  type ChatWidget,
  type SimulateWidgetAction,
} from "../api/client";
import { evaluateFormula, formatWidgetValue } from "../state/chatWidgets";

export interface ChatWidgetProps {
  widget: ChatWidget;
  onNewMessage?: (message: ChatMessage) => void;
}

const CHART_WIDTH = 320;
const CHART_HEIGHT = 160;

const BILL_STATUS_LABELS: Record<string, string> = {
  not_due: "Belum jatuh tempo",
  due_soon: "Segera jatuh tempo",
  due: "Jatuh tempo",
  paid: "Lunas",
};

function CalculatorWidgetView({
  widget,
}: {
  widget: Extract<ChatWidget, { kind: "calculator" }>;
}) {
  const [values, setValues] = useState<Record<string, number>>(() =>
    Object.fromEntries(widget.inputs.map((input) => [input.id, input.value])),
  );
  let result: number | null = null;
  let error: string | null = null;
  try {
    result = evaluateFormula(widget.formula, values);
  } catch (err) {
    error = err instanceof Error ? err.message : "Rumus tidak bisa dihitung.";
  }

  return (
    <article className="card chat-card chat-widget-calculator" data-testid="chat-widget-calculator">
      <strong>{widget.title}</strong>
      {widget.inputs.map((input) => (
        <label key={input.id}>
          {input.label}
          {input.input_kind === "slider" ? (
            <span>
              <input
                type="range"
                aria-label={input.label}
                min={input.min}
                max={input.max}
                step={input.step}
                value={values[input.id]}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [input.id]: Number(e.target.value) }))
                }
              />{" "}
              {values[input.id]}
            </span>
          ) : (
            <input
              type="number"
              aria-label={input.label}
              min={input.min}
              max={input.max}
              step={input.step}
              value={values[input.id]}
              onChange={(e) =>
                setValues((prev) => ({ ...prev, [input.id]: Number(e.target.value) }))
              }
            />
          )}
        </label>
      ))}
      <p>
        {widget.result_label}:{" "}
        <strong>
          {result === null ? "—" : formatWidgetValue(result, widget.value_format)}
        </strong>
      </p>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
    </article>
  );
}

type FormAnswers = Record<string, string | number | boolean | null>;

function FormWidgetView({
  widget,
  onNewMessage,
}: {
  widget: Extract<ChatWidget, { kind: "form" }>;
  onNewMessage?: (message: ChatMessage) => void;
}) {
  const [answers, setAnswers] = useState<FormAnswers>(() =>
    Object.fromEntries(widget.fields.map((field) => [field.id, null])),
  );
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setAnswer(id: string, value: string | number | boolean | null) {
    setAnswers((prev) => ({ ...prev, [id]: value }));
  }

  async function onSubmit() {
    if (busy || submitted) return;
    setBusy(true);
    setError(null);
    try {
      const message = await submitWidgetForm(widget, answers);
      setSubmitted(true);
      onNewMessage?.(message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengirim formulir.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="card chat-card chat-widget-form" data-testid="chat-widget-form">
      <strong>{widget.title}</strong>
      {widget.fields.map((field) => {
        const answer = answers[field.id];
        return (
          <label key={field.id}>
            {field.label}
            {field.required ? " *" : ""}
            {field.field_kind === "text" ? (
              <input
                type="text"
                aria-label={field.label}
                placeholder={field.placeholder}
                value={typeof answer === "string" ? answer : ""}
                disabled={submitted}
                onChange={(e) => setAnswer(field.id, e.target.value)}
              />
            ) : null}
            {field.field_kind === "number" ? (
              <input
                type="number"
                aria-label={field.label}
                placeholder={field.placeholder}
                value={typeof answer === "number" ? answer : ""}
                disabled={submitted}
                onChange={(e) =>
                  setAnswer(field.id, e.target.value === "" ? null : Number(e.target.value))
                }
              />
            ) : null}
            {field.field_kind === "select" ? (
              <select
                aria-label={field.label}
                value={typeof answer === "string" ? answer : ""}
                disabled={submitted}
                onChange={(e) => setAnswer(field.id, e.target.value || null)}
              >
                <option value="">Pilih…</option>
                {field.options?.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : null}
            {field.field_kind === "checkbox" ? (
              <input
                type="checkbox"
                aria-label={field.label}
                checked={answer === true}
                disabled={submitted}
                onChange={(e) => setAnswer(field.id, e.target.checked)}
              />
            ) : null}
          </label>
        );
      })}
      <div className="approval-actions">
        <button type="button" disabled={busy || submitted} onClick={() => void onSubmit()}>
          {submitted ? "Terkirim" : busy ? "Mengirim…" : widget.submit_label}
        </button>
      </div>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
    </article>
  );
}

function formatCellValue(value: unknown): string {
  if (typeof value === "number") return value.toLocaleString("id-ID");
  return String(value);
}

function ComparisonWidgetView({
  widget,
}: {
  widget: Extract<ChatWidget, { kind: "comparison" }>;
}) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(true);

  const cheapestByColumn = new Map<string, string>();
  for (const column of widget.columns) {
    if (!column.numeric) continue;
    let best: string | null = null;
    let bestValue = Number.POSITIVE_INFINITY;
    for (const row of widget.rows) {
      const value = row.values[column.id];
      if (typeof value === "number" && value < bestValue) {
        bestValue = value;
        best = row.id;
      }
    }
    if (best !== null) cheapestByColumn.set(column.id, best);
  }

  const rows = [...widget.rows];
  if (sortColumn !== null) {
    rows.sort((a, b) => {
      const av = a.values[sortColumn];
      const bv = b.values[sortColumn];
      if (typeof av === "number" && typeof bv === "number") {
        return sortAsc ? av - bv : bv - av;
      }
      return sortAsc
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }

  function onSort(columnId: string) {
    if (sortColumn === columnId) {
      setSortAsc((prev) => !prev);
    } else {
      setSortColumn(columnId);
      setSortAsc(true);
    }
  }

  return (
    <article
      className="card chat-card chat-widget-comparison"
      data-testid="chat-widget-comparison"
    >
      <strong>{widget.title}</strong>
      <table>
        <thead>
          <tr>
            <th />
            {widget.columns.map((column) => (
              <th key={column.id}>
                {column.numeric ? (
                  <button type="button" onClick={() => onSort(column.id)}>
                    {column.label}
                    {sortColumn === column.id ? (sortAsc ? " ↑" : " ↓") : ""}
                  </button>
                ) : (
                  column.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <th scope="row">{row.label}</th>
              {widget.columns.map((column) => (
                <td key={column.id}>
                  {formatCellValue(row.values[column.id])}
                  {cheapestByColumn.get(column.id) === row.id ? " ★ Termurah" : ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  );
}

function DiagramWidgetView({
  widget,
}: {
  widget: Extract<ChatWidget, { kind: "diagram" }>;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = widget.nodes.find((node) => node.id === selectedId) ?? null;
  const outgoing = new Map<string, string[]>();
  for (const edge of widget.edges) {
    outgoing.set(edge.from, [...(outgoing.get(edge.from) ?? []), edge.to]);
  }
  const labelById = new Map(widget.nodes.map((node) => [node.id, node.label]));

  return (
    <article className="card chat-card chat-widget-diagram" data-testid="chat-widget-diagram">
      <strong>{widget.title}</strong>
      <ol>
        {widget.nodes.map((node) => (
          <li key={node.id}>
            <button
              type="button"
              className="button-secondary"
              aria-pressed={selectedId === node.id}
              onClick={() => setSelectedId(node.id)}
            >
              {node.label}
            </button>
            {(outgoing.get(node.id) ?? []).length > 0 ? (
              <span className="muted">
                {" "}
                → {(outgoing.get(node.id) ?? []).map((id) => labelById.get(id)).join(", ")}
              </span>
            ) : null}
          </li>
        ))}
      </ol>
      {selected ? (
        <p data-testid="diagram-detail">
          <strong>{selected.label}:</strong> {selected.detail ?? "Tidak ada detail."}
        </p>
      ) : (
        <p className="muted">Ketuk satu langkah untuk melihat detailnya.</p>
      )}
    </article>
  );
}

function SimulationWidgetView({
  widget,
  onNewMessage,
}: {
  widget: Extract<ChatWidget, { kind: "simulation" }>;
  onNewMessage?: (message: ChatMessage) => void;
}) {
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const maxPrice = Math.max(
    1,
    ...widget.history.flatMap((day) => Object.values(day.prices)),
  );
  const barGroupWidth = CHART_WIDTH / Math.max(1, widget.history.length);
  const barWidth = Math.max(2, barGroupWidth / (widget.stores.length + 1));

  async function runAction(action: SimulateWidgetAction) {
    if (busyAction !== null) return;
    setBusyAction(action.type);
    setError(null);
    try {
      const message = await simulateWidget(widget, action);
      onNewMessage?.(message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Langkah simulasi gagal.");
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <article
      className="card chat-card chat-widget-simulation"
      data-testid="chat-widget-simulation"
    >
      <strong>{widget.title}</strong>
      <p className="muted">
        Hari simulasi {widget.day} (mulai hari {widget.start_day}) — harga simulasi, bukan harga
        pasar.
      </p>
      <table>
        <thead>
          <tr>
            <th>Toko</th>
            <th>Harga hari ini</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {widget.stores.map((store) => (
            <tr key={store}>
              <th scope="row">{store}</th>
              <td>{formatRupiah(widget.prices[store] ?? 0)}</td>
              <td>
                <button
                  type="button"
                  className="button-secondary"
                  disabled={busyAction !== null}
                  onClick={() => void runAction({ type: "buy_from_store", store })}
                >
                  Beli di {store}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <svg
        width={CHART_WIDTH}
        height={CHART_HEIGHT}
        role="img"
        aria-label={`Harga simulasi per hari: ${widget.history
          .map((d) => `hari ${d.day}`)
          .join(", ")}`}
      >
        {widget.history.map((day, dayIndex) =>
          widget.stores.map((store, storeIndex) => {
            const value = day.prices[store] ?? 0;
            const height = (value / maxPrice) * (CHART_HEIGHT - 24);
            return (
              <rect
                key={`${day.day}-${store}`}
                data-testid="simulation-bar"
                x={dayIndex * barGroupWidth + storeIndex * barWidth + 1}
                y={CHART_HEIGHT - 20 - height}
                width={barWidth - 1}
                height={height}
                fill="currentColor"
                opacity={0.45 + storeIndex * 0.2}
              >
                <title>
                  Hari {day.day} {store}: {formatRupiah(value)}
                </title>
              </rect>
            );
          }),
        )}
      </svg>
      <p>
        Tagihan {widget.bill_id}: {BILL_STATUS_LABELS[widget.bill.status] ?? widget.bill.status} —{" "}
        {widget.bill_paid
          ? "lunas"
          : `Rp${widget.bill.amount_due.toLocaleString("id-ID")} jatuh tempo hari ${widget.bill.due_day}`}
      </p>
      <div className="approval-actions">
        <button
          type="button"
          disabled={busyAction !== null || widget.day >= 30}
          onClick={() => void runAction({ type: "advance_day" })}
        >
          {busyAction === "advance_day" ? "Lanjut…" : "Hari berikutnya"}
        </button>
        <button
          type="button"
          className="button-secondary"
          disabled={busyAction !== null || widget.bill_paid}
          onClick={() => void runAction({ type: "pay_bill" })}
        >
          {widget.bill_paid ? "Tagihan lunas" : "Bayar tagihan"}
        </button>
      </div>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
    </article>
  );
}

export function ChatWidget({ widget, onNewMessage }: ChatWidgetProps) {
  switch (widget.kind) {
    case "calculator":
      return <CalculatorWidgetView widget={widget} />;
    case "form":
      return <FormWidgetView widget={widget} onNewMessage={onNewMessage} />;
    case "comparison":
      return <ComparisonWidgetView widget={widget} />;
    case "diagram":
      return <DiagramWidgetView widget={widget} />;
    case "simulation":
      return <SimulationWidgetView widget={widget} onNewMessage={onNewMessage} />;
    default:
      return null;
  }
}
