"""Atomic, per-target notification state.

The state file lives outside ``public`` so that it is never published to GitHub
Pages.  Each target stores only a SHA-256 fingerprint of its receive identity
plus the set of *recently* delivered arXiv IDs.  The real ``receive_id`` is
never written to disk.

Design (revised): instead of keeping an unbounded list of every arXiv ID ever
pushed, the ``delivered`` map is a rolling window capped by
``DELIVERED_RETENTION``.  The job of "which papers are new this round" is owned
by the upstream pipeline (``main.py``), which writes a small
``pending_push.json`` cache containing only this run's new IDs.  ``service.py``
pops that cache, pushes, then ``record_delivery`` trims the rolling window so
the on-disk state stays bounded.

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

#: Maximum number of recently-delivered arXiv IDs kept per target.  Anything
#: beyond this is pruned on every write; the set is intended only as a safety
#: net against duplicate pushes within a short window, not as a full delivery
#: history.  Older entries can always be reconstructed from the published
#: papers JSON plus the live ``receive_id`` chat log.
DELIVERED_RETENTION = 200

#: Per-target monotonically-increasing admission counter.  Every recorded or
#: bootstrapped entry stamps its slot with the next ``seq`` value, and
#: pruning ranks by (``delivered_at``, ``seq``) descending so the most
#: recently-admitted ID is always kept even when multiple writes land inside
#: the same ``delivered_at`` second.  This avoids losing a brand-new entry
#: to timestamp ties during a fast bootstrap+send sequence.
SEQ_KEY = "seq"

#: Default content for the committed empty-state file.  An empty ``targets``
#: object is *not* treated as bootstrapped by ``state.py``.
EMPTY_STATE: dict[str, Any] = {"version": STATE_VERSION, "targets": {}}


def _next_delivered_seq(entry: dict[str, Any]) -> int:
    """Returns the next ``seq`` value for a target's ``delivered`` map.

    Scans the existing entries for the highest ``seq`` and adds one, so the
    counter survives pruning and never collides with previously-written
    values.  Starts at 1 on first use.
    """
    delivered = (entry or {}).get("delivered") or {}
    highest = 0
    for slot in delivered.values():
        if isinstance(slot, dict):
            try:
                highest = max(highest, int(slot.get(SEQ_KEY, 0)))
            except (TypeError, ValueError):
                continue
    return highest + 1


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
    """Returns the *retained* set of arXiv IDs recently delivered to a target.

    Note: with the rolling-window design this set is only the recent slice kept
    for duplicate-push protection.  Callers should not treat absence from this
    set as proof that a paper has never been pushed historically; use the
    ``pending_push.json`` cache as the authoritative "new this round" list
    instead.
    """
    entry = state["targets"].get(target.id)
    if not isinstance(entry, dict):
        return set()
    delivered = entry.get("delivered")
    if not isinstance(delivered, dict):
        return set()
    return {str(arxiv_id) for arxiv_id in delivered.keys()}


def _prune_delivered(delivered: dict[str, Any]) -> dict[str, Any]:
    """Keeps only the most recent ``DELIVERED_RETENTION`` delivered entries.

    Entries are ranked by ``(delivered_at, seq)`` in descending order, where
    ``seq`` is a monotonically-increasing admission counter assigned by
    ``bootstrap_target`` / ``record_delivery``.  The double key guarantees
    that when several writes share the same ``delivered_at`` second the
    *most recently admitted* entry still wins a tie, so appending a brand-
    new arXiv ID to a full window can never drop it back out.  Pruning is a
    no-op when the map is already within the retention window.
    """
    if len(delivered) <= DELIVERED_RETENTION:
        return delivered

    def _rank(item: tuple[str, Any]) -> tuple[str, int]:
        slot = item[1] if isinstance(item[1], dict) else {}
        delivered_at = str(slot.get("delivered_at", "") or "")
        try:
            seq = int(slot.get(SEQ_KEY, 0))
        except (TypeError, ValueError):
            seq = 0
        return delivered_at, seq

    ordered = sorted(delivered.items(), key=_rank, reverse=True)
    return dict(ordered[:DELIVERED_RETENTION])


def bootstrap_target(
    state: dict[str, Any], target: Target, baseline_ids: list[str]
) -> dict[str, Any]:
    """Marks a target as bootstrapped with the supplied historical baseline.

    The supplied IDs are sorted deterministically and stored only as keys; no
    paper content is persisted.  Only the most recent ``DELIVERED_RETENTION``
    IDs are kept so that bootstrap against a large existing corpus does not
    bloat the state file; older historical deliveries are intentionally not
    tracked, since the rolling window only needs to protect the next few runs
    from duplicate pushes.
    """
    delivered: dict[str, Any] = {}
    # Stamp each entry with a monotonically-increasing ``seq`` so the pruner
    # can break ``delivered_at`` ties deterministically.  IDs are inserted in
    # ascending lexicographic order so the lex-largest IDs end up with the
    # highest ``seq`` values and survive a bootstrap-time prune.
    for seq, arxiv_id in enumerate(sorted(set(baseline_ids)), start=1):
        delivered[arxiv_id] = {
            "delivered_at": utc_now_iso(),
            "message_id": None,
            "bootstrap": True,
            SEQ_KEY: seq,
        }
    delivered = _prune_delivered(delivered)
    targets = dict(state["targets"])
    targets[target.id] = {
        "bootstrapped": True,
        "target_fingerprint": target.fingerprint,
        "delivered": delivered,
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
    """Records one successful delivery for a target and prunes the window."""
    targets = dict(state["targets"])
    entry = dict(targets.get(target.id, {}))
    entry.setdefault("bootstrapped", True)
    entry["target_fingerprint"] = target.fingerprint
    delivered = dict(entry.get("delivered") or {})
    delivered[arxiv_id] = {
        "delivered_at": utc_now_iso(),
        "message_id": message_id,
        # Brand-new admission always gets the next ``seq`` value, so when the
        # window is full the pruner keeps this entry and drops the oldest
        # instead of evicting the just-recorded paper on a timestamp tie.
        SEQ_KEY: _next_delivered_seq(entry),
    }
    entry["delivered"] = _prune_delivered(delivered)
    entry["updated_at"] = utc_now_iso()
    targets[target.id] = entry
    return {"version": STATE_VERSION, "targets": targets}

