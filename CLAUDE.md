# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What This Is

ClaudeCount is a Claude Code plugin that displays real-time token usage and API costs in the status bar. It integrates via Claude Code's hook system — no build step, no external dependencies, pure Python 3 stdlib + bash.

## Common Commands

```bash
# Syntax-check the main tracker after editing
python3 -c "import ast; ast.parse(open('src/token_tracker.py').read())" && echo OK

# Test report generation against live data
python3 src/token_report.py
python3 src/token_report.py --all -v

# Manually deploy src/ files to the hooks dir (simulate install)
cp src/token_tracker.py ~/.claude/hooks/token_tracker.py
cp src/token_status.sh ~/.claude/hooks/token_status.sh
cp src/token_report.py ~/.claude/hooks/token_report.py

# Run the tracker manually in main mode (requires a real transcript path)
python3 src/token_tracker.py < <(echo '{"transcript_path":"/path/to/transcript.jsonl","session_id":"abc","hook_event_name":"Stop","cwd":"/tmp"}')

# Recompute missing turn_count / active_minutes for legacy sessions (idempotent)
python3 src/token_tracker.py --backfill

# Render the status bar manually. --render takes a status_path; live statusLine
# JSON (model.id, context_window.current_usage, ...) is read from stdin.
echo '{"session_id":"sid","cwd":"/tmp","model":{"id":"claude-sonnet-4-6"},"context_window":{"context_window_size":200000,"current_usage":{"input_tokens":1000,"cache_read_input_tokens":50000,"cache_creation_input_tokens":0,"output_tokens":500}}}' \
  | python3 src/token_tracker.py --render ~/.claude/token_usage/status/current.json
```

## Architecture

The plugin has three entry points, all backed by `src/token_tracker.py`:

| Entry Point | Hook | Mode | Trigger |
|---|---|---|---|
| `token_tracker.py` (no args) | Stop (async) | `main()` | After every Claude response |
| `token_tracker.py --session-start` | SessionStart (sync) | `session_start_mode()` | New session open |
| `token_tracker.py --pre-turn` | UserPromptSubmit (sync) | `pre_turn_mode()` | Each user message |
| `token_tracker.py --render <json>` | Status bar | `render_mode()` | Every 30s |
| `token_tracker.py --backfill` | — | `backfill_mode()` | One-shot CLI — recompute missing `turn_count` / `active_minutes` for legacy sessions by re-reading their transcripts. Idempotent. |
| `token_status.sh` | Status bar wrapper | — | Calls `--render` after merging stdin data |

**Data flow (Stop hook):**
1. Claude Code sends JSON to stdin: `{"transcript_path": "...", "session_id": "...", "cwd": "..."}`
2. `main()` reads the JSONL transcript, deduplicates API calls (multi-block responses share identical usage tuples), counts human turns (excluding tool-result messages)
3. Calculates per-turn, per-session, and per-project costs using model-specific pricing
4. Writes to `~/.claude/token_usage/projects/{pid}.json` (persistent history) and `~/.claude/token_usage/status/{pid}.json` + `current.json` (live display)

**Data flow (Status bar):**
1. Claude Code invokes `token_status.sh`, passing live context data on stdin
2. Shell script reads model/context from stdin, merges with `current.json`
3. Calls `token_tracker.py --render <merged-json>` which outputs ANSI-colored status line

## Key Design Decisions

**Project identity:** MD5 hash of `cwd` (first 12 hex chars). Changing the working directory path creates a new project.

**Deduplication:** Multi-block Claude responses generate multiple assistant entries in the transcript with identical `(input, output, cache_read, cache_creation)` tuples. Consecutive duplicates are dropped before cost calculation.

**Human turn counting:** `is_human_message()` returns False for (a) messages with a `tool_result` block, and (b) slash-command / local-command markers (`<command-name>`, `<command-message>`, `<command-args>`, `<local-command-stdout>`, `<local-command-caveat>`). Marker detection covers both top-level string content *and* `{"type":"text","text":"<…>"}` blocks inside list-of-blocks payloads — Claude Code uses both shapes.

**Active time:** Inter-message gaps >3 minutes are excluded (treated as idle). Gaps ≤3 min accumulate into `active_minutes`.

**Cache pricing:** Two tiers — 5-minute ephemeral (1.25× input rate) and 1-hour ephemeral (2× input rate). The API's `usage.cache_creation` object carries the per-tier breakdown (`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`); `calc_cost` falls back to treating the whole `cache_creation_input_tokens` as 5m when the breakdown is absent. Cache TTL is server-side wall clock and never interacts with our active-time accounting.

**Model/context window:** Live `context_window_size` from Claude Code's status-line stdin is authoritative; `MODELS[k]["context"]` is fallback only. This lets users opt into 1M variants (e.g. `claude-sonnet-4-6` 1M) without ClaudeCount knowing about each variant. Mid-session `/model` switches transiently mismatch model+window for one render cycle (≤30s) and self-heal.

**Context %:** Recomputed locally from `context_window.current_usage` over the live window size, using the input-only formula Claude Code documents: `(input + cache_creation + cache_read) / size`. Output tokens are excluded.

**Timestamps:** All `started` / `created` / `updated` / `last_updated` fields written by the tracker are UTC-aware ISO 8601 (`datetime.now(timezone.utc).isoformat()`, suffix `+00:00`). Older legacy entries written before this change are naive local time; readers (`calc_active_minutes`, `[:10]` date slicing) tolerate both.

**Status bar token notation:** `↑<total_in> ↓<out> [<cache_read> «]` — `↑` and `↓` are prefixes, `«` (rewind) is a suffix on the cache-read portion of `↑` with a leading space. `↑` is the *sum* of raw input + cache_creation + cache_read; the `«` segment is the slice of `↑` billed at the cheap 0.1× cache_read rate. This visual convention is intentional — don't move arrows or pick "fast-forward"-style symbols for cache_read; "replayed from past context" is the correct mental model.

## Storage Layout

```
~/.claude/token_usage/
  projects/{pid}.json     # Full session history, project totals, active_days
  status/{pid}.json       # Current display state for this project
  status/current.json     # Mirror of the active project's status file
  config.json             # Optional global price overrides
```

Per-project price overrides live in `.claude/claudecount.json` in the project root.

## Adding a New Model

In `src/token_tracker.py`, add **one** entry to the `MODELS` dict:

```python
"claude-NEW-id": {
    "input":          ...,  # USD per million tokens
    "output":         ...,
    "cache_write_5m": ...,  # 1.25× input
    "cache_write_1h": ...,  # 2×    input
    "cache_read":     ...,  # 0.1×  input
    "context":        ...,  # max context window in tokens
    "name":           "Display Name",
},
```

`_resolve_model()` matches `key in model` with keys sorted longest-first, so a future `claude-opus-5-3` won't accidentally collide with `claude-opus-5`, and a variant suffix like `claude-opus-4-7[1m]` resolves to the `claude-opus-4-7` entry.

**Always verify rates against the official pricing page before adding:** [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing). The defaults shipped today were verified there on 2026-04-26.
