"""Nightly smoke runner (E-04): ``python -m puspa.nightly.run``.

Reads its configuration from the environment (secret *names*
only — values never appear in code, logs, or the store),
runs the enabled probes, appends one record per probe to the
JSONL time series, and writes a Markdown summary. Exit code 0
when every required probe passed (or skipped where a skip is
allowed); 1 otherwise.

Environment (all optional unless a probe needs them):

- ``OPENROUTER_API_KEY`` — low-capped key for the OpenRouter
  legs (secret ``OPENROUTER_NIGHTLY_KEY`` in CI).
- ``OLLAMA_BASE_URL`` — default ``http://localhost:11434/v1``.
- ``OLLAMA_SMOKE_MODEL`` — default ``qwen2.5:0.5b``.
- ``PUSPA_SKILL_DISCOVERY_ENABLED`` — ``1`` enables the
  skills.sh leg; unset means that probe skips.
- ``PUSPA_GOOGLE_TEST_CLIENT_ID``,
  ``PUSPA_GOOGLE_TEST_CLIENT_SECRET``,
  ``PUSPA_GOOGLE_TEST_REDIRECT_URI`` — the Google test OAuth
  client (secrets in CI).
- ``PUSPA_GOOGLE_TEST_REFRESH_TOKEN`` — daily path: refresh
  the stored token. ``PUSPA_GOOGLE_TEST_AUTH_CODE`` plus
  ``PUSPA_GOOGLE_TEST_CODE_VERIFIER`` — full callback path
  (a fresh code each run; the refresh path is the daily one).
- ``TELEGRAM_TEST_BOT_TOKEN``, ``TELEGRAM_TEST_CHAT_ID`` —
  the Telegram test bot and chat (secrets in CI).
- ``PUSPA_NIGHTLY_STORE`` — JSONL path, default
  ``nightly-smoke/runs.jsonl``. ``PUSPA_NIGHTLY_SUMMARY`` —
  Markdown path, default ``nightly-smoke/summary.md``.
- ``PUSPA_NIGHTLY_SESSION_FILE`` — where the Google test
  session (tokens) is saved, mode 0600. It is never uploaded
  as an artifact and never written into the time series.

``--only`` runs a subset of probes by name (local runs without
all the secrets); ``--store`` / ``--summary`` override the
output paths.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from puspa.pwa.model_settings import DEFAULT_LOCAL_BASE_URL

from .probes import (
    OLLAMA_SMOKE_MODEL,
    PROBE_NAMES,
    ProbeResult,
    probe_google_login,
    probe_ollama_turn,
    probe_openrouter_fallback,
    probe_openrouter_turn,
    probe_skill_discovery,
    probe_telegram_approval,
    probe_telegram_roundtrip,
    run_probes,
)
from .store import append_records, load_records, summarize, write_summary_markdown

__all__ = ["NightlyConfig", "NightlyReport", "main", "run_nightly"]

#: Probes whose ``skip`` still counts as a healthy lane run.
#: The skills leg skips when discovery is off; the Telegram
#: approval leg skips when no button tap arrived in the poll
#: window. Every other skip is treated as a failure.
ALLOWED_SKIPS = frozenset({"skill_discovery", "telegram_approval_button"})


@dataclass
class NightlyConfig:
    """Configuration for one nightly run, read from the environment."""

    store_path: str = "nightly-smoke/runs.jsonl"
    summary_path: str = "nightly-smoke/summary.md"
    session_file: str | None = None
    openrouter_api_key: str | None = None
    ollama_base_url: str = DEFAULT_LOCAL_BASE_URL
    ollama_model: str = OLLAMA_SMOKE_MODEL
    skill_discovery_enabled: bool = False
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    google_auth_code: str | None = None
    google_code_verifier: str | None = None
    google_refresh_token: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    only: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> NightlyConfig:
        """Build the config from the environment (``os.environ`` by default)."""
        source = env if env is not None else os.environ
        return cls(
            store_path=source.get("PUSPA_NIGHTLY_STORE", "nightly-smoke/runs.jsonl"),
            summary_path=source.get("PUSPA_NIGHTLY_SUMMARY", "nightly-smoke/summary.md"),
            session_file=source.get("PUSPA_NIGHTLY_SESSION_FILE"),
            openrouter_api_key=source.get("OPENROUTER_API_KEY"),
            ollama_base_url=source.get("OLLAMA_BASE_URL", DEFAULT_LOCAL_BASE_URL),
            ollama_model=source.get("OLLAMA_SMOKE_MODEL", OLLAMA_SMOKE_MODEL),
            skill_discovery_enabled=source.get("PUSPA_SKILL_DISCOVERY_ENABLED") == "1",
            google_client_id=source.get("PUSPA_GOOGLE_TEST_CLIENT_ID"),
            google_client_secret=source.get("PUSPA_GOOGLE_TEST_CLIENT_SECRET"),
            google_redirect_uri=source.get(
                "PUSPA_GOOGLE_TEST_REDIRECT_URI",
                "http://localhost:8000/auth/google/callback",
            ),
            google_auth_code=source.get("PUSPA_GOOGLE_TEST_AUTH_CODE"),
            google_code_verifier=source.get("PUSPA_GOOGLE_TEST_CODE_VERIFIER"),
            google_refresh_token=source.get("PUSPA_GOOGLE_TEST_REFRESH_TOKEN"),
            telegram_bot_token=source.get("TELEGRAM_TEST_BOT_TOKEN"),
            telegram_chat_id=source.get("TELEGRAM_TEST_CHAT_ID"),
        )


@dataclass
class NightlyReport:
    """The outcome of one nightly run."""

    run_id: str
    results: list[ProbeResult]
    store_path: str
    summary_path: str
    exit_code: int


def _file_token_store(path: str) -> Callable[[str, dict], None]:
    """A token store that saves the Google test session to one 0600 file.

    The file holds real tokens, so it lives outside the artifact
    directory and is never uploaded: the runner writes it to
    ``PUSPA_NIGHTLY_SESSION_FILE`` (or a fresh temp file) and the
    workflow never publishes that path.
    """

    def store(_user_id: str, tokens: dict) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(tokens, ensure_ascii=False), encoding="utf-8")
        os.chmod(target, 0o600)

    return store


def _probe_functions(config: NightlyConfig) -> list[tuple[str, Callable[[], ProbeResult]]]:
    """The (name, zero-argument probe) list for this run's config."""
    token_store = _file_token_store(
        config.session_file
        or str(Path(tempfile.gettempdir()) / "puspa-nightly-google-session.json")
    )
    probes: list[tuple[str, Callable[[], ProbeResult]]] = [
        (
            "openrouter_turn",
            lambda: probe_openrouter_turn(api_key=config.openrouter_api_key),
        ),
        (
            "openrouter_fallback",
            lambda: probe_openrouter_fallback(api_key=config.openrouter_api_key),
        ),
        (
            "ollama_turn",
            lambda: probe_ollama_turn(base_url=config.ollama_base_url, model=config.ollama_model),
        ),
        (
            "skill_discovery",
            lambda: probe_skill_discovery(enabled=config.skill_discovery_enabled),
        ),
        (
            "google_login",
            lambda: probe_google_login(
                client_id=config.google_client_id,
                client_secret=config.google_client_secret,
                redirect_uri=config.google_redirect_uri,
                auth_code=config.google_auth_code,
                code_verifier=config.google_code_verifier,
                refresh_token=config.google_refresh_token,
                token_store=token_store,
            ),
        ),
        (
            "telegram_roundtrip",
            lambda: probe_telegram_roundtrip(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
            ),
        ),
        (
            "telegram_approval_button",
            lambda: probe_telegram_approval(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
            ),
        ),
    ]
    if config.only:
        wanted = set(config.only)
        probes = [(name, fn) for name, fn in probes if name in wanted]
    return probes


def run_nightly(config: NightlyConfig) -> NightlyReport:
    """Run the probes, append the records, write the summary."""
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    results = run_probes(_probe_functions(config))
    records = [result.to_record(run_id, timestamp) for result in results]
    append_records(config.store_path, records)
    summary = summarize(load_records(config.store_path))
    write_summary_markdown(config.summary_path, summary, run_id)
    failed = [
        result
        for result in results
        if result.status == "fail" or (result.status == "skip" and result.name not in ALLOWED_SKIPS)
    ]
    return NightlyReport(
        run_id=run_id,
        results=results,
        store_path=config.store_path,
        summary_path=config.summary_path,
        exit_code=1 if failed else 0,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse flags, run, print one line per probe."""
    parser = argparse.ArgumentParser(
        description="Nightly live smoke lane (E-04): probe the live providers, "
        "record latency/cost/shape as a time series."
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated probe names to run (default: all); "
        f"known names: {', '.join(PROBE_NAMES)}",
    )
    parser.add_argument("--store", default=None, help="JSONL store path")
    parser.add_argument("--summary", default=None, help="Markdown summary path")
    args = parser.parse_args(argv)
    if args.only:
        unknown = sorted(set(args.only.split(",")) - set(PROBE_NAMES))
        if unknown:
            print(f"unknown probe(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"known probes: {', '.join(PROBE_NAMES)}", file=sys.stderr)
            return 2
    config = NightlyConfig.from_env()
    if args.store:
        config.store_path = args.store
    if args.summary:
        config.summary_path = args.summary
    if args.only:
        config.only = tuple(name for name in args.only.split(",") if name)
    report = run_nightly(config)
    for result in report.results:
        line = f"{result.name}: {result.status} ({result.latency_ms} ms)"
        if result.cost_rp is not None:
            line += f" cost Rp{result.cost_rp}"
        if result.detail:
            line += f" — {result.detail}"
        if result.error:
            line += f" — {result.error}"
        print(line)
    print(f"records appended to {report.store_path}")
    print(f"summary written to {report.summary_path}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
