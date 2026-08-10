"""Configuration parsing for the Feishu notification package.

Reads Feishu credentials, target definitions, and file paths from environment
variables.  Targets are validated strictly so that downstream code can rely on
non-empty, uniquely identified receive targets.  Sensitive ``receive_id``
values are never printed; only their SHA-256 fingerprint is recorded.

Author:
    Ellen Song <jiaqi.song@z.ai>
    Modified by Wethepe <dongyangyan@stu.pku.edu.cn>
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Receive-id types accepted by the Feishu send-message API.
ALLOWED_RECEIVE_ID_TYPES = frozenset(
    {"chat_id", "open_id", "user_id", "union_id", "email"}
)

DEFAULT_PAPERS_PATH = "public/data/zhipu_papers.json"
DEFAULT_STATE_PATH = ".notification-state/feishu.json"
#: Project Pages site used when no deployment URL is injected.
DEFAULT_SITE_URL = "https://ellensong77.github.io/ZAI-Paper/"


class ConfigError(ValueError):
    """Raised when notification configuration is missing or invalid."""


@dataclass(frozen=True)
class Target:
    """One Feishu delivery target.

    ``receive_id`` is the only sensitive field here; callers must never embed
    it in logs or in the persisted state file.  Use ``fingerprint`` instead.
    """

    id: str
    name: str
    receive_id_type: str
    receive_id: str

    @property
    def fingerprint(self) -> str:
        """Stable SHA-256 fingerprint of the receive target identity."""
        serialized = json.dumps(
            {"receive_id_type": self.receive_id_type, "receive_id": self.receive_id},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Settings:
    """Resolved notifications configuration."""

    app_id: str
    app_secret: str
    targets: tuple[Target, ...]
    papers_path: Path
    state_path: Path
    site_url: str


def fingerprint_receive_id(receive_id_type: str, receive_id: str) -> str:
    """Returns the SHA-256 fingerprint for a receive target identity."""
    serialized = json.dumps(
        {"receive_id_type": receive_id_type, "receive_id": receive_id},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parse_targets(raw: str) -> tuple[Target, ...]:
    """Parses and strictly validates ``FEISHU_TARGETS_JSON``.

    Raises:
        ConfigError: on malformed JSON, non-array payloads, duplicate IDs,
            unknown receive-id types, or missing/empty fields.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigError("FEISHU_TARGETS_JSON is not valid JSON") from error
    if not isinstance(data, list):
        raise ConfigError("FEISHU_TARGETS_JSON must be a JSON array")
    if not data:
        raise ConfigError("FEISHU_TARGETS_JSON must contain at least one target")

    targets: list[Target] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ConfigError(f"target #{index} is not an object")
        target_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        receive_id_type = str(item.get("receive_id_type", "")).strip()
        receive_id = str(item.get("receive_id", "")).strip()
        if not target_id:
            raise ConfigError(f"target #{index} is missing a non-empty id")
        if target_id in seen_ids:
            raise ConfigError(f"duplicate target id: {target_id}")
        seen_ids.add(target_id)
        if not name:
            raise ConfigError(f"target '{target_id}' is missing a non-empty name")
        if receive_id_type not in ALLOWED_RECEIVE_ID_TYPES:
            raise ConfigError(
                f"target '{target_id}' has unsupported receive_id_type "
                f"'{receive_id_type}'"
            )
        if not receive_id:
            raise ConfigError(f"target '{target_id}' has an empty receive_id")
        targets.append(
            Target(
                id=target_id,
                name=name,
                receive_id_type=receive_id_type,
                receive_id=receive_id,
            )
        )
    return tuple(targets)


def _env(name: str) -> str:
    """Returns a stripped environment value (empty string when unset)."""
    return os.getenv(name, "").strip()


def load_settings() -> Settings:
    """Loads and validates all notification settings from the environment.

    Network credentials are only required by callers that actually talk to
    Feishu (``smoke-test``, ``send``).  ``dry-run`` and ``bootstrap`` accept an
    empty ``FEISHU_APP_ID``/``FEISHU_APP_SECRET``.
    """
    targets_raw = _env("FEISHU_TARGETS_JSON")
    if not targets_raw:
        raise ConfigError("FEISHU_TARGETS_JSON is not set")
    targets = parse_targets(targets_raw)

    papers_path_value = _env("FEISHU_PAPERS_PATH") or DEFAULT_PAPERS_PATH
    state_path_value = _env("FEISHU_STATE_PATH") or DEFAULT_STATE_PATH
    site_url = _env("FEISHU_SITE_URL") or DEFAULT_SITE_URL

    papers_path = Path(papers_path_value)
    if not papers_path.is_absolute():
        papers_path = ROOT / papers_path
    state_path = Path(state_path_value)
    if not state_path.is_absolute():
        state_path = ROOT / state_path

    return Settings(
        app_id=_env("FEISHU_APP_ID"),
        app_secret=_env("FEISHU_APP_SECRET"),
        targets=targets,
        papers_path=papers_path,
        state_path=state_path,
        site_url=site_url.rstrip("/") + "/",
    )


def require_credentials(settings: Settings) -> tuple[str, str]:
    """Returns credentials, raising a clean error when they are missing."""
    if not settings.app_id or not settings.app_secret:
        raise ConfigError(
            "FEISHU_APP_ID and FEISHU_APP_SECRET are required for this command"
        )
    return settings.app_id, settings.app_secret
