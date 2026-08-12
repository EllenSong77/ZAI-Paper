"""Unit tests for backfill_translated_abstracts.py helpers.

Covers the network-free helpers (_parse_json_array, _classify_model). No
network access and no real GLM credentials.

Author:
    Ellen Song <jiaqi.song@z.ai>
    Modified by Wethepe <dongyangyan@stu.pku.edu.cn>
"""

from __future__ import annotations

import importlib.util as _ilu
import sys
import unittest
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parent.parent

# Load the backfill script as a module so its private helpers can be tested.
_backfill_spec = _ilu.spec_from_file_location(
    "backfill_translated_abstracts",
    str(ROOT / "backfill_translated_abstracts.py"),
)
assert _backfill_spec is not None and _backfill_spec.loader is not None
backfill = _ilu.module_from_spec(_backfill_spec)
_backfill_spec.loader.exec_module(backfill)


class BackfillParserTests(TestCase):
    """Covers the backfill helpers without touching the network."""

    def test_parse_json_array_handles_plain(self):
        result = backfill._parse_json_array('[{"arxiv_id":"1","translated_abstract":"A"}]')
        self.assertEqual(result, [{"arxiv_id": "1", "translated_abstract": "A"}])

    def test_parse_json_array_handles_markdown_fence(self):
        result = backfill._parse_json_array(
            "```json\n[{\"arxiv_id\":\"2\",\"translated_abstract\":\"中文\"}]\n```"
        )
        self.assertEqual(result[0]["translated_abstract"], "中文")

    def test_parse_json_array_handles_prose_prefix(self):
        result = backfill._parse_json_array(
            "好的，以下是翻译：[{\"arxiv_id\":\"3\",\"translated_abstract\":\"x\"}]"
        )
        self.assertEqual(result[0]["arxiv_id"], "3")

    def test_parse_json_array_rejects_non_array(self):
        with self.assertRaises(ValueError):
            backfill._parse_json_array("not json at all")

    def test_classify_model_defaults(self):
        self.assertEqual(backfill._classify_model(None), "glm-5-turbo")
        self.assertEqual(backfill._classify_model("  glm-5.2  "), "glm-5.2")


if __name__ == "__main__":
    unittest.main()
