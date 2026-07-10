#!/usr/bin/env python3
"""Synchronizes Z.AI and Jie Tang papers from arXiv.

The script recalls papers with broad arXiv queries, applies deterministic
signals, asks GLM to resolve ambiguous matches and translate titles, and writes
the approved rows for the static website.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
ARXIV_API = "https://export.arxiv.org/api/query"
GLM_API = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
SEMANTIC_SCHOLAR_API = (
    "https://api.semanticscholar.org/graph/v1/paper/batch"
)
ATOM = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
OPENSEARCH = {"os": "http://a9.com/-/spec/opensearch/1.1/"}
MIN_YEAR = 2020
CACHE_VERSION = "zai-xin-lv-pairs-v7"
ARXIV_PAGE_SIZE = 200
ARXIV_ID_BATCH_SIZE = 100
ARXIV_RETRIES = 5
ARXIV_QUERY_DELAY_SECONDS = 4
DEFAULT_INCREMENTAL_OVERLAP_DAYS = 14
LLM_BATCH_SIZE = 15

PEOPLE = {
    "唐杰": "Jie Tang",
    "刘德兵": "Debing Liu",
    "张鹏": "Peng Zhang",
    "顾晓韬": "Xiaotao Gu",
    "刘潇": "Xiao Liu",
    "曾奥涵": "Aohan Zeng",
    "郑问笛": "Wendi Zheng",
    "杜政晓": "Zhengxiao Du",
    "黄明烈": "Minlie Huang",
    "张笑涵": "Xiaohan Zhang",
    "洪文逸": "Wenyi Hong",
    "吕鑫": "Xin Lv",
}
PRODUCT_ALIASES = (
    "GLM",
    "ChatGLM",
    "AutoGLM",
    "WebGLM",
    "CogView",
    "CogVideo",
    "CogVLM",
    "CogAgent",
    "CogCoM",
    "CogCartoon",
    "CodeGeeX",
    "CharacterGLM",
    "MathGLM",
)
SUPPORT_TITLE_TERMS = ("ZCube", "PhoneUse", "Phone-Use", "FastMoE")
# These links cannot be inferred reliably from arXiv metadata alone.
CURATED_SUPPORT_RELATIONS = {
    (
        "From ATOP to ZCube: Automated Topology Optimization Pipeline and A "
        "Highly Cost-Effective Network Topology for Large Model Training"
    ): (
        "ZCube is used by Zhipu as GLM inference-cluster network infrastructure"
    ),
    (
        "P-Tuning v2: Prompt Tuning Can Be Comparable to Fine-tuning "
        "Universally Across Scales and Tasks"
    ): (
        "P-Tuning is a foundation tuning technique for the GLM model family"
    ),
    "GPT Understands, Too": "P-Tuning supports adaptation of the GLM model family",
    "FastMoE: A Fast Mixture-of-Expert Training System": (
        "FastMoE provides distributed mixture-of-experts training infrastructure"
    ),
    (
        "Relay Diffusion: Unifying diffusion process across resolutions for "
        "image synthesis"
    ): (
        "Relay Diffusion is the generation technique used by CogView3"
    ),
    (
        "ImageReward: Learning and Evaluating Human Preferences for "
        "Text-to-Image Generation"
    ): (
        "ImageReward supports preference alignment for the CogView image-model line"
    ),
    (
        "WebRL: Training LLM Web Agents via Self-Evolving Online Curriculum "
        "Reinforcement Learning"
    ): (
        "WebRL supports the web-agent capabilities used by AutoGLM"
    ),
    "AndroidLab: Training and Systematic Benchmarking of Android Autonomous Agents": (
        "AndroidLab supports phone-agent training and evaluation for AutoGLM"
    ),
}
DIRECT_PRODUCT_TITLES = {
    "GPT Can Solve Mathematical Problems Without a Calculator",
}
SUPPORT_TAG_HINTS = (
    "zcube",
    "fastmoe",
    "phoneuse",
    "inference",
    "serving",
    "infrastructure",
    "training system",
    "mixture-of-expert",
    "mixture of experts",
    "reinforcement learning",
    "reward model",
    "benchmark",
    "evaluation",
    "alignment",
    "prompt tuning",
    "p-tuning",
    "phone-use",
    "phone use",
    "gui agent",
    "computer use",
)
TSINGHUA_MARKERS = ("THUDM", "Tsinghua", "Qinghua")
PAPER_TAGS = ("产品相关", "产品技术支持", "非产品相关")
LEGACY_TAGS = {"产品强相关": "产品相关", "学术输出": "非产品相关"}
TOPIC_TAGS = (
    "文本",
    "图像",
    "视频",
    "语音",
    "多模态",
    "代码",
    "智能体",
    "推理",
    "生成",
    "理解",
    "搜索",
    "检索",
    "推荐",
    "知识图谱",
    "图学习",
    "预训练",
    "后训练",
    "强化学习",
    "对齐",
    "微调",
    "蒸馏",
    "训练系统",
    "推理系统",
    "加速",
    "部署",
    "Infra",
    "模型",
    "框架",
    "数据集",
    "Benchmark",
    "评测",
    "安全",
    "综述",
)
INSTITUTION_ALIASES = {
    "tsinghua": "Tsinghua University",
    "tsinghua university": "Tsinghua University",
    "zhipu ai": "Z.AI",
    "z.ai": "Z.AI",
    "stern school of business, new york university": "New York University",
    "bytedance ai lab": "ByteDance",
}
JIE_TANG_CORE_COAUTHORS = (
    "Yuxiao Dong",
    "Juanzi Li",
    "Ming Ding",
    "Zhiyuan Liu",
    "Maosong Sun",
    "Lei Hou",
    "Zhilin Yang",
    "Jian Tang",
    *tuple(author for author in PEOPLE.values() if author != "Jie Tang"),
)
JIE_TANG_RESEARCH_CATEGORIES = {
    "cs.AI",
    "cs.CL",
    "cs.CV",
    "cs.DB",
    "cs.DC",
    "cs.HC",
    "cs.IR",
    "cs.LG",
    "cs.SE",
    "cs.SI",
}

# These four two-person papers were manually verified after the LLM confused
# same-name authors with team membership.
VERIFIED_TITLES = {
    "Training-Free Vector Quantization via Gaussian VAEs",
    "ZeroFlow: Overcoming Catastrophic Forgetting is Easier than You Think",
    "DreamPolish: Domain Score Distillation With Progressive Geometry Generation",
    (
        "Relay Diffusion: Unifying diffusion process across resolutions for "
        "image synthesis"
    ),
}

# External papers that mention or deploy Z.AI products but are not Z.AI output.
EXCLUDED_ARXIV_IDS = {
    "2607.02518",  # China Unicom GLM-5 serving-parameter report.
    "2601.03267",  # OpenAI GPT-5 System Card with a same-name Jie Tang.
}


@dataclass(frozen=True)
class Paper:
    """Normalized metadata for one arXiv paper."""

    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    published: datetime
    abs_url: str
    pdf_url: str
    author_affiliations: tuple[tuple[str, str], ...] = ()
    categories: tuple[str, ...] = ()
    primary_category: str = ""


def load_env(path: Path) -> None:
    """Loads missing environment variables from a simple dotenv file."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def clean(text: str | None) -> str:
    """Collapses whitespace in metadata returned by arXiv."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_string_list(value: object, limit: int | None = None) -> list[str]:
    """Returns unique, non-empty strings while preserving input order."""
    if not isinstance(value, (list, tuple)):
        return []
    normalized = []
    seen = set()
    for item in value:
        text = clean(str(item))
        key = text.casefold()
        if not text or key in seen:
            continue
        normalized.append(text)
        seen.add(key)
        if limit is not None and len(normalized) >= limit:
            break
    return normalized


def normalize_topic_tags(value: object) -> list[str]:
    """Keeps up to five unique topic tags from the fixed public vocabulary."""
    allowed = set(TOPIC_TAGS)
    return [
        tag
        for tag in normalize_string_list(value)
        if tag in allowed
    ][:5]


def normalize_institutions(value: object, limit: int = 8) -> list[str]:
    """Normalizes common institution aliases for consistent display."""
    institutions = [
        INSTITUTION_ALIASES.get(item.casefold(), item)
        for item in normalize_string_list(value)
    ]
    return normalize_string_list(institutions, limit=limit)


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    retries: int = 3,
    **kwargs,
) -> requests.Response:
    """Sends an HTTP request with bounded backoff for transient failures."""
    retry_statuses = {429, 500, 502, 503, 504}
    for attempt in range(retries):
        try:
            response = session.request(method, url, **kwargs)
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        if response.status_code in retry_statuses and attempt < retries - 1:
            retry_after = response.headers.get("Retry-After")
            wait = (
                int(retry_after)
                if retry_after and retry_after.isdigit()
                else 10 * (attempt + 1)
            )
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("request retry loop ended unexpectedly")


def parse_feed(xml: str) -> list[Paper]:
    """Parses an arXiv Atom feed into normalized papers."""
    papers = []
    for entry in ET.fromstring(xml).findall("atom:entry", ATOM):
        raw_id = clean(entry.findtext("atom:id", namespaces=ATOM))
        arxiv_id = re.sub(r"v\d+$", "", raw_id.split("/abs/")[-1])
        links = {
            link.get("title", link.get("rel", "")): link.get("href", "")
            for link in entry.findall("atom:link", ATOM)
        }
        author_elements = entry.findall("atom:author", ATOM)
        authors = tuple(
            clean(author.findtext("atom:name", namespaces=ATOM))
            for author in author_elements
        )
        author_affiliations = tuple(
            (clean(author.findtext("atom:name", namespaces=ATOM)), affiliation)
            for author in author_elements
            if (
                affiliation := clean(
                    author.findtext("arxiv:affiliation", namespaces=ATOM)
                )
            )
        )
        primary_category = entry.find("arxiv:primary_category", ATOM)
        papers.append(
            Paper(
                arxiv_id=arxiv_id,
                title=clean(entry.findtext("atom:title", namespaces=ATOM)),
                authors=authors,
                abstract=clean(entry.findtext("atom:summary", namespaces=ATOM)),
                published=datetime.fromisoformat(
                    entry.findtext("atom:published", namespaces=ATOM).replace(
                        "Z", "+00:00"
                    )
                ),
                abs_url=(links.get("alternate") or raw_id).replace(
                    "http://", "https://"
                ),
                pdf_url=(
                    links.get("pdf") or f"https://arxiv.org/pdf/{arxiv_id}"
                ).replace("http://", "https://"),
                author_affiliations=author_affiliations,
                categories=tuple(
                    category.get("term", "")
                    for category in entry.findall("atom:category", ATOM)
                    if category.get("term")
                ),
                primary_category=(
                    primary_category.get("term", "")
                    if primary_category is not None
                    else ""
                ),
            )
        )
    return papers


def arxiv_date(value: datetime) -> str:
    """Formats a timestamp for arXiv's submittedDate query syntax."""
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M")


def date_limited_query(query: str, since: datetime, until: datetime) -> str:
    """Restricts an arXiv query to a UTC submission window."""
    return (
        f"({query}) AND submittedDate:[{arxiv_date(since)} TO "
        f"{arxiv_date(until)}]"
    )


def incremental_window(
    existing_papers: dict[str, Paper], now: datetime
) -> tuple[str, datetime, datetime]:
    """Builds an incremental window with overlap for delayed arXiv updates."""
    min_since = datetime(MIN_YEAR, 1, 1, tzinfo=timezone.utc)
    if not existing_papers:
        return "full-seed", min_since, now + timedelta(days=1)
    latest = max(paper.published for paper in existing_papers.values())
    overlap_days = int(
        os.getenv(
            "ARXIV_INCREMENTAL_OVERLAP_DAYS",
            DEFAULT_INCREMENTAL_OVERLAP_DAYS,
        )
    )
    since = latest.astimezone(timezone.utc) - timedelta(
        days=overlap_days
    )
    since = max(since, min_since)
    return f"since-{since.date().isoformat()}", since, now + timedelta(days=1)


def search_window(
    mode: str, existing_papers: dict[str, Paper], now: datetime
) -> tuple[str, datetime, datetime]:
    """Returns the single arXiv window required by the selected sync mode."""
    if mode == "full" or not existing_papers:
        return (
            f"{MIN_YEAR}-present",
            datetime(MIN_YEAR, 1, 1, tzinfo=timezone.utc),
            now + timedelta(days=1),
        )
    return incremental_window(existing_papers, now)


def fetch_query(
    session: requests.Session,
    label: str,
    query: str,
    since: datetime,
    until: datetime,
) -> list[Paper]:
    """Fetches every page for one date-limited arXiv query."""
    papers = []
    offset = 0
    total = 1
    search_query = date_limited_query(query, since, until)
    while offset < total:
        response = request_with_retry(
            session,
            "GET",
            ARXIV_API,
            params={
                "search_query": search_query,
                "start": offset,
                "max_results": ARXIV_PAGE_SIZE,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            retries=ARXIV_RETRIES,
            timeout=60,
        )
        root = ET.fromstring(response.text)
        total = int(root.findtext("os:totalResults", "0", OPENSEARCH))
        page = parse_feed(response.text)
        if not page:
            break
        papers.extend(paper for paper in page if paper.published.year >= MIN_YEAR)
        offset += len(page)
        print(f"arXiv {label}: {offset}/{total}", flush=True)
        if offset < total:
            time.sleep(ARXIV_QUERY_DELAY_SECONDS)
    return papers


def fetch_papers_by_ids(
    session: requests.Session, arxiv_ids: list[str]
) -> dict[str, Paper]:
    """Fetches complete arXiv metadata for known IDs in bounded batches."""
    papers = {}
    for start in range(0, len(arxiv_ids), ARXIV_ID_BATCH_SIZE):
        batch = arxiv_ids[start : start + ARXIV_ID_BATCH_SIZE]
        response = request_with_retry(
            session,
            "GET",
            ARXIV_API,
            params={"id_list": ",".join(batch), "max_results": len(batch)},
            retries=ARXIV_RETRIES,
            timeout=60,
        )
        for paper in parse_feed(response.text):
            papers[paper.arxiv_id] = paper
        print(
            f"arXiv metadata: {min(start + len(batch), len(arxiv_ids))}/"
            f"{len(arxiv_ids)}",
            flush=True,
        )
        if start + len(batch) < len(arxiv_ids):
            time.sleep(ARXIV_QUERY_DELAY_SECONDS)
    return papers


def fetch_external_affiliations(
    session: requests.Session, arxiv_ids: list[str]
) -> dict[str, list[str]]:
    """Returns optional affiliation candidates from Semantic Scholar."""
    if not arxiv_ids:
        return {}
    headers = {"User-Agent": "ZAI-Paper/1.0"}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    try:
        response = request_with_retry(
            session,
            "POST",
            SEMANTIC_SCHOLAR_API,
            retries=3,
            headers=headers,
            params={"fields": "authors,authors.affiliations"},
            json={"ids": [f"ARXIV:{arxiv_id}" for arxiv_id in arxiv_ids]},
            timeout=90,
        )
    except requests.RequestException as error:
        print(
            f"Semantic Scholar affiliations skipped after error: {error}",
            flush=True,
        )
        return {}

    try:
        items = response.json()
        if not isinstance(items, list) or len(items) != len(arxiv_ids):
            raise ValueError("unexpected Semantic Scholar response")
    except (TypeError, ValueError) as error:
        print(f"Semantic Scholar affiliations ignored: {error}", flush=True)
        return {}

    affiliations = {}
    for arxiv_id, item in zip(arxiv_ids, items, strict=True):
        if not item:
            continue
        values = [
            affiliation
            for author in item.get("authors", [])
            for affiliation in author.get("affiliations", [])
        ]
        affiliations[arxiv_id] = normalize_string_list(values, limit=20)
    return affiliations


def load_existing_state(
    path: Path,
) -> tuple[dict[str, dict], dict[str, Paper]]:
    """Loads existing website rows and the metadata needed for incremental sync."""
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: dict[str, dict] = {}
    papers: dict[str, Paper] = {}
    for source in data.get("rows", []):
        arxiv_id = str(source.get("arxiv_id", "")).strip()
        title = str(source.get("title", "")).strip()
        published = str(source.get("published", "")).strip()
        if not arxiv_id or not title or not published:
            continue
        try:
            published_at = datetime.fromisoformat(published)
        except ValueError:
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        if published_at.year < MIN_YEAR:
            continue

        authors_text = str(source.get("authors", "")).strip()
        normalized = {
            "arxiv_id": arxiv_id,
            "title": title,
            "authors": authors_text,
            "translated_title": str(
                source.get("translated_title", "")
            ).strip(),
            "tag": normalize_tag(source.get("tag"), "非产品相关"),
            "topic_tags": normalize_topic_tags(source.get("topic_tags")),
            "institutions": normalize_institutions(source.get("institutions")),
            "abstract": clean(str(source.get("abstract", ""))),
            "metadata_enriched": bool(source.get("metadata_enriched")),
            "published": published_at.date().isoformat(),
            "pdf_url": str(source.get("pdf_url", "")).strip(),
            "arxiv_url": str(source.get("arxiv_url", "")).strip(),
        }
        rows[arxiv_id] = normalized
        papers[arxiv_id] = Paper(
            arxiv_id=arxiv_id,
            title=title,
            authors=tuple(
                author.strip()
                for author in authors_text.split(",")
                if author.strip()
            ),
            abstract=normalized["abstract"],
            published=published_at,
            abs_url=normalized["arxiv_url"],
            pdf_url=normalized["pdf_url"],
        )
    return rows, papers


def normalize_sync_mode(value: str) -> str:
    """Normalizes unknown values to the safe incremental mode."""
    mode = value.strip().casefold()
    return mode if mode in {"full", "incremental"} else "incremental"


def fetch_all_papers(
    session: requests.Session,
    existing_papers: dict[str, Paper],
    mode: str,
    now: datetime,
) -> tuple[dict[str, Paper], list[str]]:
    """Runs all recall queries and deduplicates their arXiv results."""
    papers: dict[str, Paper] = {}
    failures = []
    window_label, since, until = search_window(mode, existing_papers, now)
    queries = arxiv_queries()
    for query_index, (label, query) in enumerate(queries):
        run_label = f"{label} {window_label}"
        try:
            for paper in fetch_query(session, run_label, query, since, until):
                papers[paper.arxiv_id] = paper
        except requests.RequestException as error:
            failures.append(run_label)
            print(
                f"arXiv {run_label}: skipped after error: {error}",
                flush=True,
            )
        if query_index < len(queries) - 1:
            time.sleep(ARXIV_QUERY_DELAY_SECONDS)
    return papers, failures


def configured_pair_backfill(value: str) -> list[str]:
    """Returns requested non-founder authors eligible for pair backfill."""
    requested = normalize_string_list(value.split(","))
    allowed_authors = [author for author in PEOPLE.values() if author != "Jie Tang"]
    allowed = {author.casefold(): author for author in allowed_authors}
    return [
        allowed[author.casefold()]
        for author in requested
        if author.casefold() in allowed
    ]


def fetch_pair_backfill(
    session: requests.Session, authors: list[str], now: datetime
) -> dict[str, Paper]:
    """Fetches full history for pairs containing newly tracked authors."""
    papers = {}
    since = datetime(MIN_YEAR, 1, 1, tzinfo=timezone.utc)
    until = now + timedelta(days=1)
    for index, author in enumerate(authors):
        partners = [
            partner
            for partner in PEOPLE.values()
            if partner not in {"Jie Tang", author}
        ]
        pair_query = " OR ".join(
            f'(au:"{author}" AND au:"{partner}")' for partner in partners
        )
        for paper in fetch_query(
            session,
            f"pair backfill {author}",
            f"({pair_query})",
            since,
            until,
        ):
            papers[paper.arxiv_id] = paper
        if index < len(authors) - 1:
            time.sleep(ARXIV_QUERY_DELAY_SECONDS)
    return papers


def matched_people(paper: Paper) -> list[str]:
    """Returns configured people who exactly match an author name."""
    authors = {name.casefold() for name in paper.authors}
    return [cn for cn, en in PEOPLE.items() if en.casefold() in authors]


def alias_in_text(alias: str, text: str) -> bool:
    """Matches a product alias while treating bare GLM conservatively."""
    if alias == "GLM":
        return bool(re.search(r"(?<![a-z0-9])glm(?![a-z])", text, re.IGNORECASE))
    return alias.casefold() in text.casefold()


def matched_title_products(paper: Paper) -> list[str]:
    """Returns product names found in a paper title."""
    return [alias for alias in PRODUCT_ALIASES if alias_in_text(alias, paper.title)]


def matched_products(
    paper: Paper, title_products: list[str] | None = None
) -> list[str]:
    """Returns product names supported by title or abstract context."""
    matches = list(
        matched_title_products(paper)
        if title_products is None
        else title_products
    )
    for alias in PRODUCT_ALIASES:
        if alias == "GLM":
            if "GLM" not in matches and re.search(
                r"(?<![a-z0-9])glm\s*[-‐‑–—]\s*[a-z0-9]",
                paper.abstract,
                re.IGNORECASE,
            ):
                matches.append("GLM")
            continue
        if alias in matches:
            continue
        if alias_in_text(alias, paper.abstract):
            matches.append(alias)
    return matches


def matched_support_signals(paper: Paper) -> list[str]:
    """Returns terms that may connect a paper to product infrastructure."""
    text = f"{paper.title} {paper.abstract}".casefold()
    return [hint for hint in SUPPORT_TAG_HINTS if hint.casefold() in text]


def matched_tsinghua_signals(paper: Paper) -> list[str]:
    """Separates Jie Tang's affiliation from weaker Tsinghua mentions."""
    signals = []
    for author, affiliation in paper.author_affiliations:
        for marker in TSINGHUA_MARKERS:
            if marker.casefold() in affiliation.casefold():
                strength = (
                    "founder-affiliation"
                    if author.casefold() == "jie tang"
                    else "coauthor-affiliation"
                )
                signals.append(f"{strength}:{author}={affiliation}")
                break
    text = " ".join((paper.title, paper.abstract, *paper.authors))
    for marker in TSINGHUA_MARKERS:
        if marker.casefold() in text.casefold():
            signals.append(f"text:{marker}")
    return signals


def matched_founder_coauthors(paper: Paper) -> list[str]:
    """Returns frequent Jie Tang collaborators found in the author list."""
    authors = {name.casefold() for name in paper.authors}
    return [name for name in JIE_TANG_CORE_COAUTHORS if name.casefold() in authors]


def matched_org_signals(paper: Paper) -> list[str]:
    """Returns explicit Z.AI, Zhipu, or GLM team authors and affiliations."""
    signals = []
    for author in paper.authors:
        normalized = re.sub(r"[^a-z0-9]+", "", author.casefold())
        author_cf = author.casefold()
        if (
            author_cf == "zhipu"
            or "z.ai" in author_cf
            or "zhipuai" in normalized
            or ("zhipu" in normalized and "team" in normalized)
        ):
            signals.append(author)
        elif "glm" in normalized and "team" in normalized:
            signals.append(author)
    for _, affiliation in paper.author_affiliations:
        normalized = re.sub(r"[^a-z0-9]+", "", affiliation.casefold())
        if "zhipu" in normalized or "z.ai" in affiliation.casefold():
            signals.append(f"affiliation:{affiliation}")
    return signals


def hard_selection_reasons(
    people: list[str],
    products: list[str],
    org_signals: list[str],
    tsinghua_signals: list[str],
    manually_verified: bool,
    curated_support: str,
) -> list[str]:
    """Returns deterministic reasons that are sufficient for direct inclusion."""
    reasons = []
    if org_signals:
        reasons.append("verified-organization")
    if len(people) >= 3:
        reasons.append("three-listed-people")
    if products and len(people) >= 2:
        reasons.append("product-and-two-listed-people")
    strong_tsinghua = any(
        signal.startswith("founder-affiliation:") or signal == "text:THUDM"
        for signal in tsinghua_signals
    )
    if "唐杰" in people and strong_tsinghua:
        reasons.append("jie-tang-and-tsinghua")
    if manually_verified:
        reasons.append("manually-verified-title")
    if curated_support:
        reasons.append("curated-product-relation")
    return reasons


def heuristic_tag(
    direct_product_signal: bool,
    products: list[str],
    support_signals: list[str],
) -> str:
    """Provides a conservative tag when the LLM omits or changes a tag."""
    if direct_product_signal:
        return "产品相关"
    if products or support_signals:
        return "产品技术支持"
    return "非产品相关"


def normalize_tag(value: object, fallback: str) -> str:
    """Normalizes current and legacy paper tags."""
    tag = str(value or "").strip()
    tag = LEGACY_TAGS.get(tag, tag)
    return tag if tag in PAPER_TAGS else fallback


def normalize_relevant(value: object) -> bool:
    """Accepts only unambiguous boolean relevance values from the LLM."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
        return value.strip().casefold() == "true"
    raise ValueError("LLM relevant must be a boolean")


def enforce_tag_policy(candidate: dict, tag: str) -> str:
    """Prevents product tags without explicit supporting evidence."""
    if tag == "产品相关" and not candidate["direct_product_signal"]:
        return (
            "产品技术支持"
            if candidate["explicit_product_signal"] or candidate["curated_support"]
            else "非产品相关"
        )
    if (
        tag == "产品技术支持"
        and not candidate["explicit_product_signal"]
        and not candidate["curated_support"]
    ):
        return "非产品相关"
    return tag


def parse_json_array(text: str) -> list[dict]:
    """Extracts a JSON array from a possibly fenced LLM response."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("LLM response has no JSON array")
    result = json.loads(text[start : end + 1])
    if not isinstance(result, list):
        raise ValueError("LLM response is not an array")
    return result


def validate_review_results(
    results: list[dict], expected_ids: list[str]
) -> list[dict]:
    """Validates and orders one batch of LLM review results."""
    results_by_id = {
        str(item.get("arxiv_id")): item for item in results
    }
    if (
        len(results_by_id) == len(expected_ids)
        and set(results_by_id) == set(expected_ids)
    ):
        results = [results_by_id[arxiv_id] for arxiv_id in expected_ids]

    returned_ids = [str(item.get("arxiv_id")) for item in results]
    translations = [
        str(item.get("translated_title", "")).strip()
        for item in results
    ]
    for item in results:
        normalize_relevant(item.get("relevant"))
        topic_tags = normalize_topic_tags(item.get("topic_tags"))
        if not 2 <= len(topic_tags) <= 5:
            raise ValueError("LLM topic_tags must contain 2 to 5 valid tags")
        if not isinstance(item.get("institutions"), list):
            raise ValueError("LLM institutions must be an array")
    if (
        len(results) != len(expected_ids)
        or returned_ids != expected_ids
        or not all(translations)
    ):
        raise ValueError("incomplete LLM result")
    if len({translation.casefold() for translation in translations}) != len(
        translations
    ):
        raise ValueError("duplicate translations in LLM batch")
    return results


def prompt(items: list[dict]) -> str:
    """Builds the combined review, translation, and metadata prompt."""
    people = "、".join(PEOPLE)
    return (
        "审核以下 arXiv 论文是否属于智谱/Z.AI 或清华唐杰团队，并翻译标题、"
        "分类。\n\n"
        "收录范围：智谱团队或产品论文；清华唐杰本人自 2020 年起的论文；"
        "与智谱产品有明确依赖关系的底层技术论文。仅引用或比较 GLM 不收录。"
        "\n\n"
        "同名消歧：Jie Tang 姓名本身不是充分证据。founder-affiliation 指本人"
        "机构，是强证据；coauthor-affiliation 和 text:Tsinghua 只是辅助。"
        "缺少机构时结合核心合作者、计算机学科、标题和摘要判断。排除通信、"
        "天文、物理等领域同名作者及 OpenAI GPT-5 System Card。\n\n"
        "团队判断：Zhipu、Z.AI、GLM Team 等团队作者或机构是强证据；两位"
        "以上指定作者是中等证据。GLM 缩写噪音很高，必须结合团队或产品"
        "上下文。curated-product-relation 和 manually-verified-title 是人工"
        "核验证据。\n\n"
        "输入中的 evidence、hard_selected 和 hard_selection_reasons 已由代码"
        "计算；listed-people 也已经完成精确作者匹配和计数，不要重新计数。"
        "hard_selected=true 的论文已由确定性规则收录，你仍需独立返回 relevant，"
        "但该值不会推翻确定性收录。其他论文的 relevant 将决定是否收录。\n\n"
        "标签：\n"
        "- 产品相关：直接发布或研究 GLM、AutoGLM、CogView、CogVideo、"
        "CogVLM 等智谱模型或产品。\n"
        "- 产品技术支持：与确认产品直接相关的训练、推理、Infra、Agent、"
        "评测或对齐技术。\n"
        "- 非产品相关：确认属于目标唐杰或智谱团队，但无法映射到产品或"
        "技术链。\n\n"
        "研究标签：从固定词表中选择 2 到 5 个短标签，可复合使用。至少覆盖"
        "研究对象或模态，并尽量覆盖能力、训练方法、工程技术或成果形式。"
        "不要创造新标签。固定词表："
        f"{'、'.join(TOPIC_TAGS)}。\n\n"
        "机构：只能从 author_affiliations 和 external_affiliations 给出的候选"
        "信息中提取真实大学、研究机构或公司名称；去掉院系、研究方向、职位"
        "和明显不是机构的描述。不得根据作者或论文内容猜测机构；无法确认时"
        "返回空数组。最多返回 8 个去重机构。\n\n"
        f"指定作者：{people}。\n"
        "只返回与输入顺序一致的 JSON 数组：\n"
        '[{"arxiv_id":"编号","relevant":true,'
        '"translated_title":"中文标题","tag":"产品相关",'
        '"topic_tags":["文本","模型"],'
        '"institutions":["Tsinghua University"]}]\n'
        "relevant 必须是 JSON 布尔值；translated_title 非空；tag 只能是"
        "“产品相关”“产品技术支持”“非产品相关”；topic_tags 必须包含 2 到 5"
        "个固定词表标签；institutions 必须是 JSON 数组。\n\n"
        f"{json.dumps(items, ensure_ascii=False)}\n"
    )


def load_cache(path: Path) -> dict[str, dict]:
    """Loads reviews produced by the current policy version."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("items", {}) if data.get("version") == CACHE_VERSION else {}


def save_cache(path: Path, cache: dict[str, dict]) -> None:
    """Atomically saves LLM review results."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"version": CACHE_VERSION, "items": cache}, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(path)


def review_fingerprint(item: dict, model: str) -> str:
    """Hashes every input that can affect an LLM review."""
    content = {
        "version": CACHE_VERSION,
        "model": model,
        "title": item["title"],
        "authors": item["authors"],
        "author_affiliations": item["author_affiliations"],
        "external_affiliations": item.get("external_affiliations", []),
        "abstract": item["abstract"],
        "categories": item["categories"],
        "evidence": item["evidence"],
        "hard_selected": item["hard_selected"],
        "hard_selection_reasons": item["hard_selection_reasons"],
    }
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def review_and_translate(
    candidates: list[dict], api_key: str, model: str
) -> dict[str, dict]:
    """Reviews and translates candidates in cached batches of 15 papers."""
    if not api_key:
        raise ValueError("ZHIPU_API_KEY is required")

    cache_path = ROOT / ".cache/review_and_translation.json"
    cache = load_cache(cache_path)
    fingerprints = {
        item["arxiv_id"]: review_fingerprint(item, model) for item in candidates
    }
    missing = [
        item
        for item in candidates
        if cache.get(item["arxiv_id"], {}).get("fingerprint")
        != fingerprints[item["arxiv_id"]]
    ]
    session = requests.Session()

    for start in range(0, len(missing), LLM_BATCH_SIZE):
        batch = missing[start : start + LLM_BATCH_SIZE]
        payload = [
            {
                "arxiv_id": item["arxiv_id"],
                "title": item["title"],
                "authors": item["authors"],
                "author_affiliations": item["author_affiliations"],
                "external_affiliations": item.get(
                    "external_affiliations", []
                ),
                "abstract": item["abstract"][:1400],
                "categories": item["categories"],
                "evidence": item["evidence"],
                "hard_selected": item["hard_selected"],
                "hard_selection_reasons": item["hard_selection_reasons"],
                "fallback_tag": item["fallback_tag"],
            }
            for item in batch
        ]
        expected = [item["arxiv_id"] for item in batch]
        for attempt in range(3):
            try:
                response = request_with_retry(
                    session,
                    "POST",
                    GLM_API,
                    retries=3,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "temperature": 0,
                        "thinking": {"type": "disabled"},
                        "messages": [{"role": "user", "content": prompt(payload)}],
                    },
                    timeout=180,
                )
                results = parse_json_array(
                    response.json()["choices"][0]["message"]["content"]
                )
                results = validate_review_results(results, expected)
                break
            except (KeyError, TypeError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(3)

        for source, result in zip(batch, results, strict=True):
            translation = str(result.get("translated_title", "")).strip()
            cache[source["arxiv_id"]] = {
                "fingerprint": fingerprints[source["arxiv_id"]],
                "relevant": normalize_relevant(result.get("relevant")),
                "translated_title": translation,
                "tag": normalize_tag(result.get("tag"), source["fallback_tag"]),
                "topic_tags": normalize_topic_tags(result.get("topic_tags")),
                "institutions": normalize_institutions(
                    result.get("institutions")
                ),
            }
        save_cache(cache_path, cache)
        print(
            f"LLM reviewed and translated "
            f"{min(start + LLM_BATCH_SIZE, len(missing))}/"
            f"{len(missing)}",
            flush=True,
        )
    return cache


def build_candidates(
    papers: dict[str, Paper],
    external_affiliations: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Builds high-recall candidates and deterministic evidence for the LLM."""
    external_affiliations = external_affiliations or {}
    candidates = []
    for paper in papers.values():
        if paper.published.year < MIN_YEAR or paper.arxiv_id in EXCLUDED_ARXIV_IDS:
            continue
        people = matched_people(paper)
        title_products = matched_title_products(paper)
        products = matched_products(paper, title_products)
        support_signals = matched_support_signals(paper)
        org_signals = matched_org_signals(paper)
        tsinghua_signals = matched_tsinghua_signals(paper)
        is_founder_candidate = "唐杰" in people
        founder_coauthors = (
            matched_founder_coauthors(paper) if is_founder_candidate else []
        )
        manually_verified = paper.title in VERIFIED_TITLES
        curated_support = CURATED_SUPPORT_RELATIONS.get(paper.title, "")
        direct_product_signal = bool(
            title_products or paper.title in DIRECT_PRODUCT_TITLES
        )
        hard_reasons = hard_selection_reasons(
            people,
            products,
            org_signals,
            tsinghua_signals,
            manually_verified,
            curated_support,
        )
        # Broad recall is intentional here; GLM makes the final relevance call.
        if (
            not is_founder_candidate
            and len(people) < 2
            and not org_signals
            and not products
            and not support_signals
            and not manually_verified
            and not curated_support
        ):
            continue
        evidence = []
        if is_founder_candidate:
            evidence.append("exact-author:Jie Tang")
        if founder_coauthors:
            evidence.append(f"founder-coauthors:{', '.join(founder_coauthors)}")
        if (
            is_founder_candidate
            and paper.primary_category in JIE_TANG_RESEARCH_CATEGORIES
        ):
            evidence.append(f"founder-topic:{paper.primary_category}")
        if org_signals:
            evidence.append(f"organization:{', '.join(org_signals)}")
        if tsinghua_signals:
            evidence.append(f"tsinghua:{', '.join(tsinghua_signals)}")
        if len(people) >= 2:
            evidence.append(f"listed-people:{', '.join(people)}")
        if products:
            evidence.append(f"product-terms:{', '.join(products)}")
        if support_signals:
            evidence.append(f"support-terms:{', '.join(support_signals)}")
        if manually_verified:
            evidence.append("manually-verified-title")
        if curated_support:
            evidence.append(f"curated-product-relation:{curated_support}")
        fallback_tag = heuristic_tag(
            direct_product_signal,
            products,
            support_signals,
        )
        candidates.append(
            {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": list(paper.authors),
                "author_affiliations": [
                    list(item) for item in paper.author_affiliations
                ],
                "external_affiliations": external_affiliations.get(
                    paper.arxiv_id, []
                ),
                "abstract": paper.abstract,
                "categories": list(paper.categories),
                "evidence": evidence,
                "hard_selected": bool(hard_reasons),
                "hard_selection_reasons": hard_reasons,
                "direct_product_signal": direct_product_signal,
                "explicit_product_signal": bool(products),
                "curated_support": bool(curated_support),
                "fallback_tag": fallback_tag,
            }
        )
    candidates.sort(
        key=lambda item: papers[item["arxiv_id"]].published, reverse=True
    )
    return candidates


def arxiv_queries() -> list[tuple[str, str]]:
    """Returns three broad, deduplicated recall queries."""
    pair_authors = [author for author in PEOPLE.values() if author != "Jie Tang"]
    pair_query = " OR ".join(
        f'(au:"{left}" AND au:"{right}")'
        for left, right in combinations(pair_authors, 2)
    )
    company_signals = [
        "au:Zhipu",
        'au:"Z.AI"',
        "au:GLM",
        *(f'ti:"{alias}"' for alias in PRODUCT_ALIASES),
        *(f'ti:"{term}"' for term in SUPPORT_TITLE_TERMS),
    ]
    return [
        ("Jie Tang", 'au:"Jie Tang"'),
        ("trusted author pairs", f"({pair_query})"),
        ("company/product/support signals", f"({' OR '.join(company_signals)})"),
    ]


def row_from_candidate(candidate: dict, review: dict, paper: Paper) -> dict:
    """Converts an approved candidate into the public row schema."""
    tag = enforce_tag_policy(candidate, review["tag"])
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "authors": ", ".join(paper.authors),
        "translated_title": review["translated_title"],
        "tag": tag,
        "topic_tags": normalize_topic_tags(review.get("topic_tags")),
        "institutions": normalize_institutions(review.get("institutions")),
        "abstract": paper.abstract,
        "metadata_enriched": True,
        "published": paper.published.date().isoformat(),
        "pdf_url": paper.pdf_url,
        "arxiv_url": paper.abs_url,
    }


def approved_rows(
    candidates: list[dict], reviews: dict[str, dict], papers: dict[str, Paper]
) -> list[dict]:
    """Returns public rows for candidates approved by the LLM."""
    rows = []
    for candidate in candidates:
        review = reviews[candidate["arxiv_id"]]
        if not candidate["hard_selected"] and not review["relevant"]:
            continue
        paper = papers[candidate["arxiv_id"]]
        rows.append(row_from_candidate(candidate, review, paper))
    return rows


def merge_rows(
    mode: str, existing_rows: dict[str, dict], reviewed_rows: list[dict]
) -> list[dict]:
    """Replaces all rows for full syncs and merges rows for incremental syncs."""
    rows_by_id = {} if mode == "full" else dict(existing_rows)
    for arxiv_id in EXCLUDED_ARXIV_IDS:
        rows_by_id.pop(arxiv_id, None)
    for row in reviewed_rows:
        previous = rows_by_id.get(row["arxiv_id"], {})
        if mode != "full" and previous.get("institutions") and not row.get(
            "institutions"
        ):
            row = {**row, "institutions": previous["institutions"]}
        rows_by_id[row["arxiv_id"]] = row
    rows = list(rows_by_id.values())
    rows.sort(key=lambda row: row.get("published", ""), reverse=True)
    return rows


def main() -> None:
    """Runs the configured synchronization and writes the website data."""
    load_env(ROOT / ".env")
    now = datetime.now(timezone.utc)
    mode = normalize_sync_mode(os.getenv("ARXIV_SYNC_MODE", "incremental"))
    session = requests.Session()
    session.headers["User-Agent"] = "ArxivZhipuPaperSearch/1.0"

    output = ROOT / "public/data/zhipu_papers.json"
    existing_rows, existing_papers = load_existing_state(output)
    fetched_papers, arxiv_failures = fetch_all_papers(
        session, existing_papers, mode, now
    )
    pair_backfill = configured_pair_backfill(
        os.getenv("ARXIV_PAIR_BACKFILL_AUTHOR", "")
    )
    if pair_backfill:
        fetched_papers.update(fetch_pair_backfill(session, pair_backfill, now))
    if mode == "full" and arxiv_failures:
        raise RuntimeError(
            "full sync aborted because arXiv queries failed: "
            + ", ".join(arxiv_failures)
        )

    backfill_ids = [
        arxiv_id
        for arxiv_id, row in existing_rows.items()
        if (
            not row.get("metadata_enriched")
            or not row.get("abstract")
            or len(row.get("topic_tags", [])) < 2
        )
    ]
    if mode != "full" and backfill_ids:
        fetched_papers.update(fetch_papers_by_ids(session, backfill_ids))

    external_affiliations = fetch_external_affiliations(
        session, list(fetched_papers)
    )
    candidates = build_candidates(fetched_papers, external_affiliations)
    if mode != "full":
        for candidate in candidates:
            if candidate["arxiv_id"] not in existing_rows:
                continue
            candidate["hard_selected"] = True
            candidate["hard_selection_reasons"] = [
                *candidate["hard_selection_reasons"],
                "existing-approved-row",
            ]
    reviews = review_and_translate(
        candidates,
        os.getenv("ZHIPU_API_KEY", "").strip(),
        os.getenv("ZHIPU_CLASSIFIER_MODEL", "glm-5-turbo").strip(),
    )
    reviewed_rows = approved_rows(candidates, reviews, fetched_papers)
    rows = merge_rows(mode, existing_rows, reviewed_rows)

    summary = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "final_count": len(rows),
        "product_related": sum(1 for row in rows if row["tag"] == "产品相关"),
        "product_support": sum(
            1 for row in rows if row["tag"] == "产品技术支持"
        ),
        "academic_output": sum(
            1 for row in rows if row["tag"] == "非产品相关"
        ),
    }
    result = {"summary": summary, "rows": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "sync_mode": mode,
                "fetched": len(fetched_papers),
                "candidates": len(candidates),
                "approved": len(reviewed_rows),
                "query_failures": arxiv_failures,
                **summary,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
