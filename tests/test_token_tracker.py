#!/usr/bin/env python3
"""Tests for ClaudeCount's token_tracker.

Run:  python3 -m unittest discover -s tests
  or: python3 tests/test_token_tracker.py

Covers the behaviors most exposed to Claude Code format/behavior drift:
parsing & human-turn detection, model pricing (incl. Fable 5 / Mythos 5),
the transcript-dir encoder (paths with spaces/dots), subagent spend
aggregation, and the render_mode regression that previously zeroed Turn/Sess
when the statusLine session_id differed from the Stop hook's (background /
worktree sessions).
"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import token_tracker as t  # noqa: E402


def _assistant(model="claude-opus-4-8", inp=1000, out=500, cr=0, cc=0,
               ts="2026-06-26T10:00:00Z"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": inp, "output_tokens": out,
                "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc,
            },
        },
    }


def _human(text="hello", ts="2026-06-26T10:00:00Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": text}}


def _tool_result(ts="2026-06-26T10:00:00Z"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


def _command_marker(ts="2026-06-26T10:00:00Z"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user",
            "content": [{"type": "text", "text": "<command-name>/model</command-name>"}]}}


class HumanTurnTests(unittest.TestCase):
    def test_plain_human_string(self):
        self.assertTrue(t.is_human_message(_human()))

    def test_tool_result_is_not_human(self):
        self.assertFalse(t.is_human_message(_tool_result()))

    def test_command_marker_is_not_human(self):
        self.assertFalse(t.is_human_message(_command_marker()))

    def test_top_level_string_command_marker(self):
        msg = {"type": "user", "message": {"role": "user",
               "content": "<local-command-stdout>hi</local-command-stdout>"}}
        self.assertFalse(t.is_human_message(msg))

    def test_count_turns_excludes_tool_results_and_markers(self):
        msgs = [_human(), _assistant(), _tool_result(), _assistant(),
                _command_marker(), _human(), _assistant()]
        self.assertEqual(t.count_turns(msgs), 2)


class ModelPricingTests(unittest.TestCase):
    def test_fable5_present_and_priced(self):
        m = t._resolve_model("claude-fable-5")
        self.assertEqual(m["name"], "Fable 5")
        self.assertEqual((m["input"], m["output"], m["context"]), (10.0, 50.0, 1_000_000))
        self.assertEqual(m["cache_write_5m"], 12.50)
        self.assertEqual(m["cache_write_1h"], 20.00)
        self.assertEqual(m["cache_read"], 1.00)

    def test_mythos5_present(self):
        self.assertEqual(t._resolve_model("claude-mythos-5")["name"], "Mythos 5")

    def test_opus48_unchanged(self):
        m = t._resolve_model("claude-opus-4-8")
        self.assertEqual((m["input"], m["output"], m["context"]), (5.0, 25.0, 1_000_000))

    def test_variant_suffix_resolves(self):
        self.assertEqual(t._resolve_model("claude-opus-4-8[1m]")["name"], "Opus 4.8")

    def test_cost_calc_fable5(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self.assertAlmostEqual(t.calc_cost(usage, "claude-fable-5"), 60.0, places=4)


class EncoderTests(unittest.TestCase):
    def test_spaces_and_slashes(self):
        self.assertEqual(
            t.encode_project_dir("/home/ayooleo/projects/NCARB Study/archisapio"),
            "-home-ayooleo-projects-NCARB-Study-archisapio")

    def test_dots_in_worktree_path(self):
        self.assertEqual(
            t.encode_project_dir("/p/app/.claude/worktrees/x"),
            "-p-app--claude-worktrees-x")

    def test_plain_path_matches_legacy(self):
        p = "/home/ayooleo/projects/claudeCount"
        self.assertEqual(t.encode_project_dir(p), p.replace("/", "-"))


class SubagentAggregationTests(unittest.TestCase):
    def _make_session(self, tmp):
        proj = Path(tmp)
        sid = "sess-123"
        main = proj / f"{sid}.jsonl"
        main.write_text(json.dumps(_assistant(inp=1000, out=1000)) + "\n", encoding="utf-8")
        sub_dir = proj / sid / "subagents"
        sub_dir.mkdir(parents=True)
        (sub_dir / "agent-a.jsonl").write_text(
            json.dumps(_assistant(model="claude-haiku-4-5", inp=2000, out=0,
                                  ts="2026-06-26T11:00:00Z")) + "\n", encoding="utf-8")
        return str(main)

    def test_finds_subagent_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = self._make_session(tmp)
            self.assertEqual(len(t.subagent_transcript_paths(main)), 1)

    def test_no_subagents_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            lone = Path(tmp) / "x.jsonl"
            lone.write_text("", encoding="utf-8")
            self.assertEqual(t.subagent_transcript_paths(str(lone)), [])

    def test_collect_and_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = self._make_session(tmp)
            subs = t.collect_subagent_usages(main)
            self.assertEqual(len(subs), 1)
            _, cost, _ = t.sum_usages(subs)
            # 2000 input @ Haiku 4.5 $1/M = $0.002
            self.assertAlmostEqual(cost, 0.002, places=6)

    def test_since_filter_excludes_earlier_subagents(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = self._make_session(tmp)
            after = t._parse_ts("2026-06-26T12:00:00Z")   # subagent ran at 11:00
            self.assertEqual(t.collect_subagent_usages(main, since=after), [])
            before = t._parse_ts("2026-06-26T10:00:00Z")
            self.assertEqual(len(t.collect_subagent_usages(main, since=before)), 1)


class RenderNoResetRegressionTests(unittest.TestCase):
    """Turn/Sess must survive a render when the live statusLine session_id
    differs from the stored (Stop-hook) session_id — the background/worktree
    session case that previously zeroed the bar."""

    def _status(self, sid):
        return {
            "project_name": "demo", "cwd": "/tmp/demo", "pid": "abc123",
            "session_id": sid, "model": "claude-opus-4-8", "created": "",
            "last_turn": {"cost": 0.5, "input_tokens": 10, "cache_creation": 0,
                          "cache_read": 0, "output_tokens": 5},
            "session": {"cost": 1.23, "input_tokens": 100, "cache_creation": 0,
                        "cache_read": 0, "output_tokens": 50,
                        "started": "2026-06-26T10:00:00+00:00",
                        "active_minutes": 5, "turn_count": 6},
            "project": {"cost": 1.23, "session_count": 1, "turn_count": 6,
                        "active_minutes": 5, "input_tokens": 100,
                        "cache_creation": 0, "cache_read": 0, "output_tokens": 50},
            "context": {"tokens": 0, "window_size": 1_000_000, "pct": 0},
            "updated": "2026-06-26T10:00:00+00:00",
        }

    def test_mismatched_session_id_does_not_zero_turn_sess(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "abc123.json"
            status_path.write_text(json.dumps(self._status("STOP-SESSION")),
                                   encoding="utf-8")
            live = {"session_id": "STATUSLINE-SESSION", "cwd": "/tmp/demo",
                    "model": {"id": "claude-opus-4-8"},
                    "context_window": {"context_window_size": 1_000_000,
                                       "current_usage": {"input_tokens": 0,
                                                         "cache_creation_input_tokens": 0,
                                                         "cache_read_input_tokens": 0,
                                                         "output_tokens": 0}}}
            old_stdin, old_stdout = sys.stdin, sys.stdout
            sys.stdin = io.StringIO(json.dumps(live))
            sys.stdout = io.StringIO()
            try:
                t.render_mode([str(status_path)])
            finally:
                sys.stdin, sys.stdout = old_stdin, old_stdout
            after = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(after["session"]["turn_count"], 6)
            self.assertAlmostEqual(after["session"]["cost"], 1.23)
            # session_id is left as the Stop hook wrote it, not overwritten
            self.assertEqual(after["session_id"], "STOP-SESSION")


if __name__ == "__main__":
    unittest.main(verbosity=2)
