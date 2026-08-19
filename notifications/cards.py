"""Feishu CardKit (JSON 2.0) card builders for paper notifications.

The Feishu send-message API accepts the CardKit 2.0 payload directly as the
``content`` JSON string of an ``interactive`` message (verified empirically).
This module builds the dynamic per-paper structure from the local JSON rows;
no CardKit template id, online template, or remote hosting is required.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

#: Maximum length of the authors string when rendered grey under a title.
MAX_AUTHORS_CHARS = 160
TOPIC_TAG_SEPARATOR = " "
#: CardKit header background template.
HEADER_TEMPLATE_BLUE = "blue"

#: Image shown above each paper's ordinal. From the user's CardKit source JSON.
PAPER_BADGE_IMG_KEY = "img_v3_0214b_216ba22c-2533-44c7-91be-a31c4d17304g"
PAPER_BADGE_FALLBACK_IMG_KEY = (
    "img_v3_02r5_d46c633a-f0e7-458a-959e-670863031d5g"
)

#: ``text_tag`` colors used to visually distinguish the two tag categories.
TAG_COLOR_PRIMARY = "blue"      # for the high-level 产品 / 非产品 "tag" field
TAG_COLOR_TOPIC = "purple"      # for the granular "topic_tags" research labels


def _utc_today() -> str:
    """Returns today's UTC date as ``YYYY-MM-DD``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _truncate(text: str | None, limit: int) -> str:
    """Single-line, length-limited preview (handles None / whitespace)."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _escape_md(text: str | None) -> str:
    """Neutralizes markdown/lark-XML metacharacters in LLM-supplied text.

    Titles and authors are interpolated into markdown elements where
    ``<font>``, ``<text_tag>``, and emphasis characters are live syntax;
    a paper title containing them would otherwise break rendering. The
    escaping is deliberately minimal (no HTML entity substitution -- the
    card renderer is not a browser) and applied only to interpolated
    payloads, never to our own markup templates.
    """
    cleaned = _truncate(text, 10_000)  # bound well above any real field
    for ch in ("\\", "`", "*", "_", "~", "[", "]", "<", ">"):
        cleaned = cleaned.replace(ch, "\\" + ch)
    return cleaned


def _plain_text(content: str) -> dict[str, Any]:
    """Returns a CardKit plain_text text node."""
    return {"tag": "plain_text", "content": content}


def _markdown(content: str, *, align: str | None = None,
              size: str | None = None) -> dict[str, Any]:
    """Returns a CardKit markdown element."""
    element: dict[str, Any] = {"tag": "markdown", "content": content}
    if align is not None:
        element["text_align"] = align
    if size is not None:
        element["text_size"] = size
    return element


def _text_tags(values: list[str], color: str = "neutral") -> str:
    """Serializes a list of tag values into inline ``<text_tag>`` markdown."""
    parts = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            parts.append(f"<text_tag color='{color}'>{cleaned}</text_tag>")
    return " ".join(parts)


def _paper_columns(row: dict[str, Any], index: int, total: int) -> list[dict[str, Any]]:
    """Builds the two CardKit ``column`` dicts for one paper.

    Left column: badge image + blue ordinal. Right column: titles + meta +
    two-color tag chips + collapsible abstract + a row of arXiv/PDF buttons.
    The PDF button only renders when ``pdf_url`` is present.
    """
    chinese_title = str(row.get("translated_title", "")).strip()
    english_title = str(row.get("title", "")).strip()
    authors = _truncate(row.get("authors"), MAX_AUTHORS_CHARS)
    published = str(row.get("published", "")).strip() or _utc_today()
    arxiv_url = str(row.get("arxiv_url", "")).strip()
    pdf_url = str(row.get("pdf_url", "")).strip()
    abstract = " ".join((row.get("abstract") or "").split())

    # Left column: badge image + ordinal, both center-aligned.
    left_elements = [
        {
            "tag": "img",
            "img_key": PAPER_BADGE_IMG_KEY,
            "fallback_img_key": PAPER_BADGE_FALLBACK_IMG_KEY,
            "transparent": False,
            "scale_type": "fit_horizontal",
            "corner_radius": "8px",
            "margin": "0px 0px 8px 0px",
        },
        _markdown(f"**<font color='blue'>{index:02d}</font>**", align="center"),
    ]

    # Right column: titles.
    right_elements: list[dict[str, Any]] = []
    title_md = english_title or chinese_title or str(row.get("arxiv_id", ""))
    if title_md:
        right_elements.append(
            _markdown(f"**<font color='blue'>{_escape_md(title_md)}</font>**")
        )
    if chinese_title and chinese_title != english_title:
        right_elements.append(_markdown(_escape_md(chinese_title)))
    if authors:
        # Smaller, italic English-style author line in grey.
        right_elements.append(
            _markdown(
                f"<font color='grey'>作者：*{_escape_md(authors)}*</font>",
                size="notation",
            )
        )

    # Two-color tag chips line: grey date + blue tag + purple research tags.
    chips_parts: list[str] = [f"<font color='grey'>{published}</font>"]
    row_tag = (row.get("tag") or "").strip()
    if row_tag:
        # Primary classification chip in blue.
        chips_parts.append(_text_tags([row_tag], color=TAG_COLOR_PRIMARY))
    topic_tags = row.get("topic_tags")
    if isinstance(topic_tags, list) and topic_tags:
        clean_topics = [str(t).strip() for t in topic_tags if str(t).strip()]
        if clean_topics:
            # Granular research tags in purple.
            chips_parts.append(_text_tags(clean_topics, color=TAG_COLOR_TOPIC))
    right_elements.append(_markdown(" ".join(chips_parts)))

    # Abstract area: a collapsible_panel titled "查看论文摘要" (collapsed by
    # default) whose body shows the Chinese abstract only. The original
    # English abstract is dropped from the card -- the audience is primarily
    # Chinese-speaking, so the Chinese translation is the more useful reading
    # copy. When translated_abstract is missing for a row, the panel falls
    # back to the English abstract so the body is never empty.
    #
    # Degradation rules:
    #   - abstract AND translated_abstract both empty  -> render no panel;
    #   - translated_abstract empty, abstract present  -> single EN div;
    #   - translated_abstract present                  -> single ZH div.
    translated_abstract = " ".join(
        (row.get("translated_abstract") or "").split()
    )
    body_text = translated_abstract or abstract
    if body_text:
        right_elements.append({
            "tag": "collapsible_panel",
            "border": {"color": "grey", "corner_radius": "6px"},
            "expanded": False,
            "header": {
                "padding": "4px",
                "position": "top",
                "title": {"content": "查看论文摘要", "tag": "plain_text"},
                "width": "fill",
            },
            "margin": "6px 0px 0px 0px",
            "padding": "8px",
            "elements": [{
                "tag": "div",
                "margin": "0px",
                "width": "fill",
                "text": {
                    "content": _truncate(body_text, 600),
                    "tag": "plain_text",
                    "text_align": "left",
                    "text_color": "default",
                    "text_size": "notation",
                },
            }],
        })

    # Buttons row: arXiv (primary) + PDF (when available). CardKit lays out
    # sibling elements vertically by default, so to put the two buttons on the
    # same horizontal row they are wrapped in a nested ``column_set``. Using
    # ``width: "auto"`` for each column (and the button itself) avoids the
    # buttons being stretched to half the available width — they take only the
    # space the label needs and stay side by side on the left.
    button_columns: list[dict[str, Any]] = []
    if arxiv_url:
        button_columns.append({
            "tag": "column",
            "width": "auto",
            "elements": [{
                "tag": "button",
                "text": {"content": "查看 arXiv", "tag": "plain_text"},
                "type": "primary",
                "size": "small",
                "width": "default",
                "margin": "4px 0px 4px 0px",
                "behaviors": [{"type": "open_url", "default_url": arxiv_url}],
            }],
        })
    if pdf_url:
        button_columns.append({
            "tag": "column",
            "width": "auto",
            "elements": [{
                "tag": "button",
                "text": {"content": "查看 PDF", "tag": "plain_text"},
                "type": "default",
                "size": "small",
                "width": "default",
                "margin": "4px 0px 4px 8px",
                "behaviors": [{"type": "open_url", "default_url": pdf_url}],
            }],
        })
    if button_columns:
        right_elements.append({
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "margin": "4px 0px 0px 0px",
            "columns": button_columns,
        })

    return [
        {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "elements": left_elements,
        },
        {
            "tag": "column",
            "width": "weighted",
            "weight": 4,
            "vertical_spacing": "4px",
            "elements": right_elements,
        },
    ]


def _paper_block_elements(row: dict[str, Any], index: int, total: int) -> list[dict[str, Any]]:
    """One paper = ``column_set`` + (optional) trailing divider."""
    return [
        {
            "tag": "column_set",
            "flex_mode": "stretch",
            "horizontal_spacing": "12px",
            "margin": "0px",
            "columns": _paper_columns(row, index, total),
        },
    ]


def _header(count: int) -> dict[str, Any]:
    """CardKit 2.0 header with title + subtitle (e.g. ``2026-08-07 · 新增 N 篇``)."""
    title = "Z.AI 论文更新" if count else "ZAI-Paper 通知测试"
    subtitle = f"{_utc_today()} · 新增 {count} 篇" if count else "ZAI-Paper 通知测试"
    return {
        "padding": "12px",
        "template": HEADER_TEMPLATE_BLUE,
        "title": _plain_text(title),
        "subtitle": _plain_text(subtitle),
    }


def build_papers_card(rows: list[dict[str, Any]], site_url: str) -> dict[str, Any]:
    """Builds a single CardKit 2.0 card containing every paper.

    Each paper renders as a two-column row (ordinal / titles + meta + collapsible
    abstract + arXiv button) followed by a divider. A trailing footer carries
    a primary ``查看完整论文列表`` button to the site. ``site_url`` only
    influences that last button; the rest of the card is independent of it.
    """
    rows_list = list(rows)
    count = len(rows_list)
    elements: list[dict[str, Any]] = []
    for index, row in enumerate(rows_list, start=1):
        elements.extend(_paper_block_elements(row, index, count))
        if index < count:
            elements.append({"tag": "hr", "margin": "0px"})
    elements.append(
        _markdown(
            f"<font color='grey'>由 ZAI-Paper 自动整理提供，仅展示本次新增论文 · {_utc_today()}</font>",
            align="left",
            size="notation",
        )
    )
    if site_url:
        elements.append({
            "tag": "button",
            "element_id": "view_all_papers",
            "text": {"content": "查看完整论文列表", "tag": "plain_text"},
            "type": "primary_filled",
            "size": "medium",
            "width": "fill",
            "margin": "4px 0px 4px 0px",
            "behaviors": [{"type": "open_url", "default_url": site_url}],
        })
    return {
        "schema": "2.0",
        "header": _header(count),
        "body": {"elements": elements},
    }


def build_smoke_test_card(site_url: str) -> dict[str, Any]:
    """Builds a clearly-marked CardKit 2.0 smoke-test card (no real paper)."""
    elements: list[dict[str, Any]] = [
        _markdown("这是 ZAI-Paper 通知模块的测试卡片，不是真实论文。"),
    ]
    if site_url:
        elements.append({
            "tag": "button",
            "text": {"content": "访问 ZAI-Paper", "tag": "plain_text"},
            "type": "primary_filled",
            "size": "medium",
            "width": "fill",
            "behaviors": [{"type": "open_url", "default_url": site_url}],
        })
    return {
        "schema": "2.0",
        "header": {
            "padding": "12px",
            "template": HEADER_TEMPLATE_BLUE,
            "title": _plain_text("ZAI-Paper 通知测试"),
            "subtitle": _plain_text(_utc_today()),
        },
        "body": {"elements": elements},
    }


def encode_content(card: dict[str, Any]) -> str:
    """Serializes a card object to the JSON string Feishu expects.

    The Feishu send-message API wraps the card JSON inside a string field, so
    callers must serialize it exactly once before adding it to the request.
    """
    return json.dumps(card, ensure_ascii=False)
