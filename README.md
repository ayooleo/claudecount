# ClaudeCount

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Real-time token usage and cost tracking for Claude Code (Terminal), shown directly in the status bar.

```
MYPROJECT Sonnet 4.6 200k 22% │ Turn: $0.03 (↑180k ↓450 179k «) │ Sess: $0.18 (↑180k ↓450 179k «, 5 turns, 12 min) │ Proj: $2.40 (↑18M ↓62k 17M «, 5 sess, 40 turns, 3hr)
```

| Segment | Meaning |
|---------|---------|
| **PROJECT NAME** | Active project (directory name, uppercased) |
| **Model** | Current model name and context window size |
| **CW %** | Context window fill — green < 50%, blue 50–74%, yellow 75–89%, red ≥ 90% |
| **Turn** | Cost + tokens for the last completed turn (all API calls summed) |
| **Sess** | Cumulative cost, tokens, turn count, and active time for this session |
| **Proj** | All-time cost, tokens, session count, total turns, and active hours |

### Token notation

| Symbol | Meaning |
|--------|---------|
| `↑` | Total input tokens (non-cached + cache_creation + cache_read) |
| `↓` | Output tokens generated |
| `«` | Portion of `↑` served from cache (0.1× input rate) |

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/ayooleo/ClaudeCount/main/install.sh | bash
```

Restart Claude Code after installing.

**Requirements:** Python 3, curl, Claude Code with status bar support.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/ayooleo/ClaudeCount/main/uninstall.sh | bash
```

Usage data at `~/.claude/token_usage/` is kept. Delete it manually for a clean removal.

## Detailed reports

```bash
# Current project (verbose session breakdown)
python3 ~/.claude/hooks/token_report.py

# All projects, sorted by cost
python3 ~/.claude/hooks/token_report.py --all

# All projects + session-level detail
python3 ~/.claude/hooks/token_report.py --all -v
```

## Custom pricing

Create `.claude/claudecount.json` in the project root to override pricing:

```json
{
  "pricing": {
    "input": 3.00,
    "output": 15.00,
    "cache_write_5m": 3.75,
    "cache_write_1h": 6.00,
    "cache_read": 0.30
  }
}
```

To disable tracking for a specific project:

```json
{ "enabled": false }
```

A global config can also be placed at `~/.claude/token_usage/config.json`.

## How it works

Three hooks cooperate to keep the display accurate:

**SessionStart hook** (synchronous) fires the moment a session opens:
- Resets Turn, Sess, and context window display before the first prompt, so the status bar never shows stale data from a previous session
- Detects new vs. resumed sessions by comparing session IDs — resumes are left untouched

**UserPromptSubmit hook** (synchronous) fires when you send a message:
- Acts as a fallback reset in case SessionStart didn't fire (e.g. older Claude Code versions)

**Stop hook** (async) fires after each Claude response:
- Reads the session transcript and deduplicates API calls (one response can span multiple transcript entries)
- Counts real human turns (tool-result messages are excluded)
- Calculates Turn, Sess, and Proj costs and token totals
- Writes `~/.claude/token_usage/status/current.json`

**Status bar** runs every 30 seconds via `token_status.sh`:
- Receives live model ID and `context_window.current_usage` from Claude Code on stdin
- Model name updates immediately when you switch models with `/model`
- Context window **size** is taken from Claude Code's live `context_window_size` (so 1M variants opt-ins are picked up correctly); the built-in model table is a fallback only
- Context window **percentage** is recomputed locally from `current_usage` over the live window size, using the same input-only formula Claude Code uses (`input + cache_creation + cache_read`, output excluded)

**Active time** counts the total time when human and AI are collaborating: it sums all inter-message gaps (both sides) up to 3 minutes. Gaps longer than 3 minutes — idle pauses, away-from-screen periods, unanswered permission prompts — are excluded.

## Pricing reference (defaults)

Cache write has two tiers: **5-minute** (1.25× input) and **1-hour** (2× input).

| Model | Input | Output | Cache write 5m | Cache write 1h | Cache read |
|-------|-------|--------|----------------|----------------|------------|
| Opus 4.7 / 4.6 / 4.5 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| Opus 4.1 / 4 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Sonnet 4.6 / 4.5 / 4 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 4.5 | $1.00 | $5.00 | $1.25 | $2.00 | $0.10 |
| Sonnet 3.7 / 3.5 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 3.5 | $0.80 | $4.00 | $1.00 | $1.60 | $0.08 |
| Opus 3 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Haiku 3 | $0.25 | $1.25 | $0.30 | $0.50 | $0.03 |

Prices are per million tokens. **Opus 4.7 / 4.6 / Sonnet 4.6** support a 1M-token context window at standard rates; other models default to 200K. For authoritative billing check the [Claude Console usage page](https://platform.claude.com/usage).

## Data location

```
~/.claude/token_usage/
├── projects/   # per-project history (one JSON per project)
└── status/     # live status snapshots (current.json read by status bar)
```

## License

MIT — see [LICENSE](LICENSE).
