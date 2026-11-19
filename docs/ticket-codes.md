# Ticket codes — what the letters mean

Puspa's work items carry a letter code: `G`, `K`, `B`, `E`, `P`, `L`,
`D`, `F`, `Culture`, or `Other`. The code tells you where the item
came from, not how important it is and not the order it is built in.
A stranger opening this repo should be able to read a commit like
`feat: K13 anti-manipulation ...` or a doc full of codes and know
what the letters mean without asking anyone.

## The codes

- **G — Gap.** A gap found by comparing Puspa against OpenBot,
  DuskBot, and hermes-agent: something they have that Puspa did
  not. Numbered G1–G34 (G29–G34 were added later, as follow-ups
  found during the work).
- **K — Kosmos.** A mechanism of Kosmos, the brain design of
  Puspa: intent engine, bitemporal state, causal ledger,
  ignorance map, autonomy dial, and the rest. Numbered K1–K13.
- **B — Beta.** A ticket from the limited-beta improvement plan:
  the beta slice, the moats, the group features, and the
  second-brain graph view. Numbered B1–B18.
- **E — Engineering.** An engineering ticket from the §19
  register of the beta plan (CI, deploy, observability, and
  similar). Numbered E-01–E-21.
- **P — Product.** A product ticket from the §19 register of
  the beta plan (features and experience). Numbered P-01–P-08.
- **L — Legal.** A legal ticket from the §19 register of the
  beta plan (payment licensing, compliance, and similar).
  Numbered L-01–L-03.
- **D — Decision.** An owner decision from the §19 register of
  the beta plan. Only the owner can close these. Numbered
  D-01–D-07.
- **F — Follow-up.** A follow-up from the brain deepening phase:
  small jobs left over after the brain mechanisms landed
  (wiring a seam, swapping a model, refreshing a trust root).
  Numbered F1–F6.
- **Culture — Indonesian cultural capability package.** The
  capability module that gives Puspa verified Indonesian
  cultural knowledge and cultural commerce (ticket B15 plus
  the C-coded package items). Built last, after the other
  features mature.
- **Other — everything else.** Items that fit no letter: the
  CLA implementation, the 15 MVP gates and the 10 reference
  tasks, and the demo.

## Where the codes come from

- The G gaps: the repo comparison report, "Perbandingan Repo:
  Puspa vs OpenBot vs DuskBot vs hermes-agent" (18 Nov 2026).
  It lives outside this repo, in the workspace at
  `workspace/tmp/puspa-repo-comparison/report.md`.
- The K mechanisms: `kosmos-design.md` in this folder.
- The B tickets and the E/P/L/D register: `tickets-beta-plan.md`
  in this folder (built from the beta plan, §19).
- The F follow-ups: the brain deepening status notes
  (`status-brain-deepening.md`, `status-maturity.md`).
- The Culture package: plan §21 of the beta plan and ticket
  B15 in `tickets-beta-plan.md`.

## The board

The same codes are the Group column of the `Puspa — Backlog`
board in Notion, which tracks every item with its status,
test result, and commit:

https://app.notion.com/p/41ccb413f3b38171911cd1a24dfc851b
