# ログエラー検知ハンズオン手順書 (Databricks Free Edition)

Oracle アラートログ × Java アプリログから **エラーを検知する** ミニ基盤を、Medallion アーキテクチャ（Bronze → Silver → Gold）で構築するハンズオンです。所要時間 30〜45分。

---

## 0. このハンズオンで作るもの

仕込んであるストーリーはこうです。**2026-06-20 10:02 頃、Oracle でデッドロック（ORA-00060）→ 接続枯渇（ORA-12516 / ORA-12519）が発生し、ほぼ同時刻に Java アプリ側で `SQLException` とコネクションプールのタイムアウトが急増**します。最終的に Gold 層で時間窓集計し、この「DB 障害がアプリ障害の根本原因」という相関を1枚のビューで切り分けられる状態を目指します。

| 層 | 役割 | やること |
|----|------|---------|
| Bronze | 生ログをそのまま保管 | ログを 1行=1レコードで Delta テーブル化 |
| Silver | 構造化・クレンジング | 正規表現で時刻 / レベル / `ORA-xxxxx` を抽出、スタックトレース結合 |
| Gold | 集計・検知 | 10分窓ごとのエラー件数、しきい値アラート、DB×アプリ相関 |

---

## 1. 配布ファイル

| ファイル | 中身 | 使い方 |
|---------|------|--------|
| `alert_ORCL.log` | Oracle アラートログ（100行・ORA エラー13件） | Volume にアップロード |
| `app.log` | Java アプリログ（1,580行・ERROR 多数） | Volume にアップロード |
| `loghandson_notebook.ipynb` | ハンズオン本体のノートブック | Databricks にインポート |
| `generate_logs.py` | ログ生成スクリプト（参考用） | 中身を変えて再生成したいとき |

---

## 2. 手順

### Step A. カタログ・スキーマ・Volume を作る

1. Databricks Free Edition のワークスペースを開く
2. 左サイドバー **Catalog** を開く
3. 後述のノートブックの最初のセルが `CREATE CATALOG / SCHEMA / VOLUME` を実行するので、ここでは画面確認だけでOK

> サーバーレス専用のため、クラスター作成は不要です。セル実行時に自動でコンピュートが割り当たります（初回は起動に十数秒かかります）。

### Step B. ノートブックをインポート

1. 左サイドバー **Workspace** → 自分のフォルダを開く
2. 右上 **▼（または Import）** → **Import**
3. `loghandson_notebook.ipynb` をドラッグ＆ドロップ → Import
4. インポートされたノートブックを開く

### Step C. Volume を作ってログをアップロード

1. ノートブックの **Step 0 の最初のセル（`CREATE CATALOG ...`）を実行**
   → `loghandson.logs.raw` という Volume ができます
2. 左サイドバー **Catalog** → `loghandson` → `logs` → **Volumes** → `raw` を開く
3. 右上 **Upload to this volume** から `alert_ORCL.log` と `app.log` をアップロード
4. アップロード後のパスは `/Volumes/loghandson/logs/raw/<ファイル名>`

### Step D. 上から順にセルを実行

ノートブックを上から実行していくだけです。各ステップの確認ポイント：

- **Step 1（Bronze）**: `bronze_oracle_raw` / `bronze_app_raw` が作成され、行数が表示される
- **Step 2（Silver/Oracle）**: ORA エラーが時刻付きで一覧化される（10:02台に ORA-00060 / 12516 / 12519 が集中）
- **Step 3（Silver/Java）**: ERROR がパースされ、`ora_code` 列にスタックトレース内の `ORA-` が抽出される
- **Step 4（Gold）**: 10分窓ごとのエラー件数。10:00台だけ件数が突出
- **Step 4 のアラートセル**: `error_count > 5` の窓だけが `🚨 ALERT` で表示される
- **Step 5（相関分析）**: 同じ10分窓で Oracle と アプリのエラーが同時に跳ね、`ORA-` コードが一致 ← **ここがゴール**

---

## 3. キーとなる技術ポイント

- **マルチラインログの扱い**: Java のスタックトレースは複数行。行番号（`line_no`）を付けて取り込み、ヘッダ行（タイムスタンプ始まり）を起点に `Window` + `last(ignorenulls)` で後続行を1イベントに束ねています。
- **相関キーの抽出**: アプリ側のスタックトレースに含まれる `ORA-00060` 等を `regexp_extract` で拾うことで、DB ログと突き合わせ可能にしています。
- **時間窓集計**: `F.window("event_time", "10 minutes")`（PySpark）と `window(event_time, '10 minutes')`（SQL）の両方の書き方を載せています。

---

## 4. トラブルシューティング（Free Edition 特有）

| 症状 | 対処 |
|------|------|
| `CREATE CATALOG` が権限エラー | ノートブック先頭の `CATALOG = "loghandson"` を `"workspace"` に変更し、`CREATE CATALOG` 行をコメントアウト。既定の workspace カタログ配下に作る |
| `open()` でファイルが見つからない | Volume へのアップロード先・ファイル名を再確認。`os.listdir(VOLUME_PATH)` で実在を確認 |
| コンピュートが割り当たらない / 止まる | Free Edition はクォータ制。1日の上限に達した場合、翌日リセットまで待つ。アイドルセッションはこまめに停止 |
| `to_timestamp` が NULL になる | フォーマット文字列とログの実フォーマットが一致しているか確認（Oracle はマイクロ秒6桁 `.SSSSSS`、Java はミリ秒3桁 `,SSS`） |
| セルが7日で打ち切られた | サーバーレスの最大実行時間は7日。ハンズオン規模では無関係ですが念のため |

---

## 5. 発展（実プロジェクトへの橋渡し）

- **ダッシュボード**: `gold_app_errors_10m` を折れ線グラフ化してエラー件数を時系列で監視
- **AI/BI Genie**: `silver_app` / `silver_oracle` を Genie スペースに登録し、「10時台に多かったエラーは?」と日本語で質問
- **Lakeflow パイプライン**: Bronze→Silver→Gold を宣言的パイプライン化し、Volume にファイルが届いたら増分処理
- **検知ロジック高度化**: 固定しきい値（>5）を移動平均 + 3σ の乖離検知に置き換え
- **実データへの拡張**: 実際の Oracle `alert.log` や Logback 出力をそのまま流用可能（正規表現の調整のみ）

> 片付け: `DROP CATALOG loghandson CASCADE;` で一括削除できます（クォータ節約）。

---

このハンズオンは検証用の合成ログを使った非商用の学習用途を想定しています。Free Edition は商用利用不可・SLA なしの点だけご留意ください。
