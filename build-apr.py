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
import math
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

    mirror, wnat = {}, defaultdict(float)
    for c in dist["rewardClaims"]:
        b = c["body"]
        if b["claimType"] == 3:                      # 3 = MIRROR (ステーキング分)
            mirror[b["beneficiary"].lower()] = int(b["amount"]) / 1e18
        elif b["claimType"] == 2:                    # 2 = WNAT (デリゲート分、手数料控除済み)
            wnat[b["beneficiary"].lower()] += int(b["amount"]) / 1e18

    weights = {}
    entity_of = {}
    for v in info["voterRegistrationInfo"]:
        i = v["voterRegistrationInfo"]
        for nid, w in zip(i["nodeIds"], i["nodeWeights"]):
            weights[nid.lower()] = int(w) / 1e18       # vote power block時点のミラー済ステーク
            # MIRRORはノード単位ではなくエンティティ(voter)単位のプールを、
            # その全ノードのステークで分け合う（実測でノード間の率が完全一致）
            entity_of[nid.lower()] = i["voter"].lower()

    # デリゲート: プロバイダーごとの WNAT 報酬を、その時点の委任総額(wNatWeight)で割る
    providers = {}
    for v in info["voterRegistrationInfo"]:
        i = v["voterRegistrationInfo"]
        deleg = i["delegationAddress"].lower()
        providers[i["voter"].lower()] = {
            "deleg": deleg,
            "w": int(i["wNatWeight"]) / 1e18,
            "fee": i["delegationFeeBIPS"] / 100,
            "paid": wnat.get(deleg, 0.0),
        }

    return nodes, mirror, weights, entity_of, providers


def network_mirror_pool(mirror, weights) -> float:
    """ネットワーク全体のMIRROR利回り。制度変更の検出に使う。"""
    total_w = sum(weights.values())
    return (sum(mirror.get(h, 0.0) for h in weights) / total_w) if total_w else 0.0


def choose_window(pools: dict, window: int, threshold: float = REGIME_BREAK):
    """直近 window エポックを基本に、その中で原資の段差があれば段差以降だけに縮める。
    戻り値: (採用するエポックのリスト, 段差を検出したエポック or None)"""
    ordered = sorted(pools)
    start = ordered[max(0, len(ordered) - window)]
    for i in range(len(ordered) - 1, 0, -1):
        prev, cur = pools[ordered[i - 1]], pools[ordered[i]]
        if prev > 0 and abs(cur - prev) / prev > threshold:
            if ordered[i] > start:
                return [e for e in ordered if e >= ordered[i]], ordered[i]
            break
    return [e for e in ordered if e >= start], None


DEFAULT_ELASTICITY = 0.85   # 推定できないときの値（2026年6〜9月の実測は0.79〜0.92）
MIN_ELASTICITY_SAMPLES = 50


def entity_pools(mirror: dict, weights: dict, entity_of: dict):
    """エンティティごとの MIRROR 原資と、ミラー済ステークの合計"""
    pool, stake = defaultdict(float), defaultdict(float)
    for h, w in weights.items():
        ent = entity_of.get(h)
        if ent and w > 0:
            pool[ent] += mirror.get(h, 0.0)
            stake[ent] += w
    return pool, stake


def mirror_elasticity(epochs: list):
    """エンティティのステークが増減したとき、MIRROR原資がどれだけ連動するか（弾力性）。
    0 = 原資は一定で、入った分だけ全員が薄まる / 1 = 原資もステークに比例し、薄まらない。
    epochs: 連続するエポックの (pool, stake) のリスト。
    ネットワーク全体の変動を除くため、各エポックで平均を引いてから回帰する。"""
    xs, ys = [], []
    for (p0, s0), (p1, s1) in zip(epochs, epochs[1:]):
        rows = [(math.log(s1[e] / s0[e]), math.log(p1[e] / p0[e]))
                for e in p1 if e in p0 and min(p0[e], p1[e], s0.get(e, 0), s1[e]) > 0]
        if not rows:
            continue
        mx = sum(r[0] for r in rows) / len(rows)
        my = sum(r[1] for r in rows) / len(rows)
        xs += [r[0] - mx for r in rows]
        ys += [r[1] - my for r in rows]
    sxx = sum(x * x for x in xs)
    if len(xs) < MIN_ELASTICITY_SAMPLES or sxx == 0:
        return DEFAULT_ELASTICITY, len(xs)
    beta = sum(x * y for x, y in zip(xs, ys)) / sxx
    return min(1.0, max(0.0, beta)), len(xs)


def fetch_provider_names(e: int) -> dict:
    """voter → データプロバイダー名（minimal-conditions.json に載っている）"""
    try:
        rows = fetch(FSP.format(e=e, f="minimal-conditions.json"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        return {}
    return {r["voterAddress"].lower(): r.get("dataProviderName") for r in rows if r.get("voterAddress")}


def aggregate_providers(window_epochs: list, elast_epochs: list, names: dict):
    """デリゲートの実質APR（手数料控除後）をプロバイダーごとに集計する。
    window_epochs / elast_epochs: 各エポックの {voter: {deleg, w, fee, paid}}"""
    acc = defaultdict(lambda: {"paid": 0.0, "w": 0.0, "ok": 0, "seen": 0})
    latest = {}
    for ep in window_epochs:
        for voter, p in ep.items():
            if p["w"] <= 0:
                continue
            a = acc[voter]
            a["paid"] += p["paid"]
            a["w"] += p["w"]
            a["seen"] += 1
            a["ok"] += 1 if p["paid"] > 0 else 0
            latest[voter] = p
    out = {}
    for voter, a in acc.items():
        p = latest[voter]
        out[voter] = {
            "name": names.get(voter),
            "delegationAddress": p["deleg"],
            "apr": round(a["paid"] / a["w"] * 100 * ANNUALIZE, 3) if a["w"] else 0.0,
            "fee": p["fee"],
            "delegatedM": round(p["w"] / 1e6, 1),
            "paidEpochs": a["ok"],
            "observedEpochs": a["seen"],
        }
    elast, _ = mirror_elasticity([
        ({v: p["paid"] for v, p in ep.items()}, {v: p["w"] for v, p in ep.items()})
        for ep in elast_epochs])
    return out, elast



def unchanged(prev: dict, payload: dict) -> bool:
    """生成時刻以外が同じか"""
    strip = lambda d: {k: v for k, v in d.items() if k != "generatedAt"}
    return strip(prev) == strip(payload)


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
    window, brk = choose_window(pools, args.window)
    if brk is not None:
        prev_e = max(e for e in pools if e < brk)
        print(f"  制度変更を検出 (epoch {brk}: MIRROR原資 {pools[brk] / pools[prev_e]:.2f}倍)"
              f" → 窓を epoch {brk} 以降に短縮", file=sys.stderr)

    # --- ノード単位で集計 ---
    acc = defaultdict(lambda: {"rew": 0.0, "amt": 0.0, "mir": 0.0, "wt": 0.0,
                               "elig": 0, "seen": 0, "name": None, "fee": None})
    for e in window:
        nodes, mirror, weights, _, _ = loaded[e]
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

    # 窓より1つ前のエポックも使えるなら、連続するペアを増やして推定を安定させる
    elast_epochs = [e for e in sorted(loaded) if e >= window[0] - 1]
    elasticity, n_elast = mirror_elasticity(
        [entity_pools(*[loaded[e][i] for i in (1, 2, 3)]) for e in elast_epochs])
    print(f"  MIRROR原資の弾力性: {elasticity:.2f} (エンティティ×エポック {n_elast}組)", file=sys.stderr)

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

    # 名前は minimal-conditions.json を優先し、無ければバリデータ側(reward-scripts)の名前で補う
    names = {entity_of.get(node_id_to_hex(nid)): a["name"]
             for nid, a in acc.items() if a["name"] and entity_of.get(node_id_to_hex(nid))}
    names.update({k: v for k, v in fetch_provider_names(window[-1]).items() if v})
    providers, wnat_elasticity = aggregate_providers(
        [loaded[e][4] for e in window], [loaded[e][4] for e in elast_epochs], names)
    print(f"  デリゲート: {len(providers)}プロバイダー / WNAT原資の弾力性 {wnat_elasticity:.2f}", file=sys.stderr)

    if len(window) < MIN_WINDOW_EPOCHS or len(out) < MIN_NODES:
        print(f"データ不足（{len(window)}エポック / {len(out)}ノード）。"
              f"apr.json は更新しません。", file=sys.stderr)
        return 1

    aprs = sorted(v["apr"] for v in out.values())
    payload = {
        "schema": 3,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "epochs": window,
        "epochDays": EPOCH_DAYS,
        "windowDays": round(len(window) * EPOCH_DAYS, 1),
        "definition": "net APR to a delegator = VRM + MIRROR, both after fees",
        "medianApr": round(statistics.median(aprs), 3) if aprs else None,
        # 自分が入ったときのMIRROR希薄化: MIRROR後 = MIRROR × ((E+A)/E)^(弾力性-1)
        "mirrorElasticity": round(elasticity, 2),
        "nodes": out,
        # デリゲート(WFLRの委任先)。キーは FSP の voter アドレス
        "wnatElasticity": round(wnat_elasticity, 2),
        "providers": providers,
    }

    # 中身が前回と同じなら書き換えない。generatedAt だけ変わって毎日コミットされるのを防ぎ、
    # generatedAt を「新しいエポックを取り込んだ日」という意味に保つ。
    try:
        with open(args.out) as f:
            prev = json.load(f)
        if unchanged(prev, payload):
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
