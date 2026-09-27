#!/usr/bin/env python3
"""
Flare Stake Chef — 実質APR事前計算スクリプト

Flare財団が公開している「実際にいくら支払ったか」の記録から、
委任者の手取りAPR（手数料控除後）をノード単位で算出し apr.json に書き出す。

  実質APR = VRM + MIRROR
    VRM    = Σ delegatorRewardAmount ÷ Σ delegated    (P-Chain委任手数料 控除済み)
    MIRROR = ノードのMIRROR請求額 ÷ nodeWeights        (FSP手数料 控除済み)
    年率化 = × 365.25 / 3.5

窓は既定4エポック（=14日、Flare公式の請求サイクル 1単位）。
ただしネットワーク全体のMIRROR原資に段差がある場合は、
段差以降のエポックだけに自動で縮める（Graniteのような制度変更対策）。

使い方:  python3 build-apr.py [-o apr.json] [-w 4]
依存:    標準ライブラリのみ。認証不要。
"""

import argparse
import datetime
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

FSP = "https://raw.githubusercontent.com/flare-foundation/fsp-rewards/main/flare/{e}/{f}"
RS = ("https://raw.githubusercontent.com/flare-foundation/reward-scripts/main"
      "/generated-files/reward-epoch-{e}/nodes-data.json")

EPOCH_DAYS = 3.5
ANNUALIZE = 365.25 / EPOCH_DAYS
DEFAULT_WINDOW = 4          # = 14日 = 公式の請求サイクル1回分
REGIME_BREAK = 0.35         # ネットワークMIRROR原資がこれ以上動いたら制度変更とみなす
MIN_WINDOW_EPOCHS = 2       # これ未満しか取れなければ書き出さずに失敗させる
MIN_NODES = 100             # 同上（現状 約175ノード）

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def node_id_to_hex(node_id: str) -> str:
    """NodeID-xxx (base58check) → MIRROR請求の受益者である20バイトID"""
    s = node_id.replace("NodeID-", "")
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    raw = b"\0" * (len(s) - len(s.lstrip("1"))) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    body, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(body).digest()[-4:] != checksum:
        raise ValueError(f"bad NodeID checksum: {node_id}")
    return "0x" + body.hex()


def fetch(url: str, retries: int = 3):
    headers = {}
    # 未認証のGitHub APIは60回/時で、Actionsのランナーは他ユーザーとIPを共有するため枯渇しやすい
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as ex:
            if ex.code == 404 or attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(5 * (attempt + 1))


def latest_complete_epoch() -> int:
    """reward-distribution-data.json と nodes-data.json が揃っている最新エポックを探す"""
    info = fetch("https://api.github.com/repos/flare-foundation/fsp-rewards/contents/flare")
    epochs = sorted(int(x["name"]) for x in info if x["name"].isdigit())
    for e in reversed(epochs):
        try:
            urllib.request.urlopen(
                urllib.request.Request(RS.format(e=e), method="HEAD"), timeout=30)
            return e
        except urllib.error.HTTPError:
            continue
    raise RuntimeError("完成済みエポックが見つかりません")


def load_epoch(e: int):
    nodes = fetch(RS.format(e=e))
    dist = fetch(FSP.format(e=e, f="reward-distribution-data.json"))
    info = fetch(FSP.format(e=e, f="reward-epoch-info.json"))

    mirror = {}
    for c in dist["rewardClaims"]:
        b = c["body"]
        if b["claimType"] == 3:                      # 3 = MIRROR (ステーキング分)
            mirror[b["beneficiary"].lower()] = int(b["amount"]) / 1e18

    weights = {}
    entity_of = {}
    for v in info["voterRegistrationInfo"]:
        i = v["voterRegistrationInfo"]
        for nid, w in zip(i["nodeIds"], i["nodeWeights"]):
            weights[nid.lower()] = int(w) / 1e18       # vote power block時点のミラー済ステーク
            # MIRRORはノード単位ではなくエンティティ(voter)単位のプールを、
            # その全ノードのステークで分け合う（実測でノード間の率が完全一致）
            entity_of[nid.lower()] = i["voter"].lower()

    return nodes, mirror, weights, entity_of


def network_mirror_pool(mirror, weights) -> float:
    """ネットワーク全体のMIRROR利回り。制度変更の検出に使う。"""
    total_w = sum(weights.values())
    return (sum(mirror.get(h, 0.0) for h in weights) / total_w) if total_w else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="apr.json")
    ap.add_argument("-w", "--window", type=int, default=DEFAULT_WINDOW)
    ap.add_argument("-e", "--end-epoch", type=int, default=None)
    args = ap.parse_args()

    end = args.end_epoch or latest_complete_epoch()
    # 制度変更の検出用に窓より1つ多く取る
    candidates = list(range(end - args.window, end + 1))
    print(f"最新の完成エポック: {end}  取得: {candidates[0]}〜{candidates[-1]}", file=sys.stderr)

    loaded = {}
    for e in candidates:
        try:
            loaded[e] = load_epoch(e)
            print(f"  epoch {e} 取得", file=sys.stderr)
        except urllib.error.HTTPError as ex:
            print(f"  epoch {e} スキップ (HTTP {ex.code})", file=sys.stderr)
        except urllib.error.URLError as ex:
            print(f"  epoch {e} スキップ ({ex.reason})", file=sys.stderr)
    if not loaded:
        print("エポックを1つも取得できませんでした。apr.json は更新しません。", file=sys.stderr)
        return 1

    # --- 制度変更の検出: ネットワークMIRROR原資が段差を作った直近の位置を探す ---
    pools = {e: network_mirror_pool(loaded[e][1], loaded[e][2]) for e in loaded}
    ordered = sorted(loaded)
    start = ordered[max(0, len(ordered) - args.window)]
    for i in range(len(ordered) - 1, 0, -1):
        prev, cur = pools[ordered[i - 1]], pools[ordered[i]]
        if prev > 0 and abs(cur - prev) / prev > REGIME_BREAK:
            if ordered[i] > start:
                start = ordered[i]
                print(f"  制度変更を検出 (epoch {ordered[i]}: MIRROR原資 "
                      f"{cur / prev:.2f}倍) → 窓を epoch {start} 以降に短縮", file=sys.stderr)
            break
    window = [e for e in ordered if e >= start]

    # --- ノード単位で集計 ---
    acc = defaultdict(lambda: {"rew": 0.0, "amt": 0.0, "mir": 0.0, "wt": 0.0,
                               "elig": 0, "seen": 0, "name": None, "fee": None})
    for e in window:
        nodes, mirror, weights, _ = loaded[e]
        for n in nodes:
            a = acc[n["nodeId"]]
            a["rew"] += sum(int(d.get("delegatorRewardAmount", 0) or 0)
                            for d in n["delegators"]) / 1e18
            a["amt"] += sum(int(d.get("amount", 0) or 0) for d in n["delegators"]) / 1e9
            a["seen"] += 1
            a["elig"] += 1 if n.get("eligible") else 0
            a["name"] = n.get("ftsoName") or a["name"]
            a["fee"] = n["fee"] / 1e4
            h = node_id_to_hex(n["nodeId"])
            if h in weights:
                a["mir"] += mirror.get(h, 0.0)
                a["wt"] += weights[h]

    entity_of = loaded[window[-1]][3]

    out = {}
    for node_id, a in acc.items():
        vrm = a["rew"] / a["amt"] * 100 * ANNUALIZE if a["amt"] > 0 else None
        mir = a["mir"] / a["wt"] * 100 * ANNUALIZE if a["wt"] > 0 else None
        if vrm is None and mir is None:
            continue
        out[node_id] = {
            "name": a["name"],
            "entity": entity_of.get(node_id_to_hex(node_id)),
            "vrm": round(vrm or 0.0, 3),
            "mirror": round(mir or 0.0, 3),
            "apr": round((vrm or 0.0) + (mir or 0.0), 3),
            "fee": a["fee"],
            "eligibleEpochs": a["elig"],
            "observedEpochs": a["seen"],
        }

    if len(window) < MIN_WINDOW_EPOCHS or len(out) < MIN_NODES:
        print(f"データ不足（{len(window)}エポック / {len(out)}ノード）。"
              f"apr.json は更新しません。", file=sys.stderr)
        return 1

    aprs = sorted(v["apr"] for v in out.values())
    payload = {
        "schema": 2,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "epochs": window,
        "epochDays": EPOCH_DAYS,
        "windowDays": round(len(window) * EPOCH_DAYS, 1),
        "definition": "net APR to a delegator = VRM + MIRROR, both after fees",
        "medianApr": round(statistics.median(aprs), 3) if aprs else None,
        "nodes": out,
    }

    # 中身が前回と同じなら書き換えない。generatedAt だけ変わって毎日コミットされるのを防ぎ、
    # generatedAt を「新しいエポックを取り込んだ日」という意味に保つ。
    try:
        with open(args.out) as f:
            prev = json.load(f)
        if {k: v for k, v in prev.items() if k != "generatedAt"} == \
           {k: v for k, v in payload.items() if k != "generatedAt"}:
            print(f"\n変更なし（epoch {window[0]}〜{window[-1]} のまま）。"
                  f"{args.out} は書き換えません。", file=sys.stderr)
            return 0
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    with open(args.out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n{args.out} を書き出しました: {len(out)}ノード / "
          f"{os.path.getsize(args.out) / 1024:.1f} KB", file=sys.stderr)
    print(f"窓: epoch {window[0]}〜{window[-1]} ({payload['windowDays']}日) / "
          f"実質APR中央値 {payload['medianApr']}%", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
