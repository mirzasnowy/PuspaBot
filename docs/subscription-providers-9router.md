# Subscription providers: the 9Router list (V2-29)

Puspa logs in to subscription providers with its own login
(`puspa provider login <provider>`, `ProviderLoginService` in
`puspa/router/provider_login.py`, PWA panel in
`apps/pwa/src/components/ProviderLoginPanel.tsx`). V2-28 knew
3 providers (xai, github_copilot, openai_codex, the last one
not wired). V2-29 adds the 9Router list: 23 providers in one
registry, one flow per provider shape, all tested against a
fake OAuth provider, never against a live provider.

## Where the mechanisms come from

The OAuth mechanisms (authorize URL, token URL, scopes, PKCE
vs device vs setup-token shape, token-refresh shape) were
read from the 9Router clone (decolua/9router, MIT) at
`~/workspace/research/9router`: `src/lib/oauth/providers/*.js`
for the flow shapes and `open-sse/providers/registry/*.js`
plus `src/lib/oauth/constants/oauth.js` for the endpoint and
client-id data. Only the mechanisms were taken, rewritten as
Puspa code and data. No 9Router JavaScript was copied, nothing
was vendored, and no 9Router sub-project is used. Client
secrets are never in this repo: where a flow needs one it
comes from an env var, and the login refuses with a clear
error naming the var when it is missing.

## Providers

| Provider | Flow | Refresh | Notes |
| --- | --- | --- | --- |
| xai | PKCE | yes | Public client id from nanobot source |
| github_copilot | device | yes | Client id from `PUSPA_GITHUB_OAUTH_CLIENT_ID` |
| openai_codex | PKCE | yes | Wired in V2-29: client id `app_EMoamEEZ73f0CkXaXp7hrann`, authorize `auth.openai.com/oauth/authorize`, token `auth.openai.com/oauth/token`, extra params `codex_cli_simplified_flow`, `originator` |
| claude | setup token | no | User runs `claude setup-token` and pastes the `sk-ant-oat01-` token; wrong prefix refused |
| antigravity | PKCE | yes | Google OAuth, client id from 9Router, secret from `PUSPA_ANTIGRAVITY_OAUTH_CLIENT_SECRET`, offline access + consent |
| gemini_cli | PKCE | yes | Google OAuth, client id from 9Router, secret from `PUSPA_GEMINI_OAUTH_CLIENT_SECRET` |
| cursor | import token | no | User pastes the token from their own local Cursor install; Puspa never reads the Cursor database |
| kiro | device + OIDC client registration | yes (JSON) | Registers an AWS SSO OIDC client first (`oidc.<region>.amazonaws.com`), region from `PUSPA_KIRO_REGION` (default `us-east-1`, allowlist-checked); the registered client id/secret ride in the vault entry so refresh uses the same client |
| kimi | device | yes | Client id from 9Router; also accepts a 200 answer carrying `authorization_pending` |
| glm | polling | no | ZCode CLI init + poll; the business-JWT / coding-plan API key exchange after the poll is a follow-up |
| grok_cli | device | yes | Same public xAI client id as `xai`, scope includes `grok-cli:access`, `referrer=grok-build` |
| gitlab | PKCE | yes | GitLab Duo; client id/secret from `PUSPA_GITLAB_OAUTH_CLIENT_ID` / `PUSPA_GITLAB_OAUTH_CLIENT_SECRET` |
| iflow | PKCE | yes | Client id from 9Router, secret from `PUSPA_IFLOW_OAUTH_CLIENT_SECRET`; authorize param is `redirect` (not `redirect_uri`); token exchange uses HTTP Basic auth as well as the body |
| cline | encoded token | no | Pasted code is a base64 JSON blob from the Cline callback; the token-exchange URL is the fallback when it does not decode |
| nous | device | yes | Client id `hermes-cli`, scope `inference:invoke inference:mint_agent_key` |
| muse | device | no | Meta device flow, `x-api-version: 1.0.0` header; Meta rejects refresh grants; minting the Model API key (`LLM|...`) is a follow-up |
| minimax_code | device | yes | China site, `audience=agent-backend` |
| minimax_code_global | device | yes | Global site, same shape on the `.io` domain |
| kilocode | device (custom) | no | Empty-POST initiate mints a code, poll is a GET on it (202 pending, 410 expired) |
| codebuddy_cn | polling | no | POST the state URL, open the returned URL, poll the token URL with the state (11217 pending) |
| codebuddy_intl | polling | no | Same shape as CN on the `.ai` domain |
| qoder | device (custom) | no | PKCE + nonce + machine id made locally, poll is a GET; tokens live about 30 days, refresh refused |
| qoder_cn | device (custom) | no | Same shape as Qoder on the `.com.cn` hosts |

## The local-only boundary (hard rule)

Every provider above is a plan-credential provider: its
login spends the user's own subscription plan. Plan-credential
logins are LOCAL / SELF-HOST ONLY. `ProviderLoginService`
takes a `deployment` (`"local"` by default, or
`PUSPA_DEPLOYMENT=hosted`); on `"hosted"` every login
start/finish for a plan-credential provider is refused with a
clear error, while status reads still work. The host must
never hold, route through, or spend another user's plan
credentials. Anthropic bars developers from offering
claude.ai login or routing through users' plan credentials;
the allowed shape is the user signing in themselves, which is
exactly what the Claude setup-token flow does: Puspa never
opens a claude.ai login, it only stores the token the user
obtained themselves. Tokens live only in the per-user vault
(`byok:oauth:<provider>`), encrypted, and are never logged,
printed, or shared across users.

## How to add a provider

1. Verify the mechanism from a public source (the 9Router
   registry, the provider's own CLI, or its docs). Never guess
   an endpoint: an unverifiable provider stays out, listed
   below.
2. Add a `ProviderLoginConfig` in `known_provider_configs()`
   (`puspa/router/provider_login.py`): pick the flow
   (`pkce`, `device`, `polling`, `setup_token`,
   `import_token`, `encoded_token`) and set the wire-shape
   flags it needs (`extra_auth_params`, `redirect_param`,
   `token_auth`, `device_request_format`,
   `device_response_format`, `token_request_format`,
   `token_response_format`, `client_registration`,
   `poll_*`). The same config feeds the refresh registry
   (`puspa/router/router.py: register_known_oauth_providers`)
   through `to_oauth_config()`, so no second registration is
   needed. Set `refresh_supported=False` when the provider
   issues no refresh grant.
3. The CLI (`puspa provider login/logout/status`) picks the
   provider up from the registry automatically. The PWA needs
   a label in `PROVIDER_LABELS`
   (`ProviderLoginPanel.tsx`) and, for a new flow shape, a
   branch in the panel and in `provider_login_api.py`.
4. Add a login test against the fake provider in
   `tests/test_providers_9router.py` (extend `Fake9Router`
   with the new wire shape) and a PWA test when the panel
   changes.

## Not covered, and why

- Trae: multi-step login (GetLoginGuidance, browser, then
  ExchangeToken with a refresh token from the redirect) plus a
  local callback server; not a standard OAuth2 flow Puspa's
  login can run yet.
- Windsurf: implicit flow (`response_type=token`) plus a
  RegisterUser call that needs a Firebase id token.
- Zed: RSA keypair native-app auth, not OAuth (generate a
  keypair, sign in at `zed.dev/native_app_signin`, decrypt the
  RSA-encrypted token from the local callback).
- Xiaomi MiMo: custom ECDH + AES-256-GCM encrypted callback,
  not OAuth.
- Kimchi: no authorize/token endpoints in the 9Router
  registry, so the flow is unverifiable.
- ClinePass: a plain API key from `app.cline.bot`, not an
  OAuth login (9Router notes the OAuth tokens are not
  accepted by its API consumer endpoint).
- Qwen-family: no Qwen provider exists in the 9Router
  registry, so its endpoints are unverified.

## Honest limits

- Every login here is tested against a mocked OAuth provider.
  No real login against a live provider has been run: that
  check is still open (subagents have no live browser and no
  real credentials). In particular, Antigravity, Gemini CLI,
  iFlow, and GitLab need their client secrets from env before
  a live login can even start, and GitHub Copilot needs its
  client id from env.
- GLM routing through the stored token is a follow-up: the
  business-JWT / coding-plan API key exchange is not built.
- Muse routing is a follow-up: minting the Model API key is
  not built.
