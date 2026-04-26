#!/usr/bin/env bash
set -euo pipefail

echo "Uninstalling ClaudeCount..."

for f in token_tracker.py token_status.sh token_report.py; do
    rm -f "$HOME/.claude/hooks/$f"
    echo "  ✓ removed ~/.claude/hooks/$f"
done

python3 << 'PYEOF'
import json
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
if not settings_path.exists():
    exit(0)

try:
    settings = json.loads(settings_path.read_text())
except Exception:
    exit(0)

tracker_cmd = "python3 ~/.claude/hooks/token_tracker.py"
stop_hooks = settings.get("hooks", {}).get("Stop", [])
settings["hooks"]["Stop"] = [
    entry for entry in stop_hooks
    if not any(h.get("command") == tracker_cmd for h in entry.get("hooks", []))
]

if settings.get("statusLine", {}).get("command") == "bash ~/.claude/hooks/token_status.sh":
    del settings["statusLine"]

settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
print("  ✓ ~/.claude/settings.json cleaned up")
PYEOF

echo ""
echo "✓ ClaudeCount uninstalled."
echo "  Usage data preserved at ~/.claude/token_usage/"
echo "  To delete data as well: rm -rf ~/.claude/token_usage"
