# エラー検知 → AI修正案 → メール通知パイプライン 設定手順書

Microsoft Fabric 上で、レイクハウスのログからエラーを検知し、AIで修正案を生成してメール通知するパイプラインの構築手順です。追加のデータストア（Eventhouse / Warehouse 等）は使わず、**レイクハウス + ノートブック + データパイプライン**だけで完結します。

---

## 1. 全体構成

```
[Lakehouse: loghandson]
   silver_app / silver_oracle
        │
        ▼
[データパイプライン]  ※5〜10分間隔でスケジュール実行
   ① Notebook(Detect)  : 最新ERROR抽出 → ±10分相関 → AI修正案 → incidents書き込み → exit value返却
        │
        ▼
   ② If 条件(HasIncident) : incident_count > 0 のときだけ次へ
        │ (True)
        ▼
   ③ Office 365 Outlook(SendMail) : 件名・本文をexit valueから組んでメール送信
```

処理の流れ：

1. `silver_app` から最新の ERROR を1件抽出
2. `silver_oracle` を同じ時間帯（前後10分）で検索し、相関するエラーを取得
3. アプリエラー + Oracleエラーを AIモデルに渡し、修正案を生成
4. 結果を `incidents` テーブルに追記し、件数・件名・本文を exit value で返却
5. インシデントがあればメール送信

> **メモ:** メール送信はパイプラインの Outlook アクティビティが直接行うため、Power Automate は不要です。

---

## 2. 前提条件

| 項目 | 内容 |
|------|------|
| Fabric 容量 | 有料 SKU（F2 以上）または P SKU。AI Functions / 組み込みLLMの利用に必要 |
| レイクハウス | `loghandson`（`silver_app`・`silver_oracle` を含む） |
| テナント設定 | Copilot および Azure OpenAI 系機能が有効（管理者設定）。地域によってはクロスジオ処理の有効化も必要 |
| メール | エンタープライズのメールアドレス（Office 365）。個人アカウントは不可 |
| ファイル | `error_detect_pipeline.ipynb`（本手順で使用するノートブック） |

---

## 3. ノートブックの取り込みと設定

### 3-1. インポート

1. Fabric ワークスペースで **「インポート」→「ノートブック」→「このコンピューターから」** を選択
2. `error_detect_pipeline.ipynb` をアップロード
3. 開いたノートブックの左ペインから、既定のレイクハウスとして **`loghandson` をアタッチ**

### 3-2. 設定セルの編集（CELL 2）

実テーブルの構造に合わせて、以下を編集します。

| 変数 | 既定値 | 説明 |
|------|--------|------|
| `APP_TABLE` | `silver_app` | アプリログのテーブル名 |
| `ORACLE_TABLE` | `silver_oracle` | Oracleログのテーブル名 |
| `INCIDENT_TABLE` | `incidents` | 出力先テーブル（初回実行時に自動作成） |
| `TS_COL` | `event_time` | タイムスタンプ列名 |
| `LEVEL_COL` | `level` | ログレベル列名 |
| `MSG_COL` | `message` | 本文列名 |
| `WINDOW_MIN` | `10` | 相関ウィンドウ（前後の分数） |
| `AI_MODEL` | `gpt-5-mini` | 利用モデル。代替: `gpt-5.1` / `gpt-4.1` |

> **Oracle側の注意:** `silver_oracle` がログレベルを `level` 列で持たず `ORA-` コードで判定する場合は、CELL 5 のフィルタを `F.col(MSG_COL).startswith("ORA-")` などに変更してください。

### 3-3. 単体テスト

パイプラインに組み込む前に、ノートブックを上から順に実行し、以下を確認します。

- CELL 3〜5：最新ERRORとOracleの相関結果が出力される
- CELL 6：AIの修正案が表示される
- CELL 7：`incidents` テーブルに追記される
- CELL 8：exit value（JSON）が返る

---

## 4. データパイプラインの作成

> **アイテム名の確認:** 作成するのは **「データ パイプライン（Data pipeline）」** です。CI/CD用の「デプロイ パイプライン（Deployment pipeline）」とは別物なので注意してください。

### 4-1. パイプライン作成

1. ワークスペースで **「+ 新しいアイテム」→ 検索「データ パイプライン」** を選択（または左下のエクスペリエンス切り替えで Data Factory に切り替えて作成）
2. 名前を付けて作成

### 4-2. ① Notebook アクティビティ（名前: Detect）

1. アクティビティ一覧から **Notebook** をキャンバスに追加
2. Settings → Notebook で `error_detect_pipeline` を選択
3. ノートブックに既定レイクハウス `loghandson` がアタッチされていることを確認

### 4-3. ② If 条件 アクティビティ（名前: HasIncident）

1. **If 条件** を追加し、Detect の成功（緑のチェック）からつなぐ
2. 条件式に以下を設定（exit value は JSON 文字列なので `json()` でパース）：

```
@greater(json(activity('Detect').output.result.exitValue).incident_count, 0)
```

### 4-4. ③ Office 365 Outlook アクティビティ（名前: SendMail）

1. **If 条件の True 分岐の中**に **Office 365 Outlook** を追加
2. 接続で Office 365 にサインイン（エンタープライズアドレス）
3. 各項目を設定：

| 項目 | 設定値 |
|------|--------|
| To | 送信先アドレス（自分で設定。複数は `;` 区切り） |
| Subject | `@json(activity('Detect').output.result.exitValue).subject` |
| Body | `@json(activity('Detect').output.result.exitValue).body` |

> **Teamsにも通知する場合:** 同じ True 分岐に **Teams アクティビティ**を並べ、同様の動的式を設定します。

---

## 5. スケジュール設定

1. パイプラインのツールバーから **「スケジュール」** を開く
2. 実行を **オン** にし、間隔を **5〜10分** に設定
3. タイムゾーンを確認して保存

---

## 6. 動作確認

1. パイプラインを **手動実行（Run）** する
2. 各アクティビティのステータス（緑）を確認
3. `silver_app` に新しい ERROR がある場合、設定したアドレスにメールが届くことを確認
4. `incidents` テーブルに行が追記されていることを確認

---

## 7. 運用上のポイント

### 重複送信の防止
ノートブックには重複チェックが入っており、**新しいエラーが出たときだけ**メールが飛びます。同じ最新エラーが続く間は `incident_count: 0` を返し、If 条件で停止します（AIも呼ばれないためトークンも消費しません）。5分間隔で回しても無駄打ちしません。

### コスト
AI（LLM）呼び出しの CU 消費は、Spark の計算とは別に **「Copilot and AI」課金メーター**で AI Functions として計上されます。新規エラー発生時のみ呼ばれるため軽微ですが、間隔を短くする際は留意してください。

### 本文が大きくなる場合
exit value に HTML本文を載せる方式は1インシデントなら軽量です。本文が将来大きくなる場合は、exit value は件数だけにして、Outlook の本文を **Lookup アクティビティ**で `incidents` テーブルから引く方式に変更します。

```sql
SELECT TOP 1 body_html FROM incidents ORDER BY detected_at DESC
```

→ Outlook の Body に `@activity('Lookup').output.firstRow.body_html`

---

## 8. トラブルシューティング

| 症状 | 確認・対処 |
|------|-----------|
| テーブルが読めない | `loghandson` が既定レイクハウスとしてアタッチされているか。されていなければテーブル名を `loghandson.silver_app` 形式にする |
| 列が見つからない | CELL 2 の `TS_COL` / `LEVEL_COL` / `MSG_COL` を実際の列名に合わせる |
| Oracleが0件ばかり | Oracle のエラー判定（`level` か `ORA-` コードか）を CELL 5 のフィルタで調整 |
| AI呼び出しでエラー | 有料SKUか、テナントでCopilot/Azure OpenAI系が有効か、地域のクロスジオ設定を確認 |
| メールが届かない | Outlook 接続がエンタープライズアドレスでサインインされているか。個人アカウントは不可 |
| 「データ パイプライン」が見つからない | 「デプロイ パイプライン」と混同していないか。+新しいアイテムで「データ パイプライン」を検索 |
| exit value が取れない | パイプラインの参照式が `activity('Detect').output.result.exitValue` になっているか（アクティビティ名と一致しているか） |

---

## 9. 構成ファイル一覧

| ファイル | 役割 |
|----------|------|
| `error_detect_pipeline.ipynb` | 検知・相関・AI修正案・incidents書き込み・exit value返却を行うノートブック |
| （パイプライン） | Notebook → If 条件 → Outlook の3アクティビティ。本手順で作成 |
