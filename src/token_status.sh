#!/usr/bin/env bash
# Claude Code Token Status Line — reads live session data from stdin.
#
# Routes each render to the *per-project* status file (status/<pid>.json) so
# multiple Claude Code instances running side-by-side don't fight over a single
# shared current.json. The pid comes from the session's own overlay
# (status/sessions/<session_id>.json, written by the hooks), else from
# md5(project_dir)[:12] — same scheme as token_tracker. The per-session Turn/Sess
# overlay itself is applied by token_tracker --render.
# Falls back to current.json only when the per-project file doesn't exist yet
# (very first render before SessionStart / Stop has had a chance to create it).
STATUS_DIR="$HOME/.claude/token_usage/status"

LIVE=$(cat)

PID=$(printf '%s' "$LIVE" | python3 -c '
import sys, json, hashlib, os
try:
    d = json.load(sys.stdin)
    usage_dir = os.path.expanduser("~/.claude/token_usage")
    # 1. The session overlay the hooks wrote names the project this session is
    #    recorded under — the bar must show that project, whatever the cwd.
    sid = d.get("session_id") or ""
    if sid:
        try:
            with open(os.path.join(usage_dir, "status", "sessions", sid + ".json")) as f:
                pid = json.load(f).get("pid") or ""
            if pid and os.path.isfile(os.path.join(usage_dir, "status", pid + ".json")):
                print(pid); sys.exit(0)
        except (OSError, ValueError):
            pass
    # 2. Otherwise route by the launch dir. Top-level cwd and
    #    workspace.current_dir both follow the shell (cd into a worktree or a
    #    nested project), so they are fallbacks for older payloads only.
    ws = d.get("workspace") or {}
    cwd = ws.get("project_dir") or d.get("cwd") or ws.get("current_dir") or ""
    if not cwd:
        print(""); sys.exit(0)
    proj_dir = os.path.join(usage_dir, "projects")
    def pid_for(p):
        return hashlib.md5(p.encode()).hexdigest()[:12]
    own = pid_for(cwd)
    # Mirror Python resolve_pid_for_cwd: own record wins; else walk up to first
    # tracked ancestor; else fall back to own pid.
    if os.path.isfile(os.path.join(proj_dir, own + ".json")):
        print(own); sys.exit(0)
    cur = os.path.abspath(cwd)
    while True:
        par = os.path.dirname(cur)
        if not par or par == cur:
            break
        cand = pid_for(par)
        if os.path.isfile(os.path.join(proj_dir, cand + ".json")):
            print(cand); sys.exit(0)
        cur = par
    print(own)
except Exception:
    print("")
' 2>/dev/null)

if [ -n "$PID" ] && [ -f "$STATUS_DIR/$PID.json" ]; then
    STATUS_JSON="$STATUS_DIR/$PID.json"
elif [ -f "$STATUS_DIR/current.json" ]; then
    STATUS_JSON="$STATUS_DIR/current.json"
else
    echo "💰 --"
    exit 0
fi

printf '%s' "$LIVE" | python3 ~/.claude/hooks/token_tracker.py --render "$STATUS_JSON" 2>/dev/null \
    || echo "💰 --"
