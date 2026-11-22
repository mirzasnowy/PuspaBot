"""``puspa channels`` command group: run the channel gateway.

``run`` opens a :class:`GatewayRunner` for one profile and polls
Telegram until interrupted, answering through the configured
engine. ``engines`` lists the engines a channel can answer
through and whether each one is configured (keys are never
printed).

The engine switch (AST-05):

- ``puspa channels run --engine dify`` answers through Dify;
  ``coze`` and ``bailian`` likewise. Without ``--engine`` the
  ``PUSPA_CHANNEL_ENGINE`` environment variable decides, and
  without either the built-in engine answers (the default).
- The engine's own key comes from the environment only
  (``DIFY_API_KEY``, ``COZE_API_TOKEN`` + ``COZE_BOT_ID``,
  ``BAILIAN_API_KEY`` + ``BAILIAN_APP_ID``) - never from an
  argument, never printed. A selected engine without its key
  fails at start with a clear error naming the variable.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from .config import ChannelConfigError, load_config
from .engines import ENGINE_NAMES
from .run import GatewayRunner

__all__ = ["add_channels_parser", "run_channels"]


def _open_runner(profile: str, **kwargs) -> GatewayRunner:
    """Open a gateway runner (test seam: monkeypatch this)."""
    return GatewayRunner.open(profile=profile, **kwargs)


def add_channels_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``channels`` group to the top-level parser."""
    channels = subparsers.add_parser(
        "channels",
        help="Run the channel gateway (Telegram) and pick its engine.",
        description=(
            "Run the channel gateway for one profile: poll Telegram "
            "and answer through the configured engine (the built-in "
            "engine by default, or Dify, Coze, or Bailian)."
        ),
    )
    channels_sub = channels.add_subparsers(dest="channels_command", metavar="CHANNELS_COMMAND")
    run = channels_sub.add_parser(
        "run",
        help="Poll Telegram and answer through the configured engine.",
        description=(
            "Poll Telegram and answer through the configured engine. "
            "Stops on Ctrl-C. The engine comes from --engine, else "
            "PUSPA_CHANNEL_ENGINE, else the built-in engine."
        ),
    )
    run.add_argument(
        "--engine",
        choices=list(ENGINE_NAMES),
        default=None,
        help="Which engine answers a channel (default: PUSPA_CHANNEL_ENGINE or builtin).",
    )
    run.add_argument(
        "--profile",
        default="default",
        help="Gateway profile (default: default).",
    )
    channels_sub.add_parser(
        "engines",
        help="List the engines a channel can answer through and their setup state.",
        description=(
            "List the engines a channel can answer through (builtin, "
            "dify, coze, bailian) and whether each one is configured. "
            "Keys are never printed."
        ),
    )
    return channels


def _engine_status() -> list[tuple[str, str]]:
    """One (engine, status) row per engine, from the environment."""
    config = load_config()
    return [
        ("builtin", "ready (PuspaBot's own engine)"),
        (
            "dify",
            "ready" if config.dify_api_key else "missing DIFY_API_KEY",
        ),
        (
            "coze",
            "ready"
            if config.coze_api_token and config.coze_bot_id
            else "missing COZE_API_TOKEN and/or COZE_BOT_ID",
        ),
        (
            "bailian",
            "ready"
            if config.bailian_api_key and config.bailian_app_id
            else "missing BAILIAN_API_KEY and/or BAILIAN_APP_ID",
        ),
    ]


def run_channels(parsed: argparse.Namespace) -> int:
    """Run one ``puspa channels`` subcommand."""
    if parsed.channels_command == "engines":
        for name, status in _engine_status():
            print(f"{name}: {status}")
        return 0
    if parsed.channels_command == "run":
        config = load_config()
        if parsed.engine is not None:
            config = replace(config, engine=parsed.engine)
        try:
            runner = _open_runner(parsed.profile, config=config)
        except ChannelConfigError as exc:
            print(f"puspa channels: {exc}", file=sys.stderr)
            return 2
        if not runner.config.telegram_enabled:
            print(
                "puspa channels: Telegram is not configured: set TELEGRAM_BOT_TOKEN",
                file=sys.stderr,
            )
            runner.close()
            return 2
        print(
            f"channels gateway running (engine: {runner.config.engine}, "
            f"profile: {parsed.profile}). Press Ctrl-C to stop."
        )
        try:
            while True:
                runner.poll_telegram_once(timeout=30)
        except KeyboardInterrupt:
            print("channels gateway stopped.")
            return 0
        finally:
            runner.close()
    return 0
