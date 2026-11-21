"""``puspa service`` command group: lifecycle of the local agent service.

``start`` / ``stop`` / ``restart`` control the background gateway
process, ``status`` / ``logs`` inspect it, and ``update`` upgrades the
installed ``puspa`` package and restarts the service. The commands
work on Linux without Docker and without root; state lives under the
Puspa home (``PUSPA_HOME`` or ``~/.puspa``), overridable with
``--home``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from puspa.service.manager import ServiceError, ServiceManager

__all__ = ["add_service_parser", "run_service"]


def _default_factory(home: str | Path | None) -> ServiceManager:
    """Build a manager for one home (test seam: monkeypatch this)."""
    return ServiceManager(home=home)


def add_service_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``service`` group to the top-level parser."""
    service = subparsers.add_parser(
        "service",
        help="Manage the local agent service: start, stop, restart, status, logs, update.",
        description=(
            "Manage the local agent service (the PuspaBot gateway "
            "running in the background): start it, stop it, restart "
            "it, check its status, read its logs, or upgrade the "
            "installed package."
        ),
    )
    service.add_argument(
        "--home",
        default=None,
        help="Puspa home directory (defaults to PUSPA_HOME or ~/.puspa).",
    )
    service_sub = service.add_subparsers(dest="service_command", metavar="SERVICE_COMMAND")
    start = service_sub.add_parser(
        "start",
        help="Start the local agent service in the background.",
        description="Start the local agent service in the background.",
    )
    start.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    start.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    service_sub.add_parser(
        "stop",
        help="Stop the local agent service.",
        description="Stop the local agent service.",
    )
    restart = service_sub.add_parser(
        "restart",
        help="Restart the local agent service.",
        description="Restart the local agent service.",
    )
    restart.add_argument(
        "--host",
        default=None,
        help="Bind host (defaults to the running service's host).",
    )
    restart.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (defaults to the running service's port).",
    )
    status = service_sub.add_parser(
        "status",
        help="Show whether the local agent service is running.",
        description="Show whether the local agent service is running.",
    )
    status.add_argument("--json", action="store_true", help="Print the status as JSON.")
    logs = service_sub.add_parser(
        "logs",
        help="Show the service log.",
        description="Show the last lines of the service log.",
    )
    logs.add_argument(
        "--lines",
        type=int,
        default=50,
        help="How many trailing lines to show (default: 50).",
    )
    update = service_sub.add_parser(
        "update",
        help="Upgrade the installed package and restart the service.",
        description=(
            "Upgrade the installed puspa package to the latest "
            "release and restart the service when it is running."
        ),
    )
    update.add_argument(
        "--check",
        action="store_true",
        help="Only check for a newer release; install nothing.",
    )
    return service


def _run_start(args: argparse.Namespace, manager: ServiceManager, print_fn) -> int:
    try:
        result = manager.start(host=args.host, port=args.port)
    except ServiceError as exc:
        print_fn(f"puspa service start: failed: {exc}")
        return 1
    if result.already_running:
        print_fn(
            f"Service is already running (pid {result.pid}, http://{result.host}:{result.port})."
        )
        return 0
    print_fn(f"Service started (pid {result.pid}, http://{result.host}:{result.port}).")
    return 0


def _run_stop(args: argparse.Namespace, manager: ServiceManager, print_fn) -> int:
    result = manager.stop()
    if not result.stopped:
        print_fn(f"puspa service stop: {result.reason}.")
        return 1
    print_fn(f"Service stopped (pid {result.pid}).")
    return 0


def _run_restart(args: argparse.Namespace, manager: ServiceManager, print_fn) -> int:
    try:
        result = manager.restart(host=args.host, port=args.port)
    except ServiceError as exc:
        print_fn(f"puspa service restart: failed: {exc}")
        return 1
    print_fn(f"Service restarted (pid {result.pid}, http://{result.host}:{result.port}).")
    return 0


def _run_status(args: argparse.Namespace, manager: ServiceManager, print_fn) -> int:
    current = manager.status()
    if args.json:
        print_fn(json.dumps(current.as_dict(), indent=2))
        return 0
    if not current.running:
        print_fn("Service is stopped.")
        return 0
    uptime = f", up {current.uptime_seconds:.0f}s" if current.uptime_seconds is not None else ""
    started = f", started {current.started_at}" if current.started_at else ""
    print_fn(
        f"Service is running (pid {current.pid}, "
        f"http://{current.host}:{current.port}{uptime}{started})."
    )
    return 0


def _run_logs(args: argparse.Namespace, manager: ServiceManager, print_fn) -> int:
    try:
        lines = manager.logs(lines=args.lines)
    except ServiceError as exc:
        print_fn(f"puspa service logs: {exc}")
        return 1
    for line in lines:
        print_fn(line)
    return 0


def _run_update(args: argparse.Namespace, manager: ServiceManager, print_fn) -> int:
    try:
        result = manager.update(check_only=args.check)
    except ServiceError as exc:
        print_fn(f"puspa service update: failed: {exc}")
        return 1
    if not result.update_available:
        print_fn(f"Already up to date (puspa {result.installed_version}).")
        return 0
    if args.check:
        print_fn(
            f"Update available: puspa {result.installed_version} "
            f"-> {result.latest_version} (run `puspa service update` to install it)."
        )
        return 0
    print_fn(f"Updated puspa {result.installed_version} -> {result.latest_version}.")
    if result.restarted:
        print_fn("Service restarted with the new version.")
    return 0


def run_service(
    args: argparse.Namespace,
    *,
    manager_factory: Callable[[str | Path | None], ServiceManager] | None = None,
    print_fn=print,
) -> int:
    """Run one ``puspa service`` command. Returns an exit code."""
    factory = manager_factory or _default_factory
    manager = factory(args.home)
    if args.service_command == "start":
        return _run_start(args, manager, print_fn)
    if args.service_command == "stop":
        return _run_stop(args, manager, print_fn)
    if args.service_command == "restart":
        return _run_restart(args, manager, print_fn)
    if args.service_command == "status":
        return _run_status(args, manager, print_fn)
    if args.service_command == "logs":
        return _run_logs(args, manager, print_fn)
    if args.service_command == "update":
        return _run_update(args, manager, print_fn)
    from puspa.cli import build_parser

    build_parser().parse_args(["service", "--help"])
    return 0
