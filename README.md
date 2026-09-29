# Stake Chef / Delegate Bear

Flareの委任先を、公式の支払実績から比べて選ぶためのツール。タブで切り替えます。

| | 対象 | 入力 |
|---|---|---|
| 🥩 **Stake Chef** | FLRのステーキング（P-Chainのバリデータ） | 数量・終了日 |
| 🐻 **Delegate Bear** | WFLRのデリゲート（FTSOデータプロバイダー） | 数量 |

👉 **https://takonorik.github.io/flr-stake-chef/**（ステーキング） / [#delegate](https://takonorik.github.io/flr-stake-chef/#delegate)（デリゲート）

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
  MIRRORはエンティティ（FSPのvoter）単位の原資を、そのエンティティの全ノードの
  ステークで分け合います（実測でノード間の率が完全一致）。ただしステークが増えると
  原資もかなり連動して増えるため（弾力性 約0.8、毎回データから推定）、薄まるのは一部だけです。
  `MIRROR後 = MIRROR × ((E+A)/E)^(弾力性−1)`（E: エンティティ合計ステーク、A: 自分の数量）
- 稼働率だけでなく **直近エポックで実際に報酬が出たか** で絞り込みます。
  稼働率100%でも報酬ゼロのバリデータが実在します
- 運営者名が公式リストに無いノードは既定で非表示。チェックを入れたときだけ表示します

### Delegate Bear（デリゲート）の実質APR

```
実質APR = プロバイダーのWNAT報酬請求額 ÷ 委任総額(wNatWeight) × 365.25 / 3.5
```

WNAT報酬の請求額はFSPの手数料（delegationFeeBIPS）控除後。集計窓・報酬実績の条件・
名称未登録の扱いはステーキングと同じ。Granite以降はインフレ報酬の配分がステーキング側に
移っており、デリゲートのAPR中央値は約4.9% → 約1.3%に下がっている。

### 検証結果（バックテスト）

過去の各時点でこのツールが表示していた値を公式データから再現し、
その後14日間に実際のステーカーが受け取った利回りと比較しました（名前あり・報酬実績3/4以上のノード）。

| | Granite前（5回） | Granite後（12回、2026年7〜9月） |
|---|---|---|
| 表示値と実績の誤差（中央値） | 0.22pt | 0.79pt |
| 偏り（表示 − 実績） | +0.19pt | +0.81pt |
| 順位相関 | 0.69 | 0.83 |
| **表示上位10件の実績** | **7.17%** | **11.07%** |
| VRMだけで選んだ上位10件 | 6.77% | 9.36% |
| 候補全体の中央値 | 6.48% | 9.34% |
| 後から見た本当の上位10件（理論上限） | 7.45% | 11.83% |

- MIRRORを含めて選ぶことで、VRMだけで選ぶより約1.7pt高い実績（Granite後）
- Granite後の偏りは、参加者の増加でネットワーク全体の利回りが下がり続けたため
  （MIRROR APR 8.32% → 5.44%）。過去実績ベースの値は、下落局面では高めに出る
- 表示上位10件のうち、その後に報酬を取りこぼしたのは延べ120件中5件

### データの鮮度

`apr.json` は新しいエポックを取り込んだときだけ更新されます（`generatedAt` = 取り込み日）。
画面に取り込み日を表示し、8日を超えて古い場合は警告を出します。

## 公開と限定公開（アクセスコード）

GitHub Actions（`.github/workflows/update-apr.yml`）が毎日データを更新し、GitHub Pages に公開する。
main へのpushや、Actions タブの「Run workflow」でも公開し直す。

限定公開は **GitHub の設定だけで切り替えられる**（コードの変更は不要）。
`Settings → Secrets and variables → Actions` で:

| 設定 | 種類 | 値 |
|---|---|---|
| `ACCESS_CODE` | Secrets | アクセスコード（全角・大文字・前後の空白は区別しない） |
| `ACCESS_GATE` | Variables | `on`（コードが必要） / `off`（誰でも閲覧可） |

変更後すぐ反映したいときは Actions → Update data and deploy → Run workflow（押さなくても翌日の自動更新で反映）。
一度コードを入れた端末は記憶される。コードを変えると全員に再入力を求める。

> 簡易的な鍵であり、ソースや `apr.json` を直接読めば回り込める（表示データは元々すべて公開情報）。
> 鍵がオンの間は検索エンジンに載らないよう `noindex` を付けている。

## apr.json の更新

```bash
python3 build-apr.py -o apr.json
```

標準ライブラリのみ・認証不要。[GitHub Actions](.github/workflows/update-apr.yml) が
1日1回自動実行し、変更があるときだけコミットします。

## ローカルで動かす

`apr.json` を fetch するため `file://` では動きません。付属の `serve.py` で配信します。
`serve.py` は `apr.json` を配信する前に（30分に1回まで）`git pull` するので、
GitHub Actions が更新した最新データが自動で入ります。

```bash
/usr/bin/python3 serve.py
```

→ http://localhost:8765/

### ログイン時に自動起動する（任意）

```bash
scripts/autostart.sh install     # 導入
scripts/autostart.sh status      # 状況
scripts/autostart.sh uninstall   # 削除
```

`~/Library/Application Support/FlareStakeChef` に専用のクローンを作り、launchd で常駐させます。

→ http://localhost:8765/

## 免責

> **運営メモ**: 画面の免責事項に「提供者・関係者はバリデータやFTSOを運営しておらず、掲載先から対価を受け取っていない」と明記している。
> どちらかが変わった場合（バリデータ運営を始める、掲載料・紹介料・アフィリエイトなどを受け取る）は、必ずこの文言を更新して開示すること。
> 2026年7月成立の改正金商法により、暗号資産の投資助言は施行後（公布から1年以内）に投資助言業の登録対象になる。収益化や個別相談を行う場合は事前に専門家に確認する。


情報提供のみを目的としたツールであり、投資助言ではありません。表示データの正確性は
保証されません。最終的な判断はご自身の責任でお願いします。

Provided by [Flare Japan Community](https://lit.link/flarejapancommunity)
