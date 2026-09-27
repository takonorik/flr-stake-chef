# Flare Stake Chef

ステーキングしたい **数量** と **終了日** を先に指定して、その条件で実際に受け取れる
FLRバリデータを探すツール。

> ⚠️ 開発中。まだ一般公開していません。動かし方は「ローカルで動かす」を参照。

## 表示している「実質APR」の定義

Flareのステーキング報酬は2つの財布から出ます。両方を足して、手数料を引いた
**委任者の手取り**を出しています。

```
実質APR = VRM + MIRROR
  VRM    = P-Chainの検証報酬        … Σ delegatorRewardAmount ÷ Σ delegated
                                      (P-Chain委任手数料 控除済み)
  MIRROR = FTSOインフレの分け前     … ノードのMIRROR請求額 ÷ nodeWeights
                                      (FSP手数料 控除済み)
  年率化 = × 365.25 / 3.5
```

推定式ではなく、Flare財団が公開している **実際の支払記録** から算出しています。

| ソース | 用途 |
|---|---|
| [flare-foundation/reward-scripts](https://github.com/flare-foundation/reward-scripts) | VRMの実支払額（node×delegator単位）、報酬適格性 |
| [flare-foundation/fsp-rewards](https://github.com/flare-foundation/fsp-rewards) | MIRROR請求額、nodeWeights |
| Flare P-Chain RPC | 空き容量・残存期間・NodeID（リアルタイム） |

### 集計期間

既定は直近4リワードエポック（= 14日 = Flare公式の請求サイクル1回分。公式設定の
`NUM_EPOCHS: 4`、公開ファイルも `epochs-414-417.json` と4本ずつ束ねられている）。

ただしネットワーク全体のMIRROR原資に段差を検出した場合は、段差以降のエポックだけに
自動で窓を縮めます。2026-07-14のGraniteハードフォークで原資が約4.4倍になっており、
それ以前と平均すると現在の水準を大きく過小評価するためです。

### 他サイトとの違い

- 多くのエクスプローラは VRM だけ、または **手数料を引く前** の数字を出しています
- このツールは数量を先に聞くので、**自分が入った後**のAPRと見込み報酬額（FLR）を出せます。
  MIRRORはエンティティ（FSPのvoter）単位のプールを、そのエンティティの全ノードの
  ステークで分け合います（実測でノード間の率が完全一致）。自分が入るとその分薄まるので、
  エンティティ合計ステークを分母にして希薄化を先に差し引いています
- 稼働率だけでなく **直近エポックで実際に報酬が出たか** で絞り込みます。
  稼働率100%でも報酬ゼロのバリデータが実在します
- 運営者名が公式リストに無いノードは既定で非表示。チェックを入れたときだけ表示します

### データの鮮度

`apr.json` は新しいエポックを取り込んだときだけ更新されます（`generatedAt` = 取り込み日）。
画面に取り込み日を表示し、8日を超えて古い場合は警告を出します。

## apr.json の更新

```bash
python3 build-apr.py -o apr.json
```

標準ライブラリのみ・認証不要。[GitHub Actions](.github/workflows/update-apr.yml) が
1日1回自動実行し、変更があるときだけコミットします。

## ローカルで動かす

`apr.json` を fetch するため `file://` では動きません。最新の `apr.json` は
GitHub Actions がリポジトリに反映するので、手元では先に `git pull` してください。

```bash
git pull && python3 -m http.server 8765
```

→ http://localhost:8765/

## 免責

情報提供のみを目的としたツールであり、投資助言ではありません。表示データの正確性は
保証されません。最終的な判断はご自身の責任でお願いします。

Provided by [Flare Japan Community](https://lit.link/flarejapancommunity)
