#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
ARXIV_API = "https://export.arxiv.org/api/query"
GLM_API = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
OPENSEARCH = {"os": "http://a9.com/-/spec/opensearch/1.1/"}
CACHE_VERSION = "people-products-rules-v2"

PEOPLE = {
    "唐杰": "Jie Tang",
    "刘德兵": "Debing Liu",
    "张鹏": "Peng Zhang",
    "顾晓韬": "Xiaotao Gu",
    "刘潇": "Xiao Liu",
    "曾奥涵": "Aohan Zeng",
    "郑问笛": "Wendi Zheng",
}
PRODUCTS = ("GLM", "CogView", "CogVideo", "CogVLM")
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


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def make_session(retries: int) -> requests.Session:
    retry = Retry(
        total=retries,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


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


def fetch_query(session: requests.Session, label: str, query: str) -> list[Paper]:
    papers = []
    offset = 0
    total = 1
    while offset < total:
        response = session.get(
            ARXIV_API,
            params={
                "search_query": query,
                "start": offset,
                "max_results": 300,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            timeout=30,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        total = int(root.findtext("os:totalResults", "0", OPENSEARCH))
        page = parse_feed(response.text)
        if not page:
            break
        papers.extend(page)
        offset += len(page)
        print(f"arXiv {label}: {offset}/{total}", flush=True)
        if offset < total:
            time.sleep(3)
    return papers


def fetch_author(session: requests.Session, author: str) -> list[Paper]:
    return fetch_query(session, author, f'au:"{author}"')


def matched_people(paper: Paper) -> list[str]:
    authors = {name.casefold() for name in paper.authors}
    return [cn for cn, en in PEOPLE.items() if en.casefold() in authors]


def matched_products(paper: Paper) -> list[str]:
    text = f"{paper.title} {paper.abstract}".casefold()
    return [product for product in PRODUCTS if product.casefold() in text]


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
- 仅命中2人且无产品词时，结合标题、摘要、完整作者排除同名作者和无关论文。

指定作者：唐杰、刘德兵、张鹏、顾晓韬、刘潇、曾奥涵、郑问笛。
只返回与输入顺序一致的 JSON 数组：
[{{"arxiv_id":"编号","relevant":true,"translated_title":"中文标题"}}]
所有论文都必须返回非空 translated_title。

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
    session = make_session(6)

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
                "automatic": item["automatic"],
            }
            for item in batch
        ]
        expected = [item["arxiv_id"] for item in batch]
        for attempt in range(3):
            try:
                response = session.post(
                    GLM_API,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "temperature": 0,
                        "thinking": {"type": "disabled"},
                        "messages": [{"role": "user", "content": prompt(payload)}],
                    },
                    timeout=180,
                )
                response.raise_for_status()
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
        people = matched_people(paper)
        products = matched_products(paper)
        org_signals = matched_org_signals(paper)
        if len(people) < 2 and not org_signals:
            continue
        candidates.append(
            {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": list(paper.authors),
                "abstract": paper.abstract,
                "people": people,
                "products": products,
                "org_signals": org_signals,
                "automatic": (
                    len(people) >= 3
                    or bool(org_signals)
                    or (bool(products) and len(people) >= 2)
                    or paper.title in VERIFIED_TITLES
                ),
            }
        )
    candidates.sort(
        key=lambda item: papers[item["arxiv_id"]].published, reverse=True
    )
    return candidates


def main() -> None:
    load_dotenv(ROOT / ".env")
    session = make_session(3)
    session.headers["User-Agent"] = "ArxivZhipuPaperSearch/1.0"

    papers = {
        paper.arxiv_id: paper
        for label, query in (
            [(author, f'au:"{author}"') for author in PEOPLE.values()]
            + list(ORG_AUTHOR_QUERIES.items())
        )
        for paper in fetch_query(session, label, query)
    }
    candidates = build_candidates(papers)
    reviews = review_and_translate(
        candidates,
        os.getenv("ZHIPU_API_KEY", "").strip(),
        os.getenv("ZHIPU_CLASSIFIER_MODEL", "glm-5-turbo").strip(),
    )

    rows = []
    llm_approved = 0
    for candidate in candidates:
        review = reviews[candidate["arxiv_id"]]
        if not candidate["automatic"] and not review["relevant"]:
            continue
        llm_approved += not candidate["automatic"]
        paper = papers[candidate["arxiv_id"]]
        journal_name, journal_issue = split_journal_ref(paper.journal_ref)
        rows.append(
            {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": ", ".join(paper.authors),
                "translated_title": review["translated_title"],
                "journal_name": journal_name,
                "journal_issue": journal_issue,
                "published": paper.published.date().isoformat(),
                "pdf_url": paper.pdf_url,
                "arxiv_url": paper.abs_url,
            }
        )

    result = {
        "summary": {
            "queried_unique_papers": len(papers),
            "at_least_two_people": len(candidates),
            "automatic_include": sum(item["automatic"] for item in candidates),
            "probability_approved_by_llm": llm_approved,
            "final_count": len(rows),
        },
        "rows": rows,
    }
    result["summary"]["updated_at"] = datetime.now().astimezone().isoformat()
    for output in (
        ROOT / "outputs/zhipu_papers.json",
        ROOT / "public/data/zhipu_papers.json",
    ):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
