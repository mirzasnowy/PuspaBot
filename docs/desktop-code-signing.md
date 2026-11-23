# Code signing for the PuspaBot desktop installer (SmartScreen)

Checked 23 Nov 2026. Nothing bought, no accounts created. All
prices below were verified against vendor pages or Microsoft Learn
on this date.

## The honest fact first

**No certificate you can buy removes SmartScreen on day one.**
Since 2024, even an Extended Validation (EV) certificate goes
through the same reputation process as a standard (OV) one: the
instant bypass EV used to give is gone. SmartScreen reputation
builds per publisher + per file hash over weeks and hundreds of
clean installs. What a certificate *does* change:

- The prompt changes from "Unknown publisher" to a verified
  publisher name (yours, or SignPath Foundation's — see below).
- Reputation accrues under that publisher identity, so it carries
  across releases instead of starting over per file.
- The installer looks legitimate instead of anonymous.

The only zero-warning route is MSIX through the Microsoft Store —
a different distribution path, not this installer. So: buy a
certificate for trust and reputation, not for an instant fix. The
very next installer will still trigger SmartScreen; it gets
quieter as downloads accumulate.

## Options

### 1. SignPath Foundation — FREE (recommended first)

- Free for open-source projects; PuspaBot is Apache-2.0, so it qualifies.
- Requirements: the build must run in GitHub Actions (it does), MFA for all team members, and a published "Code signing policy" page carrying the line: *"Free code signing provided by SignPath.io, certificate by SignPath Foundation"*.
- Every release needs manual approval by a named approver.
- Publisher name shown: **"SignPath Foundation"**, not PuspaBot.
- Risk: acceptance is discretionary, and a days-old project with little public footprint may be declined; applications were reported paused in 2026 due to backlog.
- Official action: `signpath/github-action-submit-signing-request@v3`.

### 2. Azure Artifact Signing — $9.99/month (~$120/year)

- Basic tier: 5,000 signatures/month, enough for any release cadence.
- **Almost certainly ineligible for Nero**: the Basic tier is for individuals in the USA and Canada only; he is an individual in Indonesia. Do not buy unless he registers an organisation in an eligible country.

### 3. Certum Open Source (cloud) — ~€49/year

- Cheapest real certificate in his own name ("Open Source Developer, <name>").
- Issuance 1–5 days. Non-commercial use only: it must be replaced if a paid tier appears.
- Catch: each signing session needs a 6-digit code from the SimplySign phone app (2-hour sessions), so releases cannot be fully unattended.
- Certum Standard OV (cloud): ~$116/year at the 3-year rate, same phone-code flow, commercial use allowed.

### 4. SSL.com OV — $129/year + eSigner cloud ~$180–240/year (~$309–369 first year)

- OV needs a registered organisation; as an individual he would buy IV ($129/year), which shows his personal name.
- Most expensive of the realistic options; no advantage over Certum for this project.

### Do not buy

- **EV anywhere**: no SmartScreen advantage since 2024, and it costs several hundred dollars a year.
- Any certificate promising "instant SmartScreen removal": that promise has not been true since 2024.

## Recommendation

Apply to SignPath first ($0, no geography restriction). In parallel, keep Certum Open Source (~€49/year) as the fallback if SignPath declines. Whichever is chosen, the next installer will still trigger SmartScreen — that is expected, not a failure.

## How the CI uses a certificate (already prepared)

`.github/workflows/release-desktop.yml` has an optional signing step. It runs only when the repo secret `WINDOWS_PFX_BASE64` exists (plus `WINDOWS_PFX_PASSWORD` for the PFX password). The step:

1. Decodes the base64 PFX to a temp file.
2. Signs `PuspaBot-Setup.exe` with `signtool` (Windows SDK on the runner) + a DigiCert timestamp server.
3. Deletes the temp PFX immediately after.

With no secret configured, the step is skipped and the release ships unsigned. To set it up later: export the certificate as a `.pfx`, base64-encode it, store it as the `WINDOWS_PFX_BASE64` secret, store its password as `WINDOWS_PFX_PASSWORD`, and tag the next release. No workflow change is needed.

## Practical notes

- CA/B Forum caps a single code-signing certificate at ~458–459 days, so expect a yearly reissue whichever vendor is chosen.
- Re-signing the same file does not help: SmartScreen reputation is per file hash, so every new build starts cold; the publisher name is what carries over.
- The installer itself is built and self-tested in CI (`desktop.yml`); signing is a separate, later step in the release workflow only.
