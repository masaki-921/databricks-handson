# ログエラー検知ハンズオン手順書 (Databricks Free Edition)

Oracle アラートログ × Java アプリログから **エラーを検知する** ミニ基盤を、Medallion アーキテクチャ（Bronze → Silver → Gold）で構築するハンズオンです。所要時間 30〜45分。

---

## 0. このハンズオンで作るもの

仕込んであるストーリーはこうです。**2026-06-20 10:02 頃、Oracle でデッドロック（ORA-00060）→ 接続枯渇（ORA-12516 / ORA-12519）が発生し、ほぼ同時刻に Java アプリ側で `SQLException` とコネクションプールのタイムアウトが急増**します。最終的に Gold 層で時間窓集計し、この「DB 障害がアプリ障害の根本原因」という相関を1枚のビューで切り分けられる状態を目指します。

| 層 | 役割 | やること |
|----|------|---------|
| Bronze | 生ログをそのまま保管 | ログを 1行=1レコードで、`.trc` はファイル単位で Delta テーブル化 |
| Silver | 構造化・クレンジング | 正規表現で時刻 / レベル / `ORA-xxxxx` を抽出、スタックトレース結合、trc のメタ情報抽出＋突合 |
| Gold | 集計・検知 | 10分窓ごとのエラー件数、しきい値アラート、DB×アプリ相関 |

---

## 1. 配布ファイル

| ファイル | 中身 | 使い方 |
|---------|------|--------|
| `alert_ORCL.log` | Oracle アラートログ（100行・ORA エラー13件） | Volume にアップロード |
| `app.log` | Java アプリログ（1,580行・ERROR 多数） | Volume にアップロード |
| `orcl_ora_*.trc` | Oracle トレースファイル（デッドロックダンプ3件） | Volume にアップロード |
| `loghandson_notebook.ipynb` | ハンズオン本体のノートブック | Databricks にインポート |
| `generate_logs.py` | ログ生成スクリプト（参考用） | 中身を変えて再生成したいとき |
| `generate_trace.py` | トレース生成スクリプト（参考用） | trc を作り変えたいとき |

> サンプルのアラートログは4つの trc（`9912 / 10233 / 10241 / 10258`）を参照しますが、配布する実体は3つだけです。`orcl_ora_10258.trc` はあえて未配置にしてあり、Step 3 で「参照あり・実体なし」を検出する練習になります（いっちーさんの環境で実体が無い trc がある状況の再現）。

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
3. 右上 **Upload to this volume** から次をアップロード
   - `alert_ORCL.log` と `app.log`
   - `orcl_ora_*.trc`（3ファイル）。`raw` 直下でも `raw/trace/` のようなサブフォルダでも可（取り込みは `raw` 配下を再帰探索）
4. アップロード後のパスは `/Volumes/loghandson/logs/raw/...`

### Step D. 上から順にセルを実行

ノートブックを上から実行していくだけです。各ステップの確認ポイント：

- **Step 1（Bronze）**: `bronze_oracle_raw` / `bronze_app_raw` が作成され、行数が表示される
- **Step 2（Silver/Oracle）**: ORA エラーが時刻付きで一覧化される（10:02台に ORA-00060 / 12516 / 12519 が集中）。`trace_file` 列に参照 trc 名が入る
- **Step 3（トレース取り込み）**: `bronze_trace_raw` / `silver_trace` が作成され、trc のメタ情報（時刻・PID・モジュール・デッドロックの SQL）が抽出される。突合検証セルで `orcl_ora_10258.trc` が `⚠️ trace missing`、他は `matched` と表示される
- **Step 4（Silver/Java）**: ERROR がパースされ、`ora_code` 列にスタックトレース内の `ORA-` が抽出される
- **Step 5（Gold）**: 10分窓ごとのエラー件数。10:00台だけ件数が突出。アラートセルで `error_count > 5` の窓が `🚨 ALERT`
- **Step 6（相関分析）**: 同じ10分窓で Oracle と アプリのエラーが同時に跳ね、`ORA-` コードが一致 ← **ここがゴール**

> 今回のご依頼どおり、trc は **Silver までの作成**にしています（Step 3）。Gold への接続（デッドロック窓ごとの SQL 集約など）は Step 7 の発展課題に回しています。

---

## 3. キーとなる技術ポイント

- **マルチラインログの扱い**: Java のスタックトレースは複数行。行番号（`line_no`）を付けて取り込み、ヘッダ行（タイムスタンプ始まり）を起点に `Window` + `last(ignorenulls)` で後続行を1イベントに束ねています。
- **トレースファイルの扱い**: `.trc` は1ファイル=1イベントなので、行単位ではなく `os.walk` + `open()` で**ファイル単位**に取り込み（`bronze_trace_raw`）、ヘッダから時刻・PID・モジュール・デッドロックの代表 SQL を `regexp_extract` で抽出します（`silver_trace`）。
- **trc の突合**: アラート本文の `...orcl_ora_XXXX.trc` を `silver_oracle.trace_file` に抽出し、`silver_trace` と LEFT JOIN。参照のみで実体が無い trc を `⚠️ trace missing` として検出します。
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
