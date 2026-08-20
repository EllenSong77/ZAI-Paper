"""Atomic, per-target notification state.

The state file lives outside ``public`` so that it is never published to GitHub
Pages. Each target stores a SHA-256 fingerprint, a complete baseline of papers
already handled, and a bounded delivery audit window. The real ``receive_id``
is never written to disk.

The Git-persisted ``pending_push.json`` queue decides what needs delivery.
``baseline_ids`` provides permanent per-target idempotency, while the
``delivered`` map is a rolling audit window capped by
``DELIVERED_RETENTION``.

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

#: Maximum number of recently delivered arXiv IDs kept per target for audit
#: and diagnostics. Permanent idempotency is provided by ``baseline_ids``.
DELIVERED_RETENTION = 200

#: Per-target monotonically increasing delivery counter. Every recorded send
#: stamps its slot with the next ``seq`` value. Pruning ranks by
#: (``delivered_at``, ``seq``) so timestamp ties cannot discard a new entry.
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


def baseline_ids(state: dict[str, Any], target: Target) -> set[str]:
    """Returns every arXiv ID this target has already received.

    This is the "seen set": the bootstrap history plus every successful
    send (see :func:`record_delivery`, which admits sent IDs into the
    baseline). It grows exactly as fast as the corpus itself, which the
    repository already maintains, so it never outpaces the papers JSON.
    The fallback diff ("corpus minus seen set") is therefore idempotent
    forever: a quiet CI day with no pending queue re-pushes nothing, and a
    recovery after a lost queue still delivers only to targets that have not
    received the batch.

    For entries written before the baseline split it falls back to the
    legacy delivered map's bootstrap markers (which a pre-split pruner
    may have capped -- re-bootstrap with ``--replace-target`` when
    upgrading an old state whose corpus exceeded the retention window).
    A ``baseline_ids`` field of the wrong type fails closed with
    :class:`StateError` instead of silently acting on an empty set.
    """
    entry = state["targets"].get(target.id)
    if not isinstance(entry, dict):
        return set()
    if "baseline_ids" in entry:
        raw = entry["baseline_ids"]
        if not isinstance(raw, list):
            raise StateError(
                f"target '{target.id}' has a malformed baseline_ids field "
                f"({type(raw).__name__}); refusing to compute a fallback "
                "diff from an unknown seen-set"
            )
        # Element-level check: a nested list (e.g. ["id", ["other"]]) would
        # otherwise str()-coerce into garbage here and crash record_delivery
        # with a bare TypeError AFTER the card has already been sent.
        for arxiv_id in raw:
            if not isinstance(arxiv_id, str):
                raise StateError(
                    f"target '{target.id}' baseline_ids contains a "
                    f"non-string entry ({type(arxiv_id).__name__}); "
                    "refusing to compute a fallback diff from a corrupted "
                    "seen-set"
                )
        return set(raw)
    # Legacy entry (pre-split): bootstrap markers lived in the delivered map.
    delivered = entry.get("delivered")
    if not isinstance(delivered, dict):
        return set()
    return {
        str(arxiv_id)
        for arxiv_id, slot in delivered.items()
        if isinstance(slot, dict) and slot.get("bootstrap")
    }


def sent_ids(state: dict[str, Any], target: Target) -> set[str]:
    """Returns arXiv IDs that were *actually sent* to this target.

    Only entries with a non-null ``message_id`` count. This bounded set is
    used for reporting; delivery idempotency relies on the complete baseline.
    """
    entry = state["targets"].get(target.id)
    if not isinstance(entry, dict):
        return set()
    delivered = entry.get("delivered")
    if not isinstance(delivered, dict):
        return set()
    return {
        str(arxiv_id)
        for arxiv_id, slot in delivered.items()
        if isinstance(slot, dict) and slot.get("message_id")
    }


def delivered_ids(state: dict[str, Any], target: Target) -> set[str]:
    """Returns the *retained* set of arXiv IDs recently delivered to a target.

    This set is only a recent audit slice. Absence does not prove that a paper
    was never handled; callers must use :func:`baseline_ids` for that decision.
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

    The full baseline is stored verbatim in ``baseline_ids`` (sorted, no
    truncation): the fallback diff ("whole corpus minus baseline") needs
    every historical ID, or papers older than the rolling window would be
    misjudged as new and mass-pushed on the next cache-less run.

    The ``delivered`` rolling window starts empty at bootstrap because it only
    records real sends. ``baseline_ids`` remains the authoritative idempotency
    set for both queued delivery and the full-corpus fallback.
    """
    targets = dict(state["targets"])
    targets[target.id] = {
        "bootstrapped": True,
        "target_fingerprint": target.fingerprint,
        "baseline_ids": sorted(set(baseline_ids)),
        "delivered": {},
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
    """Records one successful delivery for a target and prunes the window.

    The ID is admitted into BOTH stores:
    - ``baseline_ids`` (the seen set): makes queued sends and fallback diffs
      idempotent. It grows with the corpus and is never pruned.
    - ``delivered`` (the rolling window): recent delivery metadata for
      reporting and diagnostics, capped by ``DELIVERED_RETENTION``.

    Legacy entries without a ``baseline_ids`` field get one created here,
    seeded from their bootstrap markers, so old state files self-heal on
    their first successful send.
    """
    targets = dict(state["targets"])
    entry = dict(targets.get(target.id, {}))
    entry.setdefault("bootstrapped", True)
    entry["target_fingerprint"] = target.fingerprint
    # Self-heal legacy entries: seed the seen set from bootstrap markers.
    # baseline_ids() validates shape and element types, raising StateError
    # before this send's state is persisted if the entry is poisoned.
    if "baseline_ids" not in entry:
        entry["baseline_ids"] = sorted(baseline_ids(state, target))
    seen = list(entry["baseline_ids"])
    # Defense in depth: even a hand-edited field that somehow survived the
    # reader above cannot be written back with non-string elements.
    for existing in seen:
        if not isinstance(existing, str):
            raise StateError(
                f"target '{target.id}' baseline_ids contains a non-string "
                f"entry ({type(existing).__name__}); refusing to record"
            )
    if arxiv_id not in seen:
        seen.append(arxiv_id)
    entry["baseline_ids"] = sorted(seen)
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
