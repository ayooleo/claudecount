#!/usr/bin/env bash
# Claude Code Token Status Line — reads live session data from stdin.
#
# Routes each render to the *per-project* status file (status/<pid>.json) so
# multiple Claude Code instances running side-by-side don't fight over a single
# shared current.json. The pid is md5(cwd)[:12] — same scheme as token_tracker.
# Falls back to current.json only when the per-project file doesn't exist yet
# (very first render before SessionStart / Stop has had a chance to create it).
STATUS_DIR="$HOME/.claude/token_usage/status"

LIVE=$(cat)

PID=$(printf '%s' "$LIVE" | python3 -c '
import sys, json, hashlib, os
try:
    d = json.load(sys.stdin)
    # Prefer top-level cwd (session startup dir, fixed for the session lifetime)
    # over workspace.current_dir (which may track the shell cwd if it drifts).
    cwd = d.get("cwd") or (d.get("workspace") or {}).get("current_dir") or ""
    if not cwd:
        print(""); sys.exit(0)
    proj_dir = os.path.expanduser("~/.claude/token_usage/projects")
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
