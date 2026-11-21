"""Puspa-native subscription-provider login (V2-28).

Before this module, logging in to a subscription provider (xAI
Grok, GitHub Copilot, Codex) meant running nanobot's login
(``nanobot provider login xai`` and friends): Puspa reused
nanobot's provider machinery and had no login of its own. This
module is Puspa's own login, built on the two pieces that already
existed:

- the OAuth refresh side in ``puspa/router/oauth.py``
  (``TokenRefresher`` / ``TokenSet``), reused as-is: this module
  never implements a second refresh, it only hands the refresher
  a token set loaded from the vault and saves back whatever the
  refresher renews;
- the per-user vault in ``puspa/accounts/vault.py``, where tokens
  live encrypted under ``byok:oauth:<provider>`` (the same
  ``byok:`` prefix the BYOK factory uses for API keys).

Two flows, one per provider shape (the same shape as
``claude setup-token`` / ``codex login``):

- Authorization code + PKCE (xAI, Codex, Antigravity, Gemini
  CLI, GitLab, iFlow): build an authorize URL, the user opens
  it and approves, the returned code is exchanged for tokens.
  The terminal prints the URL and asks for the pasted code; the
  PWA opens the URL in a tab and receives the code at its
  callback endpoint. The pending PKCE session (state +
  verifier) lives in memory only, for ten minutes.
- Device flow (GitHub Copilot, Kimi, Kiro, Grok CLI, Nous,
  Muse, MiniMax Code, KiloCode, Qoder): the provider returns
  a user code and a verification URL, the user approves in a
  browser, Puspa polls until the token arrives. The terminal
  prints the code and URL; the PWA shows them and polls its
  own endpoint. Kiro first registers an AWS SSO OIDC client;
  KiloCode and Qoder use their own device-code wire shapes,
  carried as data on the same flow.
- Polling flow (GLM ZCode CLI, CodeBuddy): Puspa mints a
  one-off poll token (GLM) or a state (CodeBuddy), the user
  signs in at the server-generated URL, Puspa polls until
  the token is ready.
- Pasted-token flows (Claude setup-token, Cursor import,
  Cline encoded token): the user pastes a token they already
  hold (``claude setup-token`` prints one starting
  ``sk-ant-oat01-``; Cursor keeps one in its local install;
  Cline hands one back base64-encoded from its callback).
  No browser round trip, no refresh grant: when the token
  dies, the user pastes a fresh one.

Local-only boundary (hard rule, from the V2-29 issue)
---------------------------------------------------
Plan-credential logins are LOCAL / SELF-HOST ONLY. On a
hosted multi-tenant deployment a user's subscription login
must stay theirs: the host must never route through, spend,
or even hold another user's plan credentials (Anthropic
bars developers from offering claude.ai login or routing
through users' plan credentials; the allowed shape is the
user signing in themselves). ``ProviderLoginService`` takes
a ``deployment`` (``"local"`` by default, or
``PUSPA_DEPLOYMENT=hosted``); on ``"hosted"`` every login
start/finish for a plan-credential provider is refused
with a clear error. Tokens live only in the per-user vault
in either case, and are never logged, printed, or shared
across users.

Tokens are never logged, never printed, and never leave the
vault except as a Bearer <redacted> the router sends. The only values
this module prints or logs are provider names, URLs, user codes
(which the user must type in by hand), and expiry times.

Honest limits
-------------
- Codex is now wired: its authorize and token endpoints and
  public client id were verified from the 9Router registry
  (decolua/9router, MIT), so Puspa login works for Codex.
- GitHub Copilot's device flow needs a GitHub OAuth client id,
  which lives in the ``PUSPA_GITHUB_OAUTH_CLIENT_ID`` env var
  (or a ``client_id`` passed to the config); without one the
  login refuses with a clear error.
- Antigravity and Gemini CLI use Google OAuth clients whose
  client ids are public (they ship inside the public CLI
  binaries) but whose client secrets are NOT in this code:
  they come from ``PUSPA_ANTIGRAVITY_OAUTH_CLIENT_SECRET``
  and ``PUSPA_GEMINI_OAUTH_CLIENT_SECRET``. Without the
  secret the login refuses with a clear error naming the
  env var. The same rule holds for iFlow
  (``PUSPA_IFLOW_OAUTH_CLIENT_SECRET``) and GitLab
  (``PUSPA_GITLAB_OAUTH_CLIENT_ID`` /
  ``PUSPA_GITLAB_OAUTH_CLIENT_SECRET``).
- xAI's authorize URL comes from its issuer
  (``https://auth.x.ai``) plus ``/oauth2/authorize``; the token
  endpoint and public client id are the ones read from nanobot's
  source (``nanobot/nanobot/providers/xai_oauth.py``). If xAI
  ever moves its authorize endpoint, set
  ``PUSPA_XAI_AUTHORIZE_URL``.
- Claude logs in with the setup-token flow only: the user
  runs ``claude setup-token`` themselves and pastes the
  ``sk-ant-oat01-`` token. Puspa never asks for, stores, or
  routes through a claude.ai browser login, and the pasted
  token has no refresh grant: when it stops working, the
  user pastes a fresh one. The PKCE endpoints 9Router
  records for Claude are noted in the Claude config for a
  future step, not wired, because the issue names the
  setup-token flow.
- Cursor logs in by importing the token from the user's own
  local Cursor install (paste it); Puspa never reads the
  Cursor database itself and there is no refresh grant.
- GLM stores the Z.AI OAuth token from its CLI polling flow;
  the follow-on exchange for a business JWT and a long-lived
  coding-plan API key (what 9Router does after the poll) is
  not built yet, so GLM routing through that token is a
  follow-up, not part of this login.
- Muse stores the Meta device-flow token; minting the Model
  API key (``LLM|...``) that the chat transport actually
  uses is a follow-up.
- Qoder tokens live about 30 days and its refresh endpoint
  refuses our flow, so expiry means re-login.
- No live provider is contacted by the tests in this repo: every
  flow is tested against a fake OAuth provider (a stub HTTP
  client), never real credentials or real network. A real login
  against a live provider is still an open follow-up.
- Providers NOT covered here, and why (see
  ``docs/subscription-providers-9router.md``): Trae and
  Windsurf (custom multi-step login with a local callback
  server and extra registration calls), Zed (RSA keypair
  native-app auth, not OAuth), Xiaomi MiMo (custom ECDH +
  AES-GCM encrypted callback, not OAuth), Kimchi (no
  authorize/token endpoints in the 9Router registry, flow
  unverifiable), ClinePass (a plain API key, not an OAuth
  login), and Qwen-family (no Qwen provider exists in the
  9Router registry, so its endpoints are unverified).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from .oauth import (
    DEFAULT_REFRESH_LEEWAY_SECONDS,
    OAuthProviderConfig,
    TokenRefresher,
    TokenRefreshError,
    TokenSet,
    TokenStore,
)

__all__ = [
    "AWS_REGION_PATTERN",
    "DEFAULT_PKCE_SESSION_TTL_SECONDS",
    "DeviceCode",
    "LoginSession",
    "PollingSession",
    "ProviderLoginConfig",
    "ProviderLoginError",
    "ProviderLoginService",
    "VaultTokenStore",
    "assert_valid_aws_region",
    "build_pkce_auth_url",
    "exchange_pkce_code",
    "finish_encoded_token",
    "finish_pasted_token",
    "known_provider_configs",
    "new_code_challenge",
    "new_code_verifier",
    "plan_credential_providers",
    "poll_device_code",
    "poll_polling_login",
    "start_device_flow",
    "start_polling_login",
    "token_vault_name",
]

logger = logging.getLogger(__name__)

#: Providers Puspa knows how to log in to: the three V2-28
#: providers plus the 9Router list (V2-29). Every name here is
#: a plan-credential provider: local/self-host only, see the
#: module docstring.
PROVIDERS = (
    "antigravity",
    "claude",
    "cline",
    "codebuddy_cn",
    "codebuddy_intl",
    "cursor",
    "gemini_cli",
    "github_copilot",
    "gitlab",
    "glm",
    "grok_cli",
    "iflow",
    "kilocode",
    "kimi",
    "kiro",
    "minimax_code",
    "minimax_code_global",
    "muse",
    "nous",
    "openai_codex",
    "qoder",
    "qoder_cn",
    "xai",
)

#: Vault entry name prefix for subscription tokens.
VAULT_NAME_PREFIX = "byok:oauth:"

#: How long a pending PKCE login session (state + verifier) lives
#: in memory before it is refused as stale.
DEFAULT_PKCE_SESSION_TTL_SECONDS = 600

#: AWS region allowlist, the same shape 9Router uses before it
#: interpolates a region into an upstream URL (its guard against
#: region injection into the SSO OIDC endpoints).
AWS_REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-\d{1,2}$")


def assert_valid_aws_region(region: str) -> str:
    """Return ``region`` when it is a well-formed AWS region id.

    Raises ValueError otherwise, before the value is ever put
    into a URL. Kiro's SSO OIDC endpoints are per-region
    (``https://oidc.<region>.amazonaws.com``), so an unchecked
    region would be a URL-injection hole.
    """
    if not isinstance(region, str) or not AWS_REGION_PATTERN.match(region):
        raise ValueError(f"invalid AWS region: {region!r}")
    return region


def plan_credential_providers() -> tuple[str, ...]:
    """Every known provider: all of them are plan-credential.

    A plan-credential provider is one whose login spends the
    user's own subscription plan. All of them are local-only:
    on a hosted multi-tenant deployment the login is refused
    (see ``ProviderLoginService``), so the host can never spend
    a user's plan.
    """
    return PROVIDERS


#: xAI public client id, read from nanobot's own source
#: (``nanobot/nanobot/providers/xai_oauth.py``). A public client
#: id is not a secret.
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_ISSUER = "https://auth.x.ai"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_AUTHORIZE_URL = "https://auth.x.ai/oauth2/authorize"
XAI_SCOPES = ("openid", "profile", "email", "offline_access")

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"

#: Claude setup-token prefix: ``claude setup-token`` prints a
#: token starting with this. The prefix is a format check, not
#: a secret, so it is safe to show in help text and errors.
CLAUDE_SETUP_TOKEN_PREFIX = "sk-ant-oat01-"

#: Codex (OpenAI) public client id and endpoints, verified from
#: the 9Router registry (decolua/9router, MIT:
#: ``open-sse/providers/registry/codex.js``). The extra authorize
#: params are the ones the Codex CLI sends.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_SCOPES = ("openid", "profile", "email", "offline_access")
CODEX_EXTRA_AUTH_PARAMS = {
    "id_token_add_organizations": "true",
    "codex_cli_simplified_flow": "true",
    "originator": "codex_cli_rs",
}

#: Antigravity / Gemini CLI Google OAuth endpoints (both are
#: Google OAuth with different public client ids). The client
#: ids are public (they ship inside the public CLI binaries);
#: the client secrets are NOT in this code and come from env.
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
ANTIGRAVITY_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
ANTIGRAVITY_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
)
GEMINI_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
GEMINI_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
)
GOOGLE_OFFLINE_PARAMS = {"access_type": "offline", "prompt": "consent"}

#: Kimi device flow (9Router registry ``kimi.js``).
KIMI_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
KIMI_DEVICE_CODE_URL = "https://auth.kimi.com/api/oauth/device_authorization"
KIMI_TOKEN_URL = "https://auth.kimi.com/api/oauth/token"
KIMI_AUTHORIZE_DEVICE_URL = "https://www.kimi.com/code/authorize_device"

#: Kiro AWS SSO OIDC (9Router registry ``kiro.js``). The
#: ``{region}`` placeholder is filled from ``PUSPA_KIRO_REGION``
#: (default ``us-east-1``) after the region allowlist check.
KIRO_START_URL = "https://view.awsapps.com/start"
KIRO_ISSUER_URL = "https://identitycenter.amazonaws.com/ssoins-722374e8c3c8e6c6"
KIRO_SCOPES = (
    "codewhisperer:completions",
    "codewhisperer:analysis",
    "codewhisperer:conversations",
)
KIRO_GRANT_TYPES = (
    "urn:ietf:params:oauth:grant-type:device_code",
    "refresh_token",
)

#: Grok CLI device flow (9Router registry ``grok-cli.js``):
#: the same public xAI client id as ``xai``, different flow.
GROK_CLI_DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
GROK_CLI_SCOPE = (
    "openid profile email offline_access grok-cli:access api:access "
    "conversations:read conversations:write"
)

#: GitLab Duo PKCE (9Router registry ``gitlab.js``): the client
#: id and secret belong to the user's own GitLab OAuth app and
#: come from env, never from code.
GITLAB_BASE_URL = "https://gitlab.com"
GITLAB_SCOPES = ("api", "read_user")

#: iFlow PKCE (9Router registry ``iflow.js``). The client id is
#: public; the client secret comes from env, never from code.
IFLOW_CLIENT_ID = "10009311001"
IFLOW_AUTHORIZE_URL = "https://iflow.cn/oauth"
IFLOW_TOKEN_URL = "https://iflow.cn/oauth/token"

#: Cline authorization code (9Router ``cline.js``): the callback
#: hands back a base64-encoded JSON blob carrying the tokens,
#: so the login is a paste of that blob; the token-exchange URL
#: is the fallback when the blob does not decode.
CLINE_AUTHORIZE_URL = "https://api.cline.bot/api/v1/auth/authorize"
CLINE_TOKEN_EXCHANGE_URL = "https://api.cline.bot/api/v1/auth/token"

#: Nous device flow (9Router constants ``oauth.js``).
NOUS_CLIENT_ID = "hermes-cli"
NOUS_DEVICE_CODE_URL = "https://portal.nousresearch.com/api/oauth/device/code"
NOUS_TOKEN_URL = "https://portal.nousresearch.com/api/oauth/token"
NOUS_SCOPES = ("inference:invoke", "inference:mint_agent_key")

#: Muse (Meta) device flow (9Router registry ``muse.js``). Meta
#: rejects refresh-token grants for this flow, so there is no
#: refresh: expiry means re-login (and the Model API key mint
#: is a follow-up, see the module docstring).
MUSE_CLIENT_ID = "1031625952748946"
MUSE_DEVICE_CODE_URL = "https://auth.meta.com/oidc/device/authorization/"
MUSE_TOKEN_URL = "https://auth.meta.com/oidc/device/token/"

#: MiniMax Code device flow (9Router registry
#: ``minimax-code.js`` / ``minimax-code-global.js``).
MINIMAX_CLIENT_ID = "mcode-public"
MINIMAX_SCOPES = ("agent.default",)
MINIMAX_CN_DEVICE_URL = "https://account.minimax.cn/oauth2/device/code"
MINIMAX_CN_TOKEN_URL = "https://account.minimax.cn/oauth2/token"
MINIMAX_GLOBAL_DEVICE_URL = "https://account.minimax.io/oauth2/device/code"
MINIMAX_GLOBAL_TOKEN_URL = "https://account.minimax.io/oauth2/token"

#: KiloCode custom device flow (9Router ``kilocode.js``):
#: initiate mints a code, poll is a GET on that code.
KILOCODE_INITIATE_URL = "https://api.kilo.ai/api/device-auth/codes"
KILOCODE_POLL_URL_BASE = "https://api.kilo.ai/api/device-auth/codes"

#: GLM ZCode CLI polling flow (9Router ``glm.js`` + registry
#: ``glm.js``): init mints a flow id, the browser opens the
#: server-generated URL, poll is a GET on the flow id.
GLM_CLI_INIT_URL = "https://zcode.z.ai/api/v1/oauth/cli/init"
GLM_CLI_POLL_URL = "https://zcode.z.ai/api/v1/oauth/cli/poll"

#: CodeBuddy browser polling flow (9Router
#: ``codebuddy-cn.js`` + registries ``codebuddy-cn.js`` /
#: ``codebuddy-intl.js``): POST the state URL, open the
#: returned URL, poll the token URL with the state.
CODEBUDDY_CN_STATE_URL = "https://copilot.tencent.com/v2/plugin/auth/state"
CODEBUDDY_CN_TOKEN_URL = "https://copilot.tencent.com/v2/plugin/auth/token"
CODEBUDDY_INTL_STATE_URL = "https://www.codebuddy.ai/v2/plugin/auth/state"
CODEBUDDY_INTL_TOKEN_URL = "https://www.codebuddy.ai/v2/plugin/auth/token"
CODEBUDDY_HEADERS = {
    "User-Agent": "CLI/2.63.2 CodeBuddy/2.63.2",
    "X-Requested-With": "XMLHttpRequest",
    "X-Domain": "copilot.tencent.com",
    "X-No-Authorization": "true",
    "X-No-User-Id": "true",
    "X-Product": "SaaS",
}

#: Qoder custom device flow (9Router ``qoder.js`` + service
#: ``services/qoder.js``): PKCE + nonce + machine id are made
#: locally, the user opens the login URL with them, and the
#: poll is a GET on the device-token URL with nonce + verifier.
#: Tokens live about 30 days; the refresh endpoint refuses
#: this flow, so expiry means re-login.
QODER_DEVICE_TOKEN_URL = "https://openapi.qoder.sh/api/v1/deviceToken/poll"
QODER_LOGIN_URL = "https://qoder.com/device/selectAccounts"
QODER_CN_DEVICE_TOKEN_URL = "https://openapi.qoder.com.cn/api/v1/deviceToken/poll"
QODER_CN_LOGIN_URL = "https://qoder.com.cn/device/selectAccounts"


def token_vault_name(provider: str) -> str:
    """Vault entry name for one provider's token set."""
    return f"{VAULT_NAME_PREFIX}{provider}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProviderLoginError(Exception):
    """A subscription login failed.

    ``provider`` names the provider, ``reauth_required`` is True
    when only a fresh interactive login can fix it. The message
    never contains a token value: only the provider, the reason,
    and the provider's error code.
    """

    def __init__(
        self,
        provider: str,
        reason: str,
        *,
        error_code: str | None = None,
        reauth_required: bool = False,
    ) -> None:
        self.provider = provider
        self.reason = reason
        self.error_code = error_code
        self.reauth_required = reauth_required
        message = f"provider login failed for {provider!r}: {reason}"
        if error_code:
            message += f" (error={error_code})"
        if reauth_required:
            message += " (interactive login required)"
        super().__init__(message)


@dataclass
class ProviderLoginConfig:
    """How to log in to one subscription provider.

    ``flow`` is ``"pkce"`` (authorization code + PKCE),
    ``"device"`` (device flow), ``"setup_token"`` (paste a
    token the user already holds, e.g. Claude), ``"import_token"``
    (paste a token imported from the user's own local install,
    e.g. Cursor), ``"polling"`` (server-minted poll token or
    state, e.g. GLM, CodeBuddy), or ``"encoded_token"`` (paste
    a base64-encoded token blob, e.g. Cline).
    ``authorize_url`` is used only by the PKCE flow,
    ``device_code_url`` only by the device flow, ``poll_init_url``
    only by the polling flow. ``token_url`` and ``client_id``
    come from the matching ``OAuthProviderConfig`` in the
    refresher; a None value means the login is not wired yet
    and starting it raises a clear error naming the gap.
    ``client_secret`` is only for confidential clients and must
    come from env or the vault, never from code; it is never
    logged. ``local_only`` is True for every plan-credential
    provider: on a hosted deployment the login is refused.
    """

    provider: str
    flow: str  # "pkce" | "device" | "setup_token" | "import_token" | "polling" | "encoded_token"
    authorize_url: str | None = None
    device_code_url: str | None = None
    token_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: tuple[str, ...] = ()
    notes: str = ""
    # PKCE extras: added to the authorize URL (Codex, Google
    # offline access, iFlow login method) and the name of the
    # redirect parameter (iFlow uses "redirect", not
    # "redirect_uri"). ``token_auth`` is "body" (the secret
    # travels in the POST body) or "basic" (HTTP Basic auth
    # header as well, iFlow).
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    redirect_param: str = "redirect_uri"
    token_auth: str = "body"
    # Device-flow wire shape, one per provider family. The
    # default is the standard OAuth2 device flow (form POST,
    # snake_case fields). Kiro registers an OIDC client first
    # and speaks JSON with camelCase fields; KiloCode mints a
    # code with an empty POST and polls it with a GET; Qoder
    # builds the PKCE pair + nonce locally and polls with a GET.
    extra_headers: dict[str, str] = field(default_factory=dict)
    device_request_extra: dict[str, str] = field(default_factory=dict)
    device_request_format: str = "form"  # "form" | "json" | "empty"
    device_response_format: str = "oauth"  # "oauth" | "oidc" | "kilocode"
    token_request_format: str = "form"  # "form" | "json" | "get"
    token_response_format: str = "oauth"  # "oauth" | "oidc" | "kilocode"
    client_registration: dict | None = None
    region: str | None = None
    device_json_start_url: str | None = None
    device_local_flow: bool = False
    device_login_url: str | None = None
    # Polling flow (GLM, CodeBuddy): init mints the poll key,
    # poll reads it back. ``poll_auth`` is "none" or
    # "bearer_local" (GLM: a locally generated token sent as
    # Bearer on init and on every poll). ``poll_url_style`` is
    # "path" (``{poll_url}/{flow_id}``, GLM) or "query_state"
    # (``{poll_url}?state={flow_id}``, CodeBuddy).
    poll_init_url: str | None = None
    poll_init_headers: dict[str, str] = field(default_factory=dict)
    poll_init_body: dict | None = None
    poll_init_query: dict[str, str] = field(default_factory=dict)
    poll_url: str | None = None
    poll_url_style: str = "path"
    poll_auth: str = "none"
    poll_kind: str = "glm"  # "glm" | "codebuddy"
    poll_provider_id: str = ""
    # Pasted-token flows: ``token_prefix`` is the format check
    # for setup tokens (Claude ``sk-ant-oat01-``); empty means
    # any non-empty token is accepted (Cursor import).
    token_prefix: str | None = None
    setup_token_help: str = ""
    # Boundary: every plan-credential provider is local-only.
    local_only: bool = True
    # False when the provider issues no refresh grant at all
    # (pasted tokens, GLM, Muse, KiloCode, CodeBuddy, Qoder):
    # refresh then fails fast with "log in again" instead of
    # posting a grant the provider would refuse.
    refresh_supported: bool = True

    def __repr__(self) -> str:  # never leak the client secret
        return (
            f"ProviderLoginConfig(provider={self.provider!r}, flow={self.flow!r}, "
            f"authorize_url={self.authorize_url!r}, "
            f"device_code_url={self.device_code_url!r}, "
            f"token_url={self.token_url!r}, client_id={self.client_id!r}, "
            f"client_secret=<redacted> "
            f"scope={self.scope!r})"
        )

    def to_oauth_config(self) -> OAuthProviderConfig:
        """The matching refresh config for ``puspa/router/oauth.py``."""
        return OAuthProviderConfig(
            provider=self.provider,
            token_url=self.token_url,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scope=self.scope,
            simulated=True,
            notes=self.notes,
            refresh_json=(self.token_request_format == "json"),
            refresh_grant=self.refresh_supported,
        )

    def resolve_url(self, url: str) -> str:
        """Fill the ``{region}`` placeholder in a Kiro URL.

        The region is checked against the AWS allowlist first,
        so a bad value can never reach a URL. URLs without the
        placeholder pass through untouched.
        """
        if "{region}" not in url:
            return url
        region = self.region or os.environ.get("PUSPA_KIRO_REGION") or "us-east-1"
        return url.replace("{region}", assert_valid_aws_region(region))


def known_provider_configs() -> dict[str, ProviderLoginConfig]:
    """Login configs for every known subscription provider.

    Endpoints and public client ids are the ones verified from
    the 9Router registry (decolua/9router, MIT:
    ``open-sse/providers/registry/*.js`` and
    ``src/lib/oauth/providers/*.js``), rewritten here as Puspa
    data, never copied as code. Client secrets are NEVER in
    this function: they come from env vars, and a missing one
    makes the login refuse with a clear error naming the var.
    """
    github_client_id = os.environ.get("PUSPA_GITHUB_OAUTH_CLIENT_ID") or None
    xai_authorize_url = os.environ.get("PUSPA_XAI_AUTHORIZE_URL") or XAI_AUTHORIZE_URL
    antigravity_secret = os.environ.get("PUSPA_ANTIGRAVITY_OAUTH_CLIENT_SECRET") or None
    gemini_secret = os.environ.get("PUSPA_GEMINI_OAUTH_CLIENT_SECRET") or None
    iflow_secret = os.environ.get("PUSPA_IFLOW_OAUTH_CLIENT_SECRET") or None
    gitlab_client_id = os.environ.get("PUSPA_GITLAB_OAUTH_CLIENT_ID") or None
    gitlab_secret = os.environ.get("PUSPA_GITLAB_OAUTH_CLIENT_SECRET") or None
    kiro_region = os.environ.get("PUSPA_KIRO_REGION") or "us-east-1"
    assert_valid_aws_region(kiro_region)
    kiro_registration = {
        "register_url": "https://oidc.{region}.amazonaws.com/client/register",
        "client_name": "kiro-oauth-client",
        "client_type": "public",
        "scopes": list(KIRO_SCOPES),
        "grant_types": list(KIRO_GRANT_TYPES),
        "issuer_url": KIRO_ISSUER_URL,
    }
    return {
        "xai": ProviderLoginConfig(
            provider="xai",
            flow="pkce",
            authorize_url=xai_authorize_url,
            token_url=XAI_TOKEN_URL,
            client_id=XAI_CLIENT_ID,
            scope=XAI_SCOPES,
            notes="public client id from nanobot source; no client secret",
        ),
        "github_copilot": ProviderLoginConfig(
            provider="github_copilot",
            flow="device",
            device_code_url=GITHUB_DEVICE_CODE_URL,
            token_url=GITHUB_TOKEN_URL,
            client_id=github_client_id,
            scope=(),
            notes=("client id from PUSPA_GITHUB_OAUTH_CLIENT_ID; device flow, no client secret"),
        ),
        "openai_codex": ProviderLoginConfig(
            provider="openai_codex",
            flow="pkce",
            authorize_url=CODEX_AUTHORIZE_URL,
            token_url=CODEX_TOKEN_URL,
            client_id=CODEX_CLIENT_ID,
            scope=CODEX_SCOPES,
            extra_auth_params=dict(CODEX_EXTRA_AUTH_PARAMS),
            notes=("public client id and endpoints from the 9Router registry; no client secret"),
        ),
        "claude": ProviderLoginConfig(
            provider="claude",
            flow="setup_token",
            token_prefix=CLAUDE_SETUP_TOKEN_PREFIX,
            refresh_supported=False,
            setup_token_help=(
                "Run `claude setup-token` in your own terminal and paste "
                "the token it prints (it starts with sk-ant-oat01-). Puspa "
                "never opens a claude.ai login for you."
            ),
            notes=(
                "setup-token flow only, per the V2-29 issue; no refresh "
                "grant, paste a fresh token when it stops working. The "
                "PKCE endpoints 9Router records (client id "
                "9d1c250a-e61b-44d9-88ed-5944d1962f5e, authorize "
                "https://claude.ai/oauth/authorize, token "
                "https://api.anthropic.com/v1/oauth/token) are not wired."
            ),
        ),
        "antigravity": ProviderLoginConfig(
            provider="antigravity",
            flow="pkce",
            authorize_url=GOOGLE_AUTHORIZE_URL,
            token_url=GOOGLE_TOKEN_URL,
            client_id=ANTIGRAVITY_CLIENT_ID,
            client_secret=antigravity_secret,
            scope=ANTIGRAVITY_SCOPES,
            extra_auth_params=dict(GOOGLE_OFFLINE_PARAMS),
            notes=(
                "Google OAuth client id from the 9Router registry; client "
                "secret from PUSPA_ANTIGRAVITY_OAUTH_CLIENT_SECRET"
            ),
        ),
        "gemini_cli": ProviderLoginConfig(
            provider="gemini_cli",
            flow="pkce",
            authorize_url=GOOGLE_AUTHORIZE_URL,
            token_url=GOOGLE_TOKEN_URL,
            client_id=GEMINI_CLIENT_ID,
            client_secret=gemini_secret,
            scope=GEMINI_SCOPES,
            extra_auth_params=dict(GOOGLE_OFFLINE_PARAMS),
            notes=(
                "Google OAuth client id from the 9Router registry; client "
                "secret from PUSPA_GEMINI_OAUTH_CLIENT_SECRET"
            ),
        ),
        "cursor": ProviderLoginConfig(
            provider="cursor",
            flow="import_token",
            refresh_supported=False,
            setup_token_help=(
                "Copy the access token from your own local Cursor install "
                "and paste it here. Puspa never reads the Cursor database "
                "itself, and the token has no refresh grant."
            ),
            notes=(
                "import-token flow (9Router: Cursor tokens come from the "
                "local IDE install, no public OAuth endpoint); no refresh"
            ),
        ),
        "kiro": ProviderLoginConfig(
            provider="kiro",
            flow="device",
            device_code_url="https://oidc.{region}.amazonaws.com/device_authorization",
            token_url="https://oidc.{region}.amazonaws.com/token",
            device_request_format="json",
            device_response_format="oidc",
            token_request_format="json",
            token_response_format="oidc",
            client_registration=kiro_registration,
            region=kiro_region,
            device_json_start_url=KIRO_START_URL,
            notes=(
                "AWS SSO OIDC device flow (9Router registry kiro.js): the "
                "client is registered first, region from "
                "PUSPA_KIRO_REGION (default us-east-1)"
            ),
        ),
        "kimi": ProviderLoginConfig(
            provider="kimi",
            flow="device",
            device_code_url=KIMI_DEVICE_CODE_URL,
            token_url=KIMI_TOKEN_URL,
            client_id=KIMI_CLIENT_ID,
            notes=(
                "device flow (9Router registry kimi.js); the verification "
                "URL falls back to "
                "https://www.kimi.com/code/authorize_device"
            ),
        ),
        "glm": ProviderLoginConfig(
            provider="glm",
            flow="polling",
            poll_init_url=GLM_CLI_INIT_URL,
            poll_url=GLM_CLI_POLL_URL,
            poll_url_style="path",
            poll_auth="bearer_local",
            poll_kind="glm",
            poll_provider_id="zai",
            refresh_supported=False,
            notes=(
                "ZCode CLI polling flow (9Router glm.js): init mints a "
                "flow id, poll is a GET on it; no refresh grant, and the "
                "business-JWT / plan-key exchange is a follow-up"
            ),
        ),
        "grok_cli": ProviderLoginConfig(
            provider="grok_cli",
            flow="device",
            device_code_url=GROK_CLI_DEVICE_CODE_URL,
            token_url=XAI_TOKEN_URL,
            client_id=XAI_CLIENT_ID,
            scope=(GROK_CLI_SCOPE,),
            device_request_extra={"referrer": "grok-build"},
            notes="device flow (9Router registry grok-cli.js); same public client id as xai",
        ),
        "gitlab": ProviderLoginConfig(
            provider="gitlab",
            flow="pkce",
            authorize_url=f"{GITLAB_BASE_URL}/oauth/authorize",
            token_url=f"{GITLAB_BASE_URL}/oauth/token",
            client_id=gitlab_client_id,
            client_secret=gitlab_secret,
            scope=GITLAB_SCOPES,
            notes=(
                "GitLab Duo PKCE (9Router registry gitlab.js); client id "
                "and secret from PUSPA_GITLAB_OAUTH_CLIENT_ID / "
                "PUSPA_GITLAB_OAUTH_CLIENT_SECRET"
            ),
        ),
        "iflow": ProviderLoginConfig(
            provider="iflow",
            flow="pkce",
            authorize_url=IFLOW_AUTHORIZE_URL,
            token_url=IFLOW_TOKEN_URL,
            client_id=IFLOW_CLIENT_ID,
            client_secret=iflow_secret,
            redirect_param="redirect",
            token_auth="basic",
            extra_auth_params={"loginMethod": "phone", "type": "phone"},
            notes=(
                "authorization code flow (9Router iflow.js); client id "
                "from the registry, secret from "
                "PUSPA_IFLOW_OAUTH_CLIENT_SECRET, Basic auth on exchange"
            ),
        ),
        "cline": ProviderLoginConfig(
            provider="cline",
            flow="encoded_token",
            token_url=CLINE_TOKEN_EXCHANGE_URL,
            refresh_supported=False,
            setup_token_help=(
                "Sign in at app.cline.bot in your browser; the callback "
                "hands back a base64 token blob. Paste that blob here."
            ),
            notes=(
                "encoded-token flow (9Router cline.js): the pasted code "
                "is a base64 JSON blob carrying the tokens; the token "
                "exchange URL is the fallback when it does not decode"
            ),
        ),
        "nous": ProviderLoginConfig(
            provider="nous",
            flow="device",
            device_code_url=NOUS_DEVICE_CODE_URL,
            token_url=NOUS_TOKEN_URL,
            client_id=NOUS_CLIENT_ID,
            scope=NOUS_SCOPES,
            notes="device flow (9Router constants oauth.js); client id hermes-cli",
        ),
        "muse": ProviderLoginConfig(
            provider="muse",
            flow="device",
            device_code_url=MUSE_DEVICE_CODE_URL,
            token_url=MUSE_TOKEN_URL,
            client_id=MUSE_CLIENT_ID,
            extra_headers={"x-api-version": "1.0.0"},
            refresh_supported=False,
            notes=(
                "Meta device flow (9Router registry muse.js); Meta "
                "rejects refresh grants, and minting the Model API key "
                "is a follow-up"
            ),
        ),
        "minimax_code": ProviderLoginConfig(
            provider="minimax_code",
            flow="device",
            device_code_url=MINIMAX_CN_DEVICE_URL,
            token_url=MINIMAX_CN_TOKEN_URL,
            client_id=MINIMAX_CLIENT_ID,
            scope=MINIMAX_SCOPES,
            device_request_extra={"audience": "agent-backend"},
            notes="device flow, China site (9Router registry minimax-code.js)",
        ),
        "minimax_code_global": ProviderLoginConfig(
            provider="minimax_code_global",
            flow="device",
            device_code_url=MINIMAX_GLOBAL_DEVICE_URL,
            token_url=MINIMAX_GLOBAL_TOKEN_URL,
            client_id=MINIMAX_CLIENT_ID,
            scope=MINIMAX_SCOPES,
            device_request_extra={"audience": "agent-backend"},
            notes="device flow, global site (9Router registry minimax-code-global.js)",
        ),
        "kilocode": ProviderLoginConfig(
            provider="kilocode",
            flow="device",
            device_code_url=KILOCODE_INITIATE_URL,
            token_url=KILOCODE_POLL_URL_BASE,
            device_request_format="empty",
            device_response_format="kilocode",
            token_request_format="get",
            token_response_format="kilocode",
            refresh_supported=False,
            notes=(
                "custom device flow (9Router kilocode.js): initiate mints "
                "a code, poll is a GET on it; no refresh grant"
            ),
        ),
        "codebuddy_cn": ProviderLoginConfig(
            provider="codebuddy_cn",
            flow="polling",
            poll_init_url=CODEBUDDY_CN_STATE_URL,
            poll_init_headers=dict(CODEBUDDY_HEADERS),
            poll_init_body={},
            poll_init_query={"platform": "CLI"},
            poll_url=CODEBUDDY_CN_TOKEN_URL,
            poll_url_style="query_state",
            poll_kind="codebuddy",
            refresh_supported=False,
            notes=(
                "browser polling flow (9Router codebuddy-cn.js): POST the "
                "state URL, open the returned URL, poll the token URL "
                "with the state; no refresh grant"
            ),
        ),
        "codebuddy_intl": ProviderLoginConfig(
            provider="codebuddy_intl",
            flow="polling",
            poll_init_url=CODEBUDDY_INTL_STATE_URL,
            poll_init_headers=dict(CODEBUDDY_HEADERS),
            poll_init_body={},
            poll_init_query={"platform": "ide"},
            poll_url=CODEBUDDY_INTL_TOKEN_URL,
            poll_url_style="query_state",
            poll_kind="codebuddy",
            refresh_supported=False,
            notes=(
                "browser polling flow (9Router registry "
                "codebuddy-intl.js), same shape as CN on the .ai domain"
            ),
        ),
        "qoder": ProviderLoginConfig(
            provider="qoder",
            flow="device",
            device_code_url=QODER_DEVICE_TOKEN_URL,
            token_url=QODER_DEVICE_TOKEN_URL,
            device_local_flow=True,
            device_login_url=QODER_LOGIN_URL,
            token_request_format="get",
            token_response_format="qoder",
            refresh_supported=False,
            notes=(
                "custom device flow (9Router qoder.js + services/qoder.js): "
                "PKCE + nonce + machine id made locally, poll is a GET; "
                "tokens live about 30 days, refresh refused, re-login on expiry"
            ),
        ),
        "qoder_cn": ProviderLoginConfig(
            provider="qoder_cn",
            flow="device",
            device_code_url=QODER_CN_DEVICE_TOKEN_URL,
            token_url=QODER_CN_DEVICE_TOKEN_URL,
            device_local_flow=True,
            device_login_url=QODER_CN_LOGIN_URL,
            token_request_format="get",
            token_response_format="qoder",
            refresh_supported=False,
            notes=(
                "custom device flow, CN site (9Router registry "
                "qoder-cn.js), same shape as international Qoder"
            ),
        ),
    }


# ---------------------------------------------------------------------------
# PKCE helpers (same construction as puspa/auth/google_oauth.py)
# ---------------------------------------------------------------------------


def new_code_verifier() -> str:
    """A random PKCE code verifier (43-128 characters)."""
    return secrets.token_urlsafe(48)


def new_code_challenge(verifier: str) -> str:
    """The S256 code challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def new_state() -> str:
    """A random login state value (CSRF protection)."""
    return secrets.token_urlsafe(24)


def _http_post(http_client, url: str, data: dict | None = None, *, json_body=None, headers=None):
    """POST form data or JSON; ``http_client`` is the injectable transport.

    The transport has ``post(url, data=..., json=..., headers=...)``
    returning an object with ``status_code`` and ``json()``, the
    same seam ``puspa/router/oauth.py`` uses. With no client, a
    short-lived httpx client is used (only for a real login).
    ``data`` is form fields, ``json_body`` a JSON body; exactly
    one of them is sent. When neither a JSON body nor headers
    are given, only ``data`` is passed, so older stub transports
    with a ``post(url, data=...)`` signature keep working.
    """
    if http_client is not None:
        if json_body is None and headers is None:
            return http_client.post(url, data=data)
        return http_client.post(url, data=data, json=json_body, headers=headers)
    import httpx

    with httpx.Client(timeout=30) as client:
        if json_body is not None:
            return client.post(url, json=json_body, headers=headers)
        return client.post(url, data=data, headers=headers)


def _http_get(http_client, url: str, *, headers=None):
    """GET; ``http_client`` is the injectable transport.

    The transport has ``get(url, headers=...)`` returning an
    object with ``status_code`` and ``json()``. With no client,
    a short-lived httpx client is used (only for a real login).
    """
    if http_client is not None:
        return http_client.get(url, headers=headers)
    import httpx

    with httpx.Client(timeout=30) as client:
        return client.get(url, headers=headers)


def _token_set_from_payload(
    provider: str, payload: dict, now: datetime, *, extra: dict | None = None
) -> TokenSet:
    """Build a TokenSet from a token endpoint response.

    Raises ProviderLoginError when the response carries no access
    token. The payload itself is never logged. ``extra`` carries
    provider-side material the refresh needs later (Kiro's
    registered OIDC client id/secret/region); it is stored in
    the vault with the tokens, never logged, and never shown.
    """
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ProviderLoginError(provider, "token endpoint returned no access token")
    try:
        lifetime = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        lifetime = 3600
    if lifetime <= 0:
        lifetime = 3600
    return TokenSet(
        access_token=str(payload["access_token"]),
        refresh_token=(str(payload["refresh_token"]) if payload.get("refresh_token") else None),
        expires_at=now + timedelta(seconds=lifetime),
        token_type=str(payload.get("token_type") or "Bearer"),
        scope=str(payload.get("scope") or ""),
        obtained_at=now,
        extra=dict(extra or {}),
    )


def _token_set_no_expiry(provider: str, access_token: str, now: datetime) -> TokenSet:
    """A TokenSet for a token whose expiry Puspa does not know.

    Pasted tokens (Claude setup token, Cursor import, KiloCode
    approval token) carry no expiry: ``expires_at`` stays None,
    so the stored token is treated as usable until a call
    fails, and refresh is refused as "log in again".
    """
    return TokenSet(
        access_token=access_token,
        refresh_token=None,
        expires_at=None,
        token_type="Bearer",
        scope="",
        obtained_at=now,
    )


def _error_code(response) -> str | None:
    """The provider's error code from a failed response, if any."""
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("error")
    return str(code) if code else None


# ---------------------------------------------------------------------------
# Authorization code + PKCE flow
# ---------------------------------------------------------------------------


def build_pkce_auth_url(
    config: ProviderLoginConfig,
    redirect_uri: str,
    *,
    state: str | None = None,
    code_verifier: str | None = None,
) -> dict:
    """Build a PKCE authorize URL for ``config``.

    Returns ``{"url", "state", "code_verifier", "code_challenge"}``.
    The caller keeps ``state`` + ``code_verifier`` (in the pending
    session) and checks the returned state on callback.
    """
    if config.flow != "pkce":
        raise ProviderLoginError(config.provider, f"provider uses the {config.flow} flow, not PKCE")
    if not config.authorize_url:
        raise ProviderLoginError(
            config.provider,
            "no authorize URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    if not config.client_id:
        raise ProviderLoginError(
            config.provider,
            "no client id registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    state = state or new_state()
    code_verifier = code_verifier or new_code_verifier()
    if not 43 <= len(code_verifier) <= 128:
        raise ValueError("code_verifier must be 43-128 characters")
    params = {
        "client_id": config.client_id,
        config.redirect_param: redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scope),
        "state": state,
        "code_challenge": new_code_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    params.update(config.extra_auth_params)
    return {
        "url": config.authorize_url + "?" + urlencode(params),
        "state": state,
        "code_verifier": code_verifier,
        "code_challenge": params["code_challenge"],
    }


def exchange_pkce_code(
    config: ProviderLoginConfig,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    *,
    http_client=None,
    now: datetime | None = None,
) -> TokenSet:
    """Exchange an authorization code for a token set (PKCE)."""
    if config.flow != "pkce":
        raise ProviderLoginError(config.provider, f"provider uses the {config.flow} flow, not PKCE")
    if not config.token_url:
        raise ProviderLoginError(
            config.provider,
            "no token URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    if not code:
        raise ProviderLoginError(config.provider, "no authorization code given")
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.client_id or "",
        "code_verifier": code_verifier,
    }
    if config.client_secret:
        data["client_secret"] = config.client_secret
    headers = None
    if config.token_auth == "basic":
        # iFlow: the secret travels as an HTTP Basic header as
        # well as in the body (its own client sends both).
        credentials = base64.b64encode(
            f"{config.client_id or ''}:{config.client_secret or ''}".encode()
        ).decode()
        headers = {"Authorization": f"Basic {credentials}"}
    response = _http_post(http_client, config.token_url, data, headers=headers)
    if response.status_code != 200:
        code_out = _error_code(response)
        raise ProviderLoginError(
            config.provider,
            "token endpoint refused the authorization code",
            error_code=code_out,
            reauth_required=(code_out == "invalid_grant"),
        )
    return _token_set_from_payload(config.provider, response.json(), now or _utcnow())


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------


@dataclass
class DeviceCode:
    """One device-flow authorization, as returned by the provider.

    ``user_code`` and ``verification_uri`` are what the user types
    and opens; they are safe to print. ``device_code`` is a
    credential: it is kept in memory only and never printed or
    logged. ``extra`` carries provider-side material the poll
    needs (Kiro's registered OIDC client id/secret/region,
    Qoder's PKCE verifier/nonce/machine id); its VALUES are
    never printed or logged, only the key names.
    """

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int = 900
    interval: int = 5
    extra: dict = field(default_factory=dict)

    def __repr__(self) -> str:  # never leak the device code or extras
        return (
            f"DeviceCode(device_code=<redacted> "
            f"user_code={self.user_code!r}, "
            f"verification_uri={self.verification_uri!r}, "
            f"expires_in={self.expires_in!r}, interval={self.interval!r}, "
            f"extra_keys={sorted(self.extra)!r})"
        )

    def public_dict(self) -> dict:
        """What the terminal and the PWA may show the user."""
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "expires_in": self.expires_in,
            "interval": self.interval,
        }


def _register_oidc_client(config: ProviderLoginConfig, *, http_client=None) -> dict:
    """Register a Kiro-style AWS SSO OIDC client (9Router kiro.js).

    POSTs the registration body to the per-region register URL
    and returns ``{"client_id", "client_secret"}``. The values
    are credentials: they are kept in memory, stored in the
    vault with the tokens, and never logged.
    """
    registration = config.client_registration or {}
    raw_register_url = registration.get("register_url")
    if not raw_register_url:
        raise ProviderLoginError(
            config.provider, "no client-registration URL registered for this provider"
        )
    register_url = config.resolve_url(str(raw_register_url))
    body = {
        "clientName": registration.get("client_name"),
        "clientType": registration.get("client_type"),
        "scopes": registration.get("scopes") or list(config.scope),
        "grantTypes": registration.get("grant_types") or [],
        "issuerUrl": registration.get("issuer_url"),
    }
    response = _http_post(http_client, register_url, json_body=body)
    if response.status_code != 200:
        raise ProviderLoginError(
            config.provider,
            "client registration refused by the provider",
            error_code=_error_code(response),
        )
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("clientId"):
        raise ProviderLoginError(config.provider, "client registration returned no client id")
    return {
        "client_id": str(payload["clientId"]),
        "client_secret": str(payload.get("clientSecret") or ""),
    }


def _start_local_device_flow(config: ProviderLoginConfig) -> DeviceCode:
    """Qoder's device flow, built locally (9Router qoder.js).

    The PKCE pair, nonce, and machine id are made here; the
    user opens the login URL with the challenge + nonce, and
    the poll identifies the login by nonce + verifier. The
    user code shown is the first 8 characters of the nonce,
    the same shape 9Router shows in its modal.
    """
    verifier = new_code_verifier()
    challenge = new_code_challenge(verifier)
    nonce = secrets.token_hex(16)
    machine_id = secrets.token_hex(16)
    params = {
        "challenge": challenge,
        "challenge_method": "S256",
        "machine_id": machine_id,
        "nonce": nonce,
    }
    verification_uri = (config.device_login_url or "") + "?" + urlencode(params)
    return DeviceCode(
        device_code=nonce,
        user_code=nonce[:8].upper(),
        verification_uri=verification_uri,
        expires_in=300,
        interval=2,
        extra={
            "code_verifier": verifier,
            "nonce": nonce,
            "machine_id": machine_id,
        },
    )


def _device_code_from_payload(config: ProviderLoginConfig, payload: dict) -> DeviceCode:
    """Map a device-code response to a DeviceCode, per wire shape.

    ``"oauth"`` is the standard snake_case shape (GitHub, Kimi,
    Nous, MiniMax, Grok CLI, Muse). ``"oidc"`` is AWS SSO OIDC
    camelCase (Kiro). ``"kilocode"`` mints a code with a
    verification URL (KiloCode).
    """
    fmt = config.device_response_format
    if fmt == "oidc":
        return DeviceCode(
            device_code=str(payload.get("deviceCode") or ""),
            user_code=str(payload.get("userCode") or ""),
            verification_uri=str(
                payload.get("verificationUriComplete") or payload.get("verificationUri") or ""
            ),
            expires_in=int(payload.get("expiresIn") or 900),
            interval=int(payload.get("interval") or 5),
        )
    if fmt == "kilocode":
        return DeviceCode(
            device_code=str(payload.get("code") or ""),
            user_code=str(payload.get("code") or ""),
            verification_uri=str(payload.get("verificationUrl") or ""),
            expires_in=int(payload.get("expiresIn") or 300),
            interval=3,
        )
    verification_uri = str(
        payload.get("verification_uri_complete")
        or payload.get("verification_uri")
        or payload.get("verification_url")
        or ""
    )
    if not verification_uri and config.provider == "kimi":
        # Kimi's device-code answer may carry no URL; the
        # authorize page is the fallback (9Router kimi.js).
        user_code = str(payload.get("user_code") or "")
        verification_uri = f"{KIMI_AUTHORIZE_DEVICE_URL}?user_code={user_code}" if user_code else ""
    return DeviceCode(
        device_code=str(payload.get("device_code") or ""),
        user_code=str(payload.get("user_code") or ""),
        verification_uri=verification_uri,
        expires_in=int(payload.get("expires_in") or 900),
        interval=int(payload.get("interval") or 5),
    )


def start_device_flow(config: ProviderLoginConfig, *, http_client=None) -> DeviceCode:
    """Start a device flow: ask the provider for a user code.

    Kiro registers an OIDC client first; Qoder builds the flow
    locally (no server round trip); KiloCode posts an empty
    body. Every other provider posts the standard form body
    (client id + scope + provider extras such as Grok CLI's
    referrer or MiniMax's audience).
    """
    if config.flow != "device":
        raise ProviderLoginError(
            config.provider, f"provider uses the {config.flow} flow, not device flow"
        )
    if config.device_local_flow:
        if not config.device_login_url:
            raise ProviderLoginError(
                config.provider,
                "no device login URL registered for this provider; "
                "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
            )
        return _start_local_device_flow(config)
    if not config.device_code_url:
        raise ProviderLoginError(
            config.provider,
            "no device-code URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    if (
        not config.client_id
        and config.client_registration is None
        and config.device_request_format != "empty"
    ):
        raise ProviderLoginError(
            config.provider,
            "no client id registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    device_code_url = config.resolve_url(config.device_code_url)
    headers = dict(config.extra_headers) or None
    extra: dict = {}
    if config.client_registration is not None:
        registered = _register_oidc_client(config, http_client=http_client)
        extra["client_id"] = registered["client_id"]
        extra["client_secret"] = registered["client_secret"]
        extra["region"] = config.region or os.environ.get("PUSPA_KIRO_REGION") or "us-east-1"
        extra["start_url"] = config.device_json_start_url or ""
        if config.device_request_format == "json":
            body = {
                "clientId": registered["client_id"],
                "clientSecret": registered["client_secret"],
                "startUrl": extra["start_url"],
            }
            response = _http_post(http_client, device_code_url, json_body=body, headers=headers)
            return _finish_device_start(config, response, extra)
    if config.device_request_format == "empty":
        response = _http_post(http_client, device_code_url, json_body=None, headers=headers)
        return _finish_device_start(config, response, extra)
    data: dict[str, str] = {
        "client_id": config.client_id or "",
        "scope": " ".join(config.scope),
    }
    data.update(config.device_request_extra)
    response = _http_post(http_client, device_code_url, data, headers=headers)
    return _finish_device_start(config, response, extra)


def _finish_device_start(config: ProviderLoginConfig, response, extra: dict) -> DeviceCode:
    """Turn a device-code HTTP answer into a DeviceCode."""
    if response.status_code != 200:
        raise ProviderLoginError(
            config.provider,
            "device-code endpoint refused the request",
            error_code=_error_code(response),
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderLoginError(config.provider, "device-code endpoint returned no device code")
    has_code = bool(payload.get("device_code"))
    if config.device_response_format == "oidc":
        has_code = has_code or bool(payload.get("deviceCode"))
    if config.device_response_format == "kilocode":
        has_code = has_code or bool(payload.get("code"))
    if not has_code:
        raise ProviderLoginError(config.provider, "device-code endpoint returned no device code")
    device = _device_code_from_payload(config, payload)
    device.extra.update(extra)
    return device


def poll_device_code(
    config: ProviderLoginConfig,
    device_code: str,
    *,
    http_client=None,
    now: datetime | None = None,
    extra: dict | None = None,
) -> TokenSet | None:
    """Poll once for a device-flow token.

    Returns a TokenSet when the user has approved, None when the
    provider says ``authorization_pending`` (keep polling), and
    raises ProviderLoginError on ``expired_token`` or any other
    refusal. ``slow_down`` is reported as pending as well: the
    caller waits longer before the next poll. Some providers
    answer 200 with an error field while pending (Kimi) instead
    of a 400: that is pending too.
    """
    if not config.token_url:
        raise ProviderLoginError(
            config.provider,
            "no token URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    extra = extra or {}
    token_url = config.resolve_url(config.token_url)
    headers = dict(config.extra_headers) or None
    if config.token_response_format == "qoder":
        # Qoder: GET the device-token URL with nonce + verifier.
        query = urlencode(
            {
                "nonce": device_code,
                "verifier": str(extra.get("code_verifier") or ""),
                "challenge_method": "S256",
            }
        )
        response = _http_get(http_client, f"{token_url}?{query}", headers=headers)
        if response.status_code in (202, 404):
            return None
        if response.status_code != 200:
            raise ProviderLoginError(
                config.provider,
                "device flow failed",
                error_code=_error_code(response),
            )
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("token"):
            raise ProviderLoginError(config.provider, "device flow returned no token")
        try:
            lifetime = int(payload.get("expires_in") or 30 * 24 * 3600)
        except (TypeError, ValueError):
            lifetime = 30 * 24 * 3600
        return TokenSet(
            access_token=str(payload["token"]),
            refresh_token=(str(payload["refresh_token"]) if payload.get("refresh_token") else None),
            expires_at=(now or _utcnow()) + timedelta(seconds=lifetime),
            token_type="Bearer",
            scope="",
            obtained_at=now or _utcnow(),
        )
    if config.token_request_format == "get":
        # KiloCode: GET the poll URL with the code appended.
        response = _http_get(http_client, f"{token_url}/{device_code}", headers=headers)
        if response.status_code == 202:
            return None
        if response.status_code == 403:
            raise ProviderLoginError(
                config.provider, "device flow failed", error_code="access_denied"
            )
        if response.status_code == 410:
            raise ProviderLoginError(
                config.provider,
                "device flow failed",
                error_code="expired_token",
                reauth_required=True,
            )
        if response.status_code != 200:
            raise ProviderLoginError(
                config.provider,
                "device flow failed",
                error_code=_error_code(response),
            )
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "approved":
            return None
        if not payload.get("token"):
            raise ProviderLoginError(config.provider, "device flow returned no token")
        return _token_set_no_expiry(config.provider, str(payload["token"]), now or _utcnow())
    if config.token_request_format == "json":
        # Kiro OIDC: JSON body with the registered client creds.
        body = {
            "clientId": extra.get("client_id") or config.client_id or "",
            "clientSecret": extra.get("client_secret") or config.client_secret or "",
            "deviceCode": device_code,
            "grantType": "urn:ietf:params:oauth:grant-type:device_code",
        }
        response = _http_post(http_client, token_url, json_body=body, headers=headers)
        payload = _poll_payload_or_raise(config, response)
        if payload is None:
            return None
        mapped = {
            "access_token": payload.get("accessToken"),
            "refresh_token": payload.get("refreshToken"),
            "expires_in": payload.get("expiresIn"),
            "token_type": payload.get("tokenType") or "Bearer",
        }
        stored_extra = {
            "client_id": str(body["clientId"]),
            "client_secret": str(body["clientSecret"]),
            "region": str(extra.get("region") or config.region or "us-east-1"),
            "start_url": str(extra.get("start_url") or config.device_json_start_url or ""),
        }
        return _token_set_from_payload(
            config.provider, mapped, now or _utcnow(), extra=stored_extra
        )
    data: dict[str, str] = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": config.client_id or "",
    }
    if config.client_secret:
        data["client_secret"] = config.client_secret
    response = _http_post(http_client, token_url, data, headers=headers)
    payload = _poll_payload_or_raise(config, response)
    if payload is None:
        return None
    return _token_set_from_payload(config.provider, payload, now or _utcnow())


def _poll_payload_or_raise(config: ProviderLoginConfig, response) -> dict | None:
    """One device-poll answer: payload, None when pending.

    A 200 answer whose body carries an ``authorization_pending``
    or ``slow_down`` error (Kimi answers pending this way) is
    pending too. Any other refusal raises ProviderLoginError.
    """
    if response.status_code == 200:
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderLoginError(
                config.provider, "device flow returned a response that is not JSON"
            ) from exc
        if isinstance(payload, dict) and payload.get("error") in (
            "authorization_pending",
            "slow_down",
        ):
            return None
        return payload if isinstance(payload, dict) else {}
    error_code = _error_code(response)
    if error_code in ("authorization_pending", "slow_down"):
        return None
    raise ProviderLoginError(
        config.provider,
        "device flow failed",
        error_code=error_code,
        reauth_required=(error_code == "expired_token"),
    )


# ---------------------------------------------------------------------------
# Pasted-token flows (Claude setup token, Cursor import, Cline blob)
# ---------------------------------------------------------------------------


def finish_pasted_token(
    config: ProviderLoginConfig, token: str, *, now: datetime | None = None
) -> TokenSet:
    """Store a token the user pasted (Claude setup token / Cursor import).

    The token is checked for shape only: it must be non-empty,
    and when the provider names a prefix (Claude
    ``sk-ant-oat01-``) it must start with it, so a pasted API
    key or a typo is refused with a clear error instead of
    being stored as a login that can never work. No network is
    involved. The stored token has no known expiry and no
    refresh grant.
    """
    if config.flow not in ("setup_token", "import_token"):
        raise ProviderLoginError(
            config.provider, f"provider uses the {config.flow} flow, not a pasted token"
        )
    token = (token or "").strip()
    if not token:
        raise ProviderLoginError(config.provider, "no token pasted; login cancelled")
    if config.token_prefix and not token.startswith(config.token_prefix):
        raise ProviderLoginError(
            config.provider,
            f"that token does not look like a {config.provider} token: "
            f"it must start with {config.token_prefix!r}",
        )
    return _token_set_no_expiry(config.provider, token, now or _utcnow())


def finish_encoded_token(
    config: ProviderLoginConfig,
    code: str,
    *,
    http_client=None,
    redirect_uri: str = "",
    now: datetime | None = None,
) -> TokenSet:
    """Store a Cline-style base64 token blob pasted by the user.

    The blob is the base64 JSON Cline's callback hands back
    (``access_token`` / ``refresh_token`` / ``expiresAt``).
    When the blob does not decode, the fallback is the token
    exchange URL (9Router cline.js): POST the code as JSON
    and read the tokens from the answer.
    """
    if config.flow != "encoded_token":
        raise ProviderLoginError(
            config.provider, f"provider uses the {config.flow} flow, not an encoded token"
        )
    code = (code or "").strip()
    if not code:
        raise ProviderLoginError(config.provider, "no token pasted; login cancelled")
    moment = now or _utcnow()
    decoded = _decode_token_blob(code)
    if decoded is not None:
        access_token = decoded.get("access_token")
        if access_token:
            expires_at = None
            raw_expiry = decoded.get("expiresAt") or decoded.get("expires_at")
            if raw_expiry:
                try:
                    expires_at = datetime.fromisoformat(str(raw_expiry))
                except ValueError:
                    expires_at = None
            return TokenSet(
                access_token=str(access_token),
                refresh_token=(
                    str(decoded["refresh_token"]) if decoded.get("refresh_token") else None
                ),
                expires_at=expires_at,
                token_type="Bearer",
                scope="",
                obtained_at=moment,
            )
    if not config.token_url:
        raise ProviderLoginError(
            config.provider,
            "that pasted code is not a token blob and no token-exchange "
            "URL is registered; sign in again and paste the full code",
        )
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "client_type": "extension",
        "redirect_uri": redirect_uri,
    }
    response = _http_post(http_client, config.token_url, json_body=body)
    if response.status_code != 200:
        raise ProviderLoginError(
            config.provider,
            "token exchange refused the pasted code",
            error_code=_error_code(response),
        )
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    merged = dict(data) if isinstance(data, dict) else {}
    if isinstance(payload, dict):
        merged.update({k: v for k, v in payload.items() if k != "data"})
    if not merged.get("access_token"):
        raise ProviderLoginError(config.provider, "token exchange returned no access token")
    return _token_set_from_payload(config.provider, merged, moment)


def _decode_token_blob(code: str) -> dict | None:
    """Base64-decode a token blob into a dict, or None.

    The blob may carry trailing bytes after the JSON object;
    only the text up to the last ``}`` is parsed (the same
    tolerance 9Router's Cline flow uses).
    """
    padded = code + "=" * (-len(code) % 4)
    try:
        raw = base64.b64decode(padded, validate=False).decode("utf-8", errors="strict")
    except Exception:
        return None
    end = raw.rfind("}")
    if end == -1:
        return None
    try:
        parsed = json.loads(raw[: end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Polling flow (GLM ZCode CLI, CodeBuddy)
# ---------------------------------------------------------------------------


@dataclass
class PollingSession:
    """One pending polling login: flow id + poll token.

    ``flow_id`` identifies the login at the provider (GLM flow
    id, CodeBuddy state). ``poll_token`` is the one-off Bearer
    token GLM mints locally; CodeBuddy has none. Both are
    credentials: kept in memory only, never printed or logged.
    """

    provider: str
    flow_id: str
    authorize_url: str
    poll_token: str = ""
    interval: int = 3
    expires_in: int = 300
    created_at: datetime = field(default_factory=_utcnow)
    ttl_seconds: int = DEFAULT_PKCE_SESSION_TTL_SECONDS

    def __repr__(self) -> str:  # never leak the flow id or poll token
        return (
            f"PollingSession(provider={self.provider!r}, flow_id=<redacted> "
            f"authorize_url={self.authorize_url!r}, poll_token=<redacted> "
            f"interval={self.interval!r}, expires_in={self.expires_in!r})"
        )

    def is_expired(self, now: datetime) -> bool:
        return (now - self.created_at).total_seconds() > self.ttl_seconds

    def public_dict(self) -> dict:
        """What the terminal and the PWA may show the user."""
        return {
            "verification_uri": self.authorize_url,
            "expires_in": self.expires_in,
            "interval": self.interval,
        }


def start_polling_login(
    config: ProviderLoginConfig, *, http_client=None, now: datetime | None = None
) -> PollingSession:
    """Start a polling login: mint the poll key, return the URL.

    GLM posts ``{"provider": "zai"}`` with a locally generated
    Bearer token and gets a flow id + authorize URL. CodeBuddy
    posts an empty JSON body to the state URL and gets a state
    + auth URL. The authorize URL is what the user opens.
    """
    if config.flow != "polling":
        raise ProviderLoginError(
            config.provider, f"provider uses the {config.flow} flow, not polling"
        )
    if not config.poll_init_url:
        raise ProviderLoginError(
            config.provider,
            "no polling init URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    moment = now or _utcnow()
    poll_token = ""
    headers = dict(config.poll_init_headers) or None
    if config.poll_auth == "bearer_local":
        poll_token = secrets.token_hex(32)
        headers = dict(headers or {})
        headers["Authorization"] = f"Bearer {poll_token}"
    init_url = config.poll_init_url
    if config.poll_init_query:
        init_url = init_url + "?" + urlencode(config.poll_init_query)
    body = dict(config.poll_init_body) if config.poll_init_body is not None else None
    if config.poll_provider_id:
        body = dict(body or {})
        body["provider"] = config.poll_provider_id
    if body is None:
        response = _http_post(http_client, init_url, headers=headers)
    else:
        response = _http_post(http_client, init_url, json_body=body, headers=headers)
    if response.status_code != 200:
        raise ProviderLoginError(
            config.provider,
            "polling init refused by the provider",
            error_code=_error_code(response),
        )
    payload = response.json()
    if config.poll_kind == "codebuddy":
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("code") != 0 or not data:
            raise ProviderLoginError(config.provider, "polling init returned no login state")
        if not data.get("state") or not data.get("authUrl"):
            raise ProviderLoginError(config.provider, "polling init response missing state/authUrl")
        return PollingSession(
            provider=config.provider,
            flow_id=str(data["state"]),
            authorize_url=str(data["authUrl"]),
            poll_token="",
            interval=5,
            expires_in=300,
            created_at=moment,
        )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("code") != 0 or not data:
        raise ProviderLoginError(config.provider, "polling init returned no login state")
    if not data.get("flow_id") or not data.get("authorize_url"):
        raise ProviderLoginError(
            config.provider, "polling init response missing flow_id/authorize_url"
        )
    expires_in = 300
    raw_expiry = data.get("expires_at")
    if raw_expiry:
        try:
            expires_at = datetime.fromisoformat(str(raw_expiry))
            expires_in = max(1, int((expires_at - moment).total_seconds()))
        except ValueError:
            expires_in = 300
    return PollingSession(
        provider=config.provider,
        flow_id=str(data["flow_id"]),
        authorize_url=str(data["authorize_url"]),
        poll_token=poll_token,
        interval=int(data.get("poll_interval_sec") or 3),
        expires_in=expires_in,
        created_at=moment,
    )


def poll_polling_login(
    config: ProviderLoginConfig,
    session: PollingSession,
    *,
    http_client=None,
    now: datetime | None = None,
) -> TokenSet | None:
    """Poll once for a polling login.

    Returns a TokenSet when the user has signed in, None while
    the provider still says pending, and raises
    ProviderLoginError when the login is refused or expires.
    GLM answers ``{"data": {"status": "ready", ...}}`` with the
    OAuth token; CodeBuddy answers ``code 0`` with the token in
    ``data`` and ``code 11217`` while pending.
    """
    if not config.poll_url:
        raise ProviderLoginError(
            config.provider,
            "no polling URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    moment = now or _utcnow()
    if config.poll_url_style == "query_state":
        poll_url = f"{config.poll_url}?state={session.flow_id}"
    else:
        poll_url = f"{config.poll_url}/{session.flow_id}"
    headers = None
    if config.poll_auth == "bearer_local":
        headers = {"Authorization": f"Bearer {session.poll_token}"}
    elif config.poll_init_headers:
        headers = dict(config.poll_init_headers)
    response = _http_get(http_client, poll_url, headers=headers)
    if response.status_code != 200:
        raise ProviderLoginError(
            config.provider,
            "polling endpoint refused the request",
            error_code=_error_code(response),
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderLoginError(config.provider, "polling endpoint returned no data")
    if config.poll_kind == "codebuddy":
        if payload.get("code") == 11217:
            return None
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict) or not data.get("accessToken"):
            raise ProviderLoginError(config.provider, "polling endpoint returned no access token")
        mapped = {
            "access_token": data.get("accessToken"),
            "refresh_token": data.get("refreshToken"),
            "expires_in": data.get("expiresIn") or 86400,
            "token_type": data.get("tokenType") or "Bearer",
        }
        return _token_set_from_payload(config.provider, mapped, moment)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProviderLoginError(config.provider, "polling endpoint returned no data")
    if data.get("status") == "pending":
        return None
    if data.get("status") != "ready":
        raise ProviderLoginError(
            config.provider, f"polling login ended with status {data.get('status')!r}"
        )
    access_token = data.get("accessToken") or data.get("token")
    if not access_token:
        raise ProviderLoginError(config.provider, "polling endpoint returned no access token")
    return _token_set_from_payload(
        config.provider,
        {
            "access_token": access_token,
            "refresh_token": data.get("refreshToken"),
            "expires_in": data.get("expiresIn") or 3600,
        },
        moment,
    )


# ---------------------------------------------------------------------------
# Vault-backed token storage (tokens live ONLY here, encrypted)
# ---------------------------------------------------------------------------


def _serialize_token_set(tokens: TokenSet) -> str:
    return json.dumps(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
            "token_type": tokens.token_type,
            "scope": tokens.scope,
            "obtained_at": tokens.obtained_at.isoformat(),
            # Provider-side material the refresh needs (Kiro's
            # registered OIDC client id/secret/region). Stored
            # encrypted with the tokens, never logged or shown.
            "extra": dict(tokens.extra),
        }
    )


def _deserialize_token_set(raw: str) -> TokenSet:
    data = json.loads(raw)
    expires_at = data.get("expires_at")
    obtained_at = data.get("obtained_at")
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        token_type=data.get("token_type") or "Bearer",
        scope=data.get("scope") or "",
        obtained_at=datetime.fromisoformat(obtained_at) if obtained_at else _utcnow(),
        extra=dict(data.get("extra") or {}),
    )


class VaultTokenStore:
    """One provider's token set in the per-user vault, encrypted.

    The vault entry is ``byok:oauth:<provider>``. Nothing here
    logs or prints a token value; only the provider name and the
    expiry time are ever visible outside the vault.
    """

    def __init__(self, *, dsn=None) -> None:
        self._dsn = dsn

    def save(self, user_id: str, provider: str, tokens: TokenSet) -> None:
        from puspa.accounts.vault import put as vault_put

        vault_put(user_id, token_vault_name(provider), _serialize_token_set(tokens), dsn=self._dsn)

    def load(self, user_id: str, provider: str) -> TokenSet | None:
        from puspa.accounts.vault import get as vault_get

        try:
            raw = vault_get(user_id, token_vault_name(provider), dsn=self._dsn)
        except KeyError:
            return None
        return _deserialize_token_set(raw)

    def delete(self, user_id: str, provider: str) -> None:
        from puspa.accounts.vault import delete as vault_delete

        vault_delete(user_id, token_vault_name(provider), dsn=self._dsn)

    def status(self, user_id: str, provider: str, *, now: datetime | None = None) -> dict:
        """Login state for one provider, safe to show in a UI.

        Returns ``{"state", "expires_at"}`` where state is one of
        ``logged_in`` / ``expiring`` / ``expired`` /
        ``not_logged_in``. No token value is ever in the result.
        """
        moment = now or _utcnow()
        tokens = self.load(user_id, provider)
        if tokens is None:
            return {"state": "not_logged_in", "expires_at": None}
        expires_at = tokens.expires_at.isoformat() if tokens.expires_at else None
        if tokens.is_expired(moment):
            return {"state": "expired", "expires_at": expires_at}
        if tokens.needs_refresh(moment, DEFAULT_REFRESH_LEEWAY_SECONDS):
            return {"state": "expiring", "expires_at": expires_at}
        return {"state": "logged_in", "expires_at": expires_at}


# ---------------------------------------------------------------------------
# Pending PKCE sessions (in-memory, ten minutes)
# ---------------------------------------------------------------------------


@dataclass
class LoginSession:
    """One pending PKCE login: state + verifier + provider."""

    provider: str
    state: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime = field(default_factory=_utcnow)
    ttl_seconds: int = DEFAULT_PKCE_SESSION_TTL_SECONDS

    def is_expired(self, now: datetime) -> bool:
        return (now - self.created_at).total_seconds() > self.ttl_seconds


# ---------------------------------------------------------------------------
# The service: one login, two surfaces (terminal + PWA)
# ---------------------------------------------------------------------------


class ProviderLoginService:
    """Subscription login for one user base, backed by the vault.

    The terminal and the PWA call the same methods here, so there
    is one flow with two surfaces. Refresh reuses
    ``TokenRefresher`` from ``puspa/router/oauth.py``: the stored
    token set is loaded from the vault into the refresher's store,
    ``get_valid_token`` refreshes it before expiry when needed,
    and a renewed set is written back to the vault.
    ``deployment`` is ``"local"`` (default, or
    ``PUSPA_DEPLOYMENT=local``) or ``"hosted"``: on hosted,
    starting or finishing a login for any plan-credential
    provider is refused, because a hosted service must never
    hold or spend a user's subscription plan. Every known
    provider is plan-credential (see
    ``plan_credential_providers``).
    """

    def __init__(
        self,
        *,
        dsn=None,
        http_client=None,
        now_fn=None,
        refresher: TokenRefresher | None = None,
        configs: dict[str, ProviderLoginConfig] | None = None,
        deployment: str | None = None,
    ) -> None:
        self._dsn = dsn
        self._http_client = http_client
        self._now_fn = now_fn or _utcnow
        self._store = VaultTokenStore(dsn=dsn)
        self._refresher = (
            refresher if refresher is not None else TokenRefresher(now_fn=self._now_fn)
        )
        self._configs = dict(configs) if configs is not None else known_provider_configs()
        self._sessions: dict[str, LoginSession] = {}
        self._sessions_lock = threading.Lock()
        self._device_codes: dict[str, DeviceCode] = {}
        self._device_lock = threading.Lock()
        self._polling: dict[str, PollingSession] = {}
        self._polling_lock = threading.Lock()
        deployment = deployment or os.environ.get("PUSPA_DEPLOYMENT") or "local"
        if deployment not in ("local", "hosted"):
            raise ValueError(f"deployment must be 'local' or 'hosted', got {deployment!r}")
        self.deployment = deployment

    def now(self) -> datetime:
        return self._now_fn()

    # -- local-only boundary ------------------------------------------

    def _require_local(self, provider: str) -> None:
        """Refuse a plan-credential login on a hosted deployment.

        The host must never hold or spend a user's subscription
        plan: on hosted, the user signs in themselves, on their
        own machine, and Puspa hosted never sees the login.
        """
        config = self._configs.get(provider)
        if self.deployment == "hosted" and config is not None and config.local_only:
            raise ProviderLoginError(
                provider,
                "plan-credential logins are local-only: on a hosted "
                "deployment a subscription login stays yours, sign in "
                "on your own machine instead",
            )

    # -- registry -------------------------------------------------------

    def providers(self) -> list[str]:
        return sorted(self._configs)

    def config(self, provider: str) -> ProviderLoginConfig:
        config = self._configs.get(provider)
        if config is None:
            raise ProviderLoginError(provider, "unknown subscription provider")
        return config

    # -- status / logout -------------------------------------------------

    def status(self, user_id: str, provider: str) -> dict:
        """Login state for one provider (see VaultTokenStore.status)."""
        self.config(provider)  # unknown provider raises first
        result = self._store.status(user_id, provider, now=self.now())
        result["provider"] = provider
        result["flow"] = self._configs[provider].flow
        result["local_only"] = bool(self._configs[provider].local_only)
        result["login_ready"] = self._login_ready(self._configs[provider])
        return result

    @staticmethod
    def _login_ready(config: ProviderLoginConfig) -> bool:
        """True when a login can actually start for this provider.

        Pasted-token flows need nothing at all. Polling needs
        its init URL. Device flows need a device-code URL plus
        either a client id or a client registration (Kiro
        registers one on the spot). PKCE needs an authorize URL
        and a client id.
        """
        if config.flow in ("setup_token", "import_token", "encoded_token"):
            return True
        if config.flow == "polling":
            return bool(config.poll_init_url)
        if config.flow == "device":
            return bool(config.device_code_url) and (
                bool(config.client_id)
                or config.client_registration is not None
                or config.device_request_format == "empty"
                or config.device_local_flow
            )
        return bool(config.authorize_url) and bool(config.client_id)

    def list_status(self, user_id: str) -> list[dict]:
        return [self.status(user_id, provider) for provider in self.providers()]

    def logout(self, user_id: str, provider: str) -> None:
        """Drop one provider's tokens from the vault (idempotent)."""
        self.config(provider)
        self._store.delete(user_id, provider)
        logger.info("provider %s logged out for one user", provider)

    # -- PKCE flow (terminal paste-code + PWA callback share it) --------

    def start_pkce(self, provider: str, redirect_uri: str) -> dict:
        """Begin a PKCE login: returns the URL + state to track it.

        The returned dict carries the authorize URL, the state, and
        the code challenge; the verifier stays server-side in the
        pending session and is never returned.
        """
        self._require_local(provider)
        config = self.config(provider)
        built = build_pkce_auth_url(config, redirect_uri)
        session = LoginSession(
            provider=provider,
            state=built["state"],
            code_verifier=built["code_verifier"],
            redirect_uri=redirect_uri,
            created_at=self.now(),
        )
        with self._sessions_lock:
            self._sessions[built["state"]] = session
        logger.info("pkce login started for provider %s", provider)
        return {
            "url": built["url"],
            "state": built["state"],
            "code_challenge": built["code_challenge"],
        }

    def finish_pkce(self, user_id: str, state: str, code: str, *, http_client=None) -> dict:
        """Finish a PKCE login: check state, exchange code, save.

        Returns the provider status dict. Raises
        ProviderLoginError when the state is unknown, stale, or
        for another provider, or when the exchange fails.
        """
        with self._sessions_lock:
            session = self._sessions.pop(state, None)
        if session is None:
            raise ProviderLoginError(
                "unknown", "unknown or expired login session; start the login again"
            )
        if session.is_expired(self.now()):
            raise ProviderLoginError(
                session.provider, "login session expired; start the login again"
            )
        self._require_local(session.provider)
        config = self.config(session.provider)
        tokens = exchange_pkce_code(
            config,
            code,
            session.code_verifier,
            session.redirect_uri,
            http_client=http_client if http_client is not None else self._http_client,
            now=self.now(),
        )
        self._store.save(user_id, session.provider, tokens)
        logger.info("pkce login completed for provider %s", session.provider)
        return self.status(user_id, session.provider)

    # -- device flow ------------------------------------------------------

    def start_device(self, provider: str) -> dict:
        """Begin a device login: returns what the user must open/type.

        The device code itself stays server-side, keyed by a
        one-time poll key that the caller passes back to
        :meth:`poll_device`. The user code and verification URL
        are safe to show.
        """
        self._require_local(provider)
        config = self.config(provider)
        device = start_device_flow(config, http_client=self._http_client)
        poll_key = secrets.token_urlsafe(24)
        with self._device_lock:
            self._device_codes[poll_key] = device
        result = device.public_dict()
        result["poll_key"] = poll_key
        logger.info("device login started for provider %s", provider)
        return result

    def poll_device(self, user_id: str, provider: str, poll_key: str, *, http_client=None) -> dict:
        """Poll once for a device login started by :meth:`start_device`.

        Returns ``{"status": "pending"}`` while the user has not
        approved yet, ``{"status": "complete", ...}`` with the
        provider status once the token is stored, and raises
        ProviderLoginError when the flow expires or fails.
        """
        config = self.config(provider)
        with self._device_lock:
            device = self._device_codes.get(poll_key)
        if device is None:
            raise ProviderLoginError(provider, "unknown device login; start the login again")
        tokens = poll_device_code(
            config,
            device.device_code,
            http_client=http_client if http_client is not None else self._http_client,
            now=self.now(),
            extra=device.extra,
        )
        if tokens is None:
            return {"status": "pending"}
        with self._device_lock:
            self._device_codes.pop(poll_key, None)
        self._store.save(user_id, provider, tokens)
        logger.info("device login completed for provider %s", provider)
        result = self.status(user_id, provider)
        result["status"] = "complete"
        return result

    # -- pasted-token flows (Claude setup token, Cursor import) ----

    def start_pasted_token(self, provider: str) -> dict:
        """Begin a pasted-token login: returns what to paste.

        No browser round trip: the user already holds the token
        (``claude setup-token`` prints one; Cursor keeps one in
        its local install). The returned help text tells them
        where it comes from; the token itself is pasted back
        through :meth:`finish_pasted_token` and never appears
        in any response.
        """
        self._require_local(provider)
        config = self.config(provider)
        if config.flow not in ("setup_token", "import_token"):
            raise ProviderLoginError(
                provider, f"provider uses the {config.flow} flow, not a pasted token"
            )
        return {
            "flow": config.flow,
            "provider": provider,
            "help": config.setup_token_help,
            "token_prefix": config.token_prefix,
        }

    def finish_pasted_token(self, user_id: str, provider: str, token: str) -> dict:
        """Finish a pasted-token login: check the shape, store it.

        Returns the provider status dict. Raises
        ProviderLoginError when the token is empty or has the
        wrong shape for the provider.
        """
        self._require_local(provider)
        config = self.config(provider)
        tokens = finish_pasted_token(config, token, now=self.now())
        self._store.save(user_id, provider, tokens)
        logger.info("pasted-token login completed for provider %s", provider)
        return self.status(user_id, provider)

    # -- encoded-token flow (Cline) ------------------------------------

    def finish_encoded_token(
        self, user_id: str, provider: str, code: str, *, http_client=None
    ) -> dict:
        """Finish an encoded-token login: decode the blob, store it.

        Returns the provider status dict. Raises
        ProviderLoginError when the pasted code is neither a
        token blob nor accepted by the exchange fallback.
        """
        self._require_local(provider)
        config = self.config(provider)
        tokens = finish_encoded_token(
            config,
            code,
            http_client=http_client if http_client is not None else self._http_client,
            redirect_uri="http://127.0.0.1:8765/callback",
            now=self.now(),
        )
        self._store.save(user_id, provider, tokens)
        logger.info("encoded-token login completed for provider %s", provider)
        return self.status(user_id, provider)

    # -- polling flow (GLM, CodeBuddy) ----------------------------------

    def start_polling(self, provider: str) -> dict:
        """Begin a polling login: returns the URL + a poll key.

        The user opens the verification URL and signs in; the
        caller polls with :meth:`poll_polling` until the token
        arrives. The poll key is a one-time secret passed back
        to the poll call.
        """
        self._require_local(provider)
        config = self.config(provider)
        session = start_polling_login(config, http_client=self._http_client, now=self.now())
        poll_key = secrets.token_urlsafe(24)
        with self._polling_lock:
            self._polling[poll_key] = session
        result = session.public_dict()
        result["poll_key"] = poll_key
        logger.info("polling login started for provider %s", provider)
        return result

    def poll_polling(self, user_id: str, provider: str, poll_key: str, *, http_client=None) -> dict:
        """Poll once for a polling login started by :meth:`start_polling`.

        Returns ``{"status": "pending"}`` while the user has not
        signed in yet, ``{"status": "complete", ...}`` with the
        provider status once the token is stored, and raises
        ProviderLoginError when the login expires or fails.
        """
        config = self.config(provider)
        with self._polling_lock:
            session = self._polling.get(poll_key)
        if session is None:
            raise ProviderLoginError(provider, "unknown polling login; start the login again")
        if session.is_expired(self.now()):
            with self._polling_lock:
                self._polling.pop(poll_key, None)
            raise ProviderLoginError(provider, "login session expired; start the login again")
        tokens = poll_polling_login(
            config,
            session,
            http_client=http_client if http_client is not None else self._http_client,
            now=self.now(),
        )
        if tokens is None:
            return {"status": "pending"}
        with self._polling_lock:
            self._polling.pop(poll_key, None)
        self._store.save(user_id, provider, tokens)
        logger.info("polling login completed for provider %s", provider)
        result = self.status(user_id, provider)
        result["status"] = "complete"
        return result

    # -- token use: refresh before expiry, persisted back -----------------

    def get_valid_token(self, user_id: str, provider: str, *, http_client=None) -> str:
        """Return a usable access token, refreshing before expiry.

        Loads the vault token set into the shared refresher, lets
        the refresher renew it when it expires inside the leeway,
        and writes a renewed set back to the vault, so the next
        call (even in a new process) sees the fresh token. Raises
        ``TokenRefreshError`` (from ``puspa/router/oauth.py``)
        when no usable token exists or the refresh fails; the
        expired token is never returned.
        """
        config = self.config(provider)
        stored = self._store.load(user_id, provider)
        if stored is None:
            raise TokenRefreshError(
                provider, "no token stored; interactive login required", reauth_required=True
            )
        store = TokenStore()
        store.put(provider, stored)
        refresher = TokenRefresher(store=store, now_fn=self._now_fn)
        oauth_config = config.to_oauth_config()
        # Kiro's refresh is bound to the OIDC client registered
        # at login time: its id/secret ride in the vault entry
        # (see TokenSet.extra) and replace the config's here.
        if stored.extra.get("client_id"):
            oauth_config.client_id = str(stored.extra["client_id"])
        if stored.extra.get("client_secret"):
            oauth_config.client_secret = str(stored.extra["client_secret"])
        if oauth_config.token_url is not None:
            oauth_config.token_url = config.resolve_url(oauth_config.token_url)
        refresher.register(oauth_config)
        token = refresher.get_valid_token(
            provider, http_client if http_client is not None else self._http_client
        )
        fresh = store.get(provider)
        if fresh is not None and fresh.access_token != stored.access_token:
            # The refresher builds a fresh TokenSet without the
            # provider-side material: carry it forward, so the
            # next refresh still has the same OIDC client.
            fresh.extra = dict(stored.extra)
            self._store.save(user_id, provider, fresh)
        return token

    # -- helpers for the terminal -----------------------------------------

    def wait_for_device_login(
        self,
        user_id: str,
        provider: str,
        poll_key: str,
        *,
        timeout_seconds: int = 900,
        sleep_fn=time.sleep,
    ) -> dict:
        """Poll a device login until it completes or times out.

        ``sleep_fn`` is injectable so tests never really sleep.
        Raises ProviderLoginError on timeout.
        """
        config = self.config(provider)
        started = self.now()
        interval = 5
        with self._device_lock:
            device = self._device_codes.get(poll_key)
        if device is not None:
            interval = max(1, device.interval)
        while True:
            result = self.poll_device(user_id, provider, poll_key)
            if result.get("status") == "complete":
                return result
            elapsed = (self.now() - started).total_seconds()
            if elapsed >= timeout_seconds:
                raise ProviderLoginError(
                    provider,
                    "device login timed out; start the login again",
                    reauth_required=True,
                )
            sleep_fn(interval)
            _ = config

    def wait_for_polling_login(
        self,
        user_id: str,
        provider: str,
        poll_key: str,
        *,
        timeout_seconds: int = 900,
        sleep_fn=time.sleep,
    ) -> dict:
        """Poll a polling login until it completes or times out.

        ``sleep_fn`` is injectable so tests never really sleep.
        Raises ProviderLoginError on timeout.
        """
        started = self.now()
        interval = 5
        with self._polling_lock:
            session = self._polling.get(poll_key)
        if session is not None:
            interval = max(1, session.interval)
        while True:
            result = self.poll_polling(user_id, provider, poll_key)
            if result.get("status") == "complete":
                return result
            elapsed = (self.now() - started).total_seconds()
            if elapsed >= timeout_seconds:
                raise ProviderLoginError(
                    provider,
                    "polling login timed out; start the login again",
                    reauth_required=True,
                )
            sleep_fn(interval)
