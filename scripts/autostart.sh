#!/bin/bash
# Flare Stake Chef をMacのログイン時に自動起動する（launchd のユーザーエージェント）。
#
#   scripts/autostart.sh install     … 導入（以後 http://localhost:8765/ が常に開ける）
#   scripts/autostart.sh uninstall   … 完全に削除
#   scripts/autostart.sh status      … 動作状況とログ
#
# 配信するのはデスクトップの作業用フォルダではなく、専用のクローン（下記 APP_DIR）。
# デスクトップはmacOSの保護対象で、バックグラウンドのプロセスからは読めないことがあるため。
# クローンは serve.py が30分ごとに git pull するので、Actionsの最新データも、
# GitHubにpushしたコードの変更も自動で反映される。
set -euo pipefail

LABEL="io.github.takonorik.flare-stake-chef"
REPO="https://github.com/takonorik/flare-stake-chef.git"
APP_DIR="$HOME/Library/Application Support/FlareStakeChef"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/FlareStakeChef.log"
DOMAIN="gui/$(id -u)"

install() {
  if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch -q origin
    git -C "$APP_DIR" reset -q --hard origin/main
  else
    mkdir -p "$(dirname "$APP_DIR")"
    git clone -q "$REPO" "$APP_DIR"
  fi

  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$APP_DIR/serve.py</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>STAKECHEF_MIRROR</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "導入しました → http://localhost:8765/"
}

uninstall() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  rm -rf "$APP_DIR"
  echo "削除しました（ログは $LOG に残っています）"
}

status() {
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl print "$DOMAIN/$LABEL" | grep -E "^\s*(state|pid|last exit code)" || true
  else
    echo "未導入"
  fi
  if [ -f "$LOG" ]; then echo "--- log"; tail -n 5 "$LOG"; fi
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  *) echo "usage: $0 install|uninstall|status" >&2; exit 2 ;;
esac
