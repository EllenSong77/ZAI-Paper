"""Command-line entry point for the notification package.

Usage::

    python -m notifications dry-run
    python -m notifications smoke-test
    python -m notifications bootstrap [--target ID] [--replace-target]
    python -m notifications send

Every command exits with a non-zero status on any failure.  Logs are
sanitized: App Secret, tenant token, and full receive_id values are never
emitted.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

from . import service
from .config import ConfigError, load_settings
from .state import StateError


def _build_parser() -> argparse.ArgumentParser:
    """Returns the top-level ``notifications`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="notifications",
        description="Feishu paper-notification tool for ZAI-Paper.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "dry-run",
        help="Report pending counts per target. No network, no state writes.",
    )
    sub.add_parser(
        "smoke-test",
        help="Send one fixed test card to each target. Never writes state.",
    )

    bootstrap = sub.add_parser(
        "bootstrap",
        help="Initialize target(s) with the current papers as the historical baseline.",
    )
    bootstrap.add_argument(
        "--target",
        default=None,
        help="Only bootstrap this target id (default: all targets).",
    )
    bootstrap.add_argument(
        "--replace-target",
        action="store_true",
        help=(
            "Required to reset a target whose receive identity fingerprint "
            "changed; otherwise the bootstrap is refused to protect history."
        ),
    )

    sub.add_parser(
        "send",
        help="Deliver pending papers to bootstrapped, fingerprint-matching targets.",
    )
    return parser


def _configure_logging() -> None:
    """Configures sanitized INFO-level logging for the package."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parses arguments, runs the selected command, returns an exit code."""
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as error:
        parser.error(str(error))
        return 2  # parser.error exits, but keep an explicit return for tests.

    command = args.command
    try:
        if command == "dry-run":
            ok, _ = service.run_dry_run(settings)
        elif command == "smoke-test":
            ok, _ = service.run_smoke_test(settings)
        elif command == "bootstrap":
            ok, _ = service.run_bootstrap(
                settings,
                target_filter=args.target,
                replace_target=args.replace_target,
            )
        elif command == "send":
            ok, _ = service.run_send(settings)
        else:  # pragma: no cover - argparse enforces a valid subcommand.
            parser.error(f"unknown command: {command!r}")
            return 2
    except ConfigError as error:
        # Credentials/config issues surface as a clean, sanitized message.
        logging.getLogger("notifications").error("%s", error)
        return 2
    except FileNotFoundError as error:
        logging.getLogger("notifications").error("%s", error)
        return 2
    except StateError as error:
        # Corrupt/inconsistent state files fail closed with a readable
        # message instead of a bare traceback in CI logs.
        logging.getLogger("notifications").error(
            "state error: %s (fix or remove the state file, then re-run "
            "`bootstrap`)", error
        )
        return 2
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
