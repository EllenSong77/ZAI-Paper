#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
ARXIV_API = "https://export.arxiv.org/api/query"
GLM_API = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
OPENSEARCH = {"os": "http://a9.com/-/spec/opensearch/1.1/"}
MIN_YEAR = 2020
CACHE_VERSION = "people-products-tags-v4"
ARXIV_PAGE_SIZE = 100
ARXIV_RETRIES = 2
ARXIV_QUERY_DELAY_SECONDS = 3
DEFAULT_INCREMENTAL_OVERLAP_DAYS = 14

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
}
PRODUCTS = ("GLM", "CogView", "CogVideo", "CogVLM")
PRODUCT_TAG_HINTS = (
    "GLM",
    "ChatGLM",
    "AutoGLM",
    "WebGLM",
    "CogView",
    "CogVideo",
    "CogVLM",
    "CogAgent",
    "CodeGeeX",
)
TSINGHUA_MARKERS = ("THUDM", "Tsinghua", "Qinghua")
PAPER_TAGS = ("产品强相关", "学术输出")
ORG_AUTHOR_QUERIES = {
    "Zhipu": "au:Zhipu",
    "Z.AI": 'au:"Z.AI"',
    "GLM team-like authors": "au:GLM",
}

# These four two-person papers were manually verified after the LLM confused
# same-name authors with team membership.
VERIFIED_TITLES = {
    "Training-Free Vector Quantization via Gaussian VAEs",
    "ZeroFlow: Overcoming Catastrophic Forgetting is Easier than You Think",
    "DreamPolish: Domain Score Distillation With Progressive Geometry Generation",
    "Relay Diffusion: Unifying diffusion process across resolutions for image synthesis",
}


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    published: datetime
    abs_url: str
    pdf_url: str
    journal_ref: str


def load_env(path: Path) -> None:
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
    return re.sub(r"\s+", " ", text or "").strip()


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    retries: int = 3,
    **kwargs,
) -> requests.Response:
    retry_statuses = {429, 500, 502, 503, 504}
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.request(method, url, **kwargs)
        except requests.RequestException as error:
            last_error = error
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        if response.status_code in retry_statuses and attempt < retries - 1:
            retry_after = response.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else 10 * (attempt + 1)
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("unreachable retry state") from last_error


def parse_feed(xml: str) -> list[Paper]:
    papers = []
    for entry in ET.fromstring(xml).findall("atom:entry", ATOM):
        raw_id = clean(entry.findtext("atom:id", namespaces=ATOM))
        arxiv_id = re.sub(r"v\d+$", "", raw_id.split("/abs/")[-1])
        links = {
            link.get("title", link.get("rel", "")): link.get("href", "")
            for link in entry.findall("atom:link", ATOM)
        }
        papers.append(
            Paper(
                arxiv_id=arxiv_id,
                title=clean(entry.findtext("atom:title", namespaces=ATOM)),
                authors=tuple(
                    clean(author.findtext("atom:name", namespaces=ATOM))
                    for author in entry.findall("atom:author", ATOM)
                ),
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
                journal_ref=clean(
                    entry.findtext("arxiv:journal_ref", namespaces=ATOM)
                ),
            )
        )
    return papers


def arxiv_date(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M")


def date_limited_query(query: str, since: datetime, until: datetime) -> str:
    return (
        f"({query}) AND submittedDate:[{arxiv_date(since)} TO "
        f"{arxiv_date(until)}]"
    )


def full_year_windows(now: datetime) -> list[tuple[str, datetime, datetime]]:
    windows = []
    for year in range(now.year, MIN_YEAR - 1, -1):
        since = datetime(year, 1, 1, tzinfo=timezone.utc)
        until = min(
            datetime(year + 1, 1, 1, tzinfo=timezone.utc),
            now + timedelta(days=1),
        )
        windows.append((str(year), since, until))
    return windows


def incremental_window(
    existing_papers: dict[str, Paper], now: datetime
) -> tuple[str, datetime, datetime]:
    min_since = datetime(MIN_YEAR, 1, 1, tzinfo=timezone.utc)
    if not existing_papers:
        return "full-seed", min_since, now + timedelta(days=1)
    latest = max(paper.published for paper in existing_papers.values())
    since = latest.astimezone(timezone.utc) - timedelta(
        days=int(os.getenv("ARXIV_INCREMENTAL_OVERLAP_DAYS", DEFAULT_INCREMENTAL_OVERLAP_DAYS))
    )
    since = max(since, min_since)
    return f"since-{since.date().isoformat()}", since, now + timedelta(days=1)


def query_windows(
    mode: str, existing_papers: dict[str, Paper], now: datetime
) -> list[tuple[str, datetime, datetime]]:
    if mode == "full" or not existing_papers:
        return full_year_windows(now)
    return [incremental_window(existing_papers, now)]


def fetch_query(
    session: requests.Session,
    label: str,
    query: str,
    since: datetime,
    until: datetime,
) -> list[Paper]:
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
            timeout=30,
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
            time.sleep(3)
    return papers


def load_existing_papers(path: Path) -> dict[str, Paper]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    papers = {}
    for row in data.get("rows", []):
        arxiv_id = str(row.get("arxiv_id", "")).strip()
        title = str(row.get("title", "")).strip()
        published = str(row.get("published", "")).strip()
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
        authors = tuple(
            author.strip()
            for author in str(row.get("authors", "")).split(",")
            if author.strip()
        )
        papers[arxiv_id] = Paper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            abstract="",
            published=published_at,
            abs_url=str(row.get("arxiv_url", "")).strip(),
            pdf_url=str(row.get("pdf_url", "")).strip(),
            journal_ref=", ".join(
                part
                for part in (
                    str(row.get("journal_name", "")).strip(),
                    str(row.get("journal_issue", "")).strip(),
                )
                if part
            ),
        )
    return papers


def load_existing_rows(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = {}
    for row in data.get("rows", []):
        arxiv_id = str(row.get("arxiv_id", "")).strip()
        if arxiv_id:
            rows[arxiv_id] = row
    return rows


def normalize_sync_mode(value: str) -> str:
    mode = value.strip().casefold()
    return mode if mode in {"full", "incremental"} else "incremental"


def fetch_all_papers(
    session: requests.Session,
    existing_papers: dict[str, Paper],
    mode: str,
    now: datetime,
) -> tuple[dict[str, Paper], list[str], list[dict[str, str]]]:
    papers: dict[str, Paper] = {}
    failures = []
    windows = query_windows(mode, existing_papers, now)
    window_summary = [
        {
            "label": label,
            "since": since.date().isoformat(),
            "until": until.date().isoformat(),
        }
        for label, since, until in windows
    ]
    queries = arxiv_queries()
    for query_index, (label, query) in enumerate(queries):
        for window_index, (window_label, since, until) in enumerate(windows):
            run_label = f"{label} {window_label}"
            try:
                for paper in fetch_query(session, run_label, query, since, until):
                    papers[paper.arxiv_id] = paper
            except requests.RequestException as error:
                failures.append(run_label)
                print(f"arXiv {run_label}: skipped after error: {error}", flush=True)
            is_last = (
                query_index == len(queries) - 1
                and window_index == len(windows) - 1
            )
            if not is_last:
                time.sleep(ARXIV_QUERY_DELAY_SECONDS)
    return papers, failures, window_summary

def matched_people(paper: Paper) -> list[str]:
    authors = {name.casefold() for name in paper.authors}
    return [cn for cn, en in PEOPLE.items() if en.casefold() in authors]


def matched_products(paper: Paper) -> list[str]:
    text = f"{paper.title} {paper.abstract}".casefold()
    return [product for product in PRODUCTS if product.casefold() in text]


def matched_tsinghua_signals(paper: Paper) -> list[str]:
    text = " ".join((paper.title, paper.abstract, *paper.authors))
    return [marker for marker in TSINGHUA_MARKERS if marker.casefold() in text.casefold()]


def matched_org_signals(paper: Paper) -> list[str]:
    signals = []
    for author in paper.authors:
        normalized = re.sub(r"[^a-z0-9]+", "", author.casefold())
        author_cf = author.casefold()
        if (
            author_cf.strip() == "zhipu"
            or "z.ai" in author_cf
            or "zhipuai" in normalized
            or ("zhipu" in normalized and "team" in normalized)
        ):
            signals.append(author)
        elif "glm" in normalized and "team" in normalized:
            signals.append(author)
    return signals


def heuristic_tag(paper: Paper, products: list[str], org_signals: list[str]) -> str:
    text = f"{paper.title} {paper.abstract}"
    if products or any(hint.casefold() in text.casefold() for hint in PRODUCT_TAG_HINTS):
        return "产品强相关"
    if any("glm" in re.sub(r"[^a-z0-9]+", "", signal.casefold()) for signal in org_signals):
        return "产品强相关"
    return "学术输出"


def normalize_tag(value: object, fallback: str) -> str:
    tag = str(value or "").strip()
    return tag if tag in PAPER_TAGS else fallback


def split_journal_ref(value: str) -> tuple[str, str]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return "", ""
    issue = next(
        (part for part in parts[1:] if any(char.isdigit() for char in part)), ""
    )
    return parts[0], issue


def parse_json_array(text: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("LLM response has no JSON array")
    result = json.loads(text[start : end + 1])
    if not isinstance(result, list):
        raise ValueError("LLM response is not an array")
    return result


def prompt(items: list[dict]) -> str:
    return f"""审核以下 arXiv 论文是否属于智谱团队，并翻译标题。

规则：
- 命中指定作者3人及以上，收录；
- 作者或机构包含 Zhipu、Z.AI，或同一机构名同时包含 GLM 和 Team，收录；
- 命中 GLM、CogView、CogVideo、CogVLM，且指定作者至少2人，收录；
- Jie Tang 论文中出现清华相关关键要素（如 THUDM、Tsinghua），收录；
- 仅命中2人且无产品词时，结合标题、摘要、完整作者排除同名作者和无关论文。

标签：
- 产品强相关：与智谱发布模型/产品直接相关，如 GLM、ChatGLM、AutoGLM、CogView、CogVideo、CogVLM、CodeGeeX，或这些模型的理论/技术报告/训练方法论文。
- 学术输出：作者来自相关团队，但论文主题无法直接对应公司发布模型或产品。

指定作者：唐杰、刘德兵、张鹏、顾晓韬、刘潇、曾奥涵、郑问笛、杜政晓、黄明烈、张笑涵、洪文逸。
只返回与输入顺序一致的 JSON 数组：
[{{"arxiv_id":"编号","relevant":true,"translated_title":"中文标题","tag":"产品强相关"}}]
所有论文都必须返回非空 translated_title 和 tag。tag 只能是“产品强相关”或“学术输出”。

{json.dumps(items, ensure_ascii=False)}
"""


def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("items", {}) if data.get("version") == CACHE_VERSION else {}


def save_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"version": CACHE_VERSION, "items": cache}, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(path)


def review_and_translate(
    candidates: list[dict], api_key: str, model: str
) -> dict[str, dict]:
    if not api_key:
        raise ValueError("ZHIPU_API_KEY is required")

    cache_path = ROOT / ".cache/review_and_translation.json"
    cache = load_cache(cache_path)
    missing = [item for item in candidates if item["arxiv_id"] not in cache]
    session = requests.Session()

    for start in range(0, len(missing), 15):
        batch = missing[start : start + 15]
        payload = [
            {
                "arxiv_id": item["arxiv_id"],
                "title": item["title"],
                "authors": item["authors"],
                "abstract": item["abstract"][:1400],
                "people": item["people"],
                "products": item["products"],
                "org_signals": item["org_signals"],
                "tsinghua_signals": item["tsinghua_signals"],
                "automatic": item["automatic"],
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
                returned = [str(item.get("arxiv_id")) for item in results]
                translations = [
                    str(item.get("translated_title", "")).strip()
                    for item in results
                ]
                if (
                    len(results) != len(batch)
                    or returned != expected
                    or not all(translations)
                ):
                    raise ValueError("incomplete LLM result")
                break
            except (KeyError, TypeError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(3)

        for source, result in zip(batch, results, strict=True):
            translation = str(result.get("translated_title", "")).strip()
            cache[source["arxiv_id"]] = {
                "relevant": bool(result.get("relevant")),
                "translated_title": translation,
                "tag": normalize_tag(result.get("tag"), source["fallback_tag"]),
            }
        save_cache(cache_path, cache)
        print(
            f"LLM reviewed and translated {min(start + 15, len(missing))}/"
            f"{len(missing)}",
            flush=True,
        )
    return cache


def build_candidates(papers: dict[str, Paper]) -> list[dict]:
    candidates = []
    for paper in papers.values():
        if paper.published.year < MIN_YEAR:
            continue
        people = matched_people(paper)
        products = matched_products(paper)
        org_signals = matched_org_signals(paper)
        tsinghua_signals = matched_tsinghua_signals(paper)
        jie_tang_tsinghua = "唐杰" in people and bool(tsinghua_signals)
        if len(people) < 2 and not org_signals and not jie_tang_tsinghua:
            continue
        fallback_tag = heuristic_tag(paper, products, org_signals)
        candidates.append(
            {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": list(paper.authors),
                "abstract": paper.abstract,
                "people": people,
                "products": products,
                "org_signals": org_signals,
                "tsinghua_signals": tsinghua_signals,
                "fallback_tag": fallback_tag,
                "automatic": (
                    len(people) >= 3
                    or bool(org_signals)
                    or (bool(products) and len(people) >= 2)
                    or jie_tang_tsinghua
                    or paper.title in VERIFIED_TITLES
                ),
            }
        )
    candidates.sort(
        key=lambda item: papers[item["arxiv_id"]].published, reverse=True
    )
    return candidates


def arxiv_queries() -> list[tuple[str, str]]:
    author_queries = [(author, f'au:"{author}"') for author in PEOPLE.values()]
    return author_queries + list(ORG_AUTHOR_QUERIES.items())


def row_from_candidate(candidate: dict, review: dict, paper: Paper) -> dict:
    journal_name, journal_issue = split_journal_ref(paper.journal_ref)
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "authors": ", ".join(paper.authors),
        "translated_title": review["translated_title"],
        "tag": review["tag"],
        "journal_name": journal_name,
        "journal_issue": journal_issue,
        "published": paper.published.date().isoformat(),
        "pdf_url": paper.pdf_url,
        "arxiv_url": paper.abs_url,
    }


def approved_rows(
    candidates: list[dict], reviews: dict[str, dict], papers: dict[str, Paper]
) -> tuple[list[dict], int]:
    rows = []
    llm_approved = 0
    for candidate in candidates:
        review = reviews[candidate["arxiv_id"]]
        if not candidate["automatic"] and not review["relevant"]:
            continue
        llm_approved += not candidate["automatic"]
        paper = papers[candidate["arxiv_id"]]
        rows.append(row_from_candidate(candidate, review, paper))
    return rows, llm_approved


def main() -> None:
    load_env(ROOT / ".env")
    now = datetime.now(timezone.utc)
    mode = normalize_sync_mode(os.getenv("ARXIV_SYNC_MODE", "incremental"))
    session = requests.Session()
    session.headers["User-Agent"] = "ArxivZhipuPaperSearch/1.0"

    output = ROOT / "public/data/zhipu_papers.json"
    existing_rows = load_existing_rows(output)
    existing_papers = load_existing_papers(output)
    fetched_papers, arxiv_failures, search_windows = fetch_all_papers(
        session, existing_papers, mode, now
    )
    papers = dict(existing_papers)
    papers.update(fetched_papers)

    candidate_papers = fetched_papers if mode == "incremental" else papers
    candidates = build_candidates(candidate_papers)
    reviews = review_and_translate(
        candidates,
        os.getenv("ZHIPU_API_KEY", "").strip(),
        os.getenv("ZHIPU_CLASSIFIER_MODEL", "glm-5-turbo").strip(),
    )
    reviewed_rows, llm_approved = approved_rows(candidates, reviews, candidate_papers)
    if mode == "full" and not arxiv_failures:
        rows = reviewed_rows
    else:
        rows_by_id = dict(existing_rows)
        rows_by_id.update({row["arxiv_id"]: row for row in reviewed_rows})
        rows = list(rows_by_id.values())
        rows.sort(key=lambda row: row.get("published", ""), reverse=True)

    result = {
        "summary": {
            "queried_unique_papers": len(papers),
            "fetched_unique_papers": len(fetched_papers),
            "arxiv_sync_mode": mode,
            "arxiv_search_windows": search_windows,
            "arxiv_query_failures": arxiv_failures,
            "candidate_count": len(candidates),
            "automatic_include": sum(item["automatic"] for item in candidates),
            "probability_approved_by_llm": llm_approved,
            "product_related": sum(
                1 for row in rows if row["tag"] == "产品强相关"
            ),
            "academic_output": sum(1 for row in rows if row["tag"] == "学术输出"),
            "final_count": len(rows),
        },
        "rows": rows,
    }
    result["summary"]["updated_at"] = datetime.now().astimezone().isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
