"""One-shot backfill script: translates existing rows' English ``abstract``
into Chinese ``translated_abstract`` via GLM and writes them back to the
public papers JSON.

Run from the project root:

    python backfill_translated_abstracts.py

Requires ZHIPU_API_KEY in the environment (loaded from .env if present).
Already-translated rows are skipped; failures are logged but do not roll back
successful translations. The script writes a temp file then atomically replaces
the destination.

Author:
    Ellen Song <jiaqi.song@z.ai>
    Modified by Wethepe <dongyangyan@stu.pku.edu.cn>
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
GLM_API = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
#: Rows per LLM call (matches main.py's LLM_BATCH_SIZE; tuning safe downstream).
BATCH_SIZE = 15
PAPERS_PATH = ROOT / "public/data/zhipu_papers.json"


def _load_env(env_path: Path) -> None:
    """Reads KEY=VALUE pairs from a .env file into os.environ if present."""
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        # Strip one layer of surrounding single or double quotes if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _classify_model(value: str | None) -> str:
    """Resolves the GLM classifier model mirroring main.py's default."""
    raw = (value or "").strip()
    return raw or "glm-5-turbo"


def _build_prompt(rows: list[dict]) -> str:
    """Builds the abstract-translation prompt for one batch."""
    items = [{"arxiv_id": row["arxiv_id"], "abstract": row.get("abstract", "")[:1400]} for row in rows]
    return (
        "把以下每篇论文的英文摘要翻译成中文。要求：\n"
        "- 仅输出 JSON 数组，与输入顺序一致；\n"
        "- 每一项是 "
        '{"arxiv_id":"编号","translated_abstract":"中文翻译"}；'
        "\n- 翻译流畅、专业、保留学术含义；\n"
        "- 已是中文或为空时，translated_abstract 直接返回原文或空字符串；\n"
        "- 不要补充评论或解读。\n\n"
        f"{json.dumps(items, ensure_ascii=False)}"
    )


def _parse_json_array(content: str) -> list[dict]:
    """Tolerantly extracts a JSON array from an LLM response."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no JSON array found in LLM response")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("LLM response is not a JSON array")
    return parsed


def translate_batch(
    session: requests.Session, api_key: str, model: str, rows: list[dict]
) -> dict[str, str]:
    """Translates one batch and returns {arxiv_id: translated_abstract}."""
    payload = {
        "model": model,
        "temperature": 0,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": _build_prompt(rows)}],
    }
    response = session.post(
        GLM_API,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    results = _parse_json_array(content)
    out: dict[str, str] = {}
    expected_ids = [row["arxiv_id"] for row in rows]
    id_to_zh = {str(item.get("arxiv_id")): str(item.get("translated_abstract", "")).strip() for item in results}
    for arxiv_id in expected_ids:
        out[arxiv_id] = id_to_zh.get(arxiv_id, "")
    return out


def main() -> int:
    _load_env(ROOT / ".env")
    api_key = os.getenv("ZHIPU_API_KEY", "").strip()
    model = _classify_model(os.getenv("ZHIPU_CLASSIFIER_MODEL"))
    if not api_key:
        print("ZHIPU_API_KEY missing — set it in .env or environment.", file=sys.stderr)
        return 2

    if not PAPERS_PATH.exists():
        print(f"papers file not found: {PAPERS_PATH}", file=sys.stderr)
        return 2

    # Optional limit on how many rows to translate this run, read from
    # BACKFILL_LIMIT env var. Useful for piloting translation quality on a
    # few rows before committing to a full 200+ run.
    limit_raw = os.getenv("BACKFILL_LIMIT", "").strip()
    limit = int(limit_raw) if limit_raw else 0

    data = json.loads(PAPERS_PATH.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    pending = [
        row
        for row in rows
        if row.get("abstract") and not row.get("translated_abstract")
    ]
    if limit > 0:
        pending = pending[:limit]
    print(
        f"total={len(rows)} pending={len(pending)} (skipping "
        f"{len(rows) - len(pending)} already translated)"
    )
    if not pending:
        print("nothing to do.")
        return 0

    session = requests.Session()
    session.headers["User-Agent"] = "ZAI-Paper-AbstractBackfill/1.0"
    translated = 0
    failures = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        for attempt in range(3):
            try:
                zh_map = translate_batch(session, api_key, model, batch)
                break
            except Exception as error:  # noqa: BLE001 - report and retry/abort
                if attempt == 2:
                    print(
                        f"  batch {start // BATCH_SIZE + 1} failed after 3 "
                        f"attempts: {type(error).__name__}: {error}",
                        file=sys.stderr,
                    )
                    zh_map = {}
                    break
                time.sleep(3)
        for row in batch:
            zh = zh_map.get(row["arxiv_id"], "")
            if zh:
                row["translated_abstract"] = zh
                translated += 1
            else:
                failures += 1
        done = min(start + BATCH_SIZE, len(pending))
        print(f"  translated {translated}/{len(pending)} (failures={failures})", flush=True)

    # Atomic write.
    temp = PAPERS_PATH.with_suffix(PAPERS_PATH.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(PAPERS_PATH)
    print(
        f"done: translated={translated} failures={failures} -> {PAPERS_PATH.name}",
        flush=True,
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
