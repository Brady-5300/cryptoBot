#!/usr/bin/env bash
# Manage the recorder as a macOS launchd LaunchAgent (starts at login, restarts on crash).
#
#   scripts/macos/recorder_agent.sh install    # write the plist and start it
#   scripts/macos/recorder_agent.sh status     # is it running? last exit code
#   scripts/macos/recorder_agent.sh restart    # e.g. after updating the code or secrets
#   scripts/macos/recorder_agent.sh uninstall  # stop it and remove the plist
#
# Sleep is handled system-wide with `sudo pmset -a disablesleep 1` (see the setup doc), not by
# wrapping the job in caffeinate: launchd's SIGTERM must reach Python directly so the recorder
# can flush and close its files on stop.
set -euo pipefail

LABEL="com.cryptobot.recorder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${CRYPTOBOT_PYTHON:-$HOME/.venvs/cryptobot/bin/python}"
DATA_DIR="${CRYPTOBOT_DATA_DIR:-$HOME/cryptobot-data}"
DOMAIN="gui/$(id -u)"

check_paths() {
  # macOS privacy protection (TCC) blocks background jobs from these folders.
  for protected in Desktop Documents Downloads "Library/Mobile Documents"; do
    for p in "$REPO" "$DATA_DIR"; do
      if [[ "$p" == "$HOME/$protected"* ]]; then
        echo "ERROR: $p is under ~/$protected, which launchd jobs can't read." >&2
        echo "Move the repo to ~/cryptobot and keep data in ~/cryptobot-data." >&2
        exit 1
      fi
    done
  done
  [[ -x "$PY" ]] || { echo "ERROR: no Python venv at $PY (see setup step 2)" >&2; exit 1; }
}

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" "$DATA_DIR/logs"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string><string>-m</string><string>basis.data.recorder</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CRYPTOBOT_DATA_DIR</key><string>$DATA_DIR</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Standard</string>
  <key>StandardOutPath</key><string>$DATA_DIR/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DATA_DIR/logs/launchd.err.log</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null
}

case "${1:-}" in
  install)
    check_paths
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    write_plist
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed and started $LABEL. Data: $DATA_DIR"
    ;;
  restart)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "Restarted $LABEL"
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" | grep -E "^\s*(state|pid|last exit code|runs) =" || {
      echo "$LABEL is not loaded"; exit 1; }
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL (recorded data left in $DATA_DIR)"
    ;;
  *)
    echo "usage: $0 install|status|restart|uninstall" >&2
    exit 2
    ;;
esac
