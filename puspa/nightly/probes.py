"""Live smoke probes for the nightly lane (E-04, plan section 4.4).

One probe per live dependency:

- ``openrouter_turn`` — one short turn through OpenRouter.
- ``openrouter_fallback`` — one forced fallback: the first tier
  is called with a deliberately invalid key, so the chain must
  move to the next tier (a 401 is retryable by design, see
  ``puspa.router.fallback``).
- ``ollama_turn`` — a health check against the local Ollama
  server first, then one end-to-end turn through its
  OpenAI-compatible endpoint. A dead server fails with a clear
  message naming what to start, never a raw ``CompletionError``.
- ``skill_discovery`` — one skills.sh search plus one install
  into a temporary directory (only when discovery is enabled).
- ``google_login`` — the Google login callback (exchange the
  authorization code, save the session, fetch userinfo), then a
  token refresh (tests token expiry, gate 15). When no fresh
  authorization code is available the probe runs the
  refresh-only path instead and records that honestly.
- ``telegram_roundtrip`` — one message out (with an inline
  approval keyboard) and one message in, via ``getUpdates``.
- ``telegram_approval_button`` — one approval via a button tap:
  a ``callback_query`` update is parsed and answered.

Every probe takes its HTTP transport / client / key resolver as
arguments, so tests inject fakes and no probe touches the
network unasked. A probe returns a :class:`ProbeResult` instead
of raising, except for :class:`SmokeError`, which
:func:`run_probes` converts into a failed result: a probe that
blows up still produces a record, never a missing one.

No probe ever records a secret: :meth:`ProbeResult.to_record`
emits a fixed whitelist of fields, and token values are never
put into ``detail`` or ``response_shape``.
"""

from __future__ import annotations

import hashlib
import os
import ssl
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from puspa.auth.google_oauth import (
    OAuthError,
    exchange_code,
    fetch_userinfo,
    refresh_tokens,
)
from puspa.channels.telegram import TelegramAdapter
from puspa.marketplace import MarketplaceRegistry, install_discovered
from puspa.marketplace.discovery import (
    InstallApproval,
    SkillsShClient,
    rank_results,
    search_skills,
)
from puspa.pwa.model_settings import DEFAULT_LOCAL_BASE_URL, LOCAL_DUMMY_KEY
from puspa.router.complete import complete_detailed, make_call_fn, resolve_role
from puspa.router.fallback import (
    AllTiersFailed as _AllTiersFailed,
)
from puspa.router.fallback import build_chain, call_with_fallback

if TYPE_CHECKING:
    from puspa.router.fallback import ChainEntry

__all__ = [
    "PROBE_NAMES",
    "ProbeResult",
    "SmokeError",
    "probe_google_login",
    "probe_ollama_turn",
    "probe_openrouter_fallback",
    "probe_openrouter_turn",
    "probe_skill_discovery",
    "probe_telegram_approval",
    "probe_telegram_roundtrip",
    "run_probes",
]

#: Probe names, in the order the nightly lane runs them.
PROBE_NAMES = (
    "openrouter_turn",
    "openrouter_fallback",
    "ollama_turn",
    "skill_discovery",
    "google_login",
    "telegram_roundtrip",
    "telegram_approval_button",
)

#: One short turn: a fixed prompt, 32 output tokens max. The lane
#: uses a low-capped key, and two short turns are all it spends.
SMOKE_PROMPT = "Reply with the single word: ok"
SMOKE_MAX_TOKENS = 32

#: Small CPU-friendly model the nightly lane pulls in CI.
OLLAMA_SMOKE_MODEL = "qwen2.5:0.5b"

#: The key the forced-fallback leg sends on purpose. It is not a
#: real credential — it is a deliberately invalid placeholder, and
#: the provider must reject it with a 401 so the chain moves.
BROKEN_KEY_PLACEHOLDER = "nightly-smoke-deliberately-invalid-key"

#: Inline approval keyboard on the Telegram smoke message.
SMOKE_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "Approve", "callback_data": "smoke:approve"},
            {"text": "Reject", "callback_data": "smoke:reject"},
        ]
    ]
}

#: Fields a time-series record may carry. Token values are never
#: among them, by construction.
_RECORD_FIELDS = (
    "run_id",
    "timestamp",
    "probe",
    "status",
    "latency_ms",
    "cost_rp",
    "provider",
    "model",
    "response_shape",
    "detail",
    "error",
)


class SmokeError(RuntimeError):
    """A probe failed in a way that needs a human-readable cause.

    Raised (instead of a raw provider error) when the failure is
    environmental — Ollama not running, no test account
    configured — so the nightly report says what to fix.
    """


@dataclass
class ProbeResult:
    """The outcome of one probe: status plus measured behaviour.

    ``status`` is ``"pass"``, ``"fail"``, or ``"skip"``.
    ``latency_ms`` is wall-clock time for the whole probe.
    ``cost_rp`` is the measured Rupiah cost, or None when the
    provider reports no usable rates (Ollama). ``response_shape``
    records what the provider actually returned (finish reason,
    token counts, top-level keys) so a silent provider change
    shows up in the time series. ``detail`` is a short human
    sentence; ``error`` carries the failure, if any. Neither
    ever holds a secret.
    """

    name: str
    status: str = "fail"
    latency_ms: int = 0
    cost_rp: int | None = None
    provider: str = ""
    model: str = ""
    response_shape: dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    error: str = ""

    def to_record(self, run_id: str, timestamp: str) -> dict[str, Any]:
        """The time-series record for this result.

        Only the whitelisted fields are emitted, so no secret
        can leak into the store through a result.
        """
        record = {
            "run_id": run_id,
            "timestamp": timestamp,
            "probe": self.name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "cost_rp": self.cost_rp,
            "provider": self.provider,
            "model": self.model,
            "response_shape": dict(self.response_shape),
            "detail": self.detail,
            "error": self.error,
        }
        assert set(record) == set(_RECORD_FIELDS)
        return record


def _timer() -> Callable[[], int]:
    """A millisecond stopwatch: call the returned function to read it."""
    started = time.monotonic()

    def elapsed_ms() -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    return elapsed_ms


def _http_client(*, transport=None, timeout: float) -> httpx.Client:
    """Build a plain HTTP client for one probe.

    Mirrors ``puspa.router.complete``: the egress proxy is taken
    explicitly from the environment and ``trust_env=False``, so
    httpx never parses a broken ``NO_PROXY``, and TLS verifies
    against the system trust store. A caller-supplied transport
    (tests) is honoured instead.
    """
    if transport is not None:
        return httpx.Client(transport=transport, timeout=timeout)
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or None
    )
    return httpx.Client(
        proxy=proxy,
        trust_env=False,
        verify=ssl.create_default_context(),
        timeout=timeout,
    )


def _completion_shape(result) -> dict[str, Any]:
    """The observed shape of a chat-completion result."""
    return {
        "finish_reason": result.finish_reason,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "tool_calls": len(result.tool_calls),
        "text_chars": len(result.text),
    }


def run_probes(
    probes: list[tuple[str, Callable[[], ProbeResult]]],
) -> list[ProbeResult]:
    """Run each probe, turning any exception into a failed result.

    A probe that raises still yields a record with its error
    attached, so a crash can never silently drop a probe from
    the nightly report. ``"skip"`` results pass through
    unchanged.
    """
    results: list[ProbeResult] = []
    for name, probe_fn in probes:
        elapsed = _timer()
        try:
            result = probe_fn()
        except Exception as exc:  # a crashing probe is a failed probe
            result = ProbeResult(name=name, status="fail", error=f"{type(exc).__name__}: {exc}")
        result.latency_ms = elapsed()
        if result.name != name:
            result.name = name
        results.append(result)
    return results


# -- OpenRouter ----------------------------------------------------------


def probe_openrouter_turn(
    *,
    prompt: str = SMOKE_PROMPT,
    transport=None,
    client: httpx.Client | None = None,
    key_resolver=None,
    api_key: str | None = None,
    max_tokens: int = SMOKE_MAX_TOKENS,
    timeout: float = 60.0,
) -> ProbeResult:
    """One short turn through OpenRouter (the router's cheapest reader)."""
    elapsed = _timer()
    result = complete_detailed(
        prompt,
        role="reader",
        api_key=api_key,
        transport=transport,
        client=client,
        key_resolver=key_resolver,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return ProbeResult(
        name="openrouter_turn",
        status="pass",
        latency_ms=elapsed(),
        cost_rp=result.cost_rp,
        provider=result.provider,
        model=result.model,
        response_shape=_completion_shape(result),
        detail=f"one turn answered ({result.output_tokens} output tokens)",
    )


def probe_openrouter_fallback(
    *,
    prompt: str = SMOKE_PROMPT,
    transport=None,
    client: httpx.Client | None = None,
    key_resolver=None,
    api_key: str | None = None,
    max_tokens: int = SMOKE_MAX_TOKENS,
    timeout: float = 60.0,
) -> ProbeResult:
    """One forced fallback: the first tier gets a broken key.

    The first tier is called with a deliberately invalid key, so
    the provider must reject it with a 401 and the chain must
    move to the next tier, which is called with the real key.
    The probe passes only when the chain actually moved.
    """
    elapsed = _timer()
    chain: list[ChainEntry] = build_chain(resolve_role("reader"))[:2]
    if len(chain) < 2:
        return ProbeResult(
            name="openrouter_fallback",
            status="fail",
            latency_ms=elapsed(),
            error="reader fallback chain has fewer than 2 tiers",
        )
    calls = {"count": 0}

    def counting_key(provider: str) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            return BROKEN_KEY_PLACEHOLDER
        if key_resolver is not None:
            return str(key_resolver(provider))
        if api_key:
            return api_key
        raise KeyError("no API key for provider 'openrouter': pass api_key or key_resolver")

    call_fn = make_call_fn(
        prompt,
        transport=transport,
        client=client,
        key_resolver=counting_key,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    try:
        outcome = call_with_fallback(chain, call_fn)
    except _AllTiersFailed as exc:
        return ProbeResult(
            name="openrouter_fallback",
            status="fail",
            latency_ms=elapsed(),
            provider=chain[0].provider,
            model=chain[0].model,
            error=f"chain did not move past the broken key: {exc}",
        )
    if outcome.tier_used != chain[1] or not outcome.hops:
        return ProbeResult(
            name="openrouter_fallback",
            status="fail",
            latency_ms=elapsed(),
            provider=outcome.tier_used.provider,
            model=outcome.tier_used.model,
            error=(
                "fallback did not move tiers: expected the chain to "
                f"leave {chain[0].tier} for {chain[1].tier}, "
                f"it used {outcome.tier_used.tier}"
            ),
        )
    return ProbeResult(
        name="openrouter_fallback",
        status="pass",
        latency_ms=elapsed(),
        cost_rp=outcome.total_cost_rp,
        provider=outcome.tier_used.provider,
        model=outcome.tier_used.model,
        response_shape={
            "tiers_tried": outcome.tiers_tried,
            "hops": len(outcome.hops),
            "input_tokens": outcome.total_input_tokens,
            "output_tokens": outcome.total_output_tokens,
        },
        detail=(
            f"tier {chain[0].tier} ({chain[0].provider}/{chain[0].model}) "
            f"rejected the broken key; chain moved to {chain[1].tier} "
            f"({chain[1].provider}/{chain[1].model}) and answered"
        ),
    )


# -- Ollama ---------------------------------------------------------------


def _ollama_origin(base_url: str) -> str:
    """The server root for a ``.../v1`` OpenAI-compatible base URL."""
    return base_url.rstrip("/").removesuffix("/v1")


def probe_ollama_turn(
    *,
    base_url: str = DEFAULT_LOCAL_BASE_URL,
    model: str = OLLAMA_SMOKE_MODEL,
    prompt: str = SMOKE_PROMPT,
    transport=None,
    client: httpx.Client | None = None,
    max_tokens: int = SMOKE_MAX_TOKENS,
    timeout: float = 120.0,
) -> ProbeResult:
    """Health check first, then one local end-to-end turn.

    The health check runs before the first completion call, so a
    dead server fails with a clear message naming what to start
    instead of a raw ``CompletionError`` from the call itself.
    """
    elapsed = _timer()
    origin = _ollama_origin(base_url)
    own_client = client is None
    http = client if client is not None else _http_client(transport=transport, timeout=10.0)
    try:
        try:
            health = http.get(f"{origin}/api/tags", timeout=10.0)
        except httpx.HTTPError as exc:
            raise SmokeError(
                f"Ollama is not reachable at {origin}: {type(exc).__name__}: {exc}. "
                f"Start the Ollama service with `ollama serve` and pull the model "
                f"with `ollama pull {model}`, then retry."
            ) from exc
        if health.status_code != 200:
            raise SmokeError(
                f"Ollama health check failed at {origin}/api/tags: "
                f"HTTP {health.status_code}. Start the Ollama service with "
                f"`ollama serve` and pull the model with `ollama pull {model}`, "
                "then retry."
            )
        result = complete_detailed(
            prompt,
            role="reader",
            model=model,
            provider="ollama",
            base_url=base_url,
            api_key=LOCAL_DUMMY_KEY,
            client=http,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    finally:
        if own_client:
            http.close()
    return ProbeResult(
        name="ollama_turn",
        status="pass",
        latency_ms=elapsed(),
        cost_rp=result.cost_rp,
        provider=result.provider,
        model=result.model,
        response_shape=_completion_shape(result),
        detail=f"health check ok, one local turn answered ({result.output_tokens} tokens)",
    )


# -- Skill discovery -------------------------------------------------------


def _fetch_skill_text(source: str, slug: str, *, http_client: httpx.Client | None) -> str | None:
    """Fetch a discovered skill's SKILL.md from its source repo.

    Tries the same conventional layouts the discovery module
    documents (``skills/<slug>/SKILL.md`` and ``<slug>/SKILL.md``
    on ``main``/``master``); ``None`` when none of them answers.
    The layouts live in ``puspa.marketplace.discovery`` — this
    helper mirrors them so the probe can hash the text before
    approving the install.
    """
    if "/" not in source:
        return None
    owner, repo = source.split("/", 1)
    paths = (f"skills/{slug}/SKILL.md", f"{slug}/SKILL.md")
    for branch in ("main", "master"):
        for path in paths:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
            try:
                if http_client is not None:
                    resp = http_client.get(url, timeout=10.0)
                else:
                    with httpx.Client(timeout=10.0) as fresh:
                        resp = fresh.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code == 200 and resp.text:
                return resp.text
    return None


def _fetch_license_text(source: str, *, http_client: httpx.Client | None) -> str | None:
    """Fetch a source repo's LICENSE file, if any (MIT/Apache/BSD)."""
    if "/" not in source:
        return None
    owner, repo = source.split("/", 1)
    for branch in ("main", "master"):
        for name in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{name}"
            try:
                if http_client is not None:
                    resp = http_client.get(url, timeout=10.0)
                else:
                    with httpx.Client(timeout=10.0) as fresh:
                        resp = fresh.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code == 200 and resp.text:
                return resp.text
    return None


def probe_skill_discovery(
    *,
    enabled: bool,
    query: str = "react performance",
    http_client: httpx.Client | None = None,
    skills_root: str | Path | None = None,
    skill_text: str | None = None,
    license_text: str | None = None,
    user_id: str = "nightly-smoke",
) -> ProbeResult:
    """One skills.sh search plus one install into a temp dir.

    Runs only when discovery is enabled; otherwise it skips
    (the lane does not fail for a feature that is off). The
    install goes through the normal G3 gates — hash-checked
    against the fetched SKILL.md, licence-checked — into the
    given ``skills_root`` (a fresh temp dir when none is
    given), never into the real skill store.
    """
    elapsed = _timer()
    if not enabled:
        return ProbeResult(
            name="skill_discovery",
            status="skip",
            latency_ms=elapsed(),
            detail="skill discovery is disabled (PUSPA_SKILL_DISCOVERY_ENABLED is not set)",
        )
    client = SkillsShClient(client=http_client)
    outcome = search_skills(client, query)
    if not outcome.skills:
        return ProbeResult(
            name="skill_discovery",
            status="fail",
            latency_ms=elapsed(),
            provider="skills.sh",
            error=f"skills.sh search for {query!r} returned no skills",
        )
    ranked = rank_results(outcome.skills)
    skill = ranked[0]
    text = (
        skill_text
        if skill_text is not None
        else _fetch_skill_text(skill.source, skill.slug, http_client=http_client)
    )
    if not text:
        return ProbeResult(
            name="skill_discovery",
            status="fail",
            latency_ms=elapsed(),
            provider="skills.sh",
            model=skill.slug,
            error=f"SKILL.md for {skill.slug!r} not found in {skill.source}",
        )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    resolved_license = (
        license_text
        if license_text is not None
        else _fetch_license_text(skill.source, http_client=http_client)
    )
    staging: tempfile.TemporaryDirectory | None = None
    try:
        root = Path(skills_root) if skills_root is not None else None
        if root is None:
            staging = tempfile.TemporaryDirectory(prefix="puspa-nightly-skill-")
            root = Path(staging.name)
        installed = install_discovered(
            MarketplaceRegistry(),
            skill,
            user_id=user_id,
            approval=InstallApproval.for_skill(skill.slug, digest),
            skill_text=text,
            license_text=resolved_license,
            skills_root=root,
        )
    finally:
        if staging is not None:
            staging.cleanup()
    return ProbeResult(
        name="skill_discovery",
        status="pass",
        latency_ms=elapsed(),
        provider="skills.sh",
        model=skill.slug,
        response_shape={
            "results": len(outcome.skills),
            "quality": skill.quality,
            "installs": skill.installs,
            "installed": installed.get("skill_id", skill.slug),
        },
        detail=(
            f"found {len(outcome.skills)} skill(s) for {query!r}; installed "
            f"{skill.slug} [{skill.quality}] from {skill.source} to a temp dir"
        ),
    )


# -- Google login ----------------------------------------------------------


def probe_google_login(
    *,
    client_id: str | None,
    client_secret: str | None,
    redirect_uri: str,
    auth_code: str | None = None,
    code_verifier: str | None = None,
    refresh_token: str | None = None,
    user_id: str = "nightly-smoke-test",
    http_client=None,
    token_store: Callable[[str, dict], None] | None = None,
) -> ProbeResult:
    """Finish the Google callback, save the session, refresh the token.

    With a fresh authorization code this runs the full callback
    path (exchange code, save tokens, fetch userinfo); without
    one it runs the refresh-only path (refresh the stored token,
    save, fetch userinfo) and records that path honestly. Either
    way the probe then refreshes the token once more, which is
    the token-expiry check (gate 15). Token values are never
    recorded — only the email, subject, and expiry.
    """
    elapsed = _timer()
    if not client_id or not client_secret:
        return ProbeResult(
            name="google_login",
            status="fail",
            latency_ms=elapsed(),
            provider="google",
            error=(
                "Google test account is not configured: set the "
                "PUSPA_GOOGLE_TEST_CLIENT_ID and "
                "PUSPA_GOOGLE_TEST_CLIENT_SECRET secrets"
            ),
        )
    if auth_code:
        path = "callback"
        if not code_verifier:
            return ProbeResult(
                name="google_login",
                status="fail",
                latency_ms=elapsed(),
                provider="google",
                error=(
                    "PUSPA_GOOGLE_TEST_AUTH_CODE is set but "
                    "PUSPA_GOOGLE_TEST_CODE_VERIFIER is missing"
                ),
            )
        try:
            tokens = exchange_code(
                auth_code,
                code_verifier,
                client_id,
                client_secret,
                redirect_uri,
                http_client=http_client,
            )
        except OAuthError as exc:
            return ProbeResult(
                name="google_login",
                status="fail",
                latency_ms=elapsed(),
                provider="google",
                error=f"authorization code exchange failed: {exc}",
            )
    elif refresh_token:
        path = "refresh-only"
        try:
            tokens = refresh_tokens(
                refresh_token, client_id, client_secret, http_client=http_client
            )
        except OAuthError as exc:
            return ProbeResult(
                name="google_login",
                status="fail",
                latency_ms=elapsed(),
                provider="google",
                error=(
                    "refresh token was rejected: the test account's refresh "
                    f"token may have expired (Testing-mode tokens last 7 days): {exc}"
                ),
            )
    else:
        return ProbeResult(
            name="google_login",
            status="fail",
            latency_ms=elapsed(),
            provider="google",
            error=(
                "no Google test credentials: set PUSPA_GOOGLE_TEST_REFRESH_TOKEN "
                "(daily path) or PUSPA_GOOGLE_TEST_AUTH_CODE plus "
                "PUSPA_GOOGLE_TEST_CODE_VERIFIER (full callback path)"
            ),
        )
    if token_store is not None:
        token_store(user_id, dict(tokens))
    try:
        info = fetch_userinfo(tokens["access_token"], http_client=http_client)
    except OAuthError as exc:
        return ProbeResult(
            name="google_login",
            status="fail",
            latency_ms=elapsed(),
            provider="google",
            response_shape={"path": path},
            error=f"userinfo fetch failed after login: {exc}",
        )
    current_refresh = tokens.get("refresh_token") or refresh_token
    if not current_refresh:
        return ProbeResult(
            name="google_login",
            status="fail",
            latency_ms=elapsed(),
            provider="google",
            response_shape={"path": path},
            error="login returned no refresh token, so the token cannot be refreshed",
        )
    try:
        refreshed = refresh_tokens(
            current_refresh, client_id, client_secret, http_client=http_client
        )
    except OAuthError as exc:
        return ProbeResult(
            name="google_login",
            status="fail",
            latency_ms=elapsed(),
            provider="google",
            response_shape={"path": path},
            error=(
                "token refresh failed (token expiry check): the test "
                f"account's refresh token may have expired: {exc}"
            ),
        )
    if token_store is not None:
        token_store(user_id, dict(refreshed))
    return ProbeResult(
        name="google_login",
        status="pass",
        latency_ms=elapsed(),
        provider="google",
        response_shape={
            "path": path,
            "has_refresh_token": bool(refreshed.get("refresh_token")),
            "expires_in": refreshed.get("expires_in"),
        },
        detail=(
            f"Google login via {path} for {info.get('email')} "
            f"(sub {info.get('sub')}); session saved, token refreshed"
        ),
    )


# -- Telegram ---------------------------------------------------------------


def _telegram_adapter(bot_token: str, *, http_client: httpx.Client | None) -> TelegramAdapter:
    return TelegramAdapter(bot_token, http_client=http_client, sleep=lambda _seconds: None)


def probe_telegram_roundtrip(
    *,
    bot_token: str | None,
    chat_id: str | None,
    http_client: httpx.Client | None = None,
    timeout: int = 30,
) -> ProbeResult:
    """One message out (with an approval keyboard) and one message in.

    Sends a ping to the test chat, then polls ``getUpdates`` for
    an inbound message from that chat. The inbound leg fails
    loudly when no message arrives: the operator sends one
    message to the test bot so the lane can see it.
    """
    elapsed = _timer()
    if not bot_token or not chat_id:
        return ProbeResult(
            name="telegram_roundtrip",
            status="fail",
            latency_ms=elapsed(),
            provider="telegram",
            error=(
                "Telegram test bot is not configured: set the "
                "TELEGRAM_TEST_BOT_TOKEN and TELEGRAM_TEST_CHAT_ID secrets"
            ),
        )
    adapter = _telegram_adapter(bot_token, http_client=http_client)
    sent = adapter.send_text(
        str(chat_id),
        "Puspa nightly smoke ping — reply with any message, then tap Approve.",
        reply_markup=SMOKE_KEYBOARD,
    )
    events = adapter.poll_once(timeout=timeout)
    inbound = next(
        (event for event in events if event.chat_id == str(chat_id) and not event.is_bot),
        None,
    )
    if inbound is None:
        return ProbeResult(
            name="telegram_roundtrip",
            status="fail",
            latency_ms=elapsed(),
            provider="telegram",
            response_shape={"sent_message_id": sent.get("message_id")},
            error=(
                "no inbound message from the test chat arrived: send one "
                "message to the test bot (any text) so the lane can read it, "
                "then re-run"
            ),
        )
    return ProbeResult(
        name="telegram_roundtrip",
        status="pass",
        latency_ms=elapsed(),
        provider="telegram",
        response_shape={
            "sent_message_id": sent.get("message_id"),
            "inbound_message_id": inbound.message_id,
            "inbound_chars": len(inbound.text),
        },
        detail=(
            f"sent message {sent.get('message_id')} to the test chat; "
            f"read inbound message {inbound.message_id} "
            f"({inbound.text[:60]!r})"
        ),
    )


def probe_telegram_approval(
    *,
    bot_token: str | None,
    chat_id: str | None,
    http_client: httpx.Client | None = None,
    timeout: int = 30,
) -> ProbeResult:
    """One approval via a button tap.

    Polls ``getUpdates`` for a ``callback_query`` from the test
    chat and answers it. When no tap arrived in the poll window
    the probe skips — the tap is a human action, and a missing
    tap must not fail a lane that is otherwise healthy — but
    the skip is recorded in the time series, so approval
    coverage stays visible.
    """
    elapsed = _timer()
    if not bot_token or not chat_id:
        return ProbeResult(
            name="telegram_approval_button",
            status="fail",
            latency_ms=elapsed(),
            provider="telegram",
            error=(
                "Telegram test bot is not configured: set the "
                "TELEGRAM_TEST_BOT_TOKEN and TELEGRAM_TEST_CHAT_ID secrets"
            ),
        )
    adapter = _telegram_adapter(bot_token, http_client=http_client)
    updates = adapter.fetch_updates(timeout=timeout)
    for update in updates:
        callback = adapter.parse_callback_query(update)
        if callback is None or callback["chat_id"] != str(chat_id):
            continue
        adapter.answer_callback_query(
            callback["callback_query_id"],
            text=f"received: {callback['data']}",
        )
        return ProbeResult(
            name="telegram_approval_button",
            status="pass",
            latency_ms=elapsed(),
            provider="telegram",
            response_shape={
                "callback_data": callback["data"],
                "sender_id": callback["sender_id"],
            },
            detail=f"answered button tap {callback['data']!r} from the test chat",
        )
    return ProbeResult(
        name="telegram_approval_button",
        status="skip",
        latency_ms=elapsed(),
        provider="telegram",
        detail=(
            "no button tap arrived in the poll window; tap Approve on "
            "the smoke message to cover this leg"
        ),
    )
