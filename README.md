# ClaudeCount

English | [简体中文](./README.zh-CN.md)

[![Version](https://img.shields.io/badge/version-1.4.0-blue.svg)](#changelog)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Real-time token usage and cost tracking for Claude Code (Terminal), shown directly in the status bar.

```
MYPROJECT Sonnet 4.6 200k 🌡️ 22% 🎯 87% 🎫 18M │ Turn: $0.03 (↑180k ↓450 179k «) │ Sess: $0.18 (↑180k ↓450 179k «, 5 turns, 12 min) │ Proj: $2.40 (↑18M ↓62k 17M «, 5 sess, 40 turns, 3hr)
```

| Segment | Meaning |
|---------|---------|
| **PROJECT NAME** | Active project (directory name, uppercased) |
| **Model** | Current model name, `⚡` when fast mode is on, the effort level (e.g. `medium`), and context window size — all taken live from Claude Code |
| **🌡️ %** | Context window fill — green < 50%, blue 50–74%, yellow 75–89%, red ≥ 90% |
| **🎯 %** | Session cache hit rate (`cache_read` / total input) — orange < 50%, yellow 50–74%, blue 75–89%, green ≥ 90%; hidden until the session has any input |
| **🎫 N** | Project-total token consumption (input + output + cache_read + cache_creation summed). For parent projects: family aggregate (parent + every child); for standalone and sub-projects: their own total |
| **Turn** | Cost + tokens for the last completed turn (all API calls summed, including any subagents that ran during it) |
| **Sess** | Cumulative cost, tokens, turn count, and active time for this session (includes subagent / Task-tool spend) |
| **Proj** | All-time cost, tokens, session count, total turns, and active hours |

### Token notation

| Symbol | Meaning |
|--------|---------|
| `↑` | Total input tokens (non-cached + cache_creation + cache_read) |
| `↓` | Output tokens generated |
| `«` | Portion of `↑` served from cache (0.1× input rate; 0.05× on Opus 5.5; 0.025× on Fable/Mythos 5.1) |

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

## Starting tracking on a project

Tracking starts automatically the first time a Stop hook fires in a directory — you don't normally need to do anything. The exception is when you're inside a subdirectory of an already-tracked project and want this subdir tracked as its **own** top-level project (instead of rolling up into the parent). Use `--init`:

```bash
# From the project directory
python3 ~/.claude/hooks/token_tracker.py --init

# Or point at any path
python3 ~/.claude/hooks/token_tracker.py --init /path/to/project
```

Idempotent — re-running on an already-tracked project is a no-op. If past Claude Code transcripts are on disk for this project, `--init` reports the count on a second line (`available_transcripts: N`) so you know there's history available to import.

## Adopting an older project

If you installed ClaudeCount on a project that already has a Claude Code history, you can import past sessions in one shot — Claude Code stores every transcript at `~/.claude/projects/<encoded-cwd>/*.jsonl`, so cost, tokens, turn count, and active time can all be reconstructed from disk:

```bash
# From the project directory
python3 ~/.claude/hooks/token_tracker.py --import

# Or point at any project path
python3 ~/.claude/hooks/token_tracker.py --import /path/to/project
```

Idempotent — sessions already on file are skipped, so you can re-run safely. Imported sessions are tagged `"imported": true` in `~/.claude/token_usage/projects/{pid}.json`.

## Parent / sub-projects

Each working directory is its own project — opening Claude Code inside a subfolder creates a separate counter on purpose, never automatically. If you want a subfolder rolled up under its parent (e.g. `myrepo/web` and `myrepo/server` both contributing to `myrepo`'s total), opt in with `--set-parent`:

```bash
# From the sub-project directory
python3 ~/.claude/hooks/token_tracker.py --set-parent /path/to/parent

# Or point at both ends explicitly
python3 ~/.claude/hooks/token_tracker.py --set-parent /path/to/parent /path/to/child

# Remove the link (sub-project becomes independent again, history kept)
python3 ~/.claude/hooks/token_tracker.py --unset-parent

# Merge the sub-project's history into the parent and delete the sub-project
# record. Destructive — runs a preview by default; pass --yes to actually apply.
python3 ~/.claude/hooks/token_tracker.py --merge-into-parent /path/to/child
python3 ~/.claude/hooks/token_tracker.py --merge-into-parent /path/to/child --yes
```

Single-level only — a parent can't itself have a parent. Once linked:

- **When opened in the parent directory**, the status bar becomes multi-line: the parent's full stats (🌡️ context %, 🎯 cache hit, 🎫 family-total tokens, Turn, Sess, Proj family aggregate) occupy line 1; each child with non-zero spend gets its own compact row below (`  › Name  Sub: $cost (tokens, sessions, turns, time)`), sorted by cost. Children with $0.00 stay hidden. **When opened in a sub-project directory**, the status bar remains a single line showing the sub-project's own stats
- The sub-project's status bar header reads `parent › CHILD`; its third segment is relabelled `Sub:` (instead of `Proj:`) and shows the sub-project's own totals
- The parent's status bar `Proj:` segment becomes a **family aggregate** — its cost, tokens, session count, turns and active time are summed across the parent and every child. Updated automatically: a child's Stop hook also refreshes the parent's status, so the parent's bar reflects the latest family numbers without waiting for its own Stop
- `Sess:` and `Turn:` are never aggregated — you can only be in one session at a time
- `token_report` on the parent gets a `Sub-projects:` block (children with $0.00 are hidden as noise), followed by `Project total:` summing the parent and all children
- The sub-project's `token_report` block shows a `Parent:` line at the top; its `Total cost` is its own
- Sub-projects still appear as independent top-level entries — nothing is hidden, just rolled up

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
- Folds in **subagent (Task tool) spend** — Claude Code stores each subagent run in a separate `<session_id>/subagents/agent-*.jsonl` file that is absent from the main transcript, so its tokens are read and added to Sess/Proj totals (and to Turn for subagents that ran during the last turn)
- Calculates Turn, Sess, and Proj costs and token totals
- Writes `~/.claude/token_usage/status/current.json`

**Status bar** runs every 30 seconds via `token_status.sh`:
- Receives live model ID and `context_window.current_usage` from Claude Code on stdin
- Model name updates immediately when you switch models with `/model`
- Context window **size** is taken from Claude Code's live `context_window_size` (so 1M variants opt-ins are picked up correctly); the built-in model table is a fallback only
- Context window **percentage** is recomputed locally from `current_usage` over the live window size, using the same input-only formula Claude Code uses (`input + cache_creation + cache_read`, output excluded)
- It **never resets Turn/Sess on its own.** The status line's `session_id` is not always the id the Stop hook keys on — with Claude Code background sessions and per-session git worktrees, the foreground status line reports one session while the working/Stop session is another, yet both map to the same tracked project. Session resets are owned solely by the SessionStart / UserPromptSubmit hooks (which share the working session id), so the bar reflects real-time Turn/Sess instead of flickering back to zero

**Active time** counts the total time when human and AI are collaborating: it sums all inter-message gaps (both sides) up to 3 minutes. Gaps longer than 3 minutes — idle pauses, away-from-screen periods, unanswered permission prompts — are excluded.

## Pricing reference (defaults)

Cache write has two tiers: **5-minute** (1.25× input) and **1-hour** (2× input).

| Model | Input | Output | Cache write 5m | Cache write 1h | Cache read |
|-------|-------|--------|----------------|----------------|------------|
| Fable 5.1 / Mythos 5.1 | $10.00 | $50.00 | $12.50 | $20.00 | $0.25 † |
| Fable 5 / Mythos 5 | $10.00 | $50.00 | $12.50 | $20.00 | $1.00 |
| Opus 5.5 | $4.00 | $20.00 | $5.00 | $8.00 | $0.20 † |
| Opus 5 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| Opus 4.8 / 4.7 / 4.6 / 4.5 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| Opus 4.1 / 4 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Sonnet 5 | $2.00 | $10.00 | $2.50 | $4.00 | $0.20 |
| Sonnet 4.6 / 4.5 / 4 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 4.5 | $1.00 | $5.00 | $1.25 | $2.00 | $0.10 |
| Sonnet 3.7 / 3.5 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 3.5 | $0.80 | $4.00 | $1.00 | $1.60 | $0.08 |
| Opus 3 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| Haiku 3 | $0.25 | $1.25 | $0.30 | $0.50 | $0.03 |

Prices are per million tokens. **Fable 5.1 / 5**, **Mythos 5.1 / 5**, **Opus 5.5 / 5 / 4.8 / 4.7 / 4.6**, **Sonnet 5**, and **Sonnet 4.6** support a 1M-token context window at standard rates; all other models default to 200K. Rates verified against the [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing) catalog on 2026-09-22. For authoritative billing check the [Claude Console usage page](https://platform.claude.com/usage).

† Cache reads are billed below the usual 0.1× input on three models: **Opus 5.5** at 0.05× ($0.20), **Fable 5.1 / Mythos 5.1** at 0.025× ($0.25). Every other model uses 0.1×.

**Fast mode** (research preview) bills **Opus 5.5** at $8 / $40 and **Opus 5 / Opus 4.8** at $10 / $50 per Mtok. ClaudeCount detects it from `usage.speed == "fast"` in the transcript and applies the premium tier automatically; cache multipliers stack on top of the fast base.

**Server-side model fallback.** When a request falls back to another model mid-flight (Claude Code 2.1.27x records e.g. `claude-fable-5` → `claude-opus-4-8` in `usage.iterations`), both attempts are billed, each at its own model's rates. The top-level `usage` only covers the last attempt, so ClaudeCount prices every iteration separately.

### `--audit` — check for Claude changes the tracker doesn't cover yet

```bash
python3 ~/.claude/hooks/token_tracker.py --audit             # last 14 days of transcripts
python3 ~/.claude/hooks/token_tracker.py --audit --days 60
```

Scans recent transcripts and reports, as `ISSUE:` lines, any model id with no
pricing entry (including ones that only match an older model by prefix, e.g. a
new `claude-opus-5-7` billing as Opus 5), new `usage` fields, new
`usage.iterations` types, and pricing modifiers ClaudeCount doesn't apply.
`audit: 0 issue(s)` means the price table and parser cover everything seen.
The `claudecount-sync-claude` skill runs this after a Claude update.

### `--reprice` — recompute historical costs

When a model is missing from the pricing table, its sessions are recorded at a
$3/$15 fallback rate. After adding the model, `--reprice` corrects the history:

```bash
python3 ~/.claude/hooks/token_tracker.py --reprice          # preview
python3 ~/.claude/hooks/token_tracker.py --reprice --yes     # apply
```

Sessions whose Claude Code transcript is still on disk are repriced exactly —
every API call against the model that actually served it, with subagent spend
folded in. Sessions whose transcript has been rotated away are left untouched
unless they use a model that was provably mispriced, because re-deriving a cost
from a flat token total would corrupt any session that mixed models. Idempotent.

## Data location

```
~/.claude/token_usage/
├── projects/   # per-project history (one JSON per project)
└── status/     # live status snapshots (current.json read by status bar)
```

## Changelog

This project follows [Semantic Versioning 2.0](https://semver.org/) and the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

### [1.5.0] — 2026-09-22

#### Added
- **Claude Opus 5.5** (`claude-opus-5-5`, Claude Code 2.1.280's default Opus): $4 / $20 per Mtok, 1M context. Cache write 5m $5.00 / 1h $8.00, cache read **$0.20 (0.05× input)**. Fast mode $8 / $40
- **Effort level and fast mode on the status bar**, next to the model name (`Opus 5.5 ⚡ medium 1M`), read live from Claude Code's status-line `effort.level` / `fast_mode`
- **`--audit`**: reports transcript drift the tracker doesn't cover yet (unknown or prefix-matched models, new usage fields, new iteration types, unapplied pricing modifiers)

#### Fixed
- **Streamed responses were counted two or three times.** Claude Code writes one transcript row per content block, and early rows carry a mid-stream `output_tokens` (e.g. 8, then 642 on the last row). The old consecutive-identical-usage dedup therefore kept every row, billing the request's input and cache tokens once per block. Calls are now keyed by `requestId` (falling back to `message.id`), keeping only the final row. On this repo author's data, `--reprice` moved the tracked total by −$109 (−0.7%)
- **Opus 5.5 was billed and displayed as Opus 5** through the `claude-opus-5` prefix match, over-reporting it by about 25% on input/output and 2.5× on cache reads
- **Server-side model fallback under-billed.** Only the final attempt (`fallback_message`) was priced. Each `usage.iterations` entry is now billed at its own model's rates

#### Known limitation
- Advisor-model tokens (a transcript's `advisorModel` differing from `model`) are not written to the transcript, so they can't be counted. `--audit` reports how many calls this affects

### [1.4.0] — 2026-09-12

#### Added
- **Claude Opus 5** (`claude-opus-5`) — $5 / $25 per Mtok, 1M context (cache write 5m $6.25 / 1h $10.00, cache read $0.50)
- **Claude Sonnet 5** (`claude-sonnet-5`) — $2 / $10 per Mtok, 1M context (cache write 5m $2.50 / 1h $4.00, cache read $0.20)
- **Claude Fable 5.1** (`claude-fable-5-1`) and **Claude Mythos 5.1** (`claude-mythos-5-1`) — $10 / $50 per Mtok, 1M context. Cache reads bill at **0.025× input** ($0.25), the only models that deviate from the usual 0.1×
- **Fast mode pricing** — `usage.speed == "fast"` now bills Opus 5 / Opus 4.8 at the $10 / $50 premium tier, with cache multipliers stacking on the fast base
- **`--reprice`** — recompute stored session costs at current prices. Transcript-first (exact, per-API-call, folds in subagent spend); transcript-less sessions are left untouched unless their model was provably mispriced. Preview by default, `--yes` to apply, idempotent
- Invariant tests pinning the 1.25× / 2× cache-write and 0.1× cache-read multipliers across the whole table, with the Fable/Mythos 5.1 and Haiku 3 exceptions encoded

#### Fixed
- **Opus 5 and Sonnet 5 sessions were priced at the `_DEFAULT_MODEL` $3 / $15 fallback.** Opus 5 — the current default model — was under-reported by ~40% and Sonnet 5 over-reported by 50%. Repricing this repo author's own history moved the tracked total +$1,023

#### Verified
- All rates re-checked against [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing) on 2026-09-12. Confirmed unchanged: Fable 5 / Mythos 5, Opus 4.8 / 4.7 / 4.6 / 4.5, Sonnet 4.6, Haiku 4.5. Sonnet 5's $2 / $10 introductory rate is now permanent (the scheduled 2026-09-01 rise to $3 / $15 was cancelled)
- `usage.service_tier` and `usage.inference_geo` are carried in Claude Code transcripts but read `"standard"` / `"not_available"`, so the Batch 50% discount and the 1.1× US data-residency multiplier never apply — documented rather than implemented

### [1.3.0] — 2026-06-26

**Fixed**
- **Turn / Sess no longer freeze on the status bar during background sessions.** Claude Code background sessions and per-session git worktrees make the status line's `session_id` differ from the id the Stop hook records (both still resolve to the same tracked project). `render_mode`'s session-change self-heal treated that mismatch as a brand-new session and zeroed Turn/Sess on every 30s render, so the bar appeared stuck. Render no longer resets on a `session_id` mismatch — session resets are owned solely by the SessionStart / UserPromptSubmit hooks, which share the working session id with the Stop hook
- `--import` / `--init` now find transcripts for project paths that contain **spaces or dots** (e.g. `NCARB Study/app`). Claude Code encodes its transcript directory by replacing every non-alphanumeric character with `-`, not just `/`; the previous `/`→`-`-only encoding silently missed those paths

**Added**
- **Subagent (Task tool) cost accounting.** Claude Code stores each subagent run in a separate `<session_id>/subagents/agent-*.jsonl` file that is absent from the main transcript, so subagent tokens were previously uncounted — under-reporting cost for subagent-heavy sessions. The Stop hook (and `--import`) now read those files and fold their per-model spend into Sess / Proj totals, and into Turn for subagents that ran during the last turn. Active sessions pick up the corrected totals on their next Stop; previously-imported historical sessions are unchanged unless re-imported
- **Claude Fable 5** (`claude-fable-5`) and **Claude Mythos 5** (`claude-mythos-5`) pricing — $10 / $50 per Mtok, 1M context (cache write 5m $12.50 / 1h $20.00, cache read $1.00). Verified against the platform.claude.com catalog on 2026-09-12

### [1.2.1] — 2026-05-18

**Added**
- `--init` mode: lightweight one-shot CLI to start tracking a project. Creates an empty top-level project record at the target path (cwd by default) so future Stop hooks bind to it instead of auto-rolling-up into a tracked ancestor. Idempotent. Reports `available_transcripts: N` on a second line when on-disk Claude Code transcripts are not yet imported — caller decides whether to invoke `--import`
- `claudecount-init` skill: invoke via `/claudecount-init` or natural-language triggers ("init claudecount", "start tracking this project", "把这个项目加入 ClaudeCount", "初始化 claudecount"). Runs `--init`, then prompts the user before importing past transcripts if any are available

**Changed**
- Previously, the only way to materialise a project record on demand was `--set-parent`, which forced the user to also pick a parent — overkill when the goal is just "track this folder as its own top-level project". `--init` is now the dedicated entry point

### [1.2.0] — 2026-05-07

**Added**
- Auto-rollup pid resolution: cd-ing into an untracked subdirectory of an existing project no longer creates a new top-level project record. The Stop / SessionStart / UserPromptSubmit hooks and `token_status.sh` walk up the directory tree and attribute activity to the nearest tracked ancestor. Pre-existing subdir projects (with their own record) keep accumulating to themselves; use `--merge-into-parent` to consolidate after the fact, or `--set-parent` to keep a subdir tracked separately *and* shown under its parent
- `--set-parent` now accepts project **names** in addition to paths, and supports **batch** linking: `--set-parent ginzok-online Ginweb server projects` mounts three children under one parent in a single call
- `--list-projects` mode: tab-separated machine-readable list (`name<TAB>parent<TAB>cost<TAB>cwd`), used by the `claudecount-set-parent` skill to show candidates
- `🎫` project-total token-consumption indicator. Appears in the status-bar header (after `🎯 cache hit`) and in `token_report` output (after `Cache reads`). Sums input + output + cache_read + cache_creation. Parent projects show the family aggregate (parent + every child); standalone and sub-projects show their own total. Recomputed each render from `projects/*.json` — no cached field, no drift

**Changed**
- `claudecount-set-parent` skill rewritten to handle both directions of natural-language phrasing ("把 X 挂到 Y 下" / "在这里把 X 设为子项目"), batch input, and name-based references — with an optional `--list-projects` pre-step when intent is ambiguous

### [1.1.0] — 2026-04-29

**Added**
- `🎯` session cache hit rate indicator in the status-bar header (`cache_read / total_input`), with industry-standard color tiers (green ≥90% / blue ≥75% / yellow ≥50% / orange <50%)
- `🌡️` context-window pressure indicator (replaces the previous symbol; emoji and number now separated by a space)
- `--import` mode: adopt sessions that pre-date ClaudeCount by reconstructing them from on-disk Claude Code transcripts
- `--set-parent` / `--unset-parent`: opt-in parent / sub-project linking. Sub-projects render as `parent › CHILD` in the status bar header and relabel the third segment as `Sub:` (own usage). The parent's `Proj:` segment becomes a *family aggregate* (parent + every child's cost / tokens / sessions / turns / active time), refreshed automatically when any child's Stop hook fires. `token_report` lists each visible (non-zero-spend) child and shows a `Project total:` rollup
- `--merge-into-parent [child] [--yes]`: absorb a sub-project's sessions into its parent and delete the sub-project record. Destructive — runs a preview by default; sessions are tagged `merged_from: <child_name>` for audit. Refuses if the child carries a `legacy` block (manual consolidation needed)
- Per-project routing in `token_status.sh`: simultaneous Claude Code instances in different projects now show their own data instead of fighting over a shared `current.json`
- `--version` / `-V` flag

**Changed**
- Cache hit rate granularity is per-session (per-turn fluctuates too much; per-project converges and loses signal)
- Status-bar header layout: emoji and value separated by a space (`🌡️ 35%` instead of `🌡️35%`)
- Sub-project separator in status bar uses the U+203A breadcrumb chevron (`›`) instead of `/`

**Fixed**
- `current.json` cross-project pollution that caused two simultaneous Claude Code sessions to display each other's session totals
- Cache-creation pricing now correctly handles the `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` tier breakdown when present (verified against 289 real assistant usage entries)
- `token_report.py` with no arguments now shows only the current project (matching the documented behavior); pass `--all` to see every project. Previously the no-arg path silently fell through to `--all`, burying the current project's `Sub-projects:` rollup beneath higher-cost siblings
- Status bar in parent project now renders multiple lines (parent + compact child rows) when children have non-zero spend; `token_status.sh` pid routing now prefers `cwd` (session startup directory) over `workspace.current_dir` to prevent project identity drift

### [1.0.0] — 2026-04-26

Initial release.

## License

MIT — see [LICENSE](LICENSE).
