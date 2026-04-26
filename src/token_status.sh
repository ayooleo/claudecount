#!/usr/bin/env bash
# Claude Code Token Status Line — reads live context data from stdin
STATUS_JSON="$HOME/.claude/token_usage/status/current.json"
[[ -f "$STATUS_JSON" ]] && \
    python3 ~/.claude/hooks/token_tracker.py --render "$STATUS_JSON" 2>/dev/null || \
    echo "💰 --"
