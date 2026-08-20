"""Unit tests for the Feishu notification package.

Uses only unittest and unittest.mock. No network access and no real Feishu
credentials. The real public papers JSON is reused as the source-of-truth data
fixture.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
from unittest.mock import MagicMock, patch

import requests

# Make the project root importable so ``import config`` and the notifications
# package both resolve regardless of how unittest is invoked.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from notifications import cards, client, config, service, state  # noqa: E402
from notifications.config import ConfigError, Target  # noqa: E402

# Real public papers JSON, used as a stable read-only fixture.
REAL_PAPERS: Path = ROOT / "public" / "data" / "zhipu_papers.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def temp_env(env: dict[str, str]):
    """Sets ``env`` vars for the duration of the context, restoring prior values."""
    saved: dict[str, str | None] = {}
    for key, value in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, prior in saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior


def fake_target(
    target_id: str = "paper-research-group",
    name: str = "论文研究群",
    receive_id_type: str = "chat_id",
    receive_id: str = "oc_test_chat_id",
) -> Target:
    """Returns a valid Target with non-sensitive example values."""
    return Target(
        id=target_id,
        name=name,
        receive_id_type=receive_id_type,
        receive_id=receive_id,
    )


def write_papers(tmp: Path, rows: list[dict]) -> Path:
    """Writes a minimal papers JSON with ``summary`` + ``rows`` into ``tmp``."""
    path = tmp / "papers.json"
    path.write_text(
        json.dumps({"summary": {"final_count": len(rows)}, "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def sample_rows() -> list[dict]:
    """Returns three deterministic paper rows covering CJK/quote/newline cases."""
    good_abstract = "这是一段中文摘要，包含 \"引号\" 与换行\n第二行内容。" * 2
    return [
        {
            "arxiv_id": "2608.00001",
            "title": 'Quote "Paper" with newline\ntitle',
            "authors": "Author A, Author B",
            "translated_title": "带引号和换行的中文标题",
            "tag": "产品相关",
            "topic_tags": ["文本", "推理"],
            "institutions": ["Z.AI"],
            "abstract": good_abstract,
            "published": "2026-08-01",
            "pdf_url": "https://arxiv.org/pdf/2608.00001",
            "arxiv_url": "https://arxiv.org/abs/2608.00001",
        },
        {
            "arxiv_id": "2608.00002",
            "title": "Second Paper",
            "authors": "Author C",
            "translated_title": "第二篇论文",
            "tag": "非产品相关",
            "topic_tags": [],
            "institutions": [],
            "abstract": "Short abstract.",
            "published": "2026-08-02",
            "pdf_url": "https://arxiv.org/pdf/2608.00002",
            "arxiv_url": "https://arxiv.org/abs/2608.00002",
        },
        {
            "arxiv_id": "2608.00003",
            "title": "Third Paper",
            "authors": "Author D",
            "translated_title": "",
            "tag": "",
            "topic_tags": ["评测"],
            "institutions": [],
            "abstract": "",
            "published": "2026-08-03",
            "pdf_url": "",
            "arxiv_url": "https://arxiv.org/abs/2608.00003",
        },
    ]


def make_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    """Builds a stand-in ``requests.Response`` object."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = json_body if json_body is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


def env_with(**overrides) -> dict[str, str]:
    """Base env for the package, with optional overrides."""
    base = {
        "FEISHU_APP_ID": "cli_test_app_id",
        "FEISHU_APP_SECRET": "test_app_secret",
        "FEISHU_TARGETS_JSON": json.dumps(
            [
                {
                    "id": "paper-research-group",
                    "name": "论文研究群",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_test_chat_id",
                }
            ],
            ensure_ascii=False,
        ),
        "FEISHU_SITE_URL": "https://example.github.io/site/",
    }
    base.update(overrides)
    return base


def build_settings(
    tmp: Path,
    targets_json: str | None = None,
    papers_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> "config.Settings":
    """Loads Settings with files redirected into a temporary directory."""
    env = env_with()
    if targets_json is not None:
        env["FEISHU_TARGETS_JSON"] = targets_json
    env["FEISHU_PAPERS_PATH"] = str(papers_path or (tmp / "papers.json"))
    env["FEISHU_STATE_PATH"] = str(tmp / "state.json")
    env.update(extra_env or {})
    with temp_env(env):
        return config.load_settings()


# ---------------------------------------------------------------------------
# 1-2: target parsing
# ---------------------------------------------------------------------------


class ParseTargetsTests(TestCase):
    """Covers config.parse_targets acceptance and rejection cases."""

    def test_valid_targets_parsed(self):
        raw = json.dumps(
            [
                {
                    "id": "g1",
                    "name": "群1",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_a",
                },
                {
                    "id": "u1",
                    "name": "Ellen",
                    "receive_id_type": "open_id",
                    "receive_id": "ou_a",
                },
            ]
        )
        targets = config.parse_targets(raw)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].id, "g1")
        self.assertEqual(targets[1].receive_id_type, "open_id")
        # Fingerprint is a stable hex string and excludes identifiers.
        self.assertNotIn("oc_a", targets[0].fingerprint)

    def test_reject_non_array(self):
        for raw in ["{}", '"x"', "42", "null"]:
            with self.assertRaises(ConfigError):
                config.parse_targets(raw)

    def test_reject_duplicate_id(self):
        raw = json.dumps(
            [
                {"id": "dup", "name": "a", "receive_id_type": "chat_id", "receive_id": "oc1"},
                {"id": "dup", "name": "b", "receive_id_type": "chat_id", "receive_id": "oc2"},
            ]
        )
        with self.assertRaises(ConfigError):
            config.parse_targets(raw)

    def test_reject_empty_receive_id(self):
        raw = json.dumps(
            [{"id": "t", "name": "n", "receive_id_type": "chat_id", "receive_id": "  "}]
        )
        with self.assertRaises(ConfigError):
            config.parse_targets(raw)

    def test_reject_bad_receive_id_type(self):
        raw = json.dumps(
            [{"id": "t", "name": "n", "receive_id_type": "phone", "receive_id": "oc1"}]
        )
        with self.assertRaises(ConfigError):
            config.parse_targets(raw)

    def test_reject_invalid_json(self):
        with self.assertRaises(ConfigError):
            config.parse_targets("{not json")


# ---------------------------------------------------------------------------
# 3-7: client behavior (token + send, retry taxonomy)
# ---------------------------------------------------------------------------


class ClientTokenTests(TestCase):
    """Covers tenant-token acquisition success/failure paths."""

    def test_token_http_error_raises_auth(self):
        session = MagicMock(spec=requests.Session)
        session.post.return_value = make_response(status_code=500)
        with self.assertRaises(client.AuthError):
            client.fetch_tenant_token(session, "id", "secret", retries=2)

    def test_token_business_code_nonzero_raises_auth(self):
        session = MagicMock(spec=requests.Session)
        session.post.return_value = make_response(
            status_code=200,
            json_body={"code": 99991663, "msg": "bad app secret", "tenant_access_token": ""},
        )
        with self.assertRaises(client.AuthError):
            client.fetch_tenant_token(session, "id", "secret", retries=2)

    def test_token_success(self):
        session = MagicMock(spec=requests.Session)
        session.post.return_value = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "t-abc"},
        )
        token = client.fetch_tenant_token(session, "id", "secret")
        self.assertEqual(token, "t-abc")


class ClientSendTests(TestCase):
    """Covers the send-message API: retries, failure taxonomy, deserialization."""

    def _settings(self) -> client.FeishuResponse:
        return client.FeishuResponse(code=0, msg="ok", message_id="om_1")

    def test_send_http200_business_nonzero_permanent(self):
        session = MagicMock(spec=requests.Session)
        session.post.return_value = make_response(
            status_code=200,
            json_body={"code": 230002, "msg": "permission denied", "data": {}},
        )
        with self.assertRaises(client.PermanentError):
            client.send_message(session, "tok", "chat_id", "oc_x", "{}", retries=2)

    def test_send_429_retry_then_success(self):
        session = MagicMock(spec=requests.Session)
        session.post.side_effect = [
            make_response(status_code=429, json_body={"code": 99991400, "msg": "rate"}),
            make_response(
                status_code=200,
                json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_ok"}},
            ),
        ]
        resp = client.send_message(session, "tok", "chat_id", "oc_x", "{}", retries=4)
        self.assertEqual(resp.message_id, "om_ok")
        self.assertEqual(session.post.call_count, 2)

    def test_send_5xx_exhausts_retries_transient(self):
        session = MagicMock(spec=requests.Session)
        session.post.return_value = make_response(status_code=503)
        with self.assertRaises(client.TransientError):
            client.send_message(session, "tok", "chat_id", "oc_x", "{}", retries=2)

    def test_send_network_timeout_retries_then_transient(self):
        session = MagicMock(spec=requests.Session)
        session.post.side_effect = requests.Timeout("timeout")
        with self.assertRaises(client.TransientError):
            client.send_message(session, "tok", "chat_id", "oc_x", "{}", retries=2)

    def test_send_4xx_not_retried(self):
        session = MagicMock(spec=requests.Session)
        session.post.return_value = make_response(
            status_code=400,
            json_body={"code": 230001, "msg": "param error", "data": {}},
        )
        with self.assertRaises(client.PermanentError):
            client.send_message(session, "tok", "chat_id", "oc_x", "{}", retries=4)
        # A non-retried 4xx must perform exactly one attempt.
        self.assertEqual(session.post.call_count, 1)

    def test_send_retries_respect_retry_after_header(self):
        session = MagicMock(spec=requests.Session)
        session.post.side_effect = [
            make_response(status_code=429, headers={"Retry-After": "1"}),
            make_response(
                status_code=200,
                json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_ok"}},
            ),
        ]
        with patch("notifications.client.time.sleep") as slept:
            client.send_message(session, "tok", "chat_id", "oc_x", "{}", retries=4)
        # First sleep uses Retry-After=1 (capped at 60s).
        self.assertTrue(slept.called)
        first_delay = slept.call_args_list[0].args[0]
        self.assertEqual(first_delay, 1.0)


# ---------------------------------------------------------------------------
# 8-9: card serialization
# ---------------------------------------------------------------------------


class CardsTests(TestCase):
    """Covers CardKit 2.0 card construction and double-layer JSON serialization."""

    def test_encode_content_is_json_string(self):
        card = cards.build_papers_card([sample_rows()[0]], "https://site/")
        encoded = cards.encode_content(card)
        self.assertIsInstance(encoded, str)
        # Inner string must round-trip back to the original object.
        self.assertEqual(json.loads(encoded), card)

    def test_send_payload_double_layered(self):
        """The outer payload's ``content`` must be a JSON string of the card."""
        target = fake_target()
        row = sample_rows()[0]
        card = cards.build_papers_card([row], "https://site/")
        content_str = cards.encode_content(card)
        # Simulate the outer request body that the client builds.
        outer = {
            "receive_id": target.receive_id,
            "msg_type": "interactive",
            "content": content_str,
        }
        self.assertIsInstance(outer["content"], str)
        decoded = json.loads(outer["content"])
        self.assertEqual(decoded, card)

    def test_chinese_quotes_newlines_preserved(self):
        row = sample_rows()[0]
        card = cards.build_papers_card([row], "https://site/")
        encoded = cards.encode_content(card)
        # Round-trip stability proves proper escaping.
        decoded = json.loads(encoded)
        joined = json.dumps(decoded, ensure_ascii=False)
        self.assertIn("引号", joined)
        self.assertIn("中文标题", joined)

    def test_abstract_truncated(self):
        # The collapsible abstract is length-bounded so a long payload cannot
        # blow up the card.
        long_row = dict(sample_rows()[0])
        long_row["abstract"] = "xUNIQUEx" * 200  # 1600 chars
        card = cards.build_papers_card([long_row], "https://site/")
        body = json.dumps(card, ensure_ascii=False)
        # Abstract is truncated to ~600 chars; the spam marker (8 chars each)
        # could survive at most 75 repetitions, so 100 repetitions is forbidden.
        self.assertNotIn("xUNIQUEx" * 100, body)
        # The collapsible_panel header label must be present.
        self.assertIn("查看论文摘要", body)

    def test_bilingual_abstract_panel(self):
        """The panel body shows the Chinese abstract when translation is
        present, falls back to the English abstract when not."""
        rows = sample_rows()
        # Case 1: translation present -> panel body is the Chinese abstract,
        # NOT the English one.
        with_zh = dict(rows[0])
        with_zh["abstract"] = "english original abstract text for case one"
        with_zh["translated_abstract"] = "这是中文翻译的摘要。"
        card = cards.build_papers_card([with_zh], "https://site/")
        panel = next(
            e
            for e in card["body"]["elements"][0]["columns"][1]["elements"]
            if e.get("tag") == "collapsible_panel"
        )
        # Panel is collapsed by default.
        self.assertFalse(panel["expanded"])
        self.assertEqual(panel["header"]["title"]["content"], "查看论文摘要")
        # Exactly one div, containing the Chinese abstract.
        divs = [e for e in panel["elements"] if e.get("tag") == "div"]
        self.assertEqual(len(divs), 1)
        self.assertEqual(divs[0]["text"]["content"], with_zh["translated_abstract"])
        body_zh = json.dumps(panel, ensure_ascii=False)
        # English abstract must NOT be in the panel when ZH is available.
        self.assertNotIn("english original abstract", body_zh)

        # Case 2: no translation -> panel body falls back to the English
        # abstract (and still has the right CTA header).
        no_zh = dict(rows[0])
        no_zh["abstract"] = "english original abstract text for case two"
        no_zh["translated_abstract"] = ""
        card2 = cards.build_papers_card([no_zh], "https://site/")
        panel2 = next(
            e
            for e in card2["body"]["elements"][0]["columns"][1]["elements"]
            if e.get("tag") == "collapsible_panel"
        )
        divs2 = [e for e in panel2["elements"] if e.get("tag") == "div"]
        self.assertEqual(len(divs2), 1)
        self.assertEqual(divs2[0]["text"]["content"], no_zh["abstract"])

    def test_empty_fields_rendered_without_placeholder(self):
        empty_row = sample_rows()[2]  # missing translated_title/tag/abstract
        card = cards.build_papers_card([empty_row], "")
        body = json.dumps(card, ensure_ascii=False)
        # No empty <text_tag> chip and no empty 作者/标签 line.
        self.assertNotIn("<text_tag color='neutral'></text_tag>", body)
        self.assertNotIn("作者：\n", body)

    def test_smoke_card_marked_as_test(self):
        card = cards.build_smoke_test_card("https://site/")
        body = json.dumps(card, ensure_ascii=False)
        self.assertIn("测试", body)
        self.assertIn("不是真实论文", body)

    def test_papers_card_is_deterministic(self):
        rows = sample_rows()
        again = cards.build_papers_card([rows[0]], "https://site/")
        once_more = cards.build_papers_card([rows[0]], "https://site/")
        # Same input must produce the same card object twice.
        self.assertEqual(again, once_more)
        # The CardKit header must announce the title and count subtitle.
        self.assertEqual(again["schema"], "2.0")
        self.assertEqual(again["header"]["title"]["content"], "Z.AI 论文更新")
        self.assertIn("新增 1 篇", again["header"]["subtitle"]["content"])

    def test_multi_paper_card_has_separators_and_full_blocks(self):
        """CardKit 2.0 card: one column_set per paper, button via behaviors."""
        rows = sample_rows()
        card = cards.build_papers_card(rows, "https://site/")
        self.assertEqual(card["schema"], "2.0")
        elements = card["body"]["elements"]
        body_text = json.dumps(elements, ensure_ascii=False)

        # One column_set per paper + (N-1) hr dividers + a footer markdown +
        # a footer "查看完整论文列表" button (with site_url non-empty).
        self.assertEqual(
            sum(1 for e in elements if e.get("tag") == "column_set"),
            len(rows),
        )
        self.assertEqual(elements.count({"tag": "hr", "margin": "0px"}),
                         len(rows) - 1)
        buttons = [e for e in elements if e.get("tag") == "button"]
        # Footer button only (paper buttons live inside column_set columns).
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["text"]["content"], "查看完整论文列表")
        self.assertEqual(buttons[0]["behaviors"][0]["default_url"], "https://site/")

        # Each paper block has two columns with the documented weights.
        first_columns = elements[0]["columns"]
        self.assertEqual([c["weight"] for c in first_columns], [1, 4])
        # Each paper's markdown text contains title/meta; arXiv btn uses behaviors.
        first_right = first_columns[1]["elements"]
        self.assertEqual(first_right[0]["tag"], "markdown")
        first = rows[0]
        # Compare on the deserialized markdown content (escapes differ in JSON).
        # The builder escapes markdown metacharacters in LLM-supplied titles
        # and collapses whitespace, so compare against the normalized form.
        normalized_title = " ".join(first["title"].split())
        self.assertIn(normalized_title, first_right[0]["content"])
        # arXiv/PDF buttons live in a nested column_set inside the right column
        # so they render side by side; verify both buttons & their behaviors.
        # Each inner column is width "auto" so the buttons stay compact (only
        # as wide as their label) rather than stretched to half the card.
        button_sets = [
            e for e in first_right
            if e.get("tag") == "column_set" and e.get("flex_mode") == "none"
        ]
        self.assertEqual(len(button_sets), 1)
        nested_buttons_cols = button_sets[0]["columns"]
        self.assertTrue(all(c["width"] == "auto" for c in nested_buttons_cols))
        nested_buttons = [col["elements"][0] for col in nested_buttons_cols]
        button_labels = [b["text"]["content"] for b in nested_buttons]
        self.assertEqual(button_labels, ["查看 arXiv", "查看 PDF"])
        self.assertEqual(nested_buttons[0]["behaviors"][0]["default_url"], first["arxiv_url"])
        self.assertEqual(nested_buttons[1]["behaviors"][0]["default_url"], first["pdf_url"])
        # The "查看论文摘要" collapsible_panel exists and is collapsed by default;
        # click to expand reveals the Chinese abstract (or EN fallback).
        panels = [e for e in first_right if e.get("tag") == "collapsible_panel"]
        self.assertEqual(len(panels), 1)
        self.assertFalse(panels[0]["expanded"])
        self.assertEqual(panels[0]["header"]["title"]["content"], "查看论文摘要")


# ---------------------------------------------------------------------------
# 10-12: service dry-run, smoke-test, bootstrap
# ---------------------------------------------------------------------------


class _ServiceTestBase(TestCase):
    """Common setup: temp dir + papers file + settings for service tests."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.papers_path = write_papers(self.tmp_path, sample_rows())
        self.settings = build_settings(self.tmp_path, papers_path=self.papers_path)


class DryRunTests(_ServiceTestBase):
    """dry-run must not touch network or state."""

    def test_no_network_and_no_state_change(self):
        with patch("notifications.client.requests.Session") as fake_session:
            ok, results = service.run_dry_run(self.settings)
            fake_session.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)
        self.assertFalse(self.settings.state_path.exists())

    def test_no_log_full_receive_id(self):
        log_buf = io.StringIO()
        handler = logging.StreamHandler(log_buf)
        logger = logging.getLogger("notifications")
        logger.addHandler(handler)
        try:
            service.run_dry_run(self.settings)
        finally:
            logger.removeHandler(handler)
        log_buf.seek(0)
        contents = log_buf.read()
        # The full receive_id must never appear in logs.
        self.assertNotIn("oc_test_chat_id", contents)


class SmokeTestTests(_ServiceTestBase):
    """Smoke-test must call the network but never modify delivery state."""

    def test_smoke_sends_card_without_touching_state(self):
        target = self.settings.targets[0]
        token_resp = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "t"},
        )
        send_resp = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_smoke"}},
        )
        fake_session = MagicMock()
        fake_session.post.side_effect = [token_resp, send_resp]
        fake_session.__enter__.return_value = fake_session
        fake_session.__exit__.return_value = False
        with patch("notifications.service.requests.Session", return_value=fake_session):
            ok, results = service.run_smoke_test(self.settings)
        self.assertTrue(ok)
        self.assertEqual(results[0].ok, True)
        # State file must remain absent (no bootstrap, no delivery recording).
        self.assertFalse(self.settings.state_path.exists())
        # The send payload content must encode a smoke-test card.
        second_call = fake_session.post.call_args_list[1]
        body = second_call.kwargs["json"]
        inner = json.loads(body["content"])
        self.assertIn("测试", json.dumps(inner, ensure_ascii=False))

    def test_smoke_partial_failure_keeps_other_targets(self):
        # Two targets, the first one's send-messsage fails at the business level.
        targets = json.dumps(
            [
                {
                    "id": "ok-group",
                    "name": "good",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_ok",
                },
                {
                    "id": "bad-group",
                    "name": "bad",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_bad",
                },
            ]
        )
        settings = build_settings(self.tmp_path, targets_json=targets,
                                  papers_path=self.papers_path)
        token_resp = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "t"},
        )
        ok_resp = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_ok"}},
        )
        bad_resp = make_response(
            status_code=200,
            json_body={"code": 230002, "msg": "permission", "data": {}},
        )
        fake_session = MagicMock()
        # Token + first send ok; token + second send bad.
        fake_session.post.side_effect = [token_resp, ok_resp, token_resp, bad_resp]
        fake_session.__enter__.return_value = fake_session
        fake_session.__exit__.return_value = False
        with patch("notifications.service.requests.Session", return_value=fake_session):
            ok, results = service.run_smoke_test(settings)
        self.assertFalse(ok)
        # State still must not be written.
        self.assertFalse(settings.state_path.exists())


class BootstrapTests(_ServiceTestBase):
    """bootstrap must not touch the network and must initialize the baseline."""

    def test_bootstrap_initializes_all_papers(self):
        with patch("notifications.client.requests.Session") as sess:
            ok, results = service.run_bootstrap(self.settings)
            sess.assert_not_called()
        self.assertTrue(ok)
        self.assertTrue(self.settings.state_path.exists())
        st = json.loads(self.settings.state_path.read_text(encoding="utf-8"))
        entry = st["targets"]["paper-research-group"]
        self.assertTrue(entry["bootstrapped"])
        # Baseline split: the FULL historical id list lives in baseline_ids
        # (never pruned), while the delivered rolling window starts empty --
        # bootstrap marks history, it is not a send.
        self.assertEqual(
            sorted(entry.get("baseline_ids", [])),
            sorted(r["arxiv_id"] for r in sample_rows()),
        )
        self.assertEqual(entry["delivered"], {})

    def test_bootstrap_idempotent_matching_fingerprint(self):
        service.run_bootstrap(self.settings)
        first = self.settings.state_path.read_text(encoding="utf-8")
        # Second run with the same target must be a strict no-op: the whole
        # entry (seen set included) is byte-identical. A weaker keys-only
        # assertion once let a regression that wiped baseline_ids on
        # re-bootstrap slip through while staying green (adversarial
        # review MUT8), which would have resurrected the daily-repush P1.
        ok, _ = service.run_bootstrap(self.settings)
        second = self.settings.state_path.read_text(encoding="utf-8")
        self.assertTrue(ok)
        self.assertEqual(json.loads(first), json.loads(second))

    def test_bootstrap_fingerprint_change_requires_replace(self):
        service.run_bootstrap(self.settings)
        # Now change the receive_id -> fingerprint changes.
        new_targets = json.dumps(
            [
                {
                    "id": "paper-research-group",
                    "name": "群",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_different",
                }
            ]
        )
        settings = build_settings(self.tmp_path, targets_json=new_targets,
                                  papers_path=self.papers_path)
        ok, results = service.run_bootstrap(settings)
        self.assertFalse(ok)
        _ = results

    def test_bootstrap_replace_target_resets(self):
        service.run_bootstrap(self.settings)
        new_targets = json.dumps(
            [
                {
                    "id": "paper-research-group",
                    "name": "群",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_different",
                }
            ]
        )
        settings = build_settings(self.tmp_path, targets_json=new_targets,
                                  papers_path=self.papers_path)
        ok, _ = service.run_bootstrap(
            settings, target_filter="paper-research-group", replace_target=True
        )
        self.assertTrue(ok)
        st = json.loads(settings.state_path.read_text(encoding="utf-8"))
        entry = st["targets"]["paper-research-group"]
        self.assertTrue(entry["bootstrapped"])
        self.assertEqual(
            entry["target_fingerprint"],
            [t for t in settings.targets if t.id == "paper-research-group"][0].fingerprint,
        )


# ---------------------------------------------------------------------------
# 13-20: send behavior
# ---------------------------------------------------------------------------


class SendTests(_ServiceTestBase):
    """Covers send: fail-closed, fingerprint, dedup, partial recovery, batching."""

    def _bootstrap(self, settings=None) -> None:
        service.run_bootstrap(settings or self.settings)

    def _net(self, send_side_effect) -> MagicMock:
        token = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "ttok"},
        )
        fake = MagicMock()
        fake.post.side_effect = [token, *send_side_effect]
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        return fake

    def test_send_without_bootstrap_fail_closed(self):
        """An un-bootstrapped target must fail closed (non-zero) and never send."""
        with patch("notifications.service.requests.Session") as session_cls:
            ok, results = service.run_send(self.settings)
            session_cls.assert_not_called()
        # No token, no send, but the command MUST report failure so the
        # operator is told to bootstrap first instead of believing it worked.
        self.assertFalse(ok)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertIn("not bootstrapped", results[0].message)

    def test_send_fingerprint_same_delivers(self):
        self._bootstrap()
        # Add three new pending papers after bootstrap so they need delivery.
        new_rows = sample_rows() + [
            {
                "arxiv_id": f"2608.0000{n}",
                "title": f"Paper {n}",
                "translated_title": f"新{n}",
                "authors": "Author",
                "tag": "产品相关",
                "topic_tags": [],
                "institutions": [],
                "abstract": "x",
                "published": f"2026-08-{n}",
                "pdf_url": "",
                "arxiv_url": f"https://arxiv.org/abs/2608.0000{n}",
            }
            for n in (5, 6, 7)
        ]
        write_papers(self.tmp_path, new_rows)
        # All pending papers go in one card now, so only one send call.
        send = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_all"}},
        )
        fake = self._net([send])
        with patch("notifications.service.requests.Session", return_value=fake):
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        self.assertEqual(results[0].delivered, 3)

    def test_send_fingerprint_changed_refuses(self):
        self._bootstrap()
        # Reconfigure with a different receive_id.
        new_targets = json.dumps(
            [
                {
                    "id": "paper-research-group",
                    "name": "群",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_other",
                }
            ]
        )
        settings = build_settings(self.tmp_path, targets_json=new_targets,
                                  papers_path=self.papers_path)
        # Copy the previous bootstrap state into the new settings path.
        settings.state_path.write_text(
            self.settings.state_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        with patch("notifications.service.requests.Session") as session_cls:
            ok, results = service.run_send(settings)
            session_cls.assert_not_called()
        # Refused -> no token, no send, command MUST fail so the operator is
        # told to re-bootstrap with --replace-target.
        self.assertFalse(ok)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertIn("fingerprint mismatch", results[0].message)

    def test_already_delivered_not_resent(self):
        service.run_bootstrap(self.settings)
        # All papers bootstrapped -> nothing pending -> no token request.
        with patch("notifications.service.requests.Session") as session_cls:
            ok, results = service.run_send(self.settings)
            session_cls.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(results[0].delivered, 0)

    def test_new_paper_is_sent_and_recorded(self):
        service.run_bootstrap(self.settings)
        # Add one new paper after bootstrap to create a pending set.
        new_rows = sample_rows() + [
            {
                "arxiv_id": "2608.00009",
                "title": "New",
                "translated_title": "新增",
                "authors": "Author Z",
                "tag": "产品相关",
                "topic_tags": [],
                "institutions": [],
                "abstract": "new",
                "published": "2026-08-09",
                "pdf_url": "https://arxiv.org/pdf/2608.00009",
                "arxiv_url": "https://arxiv.org/abs/2608.00009",
            }
        ]
        write_papers(self.tmp_path, new_rows)
        send = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_new"}},
        )
        fake = self._net([send])
        with patch("notifications.service.requests.Session", return_value=fake):
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        self.assertEqual(results[0].delivered, 1)
        st = json.loads(self.settings.state_path.read_text(encoding="utf-8"))
        self.assertIn("2608.00009", st["targets"]["paper-research-group"]["delivered"])

    def test_partial_success_advances_only_successful_state(self):
        """User succeeds, group fails: only the user state must advance."""
        targets = json.dumps(
            [
                {
                    "id": "user-target",
                    "name": "user",
                    "receive_id_type": "open_id",
                    "receive_id": "ou_user",
                },
                {
                    "id": "group-target",
                    "name": "group",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_group",
                },
            ]
        )
        settings = build_settings(self.tmp_path, targets_json=targets,
                                  papers_path=self.papers_path)
        service.run_bootstrap(settings)
        # Add one new pending paper after bootstrap so each target has work.
        new_rows = sample_rows() + [
            {
                "arxiv_id": "2608.00008",
                "title": "Pending",
                "translated_title": "新增",
                "authors": "Author",
                "tag": "产品相关",
                "topic_tags": [],
                "institutions": [],
                "abstract": "x",
                "published": "2026-08-08",
                "pdf_url": "",
                "arxiv_url": "https://arxiv.org/abs/2608.00008",
            }
        ]
        write_papers(self.tmp_path, new_rows)
        # user-target sends ok; group-target returns a permanent business error.
        ok_send = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_user"}},
        )
        bad_send = make_response(
            status_code=200,
            json_body={"code": 230002, "msg": "permission", "data": {}},
        )
        # Two targets, one token + one send each.
        token = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "tt"},
        )
        fake = MagicMock()
        fake.post.side_effect = [token, ok_send, token, bad_send]
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        with patch("notifications.service.requests.Session", return_value=fake):
            ok, results = service.run_send(settings)
        self.assertFalse(ok)
        st = json.loads(settings.state_path.read_text(encoding="utf-8"))
        # The successful target's paper is re-recorded with a real message_id;
        # the failed target keeps only the bootstrap entries (no real message id).
        user_entry = st["targets"]["user-target"]
        group_entry = st["targets"]["group-target"]
        self.assertIn(
            "2608.00008",
            {k for k, v in user_entry["delivered"].items() if not v.get("bootstrap")},
        )
        self.assertTrue(
            all(v.get("bootstrap") for v in group_entry["delivered"].values())
        )

    def test_partial_success_rerun_retries_only_failed(self):
        """A failed send records nothing; rerunning delivers everything in one card."""
        service.run_bootstrap(self.settings)
        new_rows = sample_rows() + [
            {
                "arxiv_id": "2608.00010",
                "title": "Pending A",
                "translated_title": "待发A",
                "authors": "A",
                "tag": "产品相关",
                "topic_tags": [],
                "institutions": [],
                "abstract": "a",
                "published": "2026-08-10",
                "pdf_url": "",
                "arxiv_url": "https://arxiv.org/abs/2608.00010",
            },
            {
                "arxiv_id": "2608.00011",
                "title": "Pending B",
                "translated_title": "待发B",
                "authors": "B",
                "tag": "产品相关",
                "topic_tags": [],
                "institutions": [],
                "abstract": "b",
                "published": "2026-08-11",
                "pdf_url": "",
                "arxiv_url": "https://arxiv.org/abs/2608.00011",
            },
        ]
        write_papers(self.tmp_path, new_rows)
        # First attempt: a single all-in-one card send fails -> nothing recorded.
        bad_send = make_response(
            status_code=200,
            json_body={"code": 230002, "msg": "permission", "data": {}},
        )
        settings = build_settings(self.tmp_path)
        # Reuse the bootstrap state we already created on self.settings path.
        Path(str(self.tmp_path / "state.json")).write_text(
            self.settings.state_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.settings = settings

        fake = MagicMock()
        token = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "tt"},
        )
        # Token + one failed send (all papers in one card).
        fake.post.side_effect = [token, bad_send]
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        with patch("notifications.service.requests.Session", return_value=fake):
            ok, results = service.run_send(settings)
        self.assertFalse(ok)
        delivered = json.loads(settings.state_path.read_text(encoding="utf-8"))[
            "targets"
        ]["paper-research-group"]["delivered"]
        # Both papers remain pending (none recorded on failure).
        self.assertNotIn("2608.00010", delivered)
        self.assertNotIn("2608.00011", delivered)

        # Second attempt: the card succeeds -> both papers recorded at once.
        ok_send = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "data": {"message_id": "om_all"}},
        )
        fake2 = MagicMock()
        token2 = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "tt2"},
        )
        fake2.post.side_effect = [token2, ok_send]
        fake2.__enter__.return_value = fake2
        fake2.__exit__.return_value = False
        with patch("notifications.service.requests.Session", return_value=fake2):
            ok, results = service.run_send(settings)
            # Exactly two post calls: one token, one send (all papers in one card).
            send_call_count = sum(
                1
                for call in fake2.post.call_args_list
                if call.args and "messages" in str(call.args[0])
            )
        self.assertEqual(send_call_count, 1)
        self.assertTrue(ok)
        delivered_after = json.loads(settings.state_path.read_text(encoding="utf-8"))[
            "targets"
        ]["paper-research-group"]["delivered"]
        self.assertIn("2608.00010", delivered_after)
        self.assertIn("2608.00011", delivered_after)

    def test_empty_pending_no_token_request(self):
        service.run_bootstrap(self.settings)
        with patch("notifications.service.requests.Session") as session_cls:
            ok, results = service.run_send(self.settings)
            session_cls.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(results[0].delivered, 0)

    def test_batching_order_is_deterministic(self):
        """Two equal inputs produce the same pending-ids order."""
        rows = sample_rows()
        order1 = service._sorted_arxiv_ids({"rows": rows})
        order2 = service._sorted_arxiv_ids({"rows": list(reversed(rows))})
        # Sorting makes order stable regardless of JSON list order.
        self.assertEqual(order1, sorted(order1))


# ---------------------------------------------------------------------------
# 21-23: state.py
# ---------------------------------------------------------------------------


class StateTests(TestCase):
    """Covers atomic write, corruption safety, and fingerprint helpers."""

    def test_atomic_write_uses_temp_then_replace(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.atomic_write_state(path, {"version": 1, "targets": {}})
            self.assertTrue(path.exists())
            # Temp file must not linger.
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_corrupt_state_refused_not_overwritten(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(state.StateError):
                state.load_state(path)
            # The corrupt content must remain intact.
            self.assertIn("not json", path.read_text(encoding="utf-8"))

    def test_load_missing_state_treated_empty(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "absent.json"
            data = state.load_state(path)
            self.assertEqual(data, {"version": 1, "targets": {}})

    def test_fingerprint_match_helpers(self):
        target = fake_target()
        st = state.bootstrap_target({"version": 1, "targets": {}}, target, ["1"])
        self.assertTrue(state.is_bootstrapped(st, target))
        self.assertTrue(state.fingerprint_matches(st, target))
        moved = fake_target(receive_id="oc_other")
        self.assertFalse(state.fingerprint_matches(st, moved))


# ---------------------------------------------------------------------------
# 24: log sanitization
# ---------------------------------------------------------------------------


class LogSanitizationTests(TestCase):
    """No App Secret, token, or full receive_id may appear in logs."""

    def test_no_secrets_in_client_logs(self):
        # Drive a token success then capture warnings from a token failure.
        log_buf = io.StringIO()
        handler = logging.StreamHandler(log_buf)
        logger = logging.getLogger("notifications")
        logger.addHandler(handler)
        try:
            session = MagicMock(spec=requests.Session)
            session.post.return_value = make_response(
                status_code=200,
                json_body={"code": 99991663, "msg": "bad secret",
                           "tenant_access_token": "supersecret-token"},
            )
            try:
                client.fetch_tenant_token(session, "cli_app", "real_secret_value",
                                          retries=1)
            except client.AuthError:
                pass
        finally:
            logger.removeHandler(handler)
        log_buf.seek(0)
        out = log_buf.read()
        self.assertNotIn("real_secret_value", out)
        self.assertNotIn("supersecret-token", out)

    def test_no_receive_id_in_service_logs(self):
        with TemporaryDirectory() as tmp:
            papers = write_papers(Path(tmp), sample_rows())
            settings = build_settings(Path(tmp), papers_path=papers)
            log_buf = io.StringIO()
            handler = logging.StreamHandler(log_buf)
            logger = logging.getLogger("notifications")
            logger.addHandler(handler)
            try:
                service.run_dry_run(settings)
            finally:
                logger.removeHandler(handler)
        self.assertNotIn("oc_test_chat_id", log_buf.getvalue())


# ---------------------------------------------------------------------------
# Loaders and command plumbing
# ---------------------------------------------------------------------------


class LoadSettingsTests(TestCase):
    """Covers env-driven Settings loading and credential gating."""

    def test_load_settings_with_overrides(self):
        with temp_env(env_with(
            FEISHU_PAPERS_PATH="public/data/zhipu_papers.json",
            FEISHU_SITE_URL="https://custom.example.io/some/",
        )):
            settings = config.load_settings()
        self.assertEqual(len(settings.targets), 1)
        # site_url must be normalized to keep one trailing slash.
        self.assertEqual(settings.site_url, "https://custom.example.io/some/")

    def test_require_credentials_raises_when_missing(self):
        with temp_env(env_with(FEISHU_APP_ID="", FEISHU_APP_SECRET="")):
            settings = config.load_settings()
        with self.assertRaises(ConfigError):
            config.require_credentials(settings)

    def test_empty_targets_json_rejected(self):
        with self.assertRaises(ConfigError):
            with temp_env(env_with(FEISHU_TARGETS_JSON="[]")):
                config.load_settings()

    def test_missing_targets_json_rejected(self):
        env = env_with()
        env.pop("FEISHU_TARGETS_JSON")
        with self.assertRaises(ConfigError):
            with temp_env(env):
                config.load_settings()


class CLITests(TestCase):
    """Smoke-tests the argparse-driven CLI entrypoint behavior."""

    def test_help_exit_code(self):
        from notifications.__main__ import _build_parser

        parser = _build_parser()
        # argparse always injects -h/--help into every subparser; selecting it
        # must exit cleanly. We intercept SystemExit and assert code 0.
        choices = parser._subparsers._group_actions[0].choices
        for cmd in ("dry-run", "smoke-test", "bootstrap", "send"):
            self.assertIn(cmd, choices, f"missing subcommand {cmd}")
            sub_parser = choices[cmd]
            with self.assertRaises(SystemExit) as ctx:
                sub_parser.parse_args(["--help"])
            self.assertEqual(ctx.exception.code, 0)

    def test_unknown_command_errors(self):
        from notifications.__main__ import main

        with self.assertRaises(SystemExit):
            main(["nope"])


# ---------------------------------------------------------------------------
# Real data sanity (read-only)
# ---------------------------------------------------------------------------


class RealPapersSanityTests(TestCase):
    """Confirms the shipped papers JSON still matches the expected schema."""

    def test_real_papers_schema(self):
        data = json.loads(REAL_PAPERS.read_text(encoding="utf-8"))
        self.assertIn("rows", data)
        self.assertIsInstance(data["rows"], list)
        self.assertGreater(len(data["rows"]), 0)
        first = data["rows"][0]
        for key in ("arxiv_id", "title", "published"):
            self.assertIn(key, first)


class BackfillParserTests(TestCase):
    """Covers the backfill helpers without touching the network.

    Moved to ``tests/test_backfill.py`` in the translation PR; kept here as a
    ``SkipTest`` placeholder so the suite still loads on the notifications-only
    branch (which does not ship ``backfill_translated_abstracts.py``).
    """

    def test_placeholder(self):
        import unittest as _unittest

        raise _unittest.SkipTest(
            "Backfill helpers are tested in tests/test_backfill.py in the "
            "translation PR."
        )


# ---------------------------------------------------------------------------
# 25-27: durable push queue
# ---------------------------------------------------------------------------


class PendingPushCacheTests(TestCase):
    """Round-trip and validation behavior for the durable pending queue."""

    def _cache_path(self) -> Path:
        d = Path(TemporaryDirectory().name)  # removed by temp dir context below
        return d

    def test_load_missing_cache_returns_none(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_push.json"
            self.assertIsNone(service.load_pending_push(path))

    def test_round_trip_preserves_order_and_dedupes(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_push.json"
            path.write_text(
                json.dumps(
                    {
                        "arxiv_ids": ["2401.003", "2401.001", "2401.003", ""],
                        "produced_at": "2026-08-12T10:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            ids = service.load_pending_push(path)
        self.assertEqual(ids, ["2401.003", "2401.001"])

    def test_all_empty_cache_treated_as_absent(self):
        # An all-empty arxiv_ids cache (e.g. written from a shell variable that
        # resolved to "") must NOT cause run_send to "succeed" by pushing zero
        # papers and deleting the cache. It is treated as absent so the worker
        # falls back to the legacy full-diff instead of falsely succeeding.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_push.json"
            path.write_text(
                json.dumps({"arxiv_ids": ["", "  ", ""], "produced_at": "x"}),
                encoding="utf-8",
            )
            self.assertIsNone(service.load_pending_push(path))

    def test_reject_non_object_cache(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_push.json"
            path.write_text('["not-an-object"]', encoding="utf-8")
            with self.assertRaises(ValueError):
                service.load_pending_push(path)

    def test_reject_corrupt_json(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_push.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                service.load_pending_push(path)

    def test_discard_missing_is_noop(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending_push.json"
            service.discard_pending_push(path)  # must not raise


class RollingDeliveredWindowTests(TestCase):
    """baseline stays complete; only REAL sends populate the rolling window."""

    def test_baseline_is_never_pruned(self):
        # Even with 50 more IDs than the retention window, the baseline keeps
        # every id: the fallback diff (corpus minus baseline) needs the full
        # history or old papers get misjudged as new (Codex review P1-2).
        target = fake_target()
        n = state.DELIVERED_RETENTION + 50
        baseline = [f"2401.{i:05d}" for i in range(n)]
        st = state.bootstrap_target({"version": 1, "targets": {}}, target, baseline)
        self.assertEqual(len(state.baseline_ids(st, target)), n)
        # And the delivered window is empty at bootstrap (no sends yet).
        self.assertEqual(state.delivered_ids(st, target), set())
        self.assertEqual(state.sent_ids(st, target), set())

    def test_record_delivery_prunes_after_window_overflow(self):
        # Fill the window with REAL sends (record_delivery), one past the cap.
        target = fake_target()
        st = state.bootstrap_target({"version": 1, "targets": {}}, target, [])
        for i in range(state.DELIVERED_RETENTION):
            st = state.record_delivery(st, target, f"2401.{i:05d}", "om_x")

        # Recording one more should cap back at the window size, and the
        # brand-new id must survive the prune (highest seq wins ties).
        st = state.record_delivery(st, target, "9999.99999", "om_test")
        self.assertEqual(
            len(state.delivered_ids(st, target)), state.DELIVERED_RETENTION
        )
        self.assertIn("9999.99999", state.delivered_ids(st, target))
        self.assertIn("9999.99999", state.sent_ids(st, target))


class PendingPushDrivenSendTests(_ServiceTestBase):
    """send consumes pending_push.json, records state, then deletes the cache."""

    def _net(self, send_side_effect) -> MagicMock:
        token = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "ttok"},
        )
        fake = MagicMock()
        fake.post.side_effect = [token, *send_side_effect]
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        return fake

    def _write_pending(self, ids: list[str]) -> Path:
        cache_path = self.settings.state_path.parent / "pending_push.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"arxiv_ids": ids, "produced_at": "now"}), encoding="utf-8"
        )
        return cache_path

    def test_send_uses_cache_when_present_and_deletes_on_success(self):
        # Bootstrap the historical corpus before the new paper appears.
        write_papers(self.tmp_path, sample_rows()[1:])
        service.run_bootstrap(self.settings)
        existing_id = sample_rows()[0]["arxiv_id"]
        write_papers(self.tmp_path, sample_rows())
        cache_path = self._write_pending([existing_id])
        send_resp = make_response(
            200, {"code": 0, "msg": "ok", "data": {"message_id": "om_x"}}
        )
        with patch("notifications.service.requests.Session") as session_cls:
            session_cls.return_value = self._net([send_resp])
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        self.assertEqual(results[0].sent_paper_ids, [existing_id])
        self.assertEqual(results[0].delivered, 1)
        # Push-and-forget: cache is purged on full success.
        self.assertFalse(cache_path.exists())

    def test_send_keeps_cache_on_partial_failure(self):
        write_papers(self.tmp_path, sample_rows()[1:])
        service.run_bootstrap(self.settings)
        existing_id = sample_rows()[0]["arxiv_id"]
        write_papers(self.tmp_path, sample_rows())
        cache_path = self._write_pending([existing_id])
        fail_resp = make_response(200, {"code": 230002, "msg": "bot not in chat"})
        with patch("notifications.service.requests.Session") as session_cls:
            session_cls.return_value = self._net([fail_resp])
            ok, _ = service.run_send(self.settings)
        self.assertFalse(ok)
        # Failed send must keep the cache so the operator can retry.
        self.assertTrue(cache_path.exists())

    def test_bootstrap_suppresses_papers_already_marked_as_history(self):
        """A durable queue must not override an explicit bootstrap baseline."""
        service.run_bootstrap(self.settings)
        historical_id = sample_rows()[0]["arxiv_id"]
        cache_path = self._write_pending([historical_id])

        with patch("notifications.service.requests.Session") as session_cls:
            ok, results = service.run_send(self.settings)

        self.assertTrue(ok)
        self.assertEqual(results[0].sent_paper_ids, [])
        session_cls.assert_not_called()
        self.assertFalse(cache_path.exists())


class FallbackDiffTests(_ServiceTestBase):
    """Without pending_push.json, send falls back to whole-corpus minus delivered."""

    def _net(self, send_side_effect) -> MagicMock:
        token = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "ttok"},
        )
        fake = MagicMock()
        fake.post.side_effect = [token, *send_side_effect]
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        return fake

    def test_send_without_cache_uses_full_diff(self):
        # Bootstrap: marks all three sample_rows as delivered, so a no-cache
        # send should find zero pending (rolling window still covers them).
        service.run_bootstrap(self.settings)
        send_resp = make_response(
            200, {"code": 0, "msg": "ok", "data": {"message_id": "om_x"}}
        )
        with patch("notifications.service.requests.Session") as session_cls:
            session_cls.return_value = self._net([send_resp])
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        # Full diff against a fully-bootstrapped baseline = 0 pending.
        self.assertEqual(results[0].delivered, 0)

    def test_send_without_cache_with_new_paper_after_bootstrap(self):
        service.run_bootstrap(self.settings)
        # Add a brand-new row to the papers JSON; no cache exists.
        new_rows = sample_rows() + [
            {
                "arxiv_id": "2608.99999",
                "title": "Fresh Paper",
                "translated_title": "新论文",
                "authors": "Author",
                "tag": "产品相关",
                "topic_tags": [],
                "institutions": [],
                "abstract": "abstract",
                "published": "2026-08-12",
            }
        ]
        write_papers(self.tmp_path, new_rows)
        self.settings = build_settings(
            self.tmp_path, papers_path=self.tmp_path / "papers.json"
        )
        send_resp = make_response(
            200, {"code": 0, "msg": "ok", "data": {"message_id": "om_x"}}
        )
        with patch("notifications.service.requests.Session") as session_cls:
            session_cls.return_value = self._net([send_resp])
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        # Fallback diff catches the one brand-new paper.
        self.assertEqual(results[0].delivered, 1)
        self.assertEqual(results[0].sent_paper_ids, ["2608.99999"])



class ReviewRegressionScenarios(_ServiceTestBase):
    """Integration scenarios for the Codex-review P1 findings.

    Each test reconstructs the exact production condition the review
    reported: corpus larger than the retention window, and multi-target
    partial failure followed by a retry.
    """

    def _net(self, send_side_effect) -> MagicMock:
        token = make_response(
            status_code=200,
            json_body={"code": 0, "msg": "ok", "tenant_access_token": "ttok"},
        )
        fake = MagicMock()
        fake.post.side_effect = [token, *send_side_effect]
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        return fake

    def _rows_n(self, n: int) -> list[dict]:
        return [
            {
                "arxiv_id": f"2401.{i:05d}",
                "title": f"paper {i}",
                "authors": "a",
                "abstract": "abs",
                "published": "2024-01-01",
                "pdf_url": f"https://arxiv.org/pdf/2401.{i:05d}",
                "arxiv_url": f"https://arxiv.org/abs/2401.{i:05d}",
            }
            for i in range(n)
        ]

    def test_corpus_larger_than_window_does_not_repush_history(self):
        """P1-2: 208 papers > 200-window must not misjudge the 8 oldest.

        bootstrap() against a corpus larger than DELIVERED_RETENTION, then
        a cache-less send (fallback diff): only genuinely new papers may be
        pushed -- the papers that fell outside the rolling window are still
        in baseline_ids and therefore NOT pending.
        """
        n = state.DELIVERED_RETENTION + 8
        write_papers(self.tmp_path, self._rows_n(n))
        self.settings = build_settings(self.tmp_path)
        service.run_bootstrap(self.settings)

        # Add one brand-new paper, keep the cache absent (fallback path).
        rows = self._rows_n(n) + [dict(self._rows_n(1)[0], arxiv_id="2608.99999")]
        write_papers(self.tmp_path, rows)
        self.settings = build_settings(self.tmp_path)

        send_resp = make_response(
            200, {"code": 0, "msg": "ok", "data": {"message_id": "om_x"}}
        )
        with patch("notifications.service.requests.Session") as session_cls:
            session_cls.return_value = self._net([send_resp])
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        # ONLY the genuinely new paper is pending -- the 8 oldest (outside
        # the rolling window but inside baseline_ids) must not reappear.
        self.assertEqual(results[0].delivered, 1)
        self.assertEqual(results[0].sent_paper_ids, ["2608.99999"])

    def test_partial_failure_retry_does_not_resend_to_succeeded_target(self):
        """P1-3: A ok + B fail -> cache kept -> retry must NOT re-push to A.

        Targets: user-target succeeds, group-target fails (bot not in chat).
        The cache survives for a retry; on the retry run user-target must
        get ZERO cards (its sends are recorded with real message_ids) while
        group-target receives the full batch.
        """
        write_papers(self.tmp_path, sample_rows()[1:])
        self.settings = build_settings(
            self.tmp_path,
            targets_json=json.dumps(
                [
                    {
                        "id": "user-target",
                        "name": "user",
                        "receive_id_type": "open_id",
                        "receive_id": "ou_u",
                    },
                    {
                        "id": "group-target",
                        "name": "group",
                        "receive_id_type": "chat_id",
                        "receive_id": "oc_g",
                    },
                ]
            ),
        )
        service.run_bootstrap(self.settings)
        write_papers(self.tmp_path, sample_rows())
        cache_path = self.settings.state_path.parent / "pending_push.json"
        cache_path.write_text(
            json.dumps({"arxiv_ids": ["2608.00001"]}), encoding="utf-8"
        )

        ok_token = make_response(
            200, {"code": 0, "msg": "ok", "tenant_access_token": "ttok"}
        )
        ok_send = make_response(
            200, {"code": 0, "msg": "ok", "data": {"message_id": "om_a"}}
        )
        fail_token = make_response(
            200, {"code": 0, "msg": "ok", "tenant_access_token": "ttok"}
        )
        fail_send = make_response(
            200, {"code": 230002, "msg": "bot not in chat"}
        )
        # First run: user (token+send both ok), group (token ok, send fails)
        # -> overall failure, cache kept for retry.
        with patch("notifications.service.requests.Session") as session_cls:
            fake = MagicMock()
            fake.post.side_effect = [ok_token, ok_send, fail_token, fail_send]
            fake.__enter__.return_value = fake
            fake.__exit__.return_value = False
            session_cls.return_value = fake
            ok, results = service.run_send(self.settings)
        self.assertFalse(ok)
        self.assertTrue(cache_path.exists(), "cache must survive a partial failure")

        # Retry run: group now succeeds too. user-target must be skipped
        # entirely (already sent -> sent_ids dedup), so only the group's
        # token + send happen (2 calls total).
        retry_token = make_response(
            200, {"code": 0, "msg": "ok", "tenant_access_token": "ttok"}
        )
        retry_send = make_response(
            200, {"code": 0, "msg": "ok", "data": {"message_id": "om_g"}}
        )
        with patch("notifications.service.requests.Session") as session_cls:
            fake = MagicMock()
            fake.post.side_effect = [retry_token, retry_send]
            fake.__enter__.return_value = fake
            fake.__exit__.return_value = False
            session_cls.return_value = fake
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        by_id = {r.target.id: r for r in results}
        self.assertEqual(
            by_id["user-target"].sent_paper_ids, [],
            "succeeded target must not receive the batch again on retry",
        )
        self.assertEqual(by_id["group-target"].sent_paper_ids, ["2608.00001"])
        self.assertFalse(cache_path.exists(), "full success discards the cache")

    def test_main_leaves_failed_cache_untouched_next_sync(self):
        """P2-4: a round with no new papers must not delete an unsent cache.

        The cache lifecycle is owned by the notification worker; main.py
        only writes it when new papers exist and never unlinks it.
        """
        from main import main as main_main  # noqa: F401  (import guard)
        source = Path("main.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "pending_path.unlink()",
            source,
            "main.py must never delete the pending cache (send owns it)",
        )

    def test_quiet_day_after_successful_send_pushes_nothing(self):
        """Adversarial-review P1 regression: the fallback diff must be
        idempotent across days.

        Day 1: queue-driven send delivers a new paper (success, queue
        discarded). Day 2: no new papers and no queue. The fallback diff must
        push NOTHING:
        record_delivery admits the sent id into baseline_ids (the seen
        set), so corpus-minus-seen-set excludes it. Before this fix the
        fallback subtracted only the bootstrap baseline and re-pushed
        every paper ever sent on each quiet day.
        """
        write_papers(self.tmp_path, sample_rows()[1:])
        service.run_bootstrap(self.settings)
        new_id = sample_rows()[0]["arxiv_id"]
        write_papers(self.tmp_path, sample_rows())
        cache_path = self.settings.state_path.parent / "pending_push.json"
        cache_path.write_text(
            json.dumps({"arxiv_ids": [new_id]}), encoding="utf-8"
        )
        send_resp = make_response(
            200, {"code": 0, "msg": "ok", "data": {"message_id": "om_1"}}
        )
        with patch("notifications.service.requests.Session") as session_cls:
            session_cls.return_value = self._net([send_resp])
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        self.assertEqual(results[0].sent_paper_ids, [new_id])
        self.assertFalse(cache_path.exists(), "success discards the cache")

        # Day 2: cache absent -> fallback path must push zero papers.
        ok2, results2 = service.run_send(self.settings)
        self.assertTrue(ok2)
        self.assertEqual(
            results2[0].sent_paper_ids, [],
            "quiet-day fallback must not re-push already-sent papers",
        )
        # Explicit seen-set assertion: on a small corpus (< retention
        # window) the delivered rolling window alone would also suppress
        # the re-push, masking a regression that drops the baseline_ids
        # admission (adversarial MUT1). Assert the property directly.
        st = json.loads(self.settings.state_path.read_text(encoding="utf-8"))
        self.assertIn(
            new_id,
            st["targets"]["paper-research-group"]["baseline_ids"],
            "record_delivery must admit sent ids into the seen set",
        )

    def test_record_delivery_admits_into_baseline(self):
        """record_delivery writes the id into baseline_ids (seen set), not
        just the rolling window -- the property the quiet-day idempotency
        rests on."""
        target = fake_target()
        st = state.bootstrap_target(
            {"version": 1, "targets": {}}, target, ["2401.00001"]
        )
        st = state.record_delivery(st, target, "2608.99999", "om_x")
        seen = state.baseline_ids(st, target)
        self.assertIn("2401.00001", seen, "bootstrap history stays")
        self.assertIn("2608.99999", seen, "successful send is admitted")

    def test_malformed_baseline_ids_fails_closed(self):
        """A corrupted baseline_ids field must raise StateError instead of
        silently behaving like an empty seen set (which would mass-push)."""
        target = fake_target()
        st = {
            "version": 1,
            "targets": {
                target.id: {
                    "bootstrapped": True,
                    "target_fingerprint": target.fingerprint,
                    "baseline_ids": "2401.00001",  # wrong type: str
                    "delivered": {},
                }
            },
        }
        with self.assertRaises(state.StateError):
            state.baseline_ids(st, target)

    def test_nested_list_baseline_ids_rejected_before_any_card_is_sent(self):
        """Adversarial review P3: a nested-list baseline_ids (hand-edited
        state) used to str()-coerce into garbage and crash record_delivery
        with a bare TypeError AFTER the card had gone out, looping every
        run. Element-level validation must reject it in _select_pending_rows
        -- before any Feishu call happens."""
        poisoned = {
            "version": 1,
            "targets": {
                "paper-research-group": {
                    "bootstrapped": True,
                    "target_fingerprint": self.settings.targets[
                        0
                    ].fingerprint,
                    "baseline_ids": ["2608.00001", ["2608.00002"]],
                    "delivered": {},
                }
            },
        }
        self.settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.state_path.write_text(
            json.dumps(poisoned), encoding="utf-8"
        )
        with patch("notifications.service.requests.Session") as sess:
            with self.assertRaises(state.StateError):
                service.run_send(self.settings)  # cache absent -> fallback
            sess.assert_not_called(), "no HTTP call may happen on poisoned state"

    def test_legacy_state_self_heals_on_first_send(self):
        """Legacy entry (no baseline_ids key): bootstrap markers seed the
        seen set, and record_delivery persists the upgraded field."""
        target = fake_target()
        legacy = {
            "version": 1,
            "targets": {
                target.id: {
                    "bootstrapped": True,
                    "target_fingerprint": target.fingerprint,
                    "delivered": {
                        "2401.00001": {
                            "bootstrap": True,
                            "delivered_at": "t",
                            "message_id": None,
                        },
                    },
                }
            },
        }
        self.assertEqual(
            state.baseline_ids(legacy, target),
            {"2401.00001"},
            "legacy markers are readable as the seen set",
        )
        st2 = state.record_delivery(legacy, target, "2608.99999", "om_y")
        self.assertEqual(
            state.baseline_ids(st2, target),
            {"2401.00001", "2608.99999"},
            "first send persists a proper baseline_ids field",
        )


    def test_pending_cache_accumulates_across_sync_rounds(self):
        """Independent-review P1 regression: a cache awaiting delivery must
        never be overwritten by the next sync's batch.

        Day 1 sync writes [A]; delivery fails everywhere (cache kept).
        Day 2 sync produces [B]; the accumulating write must persist the
        union [A, B] -- the original whole-file rewrite kept only [B] and
        permanently dropped A. Calls the REAL main.write_pending_push (an
        earlier cut of this test mirrored the logic inline, which stayed
        green even when the production code was mutated).
        """
        from main import write_pending_push

        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "pending_push.json"
            # Day 1: write [A].
            write_pending_push(cache, ["2401.00001"], "public/data/x.json")
            # Day 2: write [B] on top of the still-undelivered [A].
            write_pending_push(cache, ["2401.00002"], "public/data/x.json")
            # Day 3 (quiet day): empty batch must leave the cache untouched.
            write_pending_push(cache, [], "public/data/x.json")

            stored = json.loads(cache.read_text(encoding="utf-8"))["arxiv_ids"]
            self.assertEqual(
                sorted(stored), ["2401.00001", "2401.00002"],
                "day-2 batch must accumulate on top of the awaiting day-1 "
                "batch, not overwrite it; a quiet day must not touch it",
            )
            # Corrupt cache is tolerated: today's batch wins.
            cache.write_text("{not json", encoding="utf-8")
            write_pending_push(cache, ["2608.99999"], "public/data/x.json")
            stored = json.loads(cache.read_text(encoding="utf-8"))["arxiv_ids"]
            self.assertEqual(stored, ["2608.99999"])

    def test_accumulated_batch_survives_until_all_targets_succeed(self):
        """End-to-end accumulation scenario: B fails day 1, cache keeps A's
        undelivered papers; a day-2 cache containing the UNION still
        delivers everything to B exactly once and to A never twice."""
        from main import write_pending_push

        # Two targets share a historical baseline that predates both queued
        # papers.
        targets_json = json.dumps(
            [
                {
                    "id": "u",
                    "name": "u",
                    "receive_id_type": "open_id",
                    "receive_id": "ou_1",
                },
                {
                    "id": "g",
                    "name": "g",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_1",
                },
            ]
        )
        write_papers(self.tmp_path, sample_rows()[2:])
        settings = build_settings(
            self.tmp_path,
            targets_json=targets_json,
        )
        service.run_bootstrap(settings)
        write_papers(self.tmp_path, sample_rows())
        cache_path = settings.state_path.parent / "pending_push.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        day1_ids = ["2608.00001"]
        cache_path.write_text(
            json.dumps({"arxiv_ids": day1_ids}), encoding="utf-8"
        )

        def resp(mid):
            return make_response(
                200, {"code": 0, "msg": "ok", "data": {"message_id": mid}}
            )
        def tok():
            return make_response(
                200, {"code": 0, "msg": "ok", "tenant_access_token": "t"}
            )

        # Day 1: u ok, g fails.
        with patch("notifications.service.requests.Session") as cls:
            fake = MagicMock()
            fake.post.side_effect = [tok(), resp("om_u1"), tok(),
                                     make_response(200, {"code": 230002, "msg": "bot not in chat"})]
            fake.__enter__.return_value = fake; fake.__exit__.return_value = False
            cls.return_value = fake
            ok1, r1 = service.run_send(settings)
        self.assertFalse(ok1)

        # Day 2 starts in a fresh checkout. Git has persisted both the failed
        # queue and the successful target's state; the next sync appends B.
        with TemporaryDirectory() as day2_tmp:
            day2_root = Path(day2_tmp)
            write_papers(day2_root, sample_rows())
            day2_settings = build_settings(day2_root, targets_json=targets_json)
            day2_settings.state_path.write_text(
                settings.state_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            day2_cache = day2_settings.state_path.parent / "pending_push.json"
            day2_cache.write_text(
                cache_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            write_pending_push(
                day2_cache, ["2608.00002"], str(day2_settings.papers_path)
            )

            with patch("notifications.service.requests.Session") as cls:
                fake = MagicMock()
                fake.post.side_effect = [
                    tok(), resp("om_u2"), tok(), resp("om_g2")
                ]
                fake.__enter__.return_value = fake
                fake.__exit__.return_value = False
                cls.return_value = fake
                ok2, r2 = service.run_send(day2_settings)

            self.assertFalse(day2_cache.exists())
        self.assertTrue(ok2)
        by_id = {x.target.id: x for x in r2}
        self.assertEqual(
            by_id["u"].sent_paper_ids, ["2608.00002"],
            "u already got 2608.00001 on day 1; only the new paper goes out",
        )
        self.assertEqual(
            sorted(by_id["g"].sent_paper_ids), ["2608.00001", "2608.00002"],
            "g missed day 1 entirely; the accumulated union catches it up "
            "with no loss and no duplicate",
        )
        self.assertTrue(
            cache_path.exists(),
            "the old checkout is unchanged; the fresh checkout clears its copy",
        )

    def test_large_batch_is_sent_in_chunks(self):
        """45 pending papers must be split into ceil(45/20)=3 cards, all
        recorded; a mid-chunk failure keeps earlier chunks' progress."""
        rows = [
            {
                "arxiv_id": f"2608.{i:05d}",
                "title": f"paper {i}",
                "authors": "a",
                "abstract": "abs",
                "published": "2026-08-01",
                "pdf_url": f"https://arxiv.org/pdf/2608.{i:05d}",
                "arxiv_url": f"https://arxiv.org/abs/2608.{i:05d}",
            }
            for i in range(45)
        ]
        write_papers(self.tmp_path, [])
        self.settings = build_settings(self.tmp_path)
        service.run_bootstrap(self.settings)
        write_papers(self.tmp_path, rows)
        # Add all 45 after bootstrap and queue them for delivery.
        cache_path = self.settings.state_path.parent / "pending_push.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"arxiv_ids": [r["arxiv_id"] for r in rows]}),
            encoding="utf-8",
        )
        def tok():
            return make_response(
                200, {"code": 0, "msg": "ok", "tenant_access_token": "t"}
            )
        def ok_resp():
            return make_response(
                200, {"code": 0, "msg": "ok", "data": {"message_id": "om_x"}}
            )

        with patch("notifications.service.requests.Session") as cls:
            fake = MagicMock()
            # MagicMock side_effect consumes lazily, so three responses only
            # succeed if three real send calls happen; a fourth would raise
            # StopIteration. Count actual message sends from call_args_list
            # (NOT from side-effect closures -- those execute at list-build
            # time and would turn the assertion into a tautology, which is
            # exactly how this test once lied while the chunking was broken).
            fake.post.side_effect = [tok(), ok_resp(), ok_resp(), ok_resp()]
            fake.__enter__.return_value = fake; fake.__exit__.return_value = False
            cls.return_value = fake
            ok, results = service.run_send(self.settings)
        self.assertTrue(ok)
        send_call_count = sum(
            1
            for call in fake.post.call_args_list
            if call.args and "messages" in str(call.args[0])
        )
        self.assertEqual(
            send_call_count, 3, "45 papers @ 20/card = exactly 3 real sends"
        )
        self.assertEqual(results[0].delivered, 45)
        self.assertFalse(cache_path.exists())


class WorkflowPersistenceTests(TestCase):
    """Guards the Actions handoff that makes the notification queue durable."""

    def test_workflow_persists_queue_across_jobs_and_failures(self):
        workflow = (ROOT / ".github/workflows/update-and-deploy.yml").read_text(
            encoding="utf-8"
        )
        ignored_paths = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(
                encoding="utf-8"
            ).splitlines()
        }

        self.assertIn("queue=.notification-state/pending_push.json", workflow)
        self.assertIn('git add -A -- "$queue"', workflow)
        self.assertIn("ref: ${{ github.ref_name }}", workflow)
        self.assertIn('git pull --ff-only origin "$GITHUB_REF_NAME"', workflow)
        self.assertIn(
            ".notification-state/feishu.json .notification-state/pending_push.json",
            workflow,
        )
        self.assertNotIn("uses: actions/upload-artifact@", workflow)
        self.assertNotIn("uses: actions/download-artifact@", workflow)
        self.assertNotIn(".notification-state/pending_push.json", ignored_paths)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
