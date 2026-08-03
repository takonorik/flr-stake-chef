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
import hashlib
import json
import statistics
import sys
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


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


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
    for v in info["voterRegistrationInfo"]:
        i = v["voterRegistrationInfo"]
        for nid, w in zip(i["nodeIds"], i["nodeWeights"]):
            weights[nid.lower()] = int(w) / 1e18       # vote power block時点のミラー済ステーク

    return nodes, mirror, weights


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
        nodes, mirror, weights = loaded[e]
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

    out = {}
    for node_id, a in acc.items():
        vrm = a["rew"] / a["amt"] * 100 * ANNUALIZE if a["amt"] > 0 else None
        mir = a["mir"] / a["wt"] * 100 * ANNUALIZE if a["wt"] > 0 else None
        if vrm is None and mir is None:
            continue
        out[node_id] = {
            "name": a["name"],
            "vrm": round(vrm or 0.0, 3),
            "mirror": round(mir or 0.0, 3),
            "apr": round((vrm or 0.0) + (mir or 0.0), 3),
            "fee": a["fee"],
            "eligibleEpochs": a["elig"],
            "observedEpochs": a["seen"],
        }

    aprs = sorted(v["apr"] for v in out.values())
    payload = {
        "schema": 1,
        "generatedAt": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "epochs": window,
        "epochDays": EPOCH_DAYS,
        "windowDays": round(len(window) * EPOCH_DAYS, 1),
        "definition": "net APR to a delegator = VRM + MIRROR, both after fees",
        "medianApr": round(statistics.median(aprs), 3) if aprs else None,
        "nodes": out,
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    import os
    print(f"\n{args.out} を書き出しました: {len(out)}ノード / "
          f"{os.path.getsize(args.out) / 1024:.1f} KB", file=sys.stderr)
    print(f"窓: epoch {window[0]}〜{window[-1]} ({payload['windowDays']}日) / "
          f"実質APR中央値 {payload['medianApr']}%", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
