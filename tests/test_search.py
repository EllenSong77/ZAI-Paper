"""Tests deterministic recall, relevance, and tagging rules.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from search_papers import (
    VERIFIED_TITLES,
    Paper,
    approved_rows,
    arxiv_queries,
    build_candidates,
    date_limited_query,
    enforce_tag_policy,
    heuristic_tag,
    incremental_window,
    load_existing_state,
    merge_rows,
    matched_org_signals,
    matched_people,
    matched_products,
    matched_tsinghua_signals,
    normalize_relevant,
    normalize_sync_mode,
    normalize_topic_tags,
    parse_feed,
    row_from_candidate,
    validate_review_results,
)


class RuleTests(TestCase):
    """Covers rules that must remain stable across refactors."""

    def paper(
        self,
        authors: tuple[str, ...],
        text: str = "",
        *,
        arxiv_id: str = "1",
        abstract: str = "",
        author_affiliations: tuple[tuple[str, str], ...] = (),
        primary_category: str = "cs.AI",
    ) -> Paper:
        """Builds a recent paper fixture."""
        return Paper(
            arxiv_id=arxiv_id,
            title=text,
            authors=authors,
            abstract=abstract,
            published=datetime(2026, 1, 1, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/1",
            pdf_url="https://arxiv.org/pdf/1",
            author_affiliations=author_affiliations,
            categories=(primary_category,),
            primary_category=primary_category,
        )

    def old_paper(self, authors: tuple[str, ...], text: str = "") -> Paper:
        """Builds a fixture before the supported year range."""
        return Paper(
            arxiv_id="old",
            title=text,
            authors=authors,
            abstract="THUDM",
            published=datetime(2019, 12, 31, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/old",
            pdf_url="https://arxiv.org/pdf/old",
        )

    def test_people_are_exact_author_matches(self) -> None:
        paper = self.paper(
            ("Jie Tang", "Aohan Zeng", "Zhengxiao Du", "Wenyi Hong")
        )
        self.assertEqual(matched_people(paper), ["唐杰", "曾奥涵", "杜政晓", "洪文逸"])

    def test_product_matching_is_case_insensitive(self) -> None:
        paper = self.paper(("Jie Tang", "Wendi Zheng"), "CogVideoX report")
        self.assertEqual(matched_products(paper), ["CogVideo"])

    def test_org_signals_match_zhipu_and_glm_teams(self) -> None:
        paper = self.paper(("GLM-V Team", "AutoGLMTEAM", "Z.AI Research", "Zhipu AI"))
        self.assertEqual(
            matched_org_signals(paper),
            ["GLM-V Team", "AutoGLMTEAM", "Z.AI Research", "Zhipu AI"],
        )

    def test_zhipu_person_name_is_not_org_signal(self) -> None:
        paper = self.paper(("Zhipu Liu", "Zhipu Cui", "Zhipu Zhou"))
        self.assertEqual(matched_org_signals(paper), [])

    def test_every_jie_tang_paper_is_an_ambiguous_candidate(self) -> None:
        paper = self.paper(("Jie Tang", "Someone Else"), "Graph learning")
        candidates = build_candidates({paper.arxiv_id: paper})
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["hard_selected"])
        self.assertIn("exact-author:Jie Tang", candidates[0]["evidence"])

    def test_tsinghua_affiliation_and_text_mentions_are_distinct(self) -> None:
        paper = self.paper(
            ("Jie Tang",),
            "A THUDM benchmark",
            author_affiliations=(
                ("Jie Tang", "Department of Computer Science, Tsinghua University"),
            ),
        )
        self.assertEqual(
            matched_tsinghua_signals(paper),
            [
                (
                    "founder-affiliation:Jie Tang=Department of Computer "
                    "Science, Tsinghua University"
                ),
                "text:THUDM",
            ],
        )

    def test_coauthor_tsinghua_affiliation_is_not_founder_affiliation(
        self,
    ) -> None:
        paper = self.paper(
            ("Jie Tang", "Someone Else"),
            "System card",
            author_affiliations=(("Someone Else", "Tsinghua University"),),
        )
        self.assertEqual(
            matched_tsinghua_signals(paper),
            ["coauthor-affiliation:Someone Else=Tsinghua University"],
        )
        candidate = build_candidates({paper.arxiv_id: paper})[0]
        self.assertFalse(candidate["hard_selected"])

    def test_plain_tsinghua_text_is_not_a_hard_founder_signal(self) -> None:
        paper = self.paper(("Jie Tang",), "A Tsinghua benchmark")
        candidate = build_candidates({paper.arxiv_id: paper})[0]
        self.assertFalse(candidate["hard_selected"])

    def test_pre_2020_papers_are_ignored(self) -> None:
        paper = self.old_paper(("Jie Tang", "Xiao Liu"), "A THUDM benchmark")
        self.assertEqual(build_candidates({paper.arxiv_id: paper}), [])

    def test_heuristic_tag_separates_product_and_academic(self) -> None:
        self.assertEqual(heuristic_tag(True, ["GLM"], []), "产品相关")
        self.assertEqual(
            heuristic_tag(False, [], ["inference"]), "产品技术支持"
        )
        self.assertEqual(heuristic_tag(False, [], []), "非产品相关")

    def test_tag_policy_requires_an_explicit_product_link(self) -> None:
        direct = build_candidates(
            {"1": self.paper(("Jie Tang",), "GLM technical report")}
        )[0]
        support = build_candidates(
            {
                "1": self.paper(
                    ("Jie Tang",),
                    "CompactionRL",
                    abstract="Deployed in the GLM-5 training pipeline",
                )
            }
        )[0]
        academic = build_candidates(
            {"1": self.paper(("Jie Tang",), "A generic benchmark")}
        )[0]
        self.assertEqual(enforce_tag_policy(direct, "产品相关"), "产品相关")
        self.assertEqual(
            enforce_tag_policy(support, "产品相关"), "产品技术支持"
        )
        self.assertEqual(
            enforce_tag_policy(academic, "产品技术支持"), "非产品相关"
        )

    def test_org_signal_is_candidate_without_two_people(self) -> None:
        paper = self.paper(("GLM Team", "Someone Else"), "Technical report")
        candidates = build_candidates({paper.arxiv_id: paper})
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["hard_selected"])
        self.assertIn("organization:GLM Team", candidates[0]["evidence"])

    def test_affiliation_can_supply_org_signal(self) -> None:
        paper = self.paper(
            ("Someone Else",),
            "Technical report",
            author_affiliations=(("Someone Else", "Zhipu AI"),),
        )
        self.assertEqual(matched_org_signals(paper), ["affiliation:Zhipu AI"])

    def test_llm_rejection_removes_only_ambiguous_candidate(self) -> None:
        paper = self.paper(("Jie Tang",), "Graph learning")
        candidates = build_candidates({paper.arxiv_id: paper})
        rows = approved_rows(
            candidates,
            {
                paper.arxiv_id: {
                    "relevant": False,
                    "translated_title": "图学习",
                    "tag": "非产品相关",
                }
            },
            {paper.arxiv_id: paper},
        )
        self.assertEqual(rows, [])

    def test_hard_selection_overrides_llm_rejection(self) -> None:
        paper = self.paper(("GLM Team",), "GLM report")
        candidates = build_candidates({paper.arxiv_id: paper})
        rows = approved_rows(
            candidates,
            {
                paper.arxiv_id: {
                    "relevant": False,
                    "translated_title": "GLM 报告",
                    "tag": "产品相关",
                }
            },
            {paper.arxiv_id: paper},
        )
        self.assertEqual([row["arxiv_id"] for row in rows], [paper.arxiv_id])

    def test_hard_selection_uses_precomputed_people_counts(self) -> None:
        three_people = self.paper(
            ("Jie Tang", "Aohan Zeng", "Zhengxiao Du"),
            "Graph learning",
        )
        product_and_two = self.paper(
            ("Aohan Zeng", "Zhengxiao Du"),
            "CogView report",
        )
        two_without_product = self.paper(
            ("Aohan Zeng", "Zhengxiao Du"),
            "Graph learning",
        )
        jie_with_thudm = self.paper(("Jie Tang",), "THUDM benchmark")
        self.assertTrue(build_candidates({"1": three_people})[0]["hard_selected"])
        self.assertTrue(build_candidates({"1": product_and_two})[0]["hard_selected"])
        self.assertFalse(
            build_candidates({"1": two_without_product})[0]["hard_selected"]
        )
        self.assertTrue(build_candidates({"1": jie_with_thudm})[0]["hard_selected"])

    def test_curated_exclusion_is_removed_from_candidates_and_history(self) -> None:
        paper = self.paper(
            ("Minjie Hua",),
            "GLM-5 Serving Parameter Tuning",
            arxiv_id="2607.02518",
        )
        self.assertEqual(build_candidates({paper.arxiv_id: paper}), [])
        existing = {
            paper.arxiv_id: {
                "arxiv_id": paper.arxiv_id,
                "published": "2026-07-07",
            }
        }
        self.assertEqual(merge_rows("incremental", existing, []), [])

        system_card = self.paper(
            ("Jie Tang",),
            "OpenAI GPT-5 System Card",
            arxiv_id="2601.03267",
        )
        self.assertEqual(
            build_candidates({system_card.arxiv_id: system_card}),
            [],
        )

    def test_string_false_is_not_truthy(self) -> None:
        self.assertFalse(normalize_relevant("false"))
        self.assertTrue(normalize_relevant(True))
        with self.assertRaises(ValueError):
            normalize_relevant("no")

    def test_duplicate_batch_translations_are_rejected(self) -> None:
        results = [
            {
                "arxiv_id": "1",
                "relevant": True,
                "translated_title": "同一个标题",
                "topic_tags": ["文本", "模型"],
                "institutions": [],
            },
            {
                "arxiv_id": "2",
                "relevant": True,
                "translated_title": "同一个标题",
                "topic_tags": ["代码", "评测"],
                "institutions": [],
            },
        ]
        with self.assertRaisesRegex(ValueError, "duplicate translations"):
            validate_review_results(results, ["1", "2"])

    def test_topic_tags_are_deduplicated_and_limited_to_vocabulary(self) -> None:
        self.assertEqual(
            normalize_topic_tags(
                ["文本", "模型", "文本", "unknown", "推理", "代码", "智能体", "评测"]
            ),
            ["文本", "模型", "推理", "代码", "智能体"],
        )

    def test_public_row_keeps_abstract_institutions_and_topic_tags(self) -> None:
        paper = self.paper(
            ("GLM Team",),
            "GLM report",
            abstract="A complete arXiv abstract.",
        )
        candidate = build_candidates({paper.arxiv_id: paper})[0]
        row = row_from_candidate(
            candidate,
            {
                "relevant": True,
                "translated_title": "GLM 报告",
                "tag": "产品相关",
                "topic_tags": ["文本", "模型"],
                "institutions": ["Z.AI", "Z.AI"],
            },
            paper,
        )
        self.assertEqual(row["abstract"], "A complete arXiv abstract.")
        self.assertEqual(row["institutions"], ["Z.AI"])
        self.assertEqual(row["topic_tags"], ["文本", "模型"])
        self.assertTrue(row["metadata_enriched"])

    def test_verified_two_person_team_papers_are_preserved(self) -> None:
        self.assertIn(
            (
                "Relay Diffusion: Unifying diffusion process across resolutions "
                "for image synthesis"
            ),
            VERIFIED_TITLES,
        )

    def test_existing_papers_dates_are_timezone_aware(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "papers.json"
            path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "arxiv_id": "2601.00001",
                                "title": "GLM report",
                                "authors": "Jie Tang, Aohan Zeng",
                                "published": "2026-01-01",
                                "arxiv_url": "https://arxiv.org/abs/2601.00001",
                                "pdf_url": "https://arxiv.org/pdf/2601.00001",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _, papers = load_existing_state(path)
            self.assertIsNotNone(papers["2601.00001"].published.tzinfo)

    def test_date_limited_query_uses_arxiv_submitted_date_range(self) -> None:
        query = date_limited_query(
            'au:"Jie Tang"',
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(
            query,
            '(au:"Jie Tang") AND submittedDate:[202601010000 TO 202607100000]',
        )

    def test_search_uses_three_combined_recall_queries(self) -> None:
        queries = arxiv_queries()
        self.assertEqual([label for label, _ in queries], [
            "Jie Tang",
            "trusted author pairs",
            "company/product/support signals",
        ])
        self.assertIn('au:"Jie Tang"', queries[0][1])
        self.assertIn('au:"Aohan Zeng" AND au:"Wenyi Hong"', queries[1][1])
        self.assertIn('ti:"GLM"', queries[2][1])
        self.assertIn('ti:"Phone-Use"', queries[2][1])

    def test_parse_feed_keeps_affiliations_and_categories(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2601.00001v1</id>
            <title>Example paper</title>
            <summary>Example abstract</summary>
            <published>2026-01-01T00:00:00Z</published>
            <author>
              <name>Jie Tang</name>
              <arxiv:affiliation>Tsinghua University</arxiv:affiliation>
            </author>
            <link rel="alternate" href="http://arxiv.org/abs/2601.00001v1" />
            <link title="pdf" href="http://arxiv.org/pdf/2601.00001v1" />
            <arxiv:primary_category term="cs.AI" />
            <category term="cs.AI" />
            <category term="cs.CL" />
          </entry>
        </feed>"""
        paper = parse_feed(xml)[0]
        self.assertEqual(
            paper.author_affiliations, (("Jie Tang", "Tsinghua University"),)
        )
        self.assertEqual(paper.categories, ("cs.AI", "cs.CL"))
        self.assertEqual(paper.primary_category, "cs.AI")

    def test_incremental_window_uses_existing_latest_date_with_overlap(self) -> None:
        paper = self.paper(("Jie Tang",), "GLM report")
        label, since, until = incremental_window(
            {paper.arxiv_id: paper},
            datetime(2026, 1, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(label, "since-2025-12-18")
        self.assertEqual(since.date().isoformat(), "2025-12-18")
        self.assertEqual(until.date().isoformat(), "2026-01-21")

    def test_unknown_sync_mode_defaults_to_incremental(self) -> None:
        self.assertEqual(normalize_sync_mode("full"), "full")
        self.assertEqual(normalize_sync_mode("surprise"), "incremental")

    def test_full_rebuild_clears_old_rows_but_incremental_preserves_them(self) -> None:
        old = {
            "old": {"arxiv_id": "old", "published": "2024-01-01"},
        }
        new = [{"arxiv_id": "new", "published": "2026-01-01"}]
        self.assertEqual(
            [row["arxiv_id"] for row in merge_rows("full", old, new)],
            ["new"],
        )
        self.assertEqual(
            [row["arxiv_id"] for row in merge_rows("incremental", old, new)],
            ["new", "old"],
        )

    def test_incremental_merge_preserves_known_institutions(self) -> None:
        existing = {
            "1": {
                "arxiv_id": "1",
                "published": "2025-01-01",
                "institutions": ["Z.AI"],
            }
        }
        reviewed = [
            {
                "arxiv_id": "1",
                "published": "2025-01-01",
                "institutions": [],
            }
        ]
        rows = merge_rows("incremental", existing, reviewed)
        self.assertEqual(rows[0]["institutions"], ["Z.AI"])
