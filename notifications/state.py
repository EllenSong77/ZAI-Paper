"""Atomic, per-target notification state.

The state file lives outside ``public`` so that it is never published to GitHub
Pages.  Each target stores only a SHA-256 fingerprint of its receive identity
plus the set of delivered arXiv IDs.  The real ``receive_id`` is never written
to disk.

Author:
    Ellen Song <jiaqi.song@z.ai>
    Modified by Wethepe <dongyangyan@stu.pku.edu.cn>
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Target

STATE_VERSION = 1

#: Default content for the committed empty-state file.  An empty ``targets``
#: object is *not* treated as bootstrapped by ``state.py``.
EMPTY_STATE: dict[str, Any] = {"version": STATE_VERSION, "targets": {}}


class StateError(RuntimeError):
    """Raised for unreadable, corrupt, or inconsistent state files."""


def utc_now_iso() -> str:
    """Returns the current UTC time as a timezone-aware ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: Path) -> dict[str, Any]:
    """Loads the state file, failing safely on corruption.

    A missing file is treated as an empty state.  A structurally invalid file
    raises :class:`StateError` rather than being silently overwritten.
    """
    if not path.exists():
        return {"version": STATE_VERSION, "targets": {}}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise StateError(f"cannot read state file {path}: {error}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise StateError(f"state file {path} is not valid JSON") from error
    if not isinstance(data, dict):
        raise StateError(f"state file {path} must be a JSON object")
    if data.get("version") != STATE_VERSION:
        raise StateError(
            f"state file {path} has unsupported version {data.get('version')!r}"
        )
    targets = data.get("targets")
    if not isinstance(targets, dict):
        raise StateError(f"state file {path} target map must be an object")
    return {"version": STATE_VERSION, "targets": targets}


def atomic_write_state(path: Path, state: dict[str, Any]) -> None:
    """Writes state with a same-directory temp file and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(path)


def is_bootstrapped(state: dict[str, Any], target: Target) -> bool:
    """Returns True when the target has a matching fingerprint entry."""
    entry = state["targets"].get(target.id)
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("bootstrapped"))


def fingerprint_matches(state: dict[str, Any], target: Target) -> bool:
    """Returns True iff the target has an entry whose fingerprint matches."""
    entry = state["targets"].get(target.id)
    if not isinstance(entry, dict):
        return False
    return entry.get("target_fingerprint") == target.fingerprint


def delivered_ids(state: dict[str, Any], target: Target) -> set[str]:
    """Returns the set of arXiv IDs already delivered to a target."""
    entry = state["targets"].get(target.id)
    if not isinstance(entry, dict):
        return set()
    delivered = entry.get("delivered")
    if not isinstance(delivered, dict):
        return set()
    return {str(arxiv_id) for arxiv_id in delivered.keys()}


def bootstrap_target(
    state: dict[str, Any], target: Target, baseline_ids: list[str]
) -> dict[str, Any]:
    """Marks a target as bootstrapped with the supplied historical baseline.

    The supplied IDs are sorted deterministically and stored only as keys; no
    paper content is persisted.
    """
    targets = dict(state["targets"])
    targets[target.id] = {
        "bootstrapped": True,
        "target_fingerprint": target.fingerprint,
        "delivered": {
            arxiv_id: {
                "delivered_at": utc_now_iso(),
                "message_id": None,
                "bootstrap": True,
            }
            for arxiv_id in sorted(set(baseline_ids))
        },
        "updated_at": utc_now_iso(),
    }
    return {"version": STATE_VERSION, "targets": targets}


def reset_target(
    state: dict[str, Any], target: Target, baseline_ids: list[str]
) -> dict[str, Any]:
    """Replaces an existing target entry with a fresh bootstrap.

    Used when a target's receive identity changed and the operator explicitly
    confirmed the ``--replace-target`` reset.
    """
    return bootstrap_target(state, target, baseline_ids)


def record_delivery(
    state: dict[str, Any],
    target: Target,
    arxiv_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Records one successful delivery for a target."""
    targets = dict(state["targets"])
    entry = dict(targets.get(target.id, {}))
    entry.setdefault("bootstrapped", True)
    entry["target_fingerprint"] = target.fingerprint
    delivered = dict(entry.get("delivered") or {})
    delivered[arxiv_id] = {
        "delivered_at": utc_now_iso(),
        "message_id": message_id,
    }
    entry["delivered"] = delivered
    entry["updated_at"] = utc_now_iso()
    targets[target.id] = entry
    return {"version": STATE_VERSION, "targets": targets}
