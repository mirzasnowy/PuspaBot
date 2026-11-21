# Rich cards in Chat (plan section 12.10)

Chat can show four kinds of rich card, all as declarative JSON
payloads attached to a chat message under its `card` key. Data
describes the card; the renderer decides how to show it (the
same split as the prompt-to-visual-mockup doc: the payload is data, not
code). The cards live inside Chat — no new screen, no new
table, no migration.

## The four kinds

| Kind | Payload | What the user sees |
| --- | --- | --- |
| `draft` | `title`, `body`, `channel` (`email` / `message`), optional `recipient`, `subject`, `draft_id`, `mailto` | The draft body, copyable with one tap; an email draft with a recipient opens Mail with the body filled in. |
| `options` | `question`, `options` (2..8, each `id` + `label` + optional `detail`) | The question with tappable options; tapping one sends its label as a chat message. A choice, not a form. |
| `chart` | `title`, `metric` (`turns` / `tokens` / `cost_idr`), `points` (`label` + non-negative `value`) | Plain SVG bars, one per point. No new dependency. |
| `map` | `title`, `center` (`lat`/`lon`), `zoom` (0..19), `markers`, `route` | Free OpenStreetMap tiles with markers pinned over them and the route as a polyline. |

## Where the data comes from

- **Draft** — the co-drafting draft store. `POST /api/chat/cards/draft/{draft_id}`
  builds the card from the stored draft (`Draft.to_dict`): an
  email draft becomes the email channel (with a `mailto:` link),
  a message or document draft becomes the message channel
  (copy only). The co-drafting draft has no subject field, so the card
  carries none — the mailto link opens Mail with the body
  filled in and the user writes the subject. The draft text is
  never edited here: the card shows the draft as stored.
- **Chart** — the onboarding and cost insights Insights report. `GET
  /api/chat/cards/insights?days=N&metric=M` reads
  `activity_per_day` (every day in the window, including zero
  days, so the chart has no gaps) and the report's
  `window_days` for the title. The metric defaults to cost,
  the number the Insights screen leads with.
- **Options and map** — agent-composed. `POST /api/chat/cards`
  takes `{"text": ..., "card": {...}}`, validates the card, and
  appends a `puspa` message carrying it. The agent's turn loop
  can use the same seam directly through
  `puspa.pwa.chat_cards.append_card_message`.

## Validation (server, `puspa/pwa/chat_cards.py`)

Every card is rebuilt through its own builder, so a card that
passes is exactly the shape the renderer expects — no extra
keys survive. The refusals, all `ValueError` (400 at the API):

- unknown `kind`;
- draft: empty body, channel other than `email` / `message`;
- options: fewer than 2 or more than 8 options, duplicate ids,
  blank labels;
- chart: unknown metric, empty points, more than 90 points,
  negative values (a chart cannot draw a negative bar, so
  negatives are refused instead of clamped silently);
- map: latitude outside -90..90, longitude outside -180..180,
  zoom outside 0..19, no marker and no route of 2+ points, a
  route of exactly one point (it draws nothing; use a marker).

A non-UUID draft id on the draft endpoint is a 404, not a
500: draft ids are UUIDs and the `drafts.id` column is
UUID-typed, so a non-UUID id can never match a stored draft.
(Adjacent gap, not rich cards in chat's scope: co-drafting's own `GET
/api/drafts/{bad-id}` still returns 500 for a non-UUID id.)

## Map tiles

The map card's tiles load client-side from free OpenStreetMap
tiles (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, no API
key). The tile grid maths (standard slippy-map formulas) live
in `puspa/pwa/chat_cards.py` (`map_tiles`, `project_point`)
and are mirrored in `apps/pwa/src/state/chatCards.ts`, so the
server and the client agree on which tiles to fetch and where
markers and the route sit. `x` wraps around the world; `y` is
clamped to the valid row range, so a bad row can never request
a tile the server does not have.

## Rendering

- **SPA** (`apps/pwa/src/components/ChatCard.tsx`): `ChatScreen`
  renders `<ChatCard>` under the message text whenever a
  message carries a card. Copy uses the Clipboard API with a
  hidden-textarea fallback and says so when both fail. The
  options card locks after one pick. The chart is SVG bars
  with a `<title>` per bar. The map is a 320x240 tile grid
  with markers and an SVG route polyline over it.
- **Lite** (`apps/pwa/templates/chat.html`): the same four
  kinds render as static blocks (draft body + Mail link,
  option labels, chart points as text, marker labels + route
  point count), so the cards are visible when the React build
  is missing. Copy needs JavaScript, so the lite template does
  not offer it — the draft body is shown in full instead.

## What is NOT built (deliberate scope)

- No interactive components in chat interactive components: no calculator, slider, or
  form inside a card. The options card is a choice (tap sends
  a message), not a form.
- No pan/zoom on the map: the card is a fixed 320x240 tile
  grid centred on the card's centre. Panning would be an
  interactive component, which is interactive components in chat.
- No tile proxying on the server: the client fetches OSM tiles
  directly. The server never proxies tile bytes.

## Honest limits

- **Live tile loading is not verified in a real browser.**
  This VM has no live browser to prove an OSM tile really
  renders. What is verified: the tile URL maths (unit-tested
  in Python and TypeScript against the slippy-map formulas),
  the rendered template (vitest asserts the tile `<img>` tags,
  their URLs, the marker, and the route polyline), and the
  HTTP layer (the card message reaches the chat thread). No
  map screenshot is claimed anywhere.
- The chart is bars only: one metric, no lines, no stacking.
- The map shows markers and a route, not live traffic or
  turn-by-turn directions.

## Contract

`docs/openapi-chat-cards.yaml` holds the full contract (3
endpoints + 9 schemas); `docs/openapi-pwa.yaml` keeps a
`chat-cards` tag, `$ref` stubs for the 3 paths and the
`ChatCard` schema, and the optional `card` property on
`ChatMessage`. The generated client types
(`apps/pwa/src/api/generated-types.ts`) are regenerated from
the main spec with `npm run generate`.
