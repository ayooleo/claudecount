#!/usr/bin/env python3
"""ClaudeCount — token usage and cost tracker for Claude Code."""

import json
import sys
import os
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

BASE_DIR   = Path.home() / ".claude" / "token_usage"
STATUS_DIR = BASE_DIR / "status"
DATA_DIR   = BASE_DIR / "projects"


def _read_hook_input():
    """Parse hook stdin JSON. Returns dict or None on missing/invalid input."""
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _is_user(msg: dict) -> bool:
    return ((msg.get("role") or msg.get("type")) == "user"
            or msg.get("message", {}).get("role") == "user")


def _is_assistant(msg: dict) -> bool:
    return ((msg.get("role") or msg.get("type")) == "assistant"
            or msg.get("message", {}).get("role") == "assistant")

# Per-million-token rates and per-model metadata. Verified 2026-04-26 against
# https://platform.claude.com/docs/en/about-claude/pricing.
#   cache_write_5m = ephemeral 5-min tier (1.25× input)
#   cache_write_1h = ephemeral 1-hr tier   (2×    input)
#   cache_read     =                       (0.1×  input)
# context = 1M only for Opus 4.7 / 4.6 / Sonnet 4.6 (standard pricing across the
# full window). All other current models default to 200K.
MODELS = {
    # Claude 4 family
    "claude-opus-4-7":   {"input":  5.00, "output": 25.00, "cache_write_5m":  6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "context": 1_000_000, "name": "Opus 4.7"},
    "claude-opus-4-6":   {"input":  5.00, "output": 25.00, "cache_write_5m":  6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "context": 1_000_000, "name": "Opus 4.6"},
    "claude-opus-4-5":   {"input":  5.00, "output": 25.00, "cache_write_5m":  6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "context":   200_000, "name": "Opus 4.5"},
    "claude-opus-4-1":   {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read": 1.50, "context":   200_000, "name": "Opus 4.1"},
    "claude-opus-4":     {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read": 1.50, "context":   200_000, "name": "Opus 4"},
    "claude-sonnet-4-6": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 4.6"},
    "claude-sonnet-4-5": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 4.5"},
    "claude-sonnet-4":   {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 4"},
    "claude-haiku-4-5":  {"input":  1.00, "output":  5.00, "cache_write_5m":  1.25, "cache_write_1h":  2.00, "cache_read": 0.10, "context":   200_000, "name": "Haiku 4.5"},
    # Claude 3.x family (3.7 / 3.5 sonnet are deprecated but still callable)
    "claude-3-7-sonnet": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 3.7"},
    "claude-3-5-sonnet": {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 3.5"},
    "claude-3-5-haiku":  {"input":  0.80, "output":  4.00, "cache_write_5m":  1.00, "cache_write_1h":  1.60, "cache_read": 0.08, "context":   200_000, "name": "Haiku 3.5"},
    "claude-3-opus":     {"input": 15.00, "output": 75.00, "cache_write_5m": 18.75, "cache_write_1h": 30.00, "cache_read": 1.50, "context":   200_000, "name": "Opus 3"},
    "claude-3-sonnet":   {"input":  3.00, "output": 15.00, "cache_write_5m":  3.75, "cache_write_1h":  6.00, "cache_read": 0.30, "context":   200_000, "name": "Sonnet 3"},
    "claude-3-haiku":    {"input":  0.25, "output":  1.25, "cache_write_5m":  0.30, "cache_write_1h":  0.50, "cache_read": 0.03, "context":   200_000, "name": "Haiku 3"},
}

_DEFAULT_MODEL = {
    "input": 3.00, "output": 15.00,
    "cache_write_5m": 3.75, "cache_write_1h": 6.00, "cache_read": 0.30,
    "context": 200_000, "name": "",
}

# Cached order: longest key first so e.g. "claude-opus-4-7" wins over "claude-opus-4".
_MODEL_KEYS_BY_LEN = sorted(MODELS, key=len, reverse=True)


def _resolve_model(model: str) -> dict:
    """Find the most specific MODELS entry whose key is a substring of `model`.
    Handles variant suffixes like `claude-opus-4-7[1m]`."""
    if not model:
        return _DEFAULT_MODEL
    for key in _MODEL_KEYS_BY_LEN:
        if key in model:
            return MODELS[key]
    return _DEFAULT_MODEL


def fmt_model(model: str) -> str:
    if not model:
        return ""
    m = _resolve_model(model)
    if m is not _DEFAULT_MODEL:
        return m["name"]
    return model.replace("claude-", "")[:15]


_PRICE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")


def get_pricing(model: str, overrides: dict = None) -> dict:
    m = _resolve_model(model)
    base = {k: m[k] for k in _PRICE_KEYS}
    if not overrides:
        return base
    p = {**base, **overrides}
    # Back-compat: if caller provides old "cache_write" key, split into tiers
    if "cache_write" in overrides and "cache_write_5m" not in overrides:
        p["cache_write_5m"] = overrides["cache_write"]
        p["cache_write_1h"] = overrides["cache_write"] * 1.6
    return p


def get_context_window(model: str) -> int:
    """Last-resort lookup. Live runtime data (persisted by render_mode) is preferred."""
    return _resolve_model(model)["context"]


def calc_cost(usage: dict, model: str, price_override: dict = None) -> float:
    p = get_pricing(model, price_override)
    # Two-tier cache write: prefer detailed breakdown when available
    cc = usage.get("cache_creation", {})
    tokens_5m = cc.get("ephemeral_5m_input_tokens", 0)
    tokens_1h  = cc.get("ephemeral_1h_input_tokens", 0)
    if not (tokens_5m or tokens_1h):
        # Fallback: treat whole cache_creation as 5m tier (conservative estimate)
        tokens_5m = usage.get("cache_creation_input_tokens", 0)
    return (
        usage.get("input_tokens", 0)            / 1e6 * p["input"]
        + usage.get("output_tokens", 0)         / 1e6 * p["output"]
        + tokens_5m                             / 1e6 * p["cache_write_5m"]
        + tokens_1h                             / 1e6 * p["cache_write_1h"]
        + usage.get("cache_read_input_tokens", 0) / 1e6 * p["cache_read"]
    )


def project_id(cwd: str) -> str:
    return hashlib.md5(cwd.encode()).hexdigest()[:12]


def read_transcript(path: str) -> list:
    msgs = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        msgs.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except (OSError, IOError):
        pass
    return msgs


def extract_usage_from_msg(msg: dict) -> tuple:
    inner = msg.get("message", {})
    usage = inner.get("usage") or msg.get("usage") or {}
    model = inner.get("model") or msg.get("model") or ""
    return usage, model


def get_session_start(messages: list) -> str:
    for msg in messages:
        ts = msg.get("timestamp") or msg.get("created_at") or ""
        if ts:
            return ts
    return ""


# Slash-command and local-command wrappers that Claude Code emits as user-role messages.
# They are not real human prompts: e.g. /init produces a <command-message> marker plus the
# actual expanded prompt; /model produces only markers (no Claude turn at all).
_COMMAND_TAG_PREFIXES = (
    "<command-name>", "<command-message>", "<command-args>",
    "<local-command-stdout>", "<local-command-caveat>",
)


def _starts_with_command_tag(text) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(_COMMAND_TAG_PREFIXES)


def is_human_message(msg: dict) -> bool:
    """True if this user message is a real human prompt sent to Claude.

    Filters out (a) tool results fed back to Claude, and (b) slash-command markers /
    local-command stdout/caveat strings emitted by Claude Code's UI layer — whether
    delivered as a top-level string or as a {"type":"text","text":"<command-…>"} block
    inside a list-of-blocks content payload.
    """
    for content in (msg.get("content"), msg.get("message", {}).get("content")):
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_result":
                    return False
                if item.get("type") == "text" and _starts_with_command_tag(item.get("text")):
                    return False
        elif _starts_with_command_tag(content):
            return False
    return True


def _usage_key(usage: dict) -> tuple:
    return (
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
        usage.get("cache_creation_input_tokens", 0),
    )


def deduplicate_api_calls(pairs: list) -> list:
    """Remove consecutive assistant messages with identical usage (same API call, multiple content blocks)."""
    result = []
    for usage, model in pairs:
        if result and _usage_key(usage) == _usage_key(result[-1][0]):
            continue
        result.append((usage, model))
    return result


def count_turns(messages: list) -> int:
    """Count TUI turns = number of real human messages in the transcript."""
    return sum(1 for m in messages if _is_user(m) and is_human_message(m))


def _collect_assistant_usages(messages) -> list:
    results = []
    for msg in messages:
        if not _is_assistant(msg):
            continue
        usage, model = extract_usage_from_msg(msg)
        if usage and (usage.get("output_tokens", 0) > 0 or usage.get("input_tokens", 0) > 0):
            results.append((usage, model))
    return deduplicate_api_calls(results)


def get_last_turn_usages(messages: list) -> list:
    """Return usages for all assistant messages in the last TUI turn (since last human message)."""
    last_human_idx = -1
    for i, msg in enumerate(messages):
        if _is_user(msg) and is_human_message(msg):
            last_human_idx = i
    source = messages[last_human_idx + 1:] if last_human_idx >= 0 else messages
    return _collect_assistant_usages(source)


def get_all_assistant_usages(messages: list) -> list:
    return _collect_assistant_usages(messages)


def sum_usages(usages: list, price_override: dict = None) -> tuple:
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    total_cost = 0.0
    last_model = ""
    for usage, model in usages:
        for key in total:
            total[key] += usage.get(key, 0)
        total_cost += calc_cost(usage, model, price_override)
        if model:
            last_model = model
    return total, total_cost, last_model


def calc_active_minutes(messages: list, idle_threshold_min: int = 3) -> int:
    """Sum inter-message gaps; gaps beyond threshold are excluded as idle.

    Counts both human and AI messages so time spent watching Claude work is included.
    Appends a "now" sentinel so activity since the last logged message is captured —
    this matters because Stop fires before the final assistant text is flushed to the
    transcript. For after-the-fact reports the now-gap exceeds the threshold and is
    excluded naturally, so adding the sentinel is safe in both paths.
    """
    timestamps = []
    for msg in messages:
        ts = msg.get("timestamp") or msg.get("created_at") or ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamps.append(dt)
            except Exception:
                pass

    if not timestamps:
        return 0

    timestamps.sort()
    timestamps.append(datetime.now(timezone.utc))
    threshold_s = timedelta(minutes=idle_threshold_min).total_seconds()

    active_seconds = 0.0
    for i in range(1, len(timestamps)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if gap <= threshold_s:
            active_seconds += gap
    return max(0, round(active_seconds / 60))


def load_project_data(data_dir: Path, pid: str) -> dict:
    f = data_dir / f"{pid}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"pid": pid, "sessions": {}}


def save_project_data(data_dir: Path, pid: str, data: dict):
    f = data_dir / f"{pid}.json"
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_project_config(cwd: str) -> dict:
    """Load per-project config for custom pricing or disabling tracking."""
    for config_path in [
        Path(cwd) / ".claude" / "claudecount.json",
        Path.home() / ".claude" / "token_usage" / "config.json",
    ]:
        if config_path.exists():
            try:
                return json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


# ANSI color constants for status line rendering
_R    = "\033[0m"
_BOLD = "\033[1m"
_ORG  = "\033[38;5;214m"
_YLW  = "\033[38;5;220m"
_GRN  = "\033[38;5;120m"
_BLU  = "\033[38;5;111m"
_GRY  = "\033[38;5;183m"
_RED  = "\033[38;5;203m"
_SEP  = f"{_GRY} │ {_R}"


def _fmt_cost(c) -> str:
    if c == 0:
        return f"{_YLW}$0.00{_R}"
    return f"{_YLW}${c:.2f}{_R}"


def _fmt_tok(n) -> str:
    if n >= 1_000_000:
        s = f"{n/1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{_GRY}{s}M{_R}"
    if n >= 1_000:
        s = f"{n/1_000:.1f}".rstrip("0").rstrip(".")
        return f"{_GRY}{s}k{_R}"
    return f"{_GRY}{n}{_R}"


def _fmt_win(size) -> str:
    if size >= 1_000_000:
        return f"{int(size/1_000_000)}M"
    if size >= 1_000:
        return f"{int(size/1_000)}k"
    return str(size)


def _parens(*parts) -> str:
    return f"{_GRY}({_R}" + f"{_GRY}, {_R}".join(parts) + f"{_GRY}){_R}"


def _tok_triplet(d: dict) -> str:
    """`↑<total_in> ↓<out>[ <cache_read> «]` — shared by Turn/Sess/Proj segments.
    ↑↓ are prefix; « ('rewind') is suffix on the cache-read portion of ↑ — semantically
    'replayed from past context'."""
    total_in = d.get("input_tokens", 0) + d.get("cache_creation", 0) + d.get("cache_read", 0)
    cache_r  = d.get("cache_read", 0)
    cache_str = f" {_fmt_tok(cache_r)} {_GRY}«{_R}" if cache_r else ""
    return f"{_GRY}↑{_R}{_fmt_tok(total_in)} {_GRY}↓{_R}{_fmt_tok(d.get('output_tokens', 0))}{cache_str}"


def _render_header(status: dict) -> str:
    name = (status.get("project_name") or status.get("cwd", "").split("/")[-1] or "PROJ").upper()
    model = fmt_model(status.get("model", ""))
    header = f"{_BOLD}{_ORG}{name}{_R}"
    if not model:
        return header
    ctx          = status.get("context", {})
    ctx_pct      = ctx.get("pct", 0)
    ctx_win_size = ctx.get("window_size", 0)
    win_str = f" {_GRY}{_fmt_win(ctx_win_size)}{_R}" if ctx_win_size else ""
    if ctx_pct:
        c = (_RED if ctx_pct >= 90 else
             _YLW if ctx_pct >= 75 else
             _BLU if ctx_pct >= 50 else
             _GRN)
        cw_str = f" {c}{ctx_pct}%{_R}"
    else:
        cw_str = ""
    return header + f" {_GRY}{model}{_R}{win_str}{cw_str}"


def _render_turn_segment(t: dict) -> str:
    return f"{_BLU}Turn{_GRY}: {_R}{_fmt_cost(t.get('cost', 0))} {_parens(_tok_triplet(t))}"


def _render_sess_segment(sess: dict) -> str:
    parts = [_tok_triplet(sess)]
    turns = sess.get("turn_count", 0)
    if turns:
        parts.append(f"{_GRY}{turns} turns{_R}")
    active_min = sess.get("active_minutes")
    if active_min:
        h, m = divmod(int(active_min), 60)
        parts.append(f"{_GRY}{h} hr {m} min{_R}" if h else f"{_GRY}{m} min{_R}")
    return f"{_BLU}Sess{_GRY}: {_R}{_fmt_cost(sess.get('cost', 0))} {_parens(*parts)}"


def _render_proj_segment(proj: dict) -> str:
    parts = [_tok_triplet(proj), f"{_GRY}{proj.get('session_count', 0)} sess{_R}"]
    proj_turns = proj.get("turn_count", 0)
    if proj_turns:
        parts.append(f"{_GRY}{proj_turns} turns{_R}")
    proj_total_hr = round(proj.get("active_minutes", 0) / 60) if proj.get("active_minutes", 0) else 0
    if proj_total_hr >= 24:
        d, h = divmod(proj_total_hr, 24)
        parts.append(f"{_GRY}{d}d {h}hr{_R}" if h else f"{_GRY}{d}d{_R}")
    elif proj_total_hr >= 1:
        parts.append(f"{_GRY}{proj_total_hr}hr{_R}")
    return f"{_BLU}Proj{_GRY}: {_R}{_fmt_cost(proj.get('cost', 0))} {_parens(*parts)}"


def render_status_line(status: dict) -> str:
    return _SEP.join([
        _render_header(status),
        _render_turn_segment(status.get("last_turn", {})),
        _render_sess_segment(status.get("session", {})),
        _render_proj_segment(status.get("project", {})),
    ])


def _reset_session_state(session_id: str, cwd: str, model_hint: str = ""):
    """Reset turn/sess/context for a new session. Idempotent — only acts on new session_id.
    Used by both SessionStart and UserPromptSubmit hooks. SessionStart's `model` hook field
    seeds the model display before the first status-line render arrives."""
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    pid = project_id(cwd)
    status_file = STATUS_DIR / f"{pid}.json"

    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        status = {}

    if session_id == status.get("session_id", ""):
        return

    # Preserve window_size from live runtime data (written by render_mode from Claude Code stdin).
    live_win_size = status.get("context", {}).get("window_size", 0)

    status["session_id"] = session_id
    status["project_name"] = Path(cwd).name
    status["cwd"] = cwd
    status["pid"] = pid
    status["model"] = model_hint
    status["last_turn"] = {
        "cost": 0, "input_tokens": 0, "cache_creation": 0,
        "cache_read": 0, "output_tokens": 0,
    }
    status["session"] = {
        "cost": 0, "input_tokens": 0, "cache_creation": 0,
        "cache_read": 0, "output_tokens": 0,
        "started": datetime.now(timezone.utc).isoformat(),
        "active_minutes": 0,
        "turn_count": 0,
    }
    status["context"] = {"tokens": 0, "window_size": live_win_size, "pct": 0}

    status_json = json.dumps(status, ensure_ascii=False)
    status_file.write_text(status_json, encoding="utf-8")
    (STATUS_DIR / "current.json").write_text(status_json, encoding="utf-8")


def _hook_reset():
    """Shared body for SessionStart and UserPromptSubmit hooks. SessionStart includes
    a `model` field (per Claude Code hooks docs); UserPromptSubmit does not."""
    hook = _read_hook_input()
    if hook is None:
        return
    _reset_session_state(
        hook.get("session_id", "unknown"),
        hook.get("cwd", os.getcwd()),
        hook.get("model", ""),
    )


def session_start_mode():
    """SessionStart hook: reset display the moment a session opens, before any prompt."""
    _hook_reset()


def pre_turn_mode():
    """UserPromptSubmit hook: fallback reset in case SessionStart didn't fire."""
    _hook_reset()


def render_mode(argv: list):
    """StatusLine mode: merge live stdin data with stored current.json.

    Persists live model/window back to current.json so a mid-session /model switch
    is reflected for other consumers (token_report, next Stop hook, etc.) without
    waiting for the next assistant message.

    Self-heals on session change: if the live session_id from Claude Code differs
    from what's stored, run the same reset that SessionStart/UserPromptSubmit hooks
    do. Guarantees a fresh Turn/Sess display the moment a new session opens, even
    if those hooks aren't wired.
    """
    if len(argv) < 1:
        print("💰 --")
        return
    status_path = argv[0]
    raw_live = sys.stdin.read().strip()
    live = json.loads(raw_live) if raw_live else {}

    try:
        status = json.loads(Path(status_path).read_text(encoding="utf-8"))
    except Exception:
        status = {}

    live_session_id = live.get("session_id")
    live_cwd = (live.get("cwd")
                or (live.get("workspace") or {}).get("current_dir")
                or "")
    stored_sid = status.get("session_id", "")
    if (live_session_id and live_cwd
            and stored_sid and stored_sid != live_session_id):
        _reset_session_state(live_session_id, live_cwd)
        try:
            status = json.loads(Path(status_path).read_text(encoding="utf-8"))
        except Exception:
            status = {}

    if not status:
        print("💰 --")
        return

    # Live data from Claude Code is authoritative. Our model dict is fallback only.
    # Brief transient on mid-session /model switch self-heals in one render cycle.
    live_model = live.get("model") or {}
    live_ctx   = live.get("context_window") or {}
    changed = False

    current_model = status.get("model", "")
    new_model = live_model.get("id") if isinstance(live_model, dict) else None
    if new_model and current_model != new_model:
        status["model"] = new_model
        current_model = new_model
        changed = True

    ctx = status.setdefault("context", {})

    # Trust live window size (handles 1M variants like claude-sonnet-4-6 with 1M opt-in
    # that the dict can't represent). Fall back to dict for unknown / missing live data.
    live_size = (live_ctx.get("context_window_size")
                 if isinstance(live_ctx, dict) else None)
    new_size = live_size or get_context_window(current_model)
    if new_size and ctx.get("window_size") != new_size:
        ctx["window_size"] = new_size
        changed = True

    if isinstance(live_ctx, dict):
        # Per docs, used_percentage = (input + cache_creation + cache_read) / size.
        # Recompute from current_usage so pct/tokens stay consistent with our window_size.
        cu = live_ctx.get("current_usage") if isinstance(live_ctx.get("current_usage"), dict) else None
        if cu:
            live_tokens = (cu.get("input_tokens", 0)
                           + cu.get("cache_creation_input_tokens", 0)
                           + cu.get("cache_read_input_tokens", 0))
            if ctx.get("tokens") != live_tokens:
                ctx["tokens"] = live_tokens
                changed = True
            new_pct = round(live_tokens / new_size * 100) if (new_size and live_tokens) else 0
            if ctx.get("pct") != new_pct:
                ctx["pct"] = new_pct
                changed = True
        elif live_ctx.get("used_percentage") is not None:
            # Fallback: pre-API-call render or older Claude Code without current_usage.
            new_pct = live_ctx["used_percentage"]
            if ctx.get("pct") != new_pct:
                ctx["pct"] = new_pct
                changed = True

    if changed:
        try:
            status_json = json.dumps(status, ensure_ascii=False)
            Path(status_path).write_text(status_json, encoding="utf-8")
            # Mirror to global current.json so cross-project tools see fresh state
            current_json = Path(status_path).parent / "current.json"
            if Path(status_path).resolve() != current_json.resolve():
                current_json.write_text(status_json, encoding="utf-8")
        except Exception:
            pass

    print(render_status_line(status), end="")


def backfill_mode():
    """One-shot: recompute missing turn_count / active_minutes for legacy sessions.
    Idempotent — sessions that already have turn_count are skipped."""
    transcripts_root = Path.home() / ".claude" / "projects"
    if not DATA_DIR.exists() or not transcripts_root.exists():
        print("nothing to backfill")
        return

    sid_to_path = {p.stem: p for p in transcripts_root.glob("*/*.jsonl")}

    backfilled = 0
    for proj_file in sorted(DATA_DIR.glob("*.json")):
        try:
            data = json.loads(proj_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        sessions = data.get("sessions", {})
        proj_changed = False
        for sid, sess in sessions.items():
            if "turn_count" in sess and "active_minutes" in sess:
                continue
            transcript = sid_to_path.get(sid)
            if not transcript:
                continue
            msgs = read_transcript(str(transcript))
            if "turn_count" not in sess:
                sess["turn_count"] = count_turns(msgs)
            if "active_minutes" not in sess:
                sess["active_minutes"] = calc_active_minutes(msgs)
            proj_changed = True
            backfilled += 1
        if proj_changed:
            data["project_total_turns"] = sum(s.get("turn_count", 0) for s in sessions.values())
            data["project_active_minutes"] = sum(s.get("active_minutes", 0) for s in sessions.values())
            save_project_data(DATA_DIR, data["pid"], data)
    print(f"backfilled {backfilled} session(s)")


def main():
    if "--render" in sys.argv:
        render_mode(sys.argv[sys.argv.index("--render") + 1:])
        return
    if "--session-start" in sys.argv:
        session_start_mode()
        return
    if "--pre-turn" in sys.argv:
        pre_turn_mode()
        return
    if "--backfill" in sys.argv:
        backfill_mode()
        return

    hook = _read_hook_input()
    if hook is None:
        return

    session_id = hook.get("session_id", "unknown")
    transcript_path = hook.get("transcript_path", "")
    cwd = hook.get("cwd", os.getcwd())

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_project_config(cwd)
    price_override = cfg.get("pricing", None)

    if cfg.get("enabled") is False:
        return

    messages = read_transcript(transcript_path) if transcript_path else []
    usages = get_all_assistant_usages(messages)

    if not usages:
        return

    # Current turn: all API calls since the last human message (not tool results)
    turn_usages = get_last_turn_usages(messages) or usages[-1:]
    last_usage, last_cost, last_model = sum_usages(turn_usages, price_override)

    # Full session: all deduplicated assistant messages in transcript
    session_tokens, session_cost, model = sum_usages(usages, price_override)

    # Context window usage (last API call = most recent context snapshot).
    # window_size comes from the stored status written by render_mode with live Claude Code data.
    # The dict is a last-resort fallback only — runtime data is authoritative.
    current_model = last_model or model
    pid = project_id(cwd)
    stored_status = {}
    try:
        stored_status = json.loads((STATUS_DIR / f"{pid}.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    ctx_win_size = (stored_status.get("context", {}).get("window_size")
                    or get_context_window(current_model))
    last_api_usage = turn_usages[-1][0] if turn_usages else {}
    context_tokens = (
        last_api_usage.get("input_tokens", 0)
        + last_api_usage.get("cache_read_input_tokens", 0)
        + last_api_usage.get("cache_creation_input_tokens", 0)
    )
    context_pct = round(context_tokens / ctx_win_size * 100) if context_tokens else 0

    turn_count = count_turns(messages)
    project = load_project_data(DATA_DIR, pid)
    if "created" not in project:
        project["created"] = datetime.now(timezone.utc).isoformat()
    project["name"] = Path(cwd).name
    project["cwd"] = cwd
    project["last_model"] = current_model
    project["last_updated"] = datetime.now(timezone.utc).isoformat()

    session_start = get_session_start(messages)
    prev_session = project.get("sessions", {}).get(session_id, {})
    prev_started = prev_session.get("started", "")
    started = prev_started or session_start or datetime.now(timezone.utc).isoformat()
    active_minutes = calc_active_minutes(messages)
    project.setdefault("sessions", {})[session_id] = {
        "cost": session_cost,
        "tokens": session_tokens,
        "model": model,
        "started": started,
        "active_minutes": active_minutes,
        "turn_count": turn_count,
        "updated": datetime.now(timezone.utc).isoformat(),
    }

    all_sessions = list(project["sessions"].values())
    project["project_total_cost"] = sum(s["cost"] for s in all_sessions)
    project["project_total_tokens"] = {
        k: sum(s["tokens"].get(k, 0) for s in all_sessions)
        for k in ("input_tokens", "output_tokens",
                  "cache_creation_input_tokens", "cache_read_input_tokens")
    }
    project["session_count"] = len(all_sessions)
    project["active_days"] = len({
        (s.get("started") or s.get("updated", ""))[:10]
        for s in all_sessions
        if (s.get("started") or s.get("updated", ""))
    })
    project["project_active_minutes"] = sum(s.get("active_minutes", 0) for s in all_sessions)
    project["project_total_turns"] = sum(s.get("turn_count", 0) for s in all_sessions)

    save_project_data(DATA_DIR, pid, project)

    status = {
        "project_name": Path(cwd).name,
        "cwd": cwd,
        "pid": pid,
        "session_id": session_id,
        "model": current_model,
        "created": project.get("created", ""),
        "last_turn": {
            "cost": last_cost,
            "input_tokens": last_usage.get("input_tokens", 0),
            "cache_creation": last_usage.get("cache_creation_input_tokens", 0),
            "cache_read": last_usage.get("cache_read_input_tokens", 0),
            "output_tokens": last_usage.get("output_tokens", 0),
        },
        "session": {
            "cost": session_cost,
            "input_tokens": session_tokens.get("input_tokens", 0),
            "cache_creation": session_tokens.get("cache_creation_input_tokens", 0),
            "cache_read": session_tokens.get("cache_read_input_tokens", 0),
            "output_tokens": session_tokens.get("output_tokens", 0),
            "started": started,
            "active_minutes": active_minutes,
            "turn_count": turn_count,
        },
        "project": {
            "cost": project["project_total_cost"],
            "session_count": project["session_count"],
            "turn_count": project["project_total_turns"],
            "input_tokens": project["project_total_tokens"].get("input_tokens", 0),
            "cache_creation": project["project_total_tokens"].get("cache_creation_input_tokens", 0),
            "cache_read": project["project_total_tokens"].get("cache_read_input_tokens", 0),
            "output_tokens": project["project_total_tokens"].get("output_tokens", 0),
            "active_minutes": project["project_active_minutes"],
        },
        "context": {
            "tokens": context_tokens,
            "window_size": ctx_win_size,
            "pct": context_pct,
        },
        "updated": datetime.now(timezone.utc).isoformat(),
    }

    status_json = json.dumps(status, ensure_ascii=False)
    (STATUS_DIR / f"{pid}.json").write_text(status_json, encoding="utf-8")
    (STATUS_DIR / "current.json").write_text(status_json, encoding="utf-8")


if __name__ == "__main__":
    main()
