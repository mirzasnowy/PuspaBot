# Mockup editor (prompt-to-visual-mockup)

Prompt to visual mockup with drag-and-drop, plan §12.2. The user
describes what they want in words, the agent makes a visual
mockup, the user drags elements to rearrange it, and the agent
updates the design. Built for UMKM and online sellers: shop page
mockup, promo poster, product catalogue.

This ticket also covers the later mockup canvas v1 ticket (mockup canvas v1,
same plan section): mockup canvas v1 was a delta-only verification on top of
this, not a rebuild. The delta it found and built: uploading an
image used to mutate the stored doc without an op, so the next
edit silently dropped the asset (now a numbered `add_asset` op);
the editor had no reorder or image-change controls (now Naik/Turun
buttons, Tambah gambar, and Ganti gambar); and the canvas hid
nested children, so a catalogue card showed only "card" (now
children render inside their parent, with the real picture for
image nodes).

## Design doc (single source of truth)

A mockup is a declarative JSON document: a tree of whitelisted
components, style tokens, and assets. The LLM generates and
patches the doc, never pixels and never free HTML. The
drag-and-drop editor edits the same doc. Both write numbered
ops, so undo, redo, and version history work on one log.

- `puspa/mockup/doc.py` — doc type, validation, ops, `DesignSession`
  (undo/redo over whole-doc snapshots; the op log is append-only
  and is never rewritten by an undo).
- `puspa/mockup/templates.py` — the five beta templates.
- `puspa/mockup/generate.py` — intent parsing, variants,
  self-critique, word-change patches, LLM patch seam.
- `puspa/mockup/render.py` — deterministic SVG / safe-HTML renderer.
- `puspa/mockup/export.py` — PNG (Pillow) and PDF (stdlib writer).
- `puspa/mockup/store.py` — Postgres persistence (`mockups` table,
  migration `0015_mockup.sql`).
- `puspa/pwa/mockup_api.py` — HTTP routes, mounted in `create_app`.
- `apps/pwa/src/screens/MockupScreen.tsx` + `apps/pwa/src/state/mockup.ts`
  — the PWA editor screen and its client-side doc mirror.

### Doc shape

```json
{
  "id": "doc-...",
  "title": "Promo Warung Kopi",
  "template": "promo_banner",
  "frame": {"width": 800, "height": 600},
  "theme": {"primary": "#1d4ed8", "background": "#ffffff",
            "text": "#111827", "accent": "#16a34a",
            "font": "Inter", "radius": 12},
  "assets": {"logo": {"kind": "image", "data_uri": "data:image/png;...", "name": "logo.png"}},
  "nodes": [{"id": "n1", "type": "text", "x": 40, "y": 30, "w": 400, "h": 60,
             "text": "Diskon 20%", "style": {"font_size": 32, "bold": true}, "children": []}],
  "version": 3,
  "ops": [{"seq": 1, "type": "move_node", "node_id": "n1",
           "payload": {"x": 100, "y": 40}, "note": "Moved headline up."}],
  "decisions": [{"seq": 1, "note": "Moved headline up."}]
}
```

### Ops

Numbered from 1, one per edit, in order: `add_node`,
`remove_node`, `move_node`, `set_text`, `set_style`, `set_image`,
`reorder`, `set_theme`, `set_title`, `add_asset` (mockup canvas v1: an image
upload is an op too, so the version bumps and the cached undo
session never goes stale on an asset the store already has). An
op may carry a `note`: the note is the design-decision record, so
the workspace accumulates why a design looks the way it does, not
just what it looks like. `version` equals the number of ops applied.

### Whitelisted components

`text`, `image`, `button`, `card`, `list`, `form`, `table`,
`chart`, plus theme tokens. Anything else is rejected at
validation. Fonts are OFL only: Inter, Poppins, Nunito, Lato,
Open Sans, Roboto.

### Templates (beta scope: 5)

`hajatan_invitation`, `promo_banner`, `warung_menu`,
`chat_catalog`, `lebaran_card`. Filling is deterministic slot
substitution; missing slots fall back to template defaults.
Deferred to a follow-up (same plan section lists it, beta scope
is five): `cash_recap`.

## Prompt flow

`POST /api/mockups/generate` with `{"prompt": ...}`:

1. Parse intent: keyword matching picks the template; quoted
   text becomes the headline, the first `Rp...` becomes the price,
   the phrase after "toko"/"warung" becomes the shop name.
   Nothing is invented: a slot with no match keeps the template
   default.
2. Fill the template, then build 2-3 variants (same layout,
   different theme).
3. Self-critique each variant (deterministic checks: clipped
   text, WCAG contrast below 3:1, colliding sibling elements)
   and auto-fix what is safe (shrink the font one step, darken
   the text to the theme colour). Remaining issues ride along in
   the response so the UI can show them.

## Editing

- Drag-and-drop: the editor canvas turns a drop into a
  `move_node` op (grab offset subtracted, clamped to the frame)
  on the same doc; the op is POSTed to
  `/api/mockups/{id}/ops` and the stored doc is the doc the op
  log describes.
- Text / colour / image edits are `set_text`, `set_style`,
  `set_image` ops; reorder is a `reorder` op. In the editor
  screen the selected element has Naik/Turun buttons (a `reorder`
  op to the neighbouring top-level slot) and, for an image element,
  a Ganti gambar file picker (uploads the picture as an
  `add_asset` op, then a `set_image` op). Tambah gambar uploads a
  picture and adds an image node showing it. Files over the 256 KiB
  asset cap are refused in the editor with a plain message before
  any upload.
- The canvas renders nested children inside their parent at their
  relative position, so a catalogue card shows its product name
  and price; an image node shows the real picture from the doc
  assets. Children can be selected and text/colour-edited, but
  only top-level elements are draggable.
- Word changes: `POST /api/mockups/{id}/patch` with
  `{"instruction": "besarkan harga, ganti warna jadi hijau"}`.
  The rule parser understands size words (besarkan/perbesar,
  kecilkan/perkecil), colour words (hijau, merah, biru, kuning,
  hitam, putih, ungu, oranye, pink, coklat), and `ganti teks X
  jadi Y`. Node words match node text, node id, or a role alias
  (`harga` -> the price node, `judul` -> headline, `toko` ->
  shop, `kontak` -> contact).
- Undo/redo: `POST /api/mockups/{id}/undo` and `/redo`
  (per-app in-memory session seeded from the stored doc; the
  durable record is the stored doc + op log, so a restart loses
  only the redo stack, never the design).

## LLM patch seam

`patch_from_llm(doc, instruction, llm_complete)` in
`puspa/mockup/generate.py` takes a `complete(prompt) -> str`
callable, e.g. `puspa.router.complete.default_llm_complete`.
The model must reply with a JSON list of ops; the reply is
parsed strictly and every op is validated against the doc
(whitelisted types only, no scripts) before it is returned. A
non-JSON reply or an invalid op raises instead of guessing.
When no callable is passed, the deterministic word parser is
used. No test calls a live model.

## Rendering and export

- `GET /api/mockups/{id}/render.svg` — deterministic SVG
  (fixed attribute order, escaped text, byte-identical for the
  same doc).
- `GET /api/mockups/{id}/render.html` — standalone page served
  with the strict CSP (`default-src 'none'`, sandboxed iframe
  model; see `puspa/mockup/render.py::csp_header`).
- `GET /api/mockups/{id}/export.png` — Pillow rasterisation.
- `GET /api/mockups/{id}/export.pdf` — stdlib-only one-page
  PDF writer (Helvetica).

Export PNG/PDF is rendered output, not the doc: re-exporting
after an edit re-renders from the current doc.

## Security

- No scripts inside design docs: every string is scanned for
  `<script`, `javascript:`, event handlers, `<iframe`, and
  friends, and rejected, not cleaned silently.
- The renderer only ever emits escaped text and whitelisted
  components; free HTML from the model is never rendered.
- Assets are image data URIs only (`data:image/png|jpeg|webp|gif`),
  capped at 256 KiB each and 32 per doc; remote URLs are rejected.
- Docs are capped at 200 nodes and 1 MiB.
- All routes require login; a user can only read or edit their
  own mockups (the store filters by `user_id`, and the table
  carries an owner RLS policy).

## Workspace accumulation

One row per mockup in the `mockups` table: owner, template, and
the whole doc as JSONB (doc + numbered op log + design
decisions). Assets and decisions therefore accumulate in the
user's workspace simply by editing. `GET /api/mockups` lists a
user's mockups, newest first. The published-work shape community gallery (publish and rate cultural works) will
build on later (doc + version + provenance) is already there:
the stored doc carries its id, template, version, op log, and
decision list. Publish/rate itself is NOT built here (parked
culture scope).

## Limits (honest)

- The vision self-critique is a seam: the current checks are
  deterministic geometry/contrast rules over the doc, not a
  vision model looking at rendered pixels. Wiring a vision model
  to check the rendered PNG is follow-up work.
- PNG export uses Pillow's default bitmap font (no embedded
  fonts); the PDF is a flat one-page render (Helvetica, no bleed,
  no CMYK). Enough for a chat-shared poster or catalogue, not a
  print-shop file.
- Undo/redo sessions are per-process memory; a restart loses
  only the redo stack, never the design.
- No module binding: connecting mockup components to live data
  and actions (the "mockup to live module" step in plan §12.2)
  is a later ticket, not this one.
- No Figma import (one-way Figma-to-doc import is a later step).
- The word-change parser covers the common Indonesian
  instructions listed above; anything else raises a clear error
  instead of guessing, and the LLM seam is the path for the rest.
