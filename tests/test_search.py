from datetime import datetime, timezone
from unittest import TestCase

from search_papers import (
    VERIFIED_TITLES,
    Paper,
    matched_people,
    matched_products,
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

    def test_people_are_exact_author_matches(self) -> None:
        paper = self.paper(("Jie Tang", "Aohan Zeng", "Someone Else"))
        self.assertEqual(matched_people(paper), ["唐杰", "曾奥涵"])

    def test_product_matching_is_case_insensitive(self) -> None:
        paper = self.paper(("Jie Tang", "Wendi Zheng"), "CogVideoX report")
        self.assertEqual(matched_products(paper), ["CogVideoX"])

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
