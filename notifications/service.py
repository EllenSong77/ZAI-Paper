"""Orchestrates per-target delivery: dry-run, smoke-test, bootstrap, send.

This module is the only place that ties together config, client, cards, and
state.  Each public command returns a tuple ``(ok, summary)`` where ``ok`` is
False if any target failed; callers exit with a non-zero status in that case.

Author:
    Ellen Song <jiaqi.song@z.ai>
    Modified by Wethepe <dongyangyan@stu.pku.edu.cn>
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import cards, client, state
from .config import Settings, Target, require_credentials

logger = logging.getLogger("notifications")


def _redact_receive_id(receive_id: str) -> str:
    """Returns a short, non-identifying preview of a receive_id for logs."""
    if not receive_id:
        return "<empty>"
    if len(receive_id) <= 6:
        return "***"
    return receive_id[:2] + "***" + receive_id[-2:]


@dataclass
class TargetResult:
    """Per-target outcome for any command."""

    target: Target
    ok: bool
    message: str = ""
    delivered: int = 0
    sent_paper_ids: list[str] = field(default_factory=list)

    @property
    def short_fingerprint(self) -> str:
        return self.target.fingerprint[:6]


def _validate_papers_file(path: Path) -> dict[str, Any]:
    """Loads and validates the public papers JSON."""
    if not path.exists():
        raise FileNotFoundError(f"papers file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"papers file {path} is not valid JSON") from error
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"papers file {path} is missing a rows array")
    return data


def _rows_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Returns a deterministic id -> row map from the papers JSON."""
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in data.get("rows", []):
        arxiv_id = str(row.get("arxiv_id", "")).strip()
        if arxiv_id:
            rows_by_id[arxiv_id] = row
    return rows_by_id


def _sorted_arxiv_ids(data: dict[str, Any]) -> list[str]:
    """Returns arXiv IDs deterministically ordered by published date then id."""
    rows = [
        (str(row.get("arxiv_id", "")).strip(), str(row.get("published", "")))
        for row in data.get("rows", [])
        if str(row.get("arxiv_id", "")).strip()
    ]
    rows.sort(key=lambda item: (item[1], item[0]))
    return [arxiv_id for arxiv_id, _ in rows]


def _arxiv_id_key(row: dict[str, Any]) -> tuple[str, str]:
    """Sort key: published date then arxiv_id, both ascending."""
    return (str(row.get("published", "")), str(row.get("arxiv_id", "")))


def _log_target(level: int, target: Target, msg: str) -> None:
    """Logs a target-scoped message without leaking the receive_id."""
    logger.log(
        level,
        "target id=%s name=%s receive_id=%s %s",
        target.id,
        target.name,
        _redact_receive_id(target.receive_id),
        msg,
    )


def run_dry_run(settings: Settings) -> tuple[bool, list[TargetResult]]:
    """Reports pending counts per target without any network or state write."""
    data = _validate_papers_file(settings.papers_path)
    rows_by_id = _rows_by_id(data)
    current_ids = set(rows_by_id)
    working_state = state.load_state(settings.state_path)

    results: list[TargetResult] = []
    for target in settings.targets:
        delivered = state.delivered_ids(working_state, target)
        bootstrapped = state.is_bootstrapped(working_state, target)
        fingerprint_ok = state.fingerprint_matches(working_state, target)
        pending = (
            sorted(current_ids - delivered)
            if bootstrapped and fingerprint_ok
            else []
        )
        message = (
            "bootstrapped=False (send is disabled) "
            "total={total} delivered=0 pending=0"
        ).format(total=len(current_ids))
        if bootstrapped and fingerprint_ok:
            message = (
                "bootstrapped=True total={total} delivered={delivered} "
                "pending={pending}"
            ).format(
                total=len(current_ids),
                delivered=len(delivered),
                pending=len(pending),
            )
        _log_target(logging.INFO, target, message)
        results.append(
            TargetResult(
                target=target,
                ok=True,
                message=message,
                delivered=len(pending),
                sent_paper_ids=pending,
            )
        )
    return True, results


def run_smoke_test(settings: Settings) -> tuple[bool, list[TargetResult]]:
    """Sends one fixed test card to every target; never touches state."""
    app_id, app_secret = require_credentials(settings)
    card = cards.build_smoke_test_card(settings.site_url)
    content = cards.encode_content(card)
    working_state = state.load_state(settings.state_path)

    results: list[TargetResult] = []
    for target in settings.targets:
        with requests.Session() as session:
            try:
                token = client.fetch_tenant_token(session, app_id, app_secret)
                response = client.send_message(
                    session,
                    token,
                    target.receive_id_type,
                    target.receive_id,
                    content,
                )
            except client.FeishuError as error:
                _log_target(
                    logging.ERROR,
                    target,
                    f"smoke-test failed: {error}",
                )
                results.append(
                    TargetResult(target=target, ok=False, message=str(error))
                )
                continue
            except requests.RequestException as error:
                _log_target(
                    logging.ERROR,
                    target,
                    f"smoke-test network error: {error.__class__.__name__}",
                )
                results.append(
                    TargetResult(
                        target=target,
                        ok=False,
                        message=f"network error: {error.__class__.__name__}",
                    )
                )
                continue
        _log_target(
            logging.INFO,
            target,
            f"smoke-test sent message_id={response.message_id}",
        )
        results.append(
            TargetResult(
                target=target,
                ok=True,
                message=f"smoke-test ok (message_id={response.message_id})",
            )
        )

    # State is intentionally not loaded beyond the diagnostics above; smoke
    # tests never record deliveries.
    _ = working_state
    return all(result.ok for result in results), results


def run_bootstrap(
    settings: Settings,
    *,
    target_filter: str | None = None,
    replace_target: bool = False,
) -> tuple[bool, list[TargetResult]]:
    """Bootstraps targets using the current papers JSON as the baseline.

    - Already-bootstrapped targets with matching fingerprints are kept as-is.
    - Brand-new targets are bootstrapped with the full current paper set.
    - Fingerprint-changed targets are only reset when ``--replace-target`` is
      set and (if ``--target`` is also given) the targeted id matches.
    """
    data = _validate_papers_file(settings.papers_path)
    baseline = _sorted_arxiv_ids(data)
    working_state = state.load_state(settings.state_path)
    results: list[TargetResult] = []
    changed = False

    for target in settings.targets:
        if target_filter and target.id != target_filter:
            results.append(
                TargetResult(
                    target=target,
                    ok=True,
                    message="skipped (target filter did not match)",
                )
            )
            continue

        bootstrapped = state.is_bootstrapped(working_state, target)
        fingerprint_ok = state.fingerprint_matches(working_state, target)

        if not bootstrapped:
            working_state = state.bootstrap_target(working_state, target, baseline)
            changed = True
            msg = f"bootstrapped {len(baseline)} historical ids"
            _log_target(logging.INFO, target, msg)
            results.append(TargetResult(target=target, ok=True, message=msg))
            continue

        if fingerprint_ok:
            msg = "already bootstrapped with matching fingerprint; unchanged"
            _log_target(logging.INFO, target, msg)
            results.append(TargetResult(target=target, ok=True, message=msg))
            continue

        if not replace_target:
            msg = (
                "fingerprint changed; refusing to bootstrap without "
                "--replace-target (use `bootstrap --target "
                f"{target.id} --replace-target`)"
            )
            _log_target(logging.ERROR, target, msg)
            results.append(TargetResult(target=target, ok=False, message=msg))
            continue

        working_state = state.reset_target(working_state, target, baseline)
        changed = True
        msg = (
            "fingerprint changed; replaced baseline with "
            f"{len(baseline)} historical ids"
        )
        _log_target(logging.INFO, target, msg)
        results.append(TargetResult(target=target, ok=True, message=msg))

    if changed:
        state.atomic_write_state(settings.state_path, working_state)
    return all(result.ok for result in results), results


def _send_papers(
    session: requests.Session,
    token: str,
    target: Target,
    rows: list[dict[str, Any]],
    site_url: str,
) -> client.FeishuResponse:
    """Sends a single card containing all ``rows`` to the given target."""
    card = cards.build_papers_card(rows, site_url)
    return client.send_message(
        session,
        token,
        target.receive_id_type,
        target.receive_id,
        cards.encode_content(card),
    )


def run_send(
    settings: Settings,
    *,
    registrar: Callable[[Path, dict[str, Any]], None] | None = None,
) -> tuple[bool, list[TargetResult]]:
    """Delivers pending papers to bootstrapped targets, failing closed.

    Every configured target is evaluated.  Targets that are not bootstrapped
    or whose fingerprint no longer matches produce a failing ``TargetResult``
    *without* requesting a token or sending any message, so the overall
    command exits non-zero and the operator is told how to recover.  This is
    what prevents a fresh ``send`` from silently mass-mailing history.

    The ``registrar`` hook (used by the test suite to avoid touching the real
    filesystem) replaces :func:`state.atomic_write_state` when supplied.
    """
    app_id, app_secret = require_credentials(settings)
    data = _validate_papers_file(settings.papers_path)
    rows_by_id = _rows_by_id(data)
    working_state = state.load_state(settings.state_path)

    writer = registrar or (lambda path, st: state.atomic_write_state(path, st))

    results: list[TargetResult] = []
    overall_ok = True

    for target in settings.targets:
        if not state.is_bootstrapped(working_state, target):
            msg = "not bootstrapped; run `bootstrap` first (send is disabled)"
            _log_target(logging.ERROR, target, msg)
            results.append(TargetResult(target=target, ok=False, message=msg))
            overall_ok = False
            continue
        if not state.fingerprint_matches(working_state, target):
            msg = (
                "fingerprint mismatch; run `bootstrap --target "
                f"{target.id} --replace-target`"
            )
            _log_target(logging.ERROR, target, msg)
            results.append(TargetResult(target=target, ok=False, message=msg))
            overall_ok = False
            continue

        delivered = state.delivered_ids(working_state, target)
        pending_ids = [
            arxiv_id
            for arxiv_id in _sorted_arxiv_ids(data)
            if arxiv_id not in delivered
        ]
        pending_rows = [
            rows_by_id[arxiv_id]
            for arxiv_id in pending_ids
            if arxiv_id in rows_by_id
        ]
        if not pending_rows:
            _log_target(logging.INFO, target, "no pending papers")
            results.append(
                TargetResult(
                    target=target,
                    ok=True,
                    message="no pending papers",
                    delivered=0,
                )
            )
            continue

        token_acquired = False
        try:
            session = requests.Session()
            try:
                token = client.fetch_tenant_token(session, app_id, app_secret)
                token_acquired = True
            except client.AuthError as error:
                _log_target(logging.ERROR, target, f"auth failed: {error}")
                results.append(
                    TargetResult(
                        target=target,
                        ok=False,
                        message=f"auth failed: {error}",
                    )
                )
                overall_ok = False
                continue

            sent_ids: list[str] = []
            failure_msg = ""

            # Build one card containing all pending papers (deterministic
            # order), and send it in a single Feishu call. If the call
            # succeeds, every pending arXiv id is recorded as delivered with
            # the same message_id; if it fails, none are recorded and the next
            # run will retry the whole batch.
            ordered_rows = sorted(pending_rows, key=_arxiv_id_key)
            try:
                response = _send_papers(
                    session,
                    token,
                    target,
                    ordered_rows,
                    settings.site_url,
                )
            except client.PermanentError as error:
                failure_msg = f"permanent error: {error}"
                _log_target(logging.ERROR, target, failure_msg)
            except client.TransientError as error:
                failure_msg = f"transient error: {error}"
                _log_target(logging.ERROR, target, failure_msg)
            except requests.RequestException as error:
                failure_msg = f"network error: {error.__class__.__name__}"
                _log_target(logging.ERROR, target, failure_msg)
            else:
                message_id = response.message_id or ""
                for row in ordered_rows:
                    arxiv_id = str(row.get("arxiv_id", "")).strip()
                    if arxiv_id:
                        working_state = state.record_delivery(
                            working_state, target, arxiv_id, message_id
                        )
                        sent_ids.append(arxiv_id)
                # Flush so a later target failure never loses this progress.
                writer(settings.state_path, working_state)
        finally:
            if token_acquired:
                pass  # token is process-local only; nothing to clean up

        if failure_msg:
            results.append(
                TargetResult(
                    target=target,
                    ok=False,
                    message=failure_msg,
                    delivered=len(sent_ids),
                    sent_paper_ids=sent_ids,
                )
            )
            overall_ok = False
        else:
            _log_target(
                logging.INFO, target, f"delivered {len(sent_ids)} papers"
            )
            results.append(
                TargetResult(
                    target=target,
                    ok=True,
                    message=f"delivered {len(sent_ids)} papers",
                    delivered=len(sent_ids),
                    sent_paper_ids=sent_ids,
                )
            )

    return overall_ok, results


def utc_now_iso() -> str:
    """Re-exported for callers that want the same timestamp format."""
    datetime.now(timezone.utc)
    return state.utc_now_iso()