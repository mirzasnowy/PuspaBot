# Saved designs — unbuilt features (NOT linked from any navigation)

This folder holds the designs of features that do **not** exist in
the product yet. Nothing here is reachable from the app: no route,
no tab, no row, no button links to it. That is deliberate — a
feature that does not work must not appear in the UI (Nero, 23 Nov).

## What is in here

- `saved-designs.html` — static mockups of the 10 hidden features
  (Patungan, Preloved, Try-On & Video, QRIS, Anti-Fraud, Notulen,
  Co-Drafting, Impor Memori, Panel Operator, PPOB tiles
  Internet-TV / BPJS / E-Wallet / Voucher Game) plus the removed
  Beranda tab design.

## Provenance — read this before trusting the file

The Space export (`puspabot-app.html`, live v11) did **not**
inline this file: `pwa-saved-designs` appears 0 times in the
export, so the original saved-designs file could not be copied.
This file is a **reconstruction** from the audit record of
23 Nov 2026 (the feature list, where each feature sat in the UI,
and the Space design language: tokens, cards, title-only rows,
chevrons). It is faithful to the record, but it is not a
byte-for-byte copy of the original file — where the record does
not pin a detail down, the file says so in place.

## When a feature is built

Delete its section from `saved-designs.html` and build the real
screen in `src/screens/space/` wired to a real API, like every
other ported screen. Do not ship the mockup as the feature.
