# Own-model selection in PWA Settings (G33)

A user picks the model Puspa runs on, from the Settings screen.
Three sources, one codebase, no second frontend (decision locked
with Nero, see `docs/modes-decision.md`):

| Source | What happens | Where the key lives |
| --- | --- | --- |
| Hosted (default) | The router behaves exactly as it does today (cheapest model per role via OpenRouter). | Nowhere — no key, no URL, no override is stored. |
| BYOK | The router calls the chosen provider with the user's own key. | Only in the existing per-user vault (`puspa.accounts.vault`, entry `byok:<provider>`, envelope-encrypted). Never in the selection row, never in a log, never returned by any endpoint. |
| Local | The router calls a local OpenAI-compatible server (Ollama default `http://localhost:11434/v1`, model `llama3.1` when unnamed). | No key is needed; a fixed dummy key (`"ollama"`) is sent. |

## How the choice reaches the router

The selection is one row per user in Postgres (`model_settings`:
`user_id`, `source`, `base_url`, `model`, `provider`,
`updated_at` — no key column exists). The Settings screen reads
and writes it through `GET` / `POST /api/model-settings`; the
key has its own endpoints (`POST` /
`DELETE /api/model-settings/byok-key`).

`puspa/pwa/model_settings.py` resolves a stored selection to
router arguments:

- hosted → `{}` (no arguments: current behaviour, unchanged);
- byok → `base_url` (the provider's known endpoint, or the user's
  override), `api_key` read from the vault at call time, `model`
  when the user named one; a missing vault key raises `KeyError`
  instead of inventing a key;
- local → `base_url` (stored URL, Ollama default when unset),
  `api_key = "ollama"`, `model`, `provider = "ollama"`.

`complete_with_selection(prompt, user_id=..., ...)` passes those
arguments into `puspa.router.complete.complete_detailed`, so the
request really goes to the selected endpoint with the selected
key. `llm_complete_for_user(user_id=..., role=...)` builds the
`complete(prompt)` callable for the retrieval seams; it resolves
the selection on every call, so a Settings change takes effect
without rebuilding the callable. Explicit caller kwargs win over
the resolved selection.

One small router addition supports this: `complete_messages` /
`complete_detailed` / `complete` accept an optional `provider`
argument, used only to label an unlisted model (a local Ollama
model is not an OpenRouter model, so its result must not be
labelled `openrouter`). It is ignored for catalogued models.

## Validation

- `validate_base_url` accepts only http(s) URLs with a host, and
  refuses userinfo (`user:password@host`), so credentials can
  never hide in the stored URL. The Settings screen runs the same
  check before sending anything.
- `save_selection` takes no key at all and refuses any payload
  carrying a key field (`api_key`, `apiKey`, `key`, `secret`,
  `token`, `password`) with `ValueError`. A key can only enter
  the system through `save_byok_key`, which writes to the vault.
- A BYOK selection needs a provider name; a local selection fills
  in the Ollama defaults when the user leaves the fields blank;
  saving `hosted` clears any stored overrides.

## Frontend

`apps/pwa/src/screens/SettingsScreen.tsx` gains a Model card:
three radio choices, provider / model / base-URL fields for BYOK,
base-URL / model fields for local, a password field that saves the
key to the vault (and a delete button when one is stored). The
screen fetches the stored selection on mount and never displays
key material — only `has_byok_key` (boolean). Pure helpers live in
`apps/pwa/src/state/modelSettings.ts`; the API calls in
`apps/pwa/src/api/client.ts` (`getModelSettings`,
`saveModelSettings`, `saveByokKey`, `deleteByokKey`); the wire
type is `ModelSettings` in `docs/openapi-pwa.yaml` (generated
into `src/api/generated-types.ts`).

## Honest limits

- A local model needs real hardware. For most users BYOK is the
  realistic path; local is for users with a GPU or patience for
  CPU. Puspa does not check whether the local server is running
  or whether the named model is pulled — the server reports that
  when the first completion fails, as a `CompletionError`.
- BYOK providers outside the known-endpoint map need their base
  URL typed in; the map covers openrouter, openai, deepseek,
  groq, together, mistral, xai, and gemini.
- The selection is per user, not per role or per task: one choice
  applies to every completion for that user. Per-role or
  per-module model choice is not built.
- No live provider call is proven in the suite: tests use an
  injected fake transport and a clearly fake key, so no test
  touches the network, the vault's real master key, or a live
  provider. The first live BYOK and live Ollama completions are
  still unverified by hand.
- The hosted default keeps the existing OpenRouter surrogate
  path (`demo.byok_provider.resolve_key`); G33 does not change
  how the hosted key is obtained.
