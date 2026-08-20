#!/usr/bin/env bash
#
# Install (or reinstall) the PR Watcher as a macOS launchd agent so the UI
# starts at login and restarts if it dies. Idempotent — safe to re-run.
#
# Usage:
#   ./install-launchd.sh              # label "pr-watcher"
#   PRW_LABEL=my-watcher ./install-launchd.sh
#
# Uninstall:
#   launchctl bootout gui/$(id -u)/pr-watcher
#   rm ~/Library/LaunchAgents/pr-watcher.plist
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="${PRW_LABEL:-pr-watcher}"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
RUN_SH="$REPO_DIR/run.sh"
LOG="$HOME/.pr-watcher/launchd.log"

# Homebrew bin first so gh / claude / python resolve the same as in a login
# shell. Adjust if your tools live elsewhere.
LAUNCHD_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.pr-watcher"

# The reviewer/merger spawn `claude` headless. launchd does NOT inherit your
# shell env, so any variable Claude needs to authenticate must be baked into
# the plist. Capture them from the shell running this installer:
#   - CLAUDE_CONFIG_DIR: which config/keychain profile Claude uses. If you run
#     Claude Code with a non-default config dir, its OAuth token lives in a
#     per-dir keychain item — without this, the agent falls back to the
#     (likely unauthenticated) default and every review fails with
#     "OAuth session expired".
#   - ANTHROPIC_API_KEY: if you authenticate with an API key instead of OAuth.
ENV_ENTRIES=""
add_env() {  # key value
  [ -n "$2" ] || return 0
  ENV_ENTRIES+="        <key>$1</key>
        <string>$2</string>
"
}
add_env "CLAUDE_CONFIG_DIR" "${CLAUDE_CONFIG_DIR:-}"
add_env "ANTHROPIC_API_KEY" "${ANTHROPIC_API_KEY:-}"

# Write the plist. Do __ENV_ENTRIES__ via a temp file so newlines/keys survive.
python3 - "$REPO_DIR/pr-watcher.plist.template" "$PLIST" \
  "$LABEL" "$RUN_SH" "$LAUNCHD_PATH" "$LOG" "$ENV_ENTRIES" <<'PY'
import sys
tpl_path, out_path, label, run_sh, pathv, log, env_entries = sys.argv[1:8]
s = open(tpl_path).read()
s = (s.replace("__LABEL__", label)
      .replace("__RUN_SH__", run_sh)
      .replace("__PATH__", pathv)
      .replace("__LOG__", log)
      .replace("__ENV_ENTRIES__", env_entries.rstrip("\n")))
open(out_path, "w").write(s)
PY

# Reload cleanly if already loaded.
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/${LABEL}"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "Installed launchd agent '${LABEL}'."
echo "  plist: $PLIST"
echo "  log:   $LOG"
echo "  UI:    http://127.0.0.1:${PRW_PORT:-4747}"
echo
echo "Status:   launchctl print gui/$(id -u)/${LABEL} | grep state"
echo "Stop:     launchctl bootout gui/$(id -u)/${LABEL}"
