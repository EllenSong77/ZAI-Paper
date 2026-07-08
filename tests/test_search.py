from datetime import datetime, timezone
from unittest import TestCase

from search_papers import (
    VERIFIED_TITLES,
    Paper,
    build_candidates,
    heuristic_tag,
    matched_org_signals,
    matched_people,
    matched_products,
    matched_tsinghua_signals,
    split_journal_ref,
)


class RuleTests(TestCase):
    def paper(self, authors: tuple[str, ...], text: str = "") -> Paper:
        return Paper(
            arxiv_id="1",
            title=text,
            authors=authors,
            abstract="",
            published=datetime(2026, 1, 1, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/1",
            pdf_url="https://arxiv.org/pdf/1",
            journal_ref="Example Journal, 12(3), 2026",
        )

    def old_paper(self, authors: tuple[str, ...], text: str = "") -> Paper:
        return Paper(
            arxiv_id="old",
            title=text,
            authors=authors,
            abstract="THUDM",
            published=datetime(2019, 12, 31, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/old",
            pdf_url="https://arxiv.org/pdf/old",
            journal_ref="",
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

    def test_jie_tang_with_tsinghua_signal_is_automatic_candidate(self) -> None:
        paper = self.paper(("Jie Tang", "Someone Else"), "A THUDM benchmark")
        candidates = build_candidates({paper.arxiv_id: paper})
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["automatic"])
        self.assertEqual(matched_tsinghua_signals(paper), ["THUDM"])

    def test_pre_2020_papers_are_ignored(self) -> None:
        paper = self.old_paper(("Jie Tang", "Xiao Liu"), "A THUDM benchmark")
        self.assertEqual(build_candidates({paper.arxiv_id: paper}), [])

    def test_heuristic_tag_separates_product_and_academic(self) -> None:
        product = self.paper(("Jie Tang", "Aohan Zeng"), "GLM technical report")
        academic = self.paper(("Jie Tang", "Aohan Zeng"), "Graph learning")
        self.assertEqual(heuristic_tag(product, ["GLM"], []), "产品强相关")
        self.assertEqual(heuristic_tag(academic, [], []), "学术输出")

    def test_org_signal_is_automatic_candidate_without_two_people(self) -> None:
        paper = self.paper(("GLM Team", "Someone Else"), "Technical report")
        candidates = build_candidates({paper.arxiv_id: paper})
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["automatic"])
        self.assertEqual(candidates[0]["org_signals"], ["GLM Team"])

    def test_journal_split(self) -> None:
        self.assertEqual(
            split_journal_ref("Example Journal, 12(3), 2026"),
            ("Example Journal", "12(3)"),
        )

    def test_verified_two_person_team_papers_are_preserved(self) -> None:
        self.assertIn(
            "Relay Diffusion: Unifying diffusion process across resolutions for image synthesis",
            VERIFIED_TITLES,
        )
