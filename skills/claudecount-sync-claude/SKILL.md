---
name: claudecount-sync-claude
description: Bring ClaudeCount up to date after Claude / Claude Code changes — new models, price changes, new usage fields, transcript format drift, new status-line fields. Trigger when the user says Claude (or Claude Code) was updated and ClaudeCount should follow — "claude 更新了，让 claudecount 拉齐", "同步 claudecount 的计费/模型", "update claudecount for the new model", "claudecount 价格过时了吗", "sync claudecount with the latest Claude release", "is claudecount billing the new model right". Audits local transcripts, checks the official pricing page and Claude Code changelog, then updates the price table, parser and display test-first on a branch. Never guesses prices.
arguments:
  - name: repo-path
    description: Optional. Path to the ClaudeCount git checkout. Defaults to ~/projects/claudePlugin/claudeCount.
    required: false
allowed-tools:
  - Bash(claude --version)
  - Bash(python3 src/token_tracker.py --audit*)
  - Bash(python3 src/token_tracker.py --reprice)
  - Bash(python3 -m unittest discover -s tests*)
  - Bash(git fetch*)
  - Bash(git status*)
  - Bash(git log*)
  - Bash(git diff*)
  - WebFetch
---

Sync ClaudeCount with the latest Claude / Claude Code release.

ClaudeCount prices every transcript `usage` block with the `MODELS` table in
`src/token_tracker.py`. When Claude ships a new model, changes a price, or changes
how Claude Code writes transcripts or status-line JSON, the tracker keeps working
but reports the wrong cost. This skill finds each gap and closes it.

## Step 0 — Set up an isolated branch

Work in the repo (`"$0"`, default `~/projects/claudePlugin/claudeCount`).
Run `git fetch`, then create a worktree or branch off `origin/main`, named e.g.
`claude-sync-YYYY-MM-DD`. Never edit `main` directly and never push to it.

## Step 1 — Collect evidence (read-only)

Do all of these before proposing anything:

1. `claude --version`, the Claude Code version installed now.
2. `python3 src/token_tracker.py --audit --days 30`. Run it from the repo, not
   `~/.claude/hooks`, so it tests the current source. Every `ISSUE:` line is a
   concrete gap. A `model X has no MODELS entry — billed and shown as Y via prefix
   match` line means a new model is silently billed as an older one. That is how
   Opus 5.5 went in as Opus 5.
3. The official pricing page: https://platform.claude.com/docs/en/about-claude/pricing.
   Read each model row, plus the fast-mode, long-context, data-residency and
   footnote sections. Also read the models overview:
   https://platform.claude.com/docs/en/about-claude/models/overview.
4. The Claude Code changelog:
   https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md.
   Read the entries since the "verified against … on <date>" date in the README's
   *Pricing reference* section. Look for new models or defaults, status-line stdin
   fields (https://code.claude.com/docs/en/statusline), hook payload changes,
   transcript/subagent layout changes, and fast mode.
5. If `--audit` shows a usage field or iteration type it doesn't know, open a
   real transcript row that contains it (`~/.claude/projects/*/*.jsonl`) and
   work out its semantics from the numbers. Example: does top-level usage equal
   the sum of its iterations?

**Never guess a price.** Every rate you write must come from a fetched page;
quote the row. If a page can't be fetched, stop and tell the user. The one
allowed exception is a derived rate that the page itself defines as a
multiplier, e.g. fast-mode cache rates = fast base × the cache multipliers.
Mark those `derived` in the code comment.

## Step 2 — Show the user a change plan

List what changes and why, grouped as **billing** (new or changed prices,
dedup or parser fixes), **display** (model names, status-line fields) and
**docs**. For each item, give its source (URL + quote, or audit line). Wait
for the user's go-ahead before editing. A price change rewrites their cost
history once `--reprice` runs.

## Step 3 — Implement test-first

Write each test first and watch it fail, then change the code.

- **New model**: add a test to `ModelPricingTests` in
  `tests/test_token_tracker.py`. It asserts name, input/output/context and all
  three cache rates. Then add **one** `MODELS` entry. Keep the table
  longest-key-first safe: if the new id extends an existing key
  (`claude-opus-5-5` ⊃ `claude-opus-5`), add a `*_wins_over_*_substring` test.
- **Non-standard cache-read ratio** (anything but 0.1× input): add the key to
  `CACHE_READ_EXCEPTIONS` in the invariant test and say so in the MODELS comment.
- **Fast mode**: add a `"fast": {...}` block and a `get_pricing(..., speed="fast")` test.
- **Price change on an existing model**: update the entry and its test. Note the
  old → new rate in the changelog.
- **New usage field or iteration type that affects billing**: handle it in
  `calc_cost` / `expand_iterations` / `_collect_assistant_usages`. Then add it
  to `_KNOWN_USAGE_KEYS` / `_KNOWN_ITERATION_TYPES` so `--audit` goes quiet.
  If it does *not* affect billing, just add it to the known set, with a comment.
- **Status-line field worth showing**: merge it in `render_mode` (only when
  present), render it in `_render_header`, and add a `ModelModeDisplayTests`-style test.
- `_NEWLY_PRICED`: add a model **only** if `--audit` called it an *unknown
  model* (it was billed at the $3/$15 `_DEFAULT_MODEL`). Don't add it if it was
  prefix-matched to another model. Its stored cost isn't the default rate, so a
  flat-total reprice would be wrong.
- Bump `__version__` (minor for new models or features, patch for fixes only).
  Update both `README.md` and `README.zh-CN.md`: the pricing table, the
  "verified … on <today>" date, the footnotes, and a changelog entry.

## Step 4 — Verify

1. `python3 -m unittest discover -s tests`: all tests pass.
2. `python3 src/token_tracker.py --audit --days 30`: `audit: 0 issue(s)`, or
   only issues the user agreed to leave, listed as known limitations.
3. `python3 src/token_tracker.py --reprice`: preview only, **no `--yes`**.
   Report the per-project and total delta to the user.

## Step 5 — Hand off

Commit on the branch with a message that states the verified sources and the
reprice delta, then push the branch (never `main`). Tell the user the next steps.
They are theirs to run or approve:

```
# after merging to main, on each machine:
bash install.sh            # or: curl -fsSL https://raw.githubusercontent.com/ayooleo/ClaudeCount/main/install.sh | bash
python3 ~/.claude/hooks/token_tracker.py --reprice          # preview again with the deployed code
python3 ~/.claude/hooks/token_tracker.py --reprice --yes    # apply — only with explicit approval
```

Also copy this skill to `~/.claude/skills/claudecount-sync-claude/` if it changed.
