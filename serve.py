#!/usr/bin/env python3
"""Flare Stake Chef のローカル用サーバー。

`python3 -m http.server` の代わりに使う。apr.json を配信する前に、前回から
30分以上たっていれば `git pull` して、GitHub Actions が更新した最新データを取り込む。
手元で git pull を忘れて古いデータを見てしまうのを防ぐため。

使い方:  /usr/bin/python3 serve.py      → http://localhost:8765/
"""

import functools
import http.server
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8765"))
PULL_INTERVAL_SEC = 30 * 60
# 常駐用の専用クローン（scripts/autostart.sh が設定）は手元で編集しないので、
# GitHubと完全に一致させる。履歴が書き換わっても止まらない。作業用フォルダでは使わないこと。
MIRROR_MODE = os.environ.get("STAKECHEF_MIRROR") == "1"

_last_pull = 0.0
_lock = threading.Lock()


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, file=sys.stderr, flush=True)


def git(*args: str) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", ROOT, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    # Apple Silicon で Rosetta(x86) の Python から呼ぶと、git の xcrun が読み込めずに失敗する
    if r.returncode != 0 and "xcrun" in r.stderr and os.path.exists("/usr/bin/arch"):
        r = subprocess.run(["/usr/bin/arch", "-arm64", *cmd],
                           capture_output=True, text=True, timeout=60)
    return r


def maybe_pull() -> None:
    global _last_pull
    with _lock:
        if time.time() - _last_pull < PULL_INTERVAL_SEC:
            return
        _last_pull = time.time()
        try:
            if MIRROR_MODE:
                r = git("fetch", "-q", "origin")
                if r.returncode == 0:
                    r = git("reset", "-q", "--hard", "origin/main")
            else:
                r = git("pull", "--ff-only", "-q")
        except (OSError, subprocess.TimeoutExpired) as ex:
            log(f"git pull 失敗: {ex}（手元のデータのまま配信します）")
            return
        if r.returncode == 0:
            head = git("log", "-1", "--format=%h %s").stdout.strip()
            log(f"更新OK → {head}")
        else:
            log(f"更新失敗: {r.stderr.strip()[:300]}（手元のデータのまま配信します）")


# サイトに必要なファイルだけを配信する（.git や設定ファイルを出さない）
ALLOWED = {"/", "/index.html", "/apr.json", "/logo.svg", "/delegate-bear.svg"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].split("#")[0]
        if path not in ALLOWED:
            self.send_error(404)
            return
        if path == "/apr.json":
            maybe_pull()
        super().do_GET()

    def do_HEAD(self):
        if self.path.split("?")[0].split("#")[0] not in ALLOWED:
            self.send_error(404)
            return
        super().do_HEAD()

    def end_headers(self):
        # 更新したHTML/JSONがブラウザのキャッシュで古いまま表示されないように
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def main() -> int:
    maybe_pull()
    handler = functools.partial(Handler, directory=ROOT)
    # LANに公開しないよう localhost のみで待ち受ける
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as httpd:
        log(f"serving {ROOT} on http://localhost:{PORT}/")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
