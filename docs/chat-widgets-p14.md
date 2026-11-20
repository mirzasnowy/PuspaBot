# Interactive widgets in Chat (P-14, plan section 12.10)

Chat can show five kinds of interactive widget, all as declarative
JSON payloads attached to a chat message under its `widget` key.
Data describes the widget, the renderer decides how to show it
(the same split as the B7 mockup doc and the P-12 rich cards).
The difference from a P-12 card is state: a widget responds to
the user's input instead of only being looked at. The widgets
live inside Chat — no new screen, no new table, no migration.

## The five kinds

| Kind | Payload | What the user sees |
| --- | --- | --- |
| `calculator` | `title`, `inputs` (1..10, each `id` + `label` + `input_kind` (`number` / `slider`) + `value` + `min` + `max` + `step`), `formula`, `result_label`, `value_format` (`number` / `rupiah` / `percent`) | Number fields and sliders; the result updates live as an input changes. |
| `form` | `title`, `fields` (1..20, each `id` + `label` + `field_kind` (`text` / `number` / `select` / `checkbox`) + `required` + `options` for selects), `submit_label` | Fields the user fills in; submitting validates the answers server-side and posts them as a chat message. |
| `comparison` | `title`, `columns` (2..6, each `id` + `label` + `numeric`), `rows` (1..20, each `id` + `label` + one `values` entry per column) | A table; numeric columns sort, and the cheapest row in each numeric column is highlighted. |
| `diagram` | `title`, `nodes` (1..40, each `id` + `label` + optional `detail`), `edges` (each `from` + `to`) | An acyclic flow; tapping a node shows its detail. Cycles are refused. |
| `simulation` | `title`, `product_id`, `stores`, `start_day`, `day`, `bill_id`, `bill_paid`, `prices`, `bill`, `history` | The deterministic world simulator as a widget: prices per store, the PLN bill, a price chart, a day stepper, pay-bill and buy actions. |

## The calculator formula

The formula is a small safe expression over the input ids:
`+ - * / % **`, parentheses, and the calls `min` / `max` /
`abs` / `round`. It is evaluated by an `ast` whitelist on the
server (`puspa/pwa/chat_widgets.py`) and mirrored by a small
recursive-descent parser in the PWA
(`apps/pwa/src/state/chatWidgets.ts`) — never `eval`, on either
side. The two evaluators are pinned to each other by shared
cases in `tests/test_chat_widgets.py` and
`apps/pwa/src/state/chatWidgets.test.ts`, including the
power-precedence rule (`-2 ** 2` is `-4`, matching Python).
The client evaluates live as the user types;
`POST /api/chat/widgets/compute` is the authoritative check
the bot uses when it generates the widget.

## The simulation widget

The widget exposes the deterministic world simulator
(`puspa/world/simulator.py`): one watched phone at three
stores (Tokopedia falls Rp2,000/day, Shopee rises Rp3,000/day,
Blibli never moves) and one PLN bill of Rp150,000 due on day
20. The widget is stateless: it carries its day and bill state,
and every step re-derives the world from that state, so a
hand-forged widget whose prices do not match the simulator is
refused instead of trusted. The actions are `advance_day`
(never past day 30), `pay_bill` (refused when already paid),
and `buy_from_store` (buys at the current simulated price;
the widget itself does not change). Each step appends a new
chat message carrying the rebuilt widget, the same append
pattern as the P-12 cards.

The prices are simulated, not live market data: the widget
title and the chart caption say so, and no claim is made that
a price matches a real store.

## Where the data comes from

- **Calculator, form, comparison, diagram** — agent-composed.
  `POST /api/chat/widgets` takes `{"text": ..., "widget":
  {...}}`, validates the widget, and appends a `puspa` message
  carrying it. The agent's turn loop can use the same seam
  directly through `puspa.pwa.chat_widgets.append_widget_message`.
- **Comparison from the simulator** — `GET
  /api/chat/widgets/price-compare` builds a comparison widget
  from the simulator's day-15 prices (one row per store), so
  the bot never hand-types prices that could disagree with
  the simulator.
- **Simulation** — `GET /api/chat/widgets/simulation` builds a
  fresh day-15 simulation widget from the simulator.

## Honest limits

- The widgets are verified by the Python tests plus
  vitest/jsdom rendering and interaction tests (typing into a
  calculator, moving a slider, filling and submitting a form,
  sorting a comparison, tapping a diagram node, stepping the
  simulation). This VM has no live browser, so a tap or a
  slider drag on a real phone screen is not verified here.
- The simulation widget shows only what the deterministic
  simulator actually computes. It is not live market data and
  it does not track a real bill: paying the bill in the widget
  changes the simulated world only.
- The calculator computes locally in the browser for the live
  result; the server compute endpoint is the authoritative
  check, not the live path. If the two ever disagreed, the
  server's answer is the one to trust.
