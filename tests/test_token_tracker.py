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

    # --- Models added 2026-09-12 (verified against platform.claude.com/pricing) ---

    def test_opus5_present_and_priced(self):
        m = t._resolve_model("claude-opus-5")
        self.assertEqual(m["name"], "Opus 5")
        self.assertEqual((m["input"], m["output"], m["context"]), (5.0, 25.0, 1_000_000))
        self.assertEqual(m["cache_read"], 0.50)

    def test_opus5_not_falling_back_to_default(self):
        # Regression: before 2026-09-12 `claude-opus-5` hit _DEFAULT_MODEL ($3/$15),
        # under-reporting every Opus 5 session by ~40%.
        self.assertIsNot(t._resolve_model("claude-opus-5"), t._DEFAULT_MODEL)
        self.assertIsNot(t._resolve_model("claude-sonnet-5"), t._DEFAULT_MODEL)

    def test_sonnet5_priced_at_2_and_10(self):
        m = t._resolve_model("claude-sonnet-5")
        self.assertEqual((m["input"], m["output"], m["context"]), (2.0, 10.0, 1_000_000))
        self.assertEqual(m["cache_read"], 0.20)

    def test_fable51_cache_read_is_quarter_not_tenth(self):
        # Fable/Mythos 5.1 bill cache reads at 0.025x input, not the usual 0.1x.
        m = t._resolve_model("claude-fable-5-1")
        self.assertEqual(m["name"], "Fable 5.1")
        self.assertEqual(m["cache_read"], 0.25)
        self.assertEqual(t._resolve_model("claude-mythos-5-1")["cache_read"], 0.25)

    def test_fable51_wins_over_fable5_substring(self):
        # "claude-fable-5" is a substring of "claude-fable-5-1"; longest-first
        # ordering must keep them apart.
        self.assertEqual(t._resolve_model("claude-fable-5-1")["name"], "Fable 5.1")
        self.assertEqual(t._resolve_model("claude-fable-5")["name"], "Fable 5")
        self.assertEqual(t._resolve_model("claude-fable-5-1[1m]")["name"], "Fable 5.1")

    # Haiku 3 is retired; Anthropic published rounded cache rates for it
    # ($0.30 write / $0.03 read on a $0.25 input) rather than exact multiples.
    # Kept verbatim so old transcripts reprice to what was actually billed.
    ROUNDED_LEGACY = {"claude-3-haiku"}

    def test_cache_read_is_tenth_of_input_except_51(self):
        for key, m in t.MODELS.items():
            if key in self.ROUNDED_LEGACY:
                continue
            expected = 0.025 if key.endswith("-5-1") else 0.1
            self.assertAlmostEqual(
                m["cache_read"], m["input"] * expected, places=4,
                msg=f"{key}: cache_read {m['cache_read']} != {expected}x input")

    def test_cache_write_multipliers_hold(self):
        for key, m in t.MODELS.items():
            if key in self.ROUNDED_LEGACY:
                continue
            self.assertAlmostEqual(m["cache_write_5m"], m["input"] * 1.25, places=4, msg=key)
            self.assertAlmostEqual(m["cache_write_1h"], m["input"] * 2.0, places=4, msg=key)

    def test_haiku3_keeps_its_published_rounded_rates(self):
        m = t.MODELS["claude-3-haiku"]
        self.assertEqual((m["cache_write_5m"], m["cache_read"]), (0.30, 0.03))


class FastModeTests(unittest.TestCase):
    """Fast mode (research preview) bills Opus 5 / Opus 4.8 at $10/$50."""

    STD = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    def test_standard_speed_uses_base_rates(self):
        u = {**self.STD, "speed": "standard"}
        self.assertAlmostEqual(t.calc_cost(u, "claude-opus-5"), 30.0, places=4)

    def test_missing_speed_field_uses_base_rates(self):
        self.assertAlmostEqual(t.calc_cost(self.STD, "claude-opus-5"), 30.0, places=4)

    def test_fast_speed_doubles_opus5(self):
        u = {**self.STD, "speed": "fast"}
        self.assertAlmostEqual(t.calc_cost(u, "claude-opus-5"), 60.0, places=4)

    def test_fast_speed_doubles_opus48(self):
        u = {**self.STD, "speed": "fast"}
        self.assertAlmostEqual(t.calc_cost(u, "claude-opus-4-8"), 60.0, places=4)

    def test_fast_cache_multipliers_stack_on_fast_base(self):
        u = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000, "speed": "fast"}
        # 5m write 12.50 + cache read 1.00 on the fast base
        self.assertAlmostEqual(t.calc_cost(u, "claude-opus-5"), 13.50, places=4)

    def test_fast_ignored_on_models_without_fast_mode(self):
        # Sonnet 5 has no fast mode; a stray speed field must not change pricing.
        u = {**self.STD, "speed": "fast"}
        self.assertAlmostEqual(t.calc_cost(u, "claude-sonnet-5"), 12.0, places=4)

    def test_price_override_still_wins_over_fast(self):
        u = {**self.STD, "speed": "fast"}
        cost = t.calc_cost(u, "claude-opus-5", {"input": 1.0, "output": 1.0})
        self.assertAlmostEqual(cost, 2.0, places=4)


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


class RepriceSessionTests(unittest.TestCase):
    """--reprice must improve data, never degrade it.

    The no-transcript path re-derives cost from a flat token total at one
    model's rate. For a session that mixed models that is strictly worse than
    the stored per-call sum, so it is only allowed for models that were
    previously priced at the $3/$15 fallback.
    """

    TOKENS = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    def test_no_transcript_unchanged_model_is_skipped(self):
        # Opus 4.7's price did not move; its stored cost is an exact per-call
        # sum and must survive a reprice untouched.
        sess = {"cost": 123.45, "tokens": self.TOKENS, "model": "claude-opus-4-7"}
        tokens, cost, model, how = t._reprice_session(sess, None, None)
        self.assertEqual(how, "skip")
        self.assertEqual(cost, 123.45)

    def test_no_transcript_newly_priced_model_is_repriced(self):
        # Opus 5 sessions were recorded at the $3/$15 default — known-wrong.
        sess = {"cost": 18.0, "tokens": self.TOKENS, "model": "claude-opus-5"}
        tokens, cost, model, how = t._reprice_session(sess, None, None)
        self.assertEqual(how, "approx")
        self.assertAlmostEqual(cost, 30.0, places=4)

    def test_newly_priced_matches_with_variant_suffix(self):
        sess = {"cost": 18.0, "tokens": self.TOKENS, "model": "claude-opus-5[1m]"}
        self.assertEqual(t._reprice_session(sess, None, None)[3], "approx")

    def test_missing_transcript_file_falls_through(self):
        sess = {"cost": 9.9, "tokens": self.TOKENS, "model": "claude-opus-4-7"}
        ghost = Path(tempfile.gettempdir()) / "claudecount-no-such-transcript.jsonl"
        self.assertEqual(t._reprice_session(sess, ghost, None)[3], "skip")

    def test_exact_path_reprices_from_transcript_and_folds_subagents(self):
        with tempfile.TemporaryDirectory() as d:
            tr = Path(d) / "sess-1.jsonl"
            # Two API calls on different models — the case a flat reprice ruins.
            # Token counts must differ: consecutive calls with an identical usage
            # tuple are deduplicated as one multi-block response.
            rows = [
                {"type": "assistant", "message": {"model": "claude-opus-5", "usage": {
                    "input_tokens": 1_000_000, "output_tokens": 0,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
                {"type": "assistant", "message": {"model": "claude-haiku-4-5", "usage": {
                    "input_tokens": 2_000_000, "output_tokens": 0,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
            ]
            tr.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            sess = {"cost": 0.0, "tokens": {}, "model": "claude-opus-5"}
            tokens, cost, model, how = t._reprice_session(sess, tr, None)
            self.assertEqual(how, "exact")
            # 1M @ $5 (Opus 5) + 2M @ $1 (Haiku 4.5) = $7 — not 3M at either rate.
            self.assertAlmostEqual(cost, 7.0, places=4)
            self.assertEqual(tokens["input_tokens"], 3_000_000)

    def test_exact_path_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            tr = Path(d) / "sess-2.jsonl"
            tr.write_text(json.dumps(
                {"type": "assistant", "message": {"model": "claude-opus-5", "usage": {
                    "input_tokens": 1_000_000, "output_tokens": 0,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}),
                encoding="utf-8")
            sess = {"cost": 0.0, "tokens": {}, "model": "claude-opus-5"}
            first = t._reprice_session(sess, tr, None)
            sess.update(cost=first[1], tokens=first[0], model=first[2])
            second = t._reprice_session(sess, tr, None)
            self.assertAlmostEqual(first[1], second[1], places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
