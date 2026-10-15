# aws-cost-cleaner 詳細設計

## 目的と範囲

### 目的
開発・検証用の AWS アカウント 1 つで、放置されたリソースを見つけ、安全に停止・削除して、無駄な課金を止める。そのとき、次の 3 つのリスクを IAM とワークフローの両方で防ぐ。

1. AI がリソースを大量に作成する(No-Create)
2. 重要な環境を誤って削除する(Keep=true の保護、人間の承認、2 段階の削除)
3. 調査に使う API 自体に料金がかかる(Zero-API-Cost)

### 作るもの
| 成果物 | 内容 |
| --- | --- |
| Python CLI `aws-cost-cleaner` | サブコマンドは `scan` / `report` / `tag` / `stop` / `delete` の 5 つ。判定ルールはすべてコードに持たせる。Claude Code などの外部エージェントは、この CLI を呼ぶだけにする(Q-1) |
| CloudFormation テンプレート `infra/iam-roles.yaml` | AI 用ロール(読み取りと候補タグの付与)と、実行用ロール(人間だけが使い、停止と削除をする)を作る(Q-2, Q-4) |
| 同梱の料金表と、Price List API での料金取得 | 推定月額コストの計算に使う(Q-7) |
| `docs/` | 事前準備の手順、AI エージェント用のシステムプロンプトのテンプレート、Stage 0 の確認記録 |

### 対象にするリソース(Q-5)
次の 6 種類だけを扱う。スナップショット、Aurora、Classic LB、GWLB、ターゲットグループは扱わない。

| 種類(`resource_type`) | 表示名(`service_label`) | `resource_id` に入れる値 |
| --- | --- | --- |
| `ebs_volume` | `EC2 (EBS)` | VolumeId(`vol-…`) |
| `elastic_ip` | `VPC (EIP)` | AllocationId(`eipalloc-…`)。名前の欄には PublicIp を入れる |
| `nat_gateway` | `VPC (NAT)` | NatGatewayId(`nat-…`) |
| `load_balancer` | `ELB (ALB)` / `ELB (NLB)` | LoadBalancerName。ARN は `arn` に入れる |
| `ec2_instance` | `EC2` | InstanceId(`i-…`) |
| `rds_instance` | `RDS` | DBInstanceIdentifier。ARN は `arn` に入れる |

### ワークフロー(素案 §5 を、Q-1〜Q-3、Q-8 に合わせて具体化したもの)
| 段 | 担当(ロール) | 操作 |
| --- | --- | --- |
| 1 | 人間 | (任意)Cost Explorer の CSV を出力する |
| 2 | AI(AgentRole) | `scan` → `report`。Markdown のレポートと承認 JSON ができる |
| 3 | 人間 | 承認 JSON の `approved` を `true` に書き換える |
| 4 | AI(AgentRole) | `tag --approval`(dry-run) → 人間の指示を受けてから `--apply`。`CleanupCandidate=true` が付く |
| 5 | 人間(ExecutorRole) | `stop --approval`(Stop の項目)と `delete --approval`(Delete の項目)。どちらも dry-run の結果を見てから `--apply` |
| 6 | AI(AgentRole) | `stop_wait_days` 日がたったら、もう一度 `scan` → `report`。停止したままの EC2/RDS は、**新しい Delete の候補**としてレポートに出る |
| 7 | 人間 → AI → 人間 | 段 3〜5 と同じ流れで、Delete を承認 → `tag --apply`(`CleanupReason` を書き換える) → `delete --approval --apply` |

EC2/RDS は、Stop の承認だけでは削除されない。停止後のもう一度の scan で Delete の候補として出て、人間がその承認ファイルで承認したものだけが `delete` の対象になる。

### 前回の設計からの主な変更(見直しの指摘への対応)
- `stop` と `delete` は承認ファイル(`--approval`)を必須にした。承認ファイルの「`approved: true`、かつ推奨アクションがコマンドと一致する」項目だけを対象にする。EC2/RDS の削除には、停止後の再 scan で出た Delete の項目の承認が要る(F-7、F-9、F-11、F-12)。
- `stop` と `delete` は Resource Explorer を使わない。承認ファイルのリージョンと ID で Describe 系 API を 1 件ずつ呼んで、対象を特定する(インデックスの反映の遅れの影響を受けない)。
- `tag` で `stopped` の EC2 を Stop として承認していたときの扱いと、`stop` の分岐との関係をはっきりさせた(F-10、F-11)。
- サービス別とリージョン別の CSV を両方渡したときの集計と並べ方を決めた(F-9)。
- `tag` で確かめ直す属性(Describe 系 API だけで、メトリクスは取り直さない)、RDS のストレージ属性、経過時間の判定の精度(秒)、Resource Explorer で取れなかったときの代わりの処理、AWS のエラーの扱い(`ClientError` のコードを記録する)を決めた。

### 前提
- AWS アカウントは 1 つ。認証は boto3 の標準の認証情報チェーン(プロファイルか環境変数)を使う。
- Python 3.12 以上、boto3、uv(pyproject.toml)を使う。
- テストは moto などのモックで行い、実際のアカウントには接続しない。

---

## 構成(使う技術と、選んだ理由。退けた候補)

| 要素 | 採用 | 理由 | 退けた候補 |
| --- | --- | --- | --- |
| 言語・実行環境 | Python 3.12+ | 前提で決まっている | — |
| パッケージ管理 | uv + pyproject.toml(src レイアウト。`[project.scripts]` で `aws-cost-cleaner` を公開する) | 前提で決まっている | — |
| AWS SDK | boto3 | 前提で決まっている。botocore のイベントフックで、呼べる API をコードで制限できる | aws CLI を subprocess で呼ぶ方法。呼べる API の制限とテストが難しくなる |
| CLI フレームワーク | argparse(標準ライブラリ) | サブコマンド 5 つと少しのオプションなら足りる。依存を増やさない | click / typer。必要になる場面がない |
| 設定ファイル | TOML(`tomllib`、標準ライブラリ) | 読み込みに依存が要らない。しきい値をコメントつきで書ける | YAML。依存が増える。`yes` が真偽値になるなどの落とし穴がある |
| 承認ファイル | JSON(`json`、標準ライブラリ) | Q-3 では JSON か YAML を選べる。JSON なら依存なしで読み書きでき、`"approved": true` が曖昧さなく真偽値になる | YAML。`approved: yes` や `on` も真偽値として通ってしまい、承認したつもりかどうかが曖昧になる |
| IAM 定義 | CloudFormation YAML(Q-4)。組み込み関数は長い形式(`{"Ref": ...}`、`{"Fn::Sub": ...}`)で書き、短縮形(`!Ref`)は使わない | 短縮形を使わなければ、テストで PyYAML の `safe_load` だけでポリシーを読める | Terraform / CDK。Q-4 で退けた |
| テスト | pytest、moto(EC2 / RDS / ELBv2 / CloudWatch / STS)、botocore `Stubber`(moto が対応していないサービスや操作に使う) | 前提で決まっている。実際のアカウントには接続しない | — |
| 静的検査 | ruff(lint と format)、mypy `--strict`、boto3-stubs(`ec2,rds,elbv2,cloudwatch,resource-explorer-2,pricing,sts`)、cfn-lint | 型の検査と、テンプレートの文法・プロパティの検査を機械で行う | pyright。mypy と同等なので 1 つに絞った |
| CI | GitHub Actions(`.github/workflows/ci.yml`)。上の検査とテストを走らせる | レビューのときに、機械での検証の結果を見られる | — |

---

## ディレクトリとモジュール

```
aws-cost-cleaner/
├── README.md                         # 既存のファイルを拡充する(使い方、ワークフロー、安全設計の要約)
├── pyproject.toml
├── uv.lock
├── .gitignore                        # out/ と .cache/ を除外する
├── aws-cost-cleaner.example.toml     # 設定ファイルの例(標準値とコメント)
├── .github/workflows/ci.yml
├── infra/
│   └── iam-roles.yaml                # AgentRole と ExecutorRole
├── docs/
│   ├── setup.md                      # 事前準備(素案 §3 と、CFN のデプロイ手順)
│   ├── agent-prompt.md               # AI エージェント用のシステムプロンプトのテンプレート(CLI を呼ぶ形)
│   └── verification-notes.md         # Stage 0 の確認結果(URL、確認した日付、未確認の項目)
├── scripts/
│   └── update_prices.py              # 同梱の料金表を作り直す保守用のスクリプト(製品のコマンドではない)
├── src/aws_cost_cleaner/
│   ├── __init__.py
│   ├── __main__.py                   # python -m aws_cost_cleaner
│   ├── cli.py                        # argparse、サブコマンドの振り分け、終了コード
│   ├── clock.py                      # 現在時刻を返す Clock(テストで固定の時刻を注入する)
│   ├── config.py                     # Thresholds などの dataclass、TOML の読み込み、CLI オプションでの上書き
│   ├── errors.py                     # 例外の階層
│   ├── tags.py                       # タグキーの定数、Keep 判定、候補判定、CleanupStoppedAt の解析
│   ├── models.py                     # Resource / Finding / Skipped / ScanResult / ApprovalItem / Outcome
│   ├── catalog.py                    # 種類 × 推奨アクション × 理由コードの許可表
│   ├── aws/
│   │   ├── session.py                # boto3 Session の生成、クライアントの生成(必ず guard を通す)
│   │   └── guard.py                  # 呼んでよい API の許可リスト(モード別)と before-call フック
│   ├── discovery.py                  # Resource Explorer で、リソースがあるリージョンを絞る(scan だけが使う)
│   ├── collectors/
│   │   ├── __init__.py               # 種類ごとのコレクタの登録
│   │   ├── ebs.py
│   │   ├── eip.py
│   │   ├── nat.py
│   │   ├── elb.py
│   │   ├── ec2.py
│   │   └── rds.py
│   ├── lookup.py                     # 承認ファイルの 1 項目を、Describe 系 API で ID を指定して取り直す(tag / stop / delete が使う)
│   ├── metrics.py                    # GetMetricStatistics の呼び出しと、呼び出し回数の上限
│   ├── rules.py                      # 判定ルール(純粋関数)
│   ├── pricing/
│   │   ├── table.py                  # 同梱の表の読み込み
│   │   ├── price_list.py             # Price List Query API とキャッシュ
│   │   ├── estimate.py               # 種類ごとの月額の計算
│   │   └── data/prices.json          # 同梱の料金表(パッケージデータ)
│   ├── costcsv.py                    # Cost Explorer の CSV を読む(優先順位付けにだけ使う)
│   ├── scan.py                       # scan の処理全体
│   ├── report.py                     # Markdown と承認 JSON を書き出す
│   ├── approval.py                   # 承認ファイルの読み込みと検証
│   ├── tagging.py                    # tag の処理
│   └── actions.py                    # stop / delete の計画と実行
└── tests/
    ├── unit/
    ├── acceptance/                   # moto / Stubber を使い、CLI を端から端まで動かす
    ├── infra/                        # iam-roles.yaml の静的な検査
    └── fixtures/                     # CSV の見本、Price List の応答の見本、Resource Explorer の応答の見本、ゴールデンファイル
```

### モジュールの責務と依存の向き
- `cli` → `scan` / `report` / `tagging` / `actions` → `discovery` / `collectors` / `lookup` / `metrics` / `rules` / `pricing` → `aws.session` の順に依存する。逆向きの依存は作らない。
- `rules.py` と `catalog.py` は boto3 に依存しない純粋なモジュールにする。`rules.py` の入力は `Resource`、メトリクスの値、`Thresholds`、現在時刻で、出力は `Finding | Skipped`。
- AWS クライアントは `aws.session.client(service, region, mode)` からだけ作る。boto3 を直接呼ぶのは `aws/session.py` だけにし、ruff の `banned-api`(TID251)で、ほかのモジュールから `boto3.client` と `boto3.Session` を使えないようにする。
- 現在時刻は `clock.Clock` から取る。`datetime.now()` を直接呼ばない(ruff の `banned-api` で `datetime.datetime.now` と `datetime.datetime.utcnow` を禁止する。`clock.py` は除く)。

---

## データとコンテンツ(形式、置き場所、検証)

### 1. タグキー(`tags.py` の定数)
| キー | 値 | 付ける人とコマンド | 意味 |
| --- | --- | --- | --- |
| `Keep` | `true` | 人間(コンソール) | 保護。IAM で Deny される。コードでも除外する |
| `CleanupCandidate` | `true` | `tag`(AgentRole) | 承認済みの候補。停止・削除の前提になる(IAM の条件) |
| `CleanupReason` | 理由コード(ASCII) | `tag`(AgentRole) | 直近に承認された判定の理由。Delete の再承認のときに書き換わる |
| `CleanupStoppedAt` | ISO 8601 の UTC、秒まで(`2026-10-15T03:00:00Z`) | `stop`(ExecutorRole) | 停止した日時(Q-8) |

- `CleanupReason` には日本語の文ではなく理由コードを入れる。サービスによってはタグの値に使える文字が限られているはずなので(未確認、Stage 0 で確かめる)、ASCII にしておく。
- Keep の判定(コード側)は、値を `strip().lower() == "true"` で比べる。IAM の `StringEquals` は大文字と小文字を区別するので、`True` や `TRUE` は IAM では守られない。コード側を広めにとって、二重の守りを強くする。
- CleanupCandidate の判定(コード側)は、値が `"true"` と完全に一致するときだけにする。IAM の条件と同じ厳しさにそろえる。
- `CleanupStoppedAt` の解析は `datetime.fromisoformat` で行い、タイムゾーンがない値や読めない値は「読めない」(`None`)として扱う。

### 2. 種類 × 推奨アクション × 理由コードの許可表(`catalog.py`)
| `resource_type` | `recommended_action` | `reason_code` | どの判定から出るか |
| --- | --- | --- | --- |
| `ebs_volume` | Delete | `ebs-unattached` | 未接続 |
| `elastic_ip` | Delete | `eip-unassociated` | 関連付けなし |
| `nat_gateway` | Delete | `nat-idle` | 通信量がしきい値以下 |
| `load_balancer` | Delete | `elb-no-targets` | 登録済みのターゲットが 0 件 |
| `ec2_instance` | Stop | `ec2-low-cpu` | running で低稼働 |
| `ec2_instance` | Delete | `ec2-stopped-elapsed` | 候補として停止され、待ち期間が過ぎた |
| `rds_instance` | Stop | `rds-no-connections` | available で接続なし |
| `rds_instance` | Delete | `rds-stopped-elapsed` | 候補として停止され、待ち期間が過ぎた |

この表にない組み合わせは、承認ファイルの検証で `ApprovalFileError` にする。人間が `recommended_action` を書き換えて、EC2 を Stop の承認だけで削除させることを防ぐためである。

### 3. 設定ファイル(`aws-cost-cleaner.toml`。`--config` で指定する。指定しないときは標準値を使う)
```toml
[scan]
resource_explorer_region = "ap-northeast-1"  # アグリゲーターインデックスがあるリージョン
regions = []                                  # 空なら Resource Explorer で絞る。指定すると Resource Explorer を使わず、そのリージョンだけを調べる。--region で上書きできる
max_metric_requests = 1000                    # 1 回の scan で呼ぶ GetMetricStatistics の上限

[thresholds]
period_days = 7
ec2_cpu_max_percent = 5.0          # 期間内の最大 CPU 使用率がこの値「未満」なら低稼働
ec2_network_bytes_max = -1         # NetworkIn+Out の期間合計がこの値「以下」なら低稼働。-1 は判定に使わない
rds_connections_max = 0            # 期間内の最大 DatabaseConnections がこの値「以下」なら未使用
nat_bytes_max = 0                  # BytesInFromSource+BytesInFromDestination の期間合計がこの値「以下」なら未使用

[actions]
stop_wait_days = 7                 # CleanupStoppedAt から、この日数「以上」たったら Delete の候補にでき、削除もできる

[pricing]
use_price_list_api = true          # Stage 0 の結果で標準値を決める(後述)
cache_ttl_hours = 24
```
- 標準値は素案の例に合わせる(CPU 5% 未満、DB 接続 0、NAT の通信量 0 Byte、期間 7 日)。
- `ec2_network_bytes_max` の標準値は素案に例がない。EC2 は SSM エージェントや NTP などで常に通信があるので、0 にすると EC2 がまったく検出されなくなる。そこで、標準では判定に使わない(`-1`)ことにした。**レビューで決めてほしい点**: これでよいか。
- `stop_wait_days` は `scan`(Delete の候補にするか)と `delete`(実行してよいか)の両方で使う。
- CLI のオプションは設定ファイルの値を上書きする(`--period-days`、`--ec2-cpu-max-percent`、`--stop-wait-days` など。キー名の `_` を `-` にしたもの)。
- 検証: 読み込みのときに型と範囲を確かめる(`period_days >= 1`、`stop_wait_days >= 1`、パーセントは 0–100、`max_metric_requests >= 0`)。違反したら `ConfigError` にする。未知のキーも `ConfigError` にする(タイプミスを見逃さないため)。

### 4. 経過時間の判定(共通)
- 経過時間は `now - t >= timedelta(days=N)` で、秒の精度で比べる(日付だけで丸めない)。`now` は `Clock` から取った UTC。
- 使う場所: 作成・起動からの経過(F-7 の `period_days`)、停止からの経過(F-7 と F-12 の `stop_wait_days`)。
- 例: `stop_wait_days=7`、`CleanupStoppedAt=2026-10-08T03:00:00Z` のとき、`2026-10-15T03:00:00Z` なら経過済み、`2026-10-15T02:59:59Z` なら未経過。

### 5. scan の結果(`out/scan-<UTC時刻>.json`。`--output` で変えられる)
```json
{
  "schema_version": 1,
  "scan_id": "20261015T030000Z",
  "generated_at": "2026-10-15T03:00:00Z",
  "account_id": "123456789012",
  "thresholds": { "...": "実際に使った値(stop_wait_days を含む)" },
  "regions_scanned": ["ap-northeast-1", "us-east-1"],
  "discovery": { "method": "resource_explorer", "warnings": [] },
  "metric_requests_used": 42,
  "findings": [
    {
      "resource_type": "ebs_volume",
      "service_label": "EC2 (EBS)",
      "region": "ap-northeast-1",
      "resource_id": "vol-0123456789",
      "arn": "arn:aws:ec2:...",
      "name": "tmp-data",
      "state_summary": "available (未接続)",
      "metrics": {},
      "recommended_action": "Delete",
      "reason_code": "ebs-unattached",
      "reason_text": "どの EC2 にもアタッチされておらず課金のみ発生",
      "estimated_monthly_usd": 8.0,
      "price_source": { "kind": "price_list_api", "as_of": "2026-10-15" }
    }
  ],
  "skipped": [
    { "resource_type": "ec2_instance", "region": "us-east-1", "resource_id": "i-...", "skip_reason": "keep_tag" }
  ]
}
```
- `discovery.method` の値: `resource_explorer` / `explicit_regions` / `fallback_all_regions`(F-4)。
- `skip_reason` の値:
  | 値 | 意味 |
  | --- | --- |
  | `keep_tag` | Keep=true |
  | `active` | 条件を満たさない(使われている) |
  | `no_metrics` | データポイントがない |
  | `too_new` | 作成・起動から `period_days` 日たっていない |
  | `metric_budget_exhausted` | メトリクスの呼び出し回数の上限に達した |
  | `aurora` | Aurora のインスタンス |
  | `unsupported_lb_type` | GWLB |
  | `already_candidate` | `CleanupCandidate=true` が付いていて、ほかの行に当たらない(承認済みで、停止か削除を待っている) |
  | `stop_wait_pending` | 候補として停止されたが、`stop_wait_days` 日たっていない |
  | `stop_not_recorded` | 候補で停止しているが、`CleanupStoppedAt` がない、または読めない(`stop` をもう一度実行すれば記録される) |
  | `restarted_after_stop` | 候補の EC2 が、停止の後で `running` に戻っている |
- `estimated_monthly_usd` は、料金がわからないとき `null` にする。

### 6. 承認ファイル(`out/approval-<scan_id>.json`、Q-3)
```json
{
  "schema_version": 1,
  "scan_id": "20261015T030000Z",
  "account_id": "123456789012",
  "items": [
    {
      "resource_type": "ebs_volume",
      "region": "ap-northeast-1",
      "resource_id": "vol-0123456789",
      "arn": "arn:aws:ec2:ap-northeast-1:123456789012:volume/vol-0123456789",
      "name": "tmp-data",
      "recommended_action": "Delete",
      "reason_code": "ebs-unattached",
      "estimated_monthly_usd": 8.0,
      "approved": false
    }
  ]
}
```
- 人間が書き換えるのは `approved` だけにする。
- 同じ承認ファイルを `tag`、`stop`、`delete` の 3 つのコマンドに渡す。どのコマンドも、`approved: true` の項目だけを扱う。
- `approval.py` は次を確かめる。満たさないときは `ApprovalFileError` を出し、何も実行しない(終了コード 2)。
  - `schema_version == 1`
  - `approved` が bool 型である(`"true"` のような文字列は認めない)
  - (`resource_type`, `recommended_action`, `reason_code`)が、許可表(データ 2)にある組み合わせである
  - `region` と `resource_id` が空でない。`load_balancer` と `rds_instance` は `arn` も空でない
  - 同じ (`region`, `resource_id`) が重複していない
- `account_id` が現在の認証情報(`sts:GetCallerIdentity`)のアカウントと違うときは、`AccountMismatchError` を出す(終了コード 2)。

### 7. 料金表(`src/aws_cost_cleaner/pricing/data/prices.json`、Q-7)
```json
{
  "schema_version": 1,
  "currency": "USD",
  "hours_per_month": 730,
  "checked_at": "2026-10-xx",
  "source": "AWS Price List Query API (pricing:GetProducts) / 取得スクリプト scripts/update_prices.py",
  "regions": {
    "ap-northeast-1": {
      "ebs_gb_month": { "gp2": 0.0, "gp3": 0.0, "io1": 0.0, "io2": 0.0, "st1": 0.0, "sc1": 0.0, "standard": 0.0 },
      "ebs_iops_month": { "gp3": 0.0, "io1": 0.0, "io2": 0.0 },
      "ebs_throughput_mibps_month": { "gp3": 0.0 },
      "public_ipv4_hour": 0.0,
      "nat_gateway_hour": 0.0,
      "alb_hour": 0.0,
      "nlb_hour": 0.0,
      "ec2_linux_hour": { "t3.micro": 0.0 },
      "rds_hour": { "mysql|single-az|db.t3.micro": 0.0 },
      "rds_storage_gb_month": { "gp2|single-az": 0.0, "gp3|single-az": 0.0, "gp2|multi-az": 0.0 },
      "rds_iops_month": { "io1|single-az": 0.0 }
    }
  }
}
```
- 同梱するのは主なリージョンだけ(`ap-northeast-1`、`ap-northeast-3`、`us-east-1`、`us-west-2`)。EC2 と RDS は、よく使われるクラス(t 系と、m 系の小さいもの)だけを入れる。値(上の 0.0 はダミー)は `scripts/update_prices.py` で作る。人間が手で書かない。
- **料金の取り方(優先順位)**:
  1. Price List Query API(`pricing:GetProducts`。エンドポイントは `us-east-1`)
  2. ローカルのキャッシュ(`~/.cache/aws-cost-cleaner/prices/`。TTL は `cache_ttl_hours`)
  3. 同梱の表
  4. どれでも見つからなければ「不明」(`null`)

  どの料金を使ったかは `price_source` に残し、レポートに注記する。
- **Price List API の扱いは Stage 0 の確認で決める(Q-7)**:
  - 無料で、ReadOnlyAccess の範囲で呼べると確認できたら: `use_price_list_api = true` を標準にする。AgentRole の IAM でも `pricing:GetProducts` を明示して Allow する。
  - 無料と確認できない、または有料なら: 標準を `false` にし、同梱の表だけを使う。この場合、`scripts/update_prices.py` は人間が保守のときに実行する。そのうえで、認証の要らない公開の Bulk(offer file)を使う形に切り替えるかを、開発者と**もう一度検討する**(Q-7 の「実装不可能であれば再度検討」に当たる)。
- 月額の計算(`estimate.py`)。「概算」と明記する。
  | 種類 | 計算 | 使う属性(F-5 で取る) |
  | --- | --- | --- |
  | EBS | 容量(GB) × GB 月額。gp3 は 3000 IOPS と 125 MiB/s を超える分を足す。io1/io2 はプロビジョンド IOPS の分を足す | `Size`、`VolumeType`、`Iops`、`Throughput` |
  | EIP | パブリック IPv4 の時間単価 × 730 | — |
  | NAT Gateway | 時間単価 × 730(データ処理料金は含めない) | — |
  | ALB/NLB | 時間単価 × 730(LCU と NLCU は含めない) | `Type` |
  | EC2 | Linux のオンデマンド、共有テナンシーの時間単価 × 730。アタッチされた EBS は含めない。Windows なども Linux の単価で概算し、注記する。停止中の EC2(Delete の候補)はインスタンスの料金がかからないので、`0.0` とし、「停止中。EBS の料金は含めていない」と注記する | `InstanceType` |
  | RDS | エンジン、Single/Multi-AZ、インスタンスクラスごとの時間単価 × 730 + ストレージの GB 月額 × `AllocatedStorage` + io1 のときは IOPS 月額 × `Iops`。停止中の RDS はインスタンスの料金を 0 にし、ストレージの料金だけを計上する | `Engine`、`MultiAZ`、`DBInstanceClass`、`AllocatedStorage`、`StorageType`、`Iops` |
- Price List API のフィルタ属性名(`regionCode`、`instanceType`、`operatingSystem`、`tenancy`、`preInstalledSw`、`capacitystatus`、`volumeApiName`、`databaseEngine`、`deploymentOption` など)は未確認。Stage 0 で `pricing:DescribeServices` と `GetAttributeValues` を使って確かめ、`price_list.py` に定数として書く。
- 検証: `tests/unit/test_prices_schema.py` で、同梱の表のスキーマ(必須キー、値が 0 以上の数)と、`checked_at` の日付の形式を確かめる。

### 8. Cost Explorer の CSV(任意。`report --cost-csv <path>` で指定する。複数回指定できる、Q-6)
- 使い道は、レポートの並べ方だけにする。スキャンの対象は変えない。
- 列の形式は未確認。Stage 0 で、開発者が実際にコンソールから出力した CSV(サービス別とリージョン別の 2 種類。金額は書き換えてよい)を `tests/fixtures/` に置いてもらい、それに合わせて決める。仮の想定は、1 列目が期間、2 列目以降がグループ(サービス名かリージョン名)で、合計の列か行がある、という形。
- **CSV の種類の判定**: 合計の列を除いたグループの列名が、すべてリージョンコード(`^[a-z]{2}(-[a-z]+)+-\d$`)ならリージョン別、そうでなければサービス別とみなす。混ざっているときは `CostCsvError` にする。
- **1 つの CSV の集計**: グループの列ごとに、期間の行(合計の行は除く)の値を足す。結果は `dict[グループ名, Decimal]`。
- **複数の CSV の結合**: 種類ごとに、同じグループ名の値を足し合わせる。たとえば、リージョン別の CSV を 2 か月分渡したら、2 か月の合計になる。結果は `region_totals: dict[str, Decimal]` と `service_totals: dict[str, Decimal]` の 2 つ。
- **サービス名と種類の対応**: `costcsv.SERVICE_MAP: dict[str, frozenset[ResourceType]]`(Cost Explorer のサービス名 → 種類の集合)。見本の CSV の実際の列名から作る。たとえば、EBS と NAT は「EC2 - Other」に、EIP は VPC に含まれるはずである(未確認)。種類ごとの金額 `type_totals[resource_type]` は、その種類を含むサービス名の金額の合計にする。`SERVICE_MAP` にないサービス名は無視し、標準エラーに 1 行警告を出す。
- 並べ方への使い方は F-9 に書く。
- 読み込みは標準の `csv` モジュールの振る舞いに合わせる(J-20261009-009)。
  - `open(path, encoding="utf-8-sig", newline="")` で開く。
  - 空のファイル、またはヘッダーだけのファイルは、空の集計として扱う。エラーにはせず、警告を 1 行出す。
  - 空のセル(`""`)は 0 とみなす。Cost Explorer は料金のない日を空欄にするはずなので(未確認)。
  - 数値にできないセルは `CostCsvError` にする。メッセージにはファイル名、行番号、列名を入れる。

### 9. コマンドの結果(`models.Outcome`。tag / stop / delete が使う)
```python
@dataclass(frozen=True)
class Outcome:
    resource_type: ResourceType
    region: str
    resource_id: str
    action: Literal["tag", "stop", "tag_stopped_at", "delete"]
    status: Literal["planned", "done", "skipped", "failed"]
    error: AwsCostCleanerError | None   # failed のとき、ツールが判断した理由
    aws_error_code: str | None          # failed のとき、AWS の ClientError の Error.Code
    note: str | None                    # 人間に見せる補足(自動起動の可能性など)
```
- `planned`: dry-run で、実行する予定。`done`: 実行した。`skipped`: すでに目的の状態になっていて、何もしなくてよい。`failed`: 前提条件を満たさない、または AWS がエラーを返した。
- 標準出力には 1 項目 1 行の表を出し、最後に状態ごとの件数を出す。`failed` が 1 件でもあれば、終了コードは 1 にする。

---

## 画面・機能(素案の各項目を、実装できる単位に)

各機能の番号(F-n)は、そのままタスクの単位にできる。受け入れ条件は、すべてテストで確かめられるように書いた。

### F-1 プロジェクトの土台
- pyproject.toml(依存: `boto3`。dev: `pytest`、`moto[ec2,rds,elbv2,cloudwatch,sts]`、`ruff`、`mypy`、`boto3-stubs[...]`、`cfn-lint`、`pyyaml`、`types-PyYAML`)、`.gitignore`、CI、`aws-cost-cleaner --help` を作る。`clock.py` もここで作る。
- 受け入れ条件: `uv run aws-cost-cleaner --help` が 5 つのサブコマンドを表示する。CI で 5 つの検査がすべて通る。

### F-2 例外と終了コード(`errors.py`、`cli.py`)
- 例外の階層:
  ```
  AwsCostCleanerError
  ├── ConfigError
  ├── CostCsvError
  ├── ApprovalFileError
  │   └── AccountMismatchError
  ├── DisallowedApiCallError
  ├── ProtectedResourceError    # Keep=true
  ├── NotCandidateError         # CleanupCandidate=true がない
  ├── StateMismatchError        # 状態が想定と違う。リソースが見つからない場合も含む
  ├── WaitPeriodNotElapsedError # CleanupStoppedAt がない、読めない、または待ち期間が過ぎていない
  └── AwsOperationError         # AWS の ClientError を包んだもの。aws_error_code を持つ
  ```
- 例外メッセージには、リソース ID、リージョン、拒否した理由を入れる。テストでは型だけを確かめ、文面までは比べない(J-20261012-024)。
- 終了コード:
  | コード | 意味 |
  | --- | --- |
  | 0 | 成功(dry-run と、`skipped` だけのときを含む) |
  | 1 | 一部の項目が `failed` になった(処理は続け、最後に一覧を出す) |
  | 2 | 使い方の誤り、設定の誤り、入力ファイルの誤り、アカウントの不一致(何も実行しない) |

### F-3 API の許可リスト(`aws/guard.py`、`aws/session.py`)
- botocore の `before-call.*.*` イベントにフックを登録する。呼ぼうとした操作名がモードの許可リストにないときは、`DisallowedApiCallError` を出す。前提の「許可した種類以外の API を呼ばない」をコードでも守るための仕組みである。
- モードと許可リスト:
  | モード | 呼べる API |
  | --- | --- |
  | `read`(scan / report) | `sts:GetCallerIdentity`、`resource-explorer-2:Search`、`ec2:DescribeRegions`、`ec2:DescribeVolumes`、`ec2:DescribeAddresses`、`ec2:DescribeNatGateways`、`ec2:DescribeInstances`、`elbv2:DescribeLoadBalancers`、`elbv2:DescribeTargetGroups`、`elbv2:DescribeTargetHealth`、`elbv2:DescribeTags`、`rds:DescribeDBInstances`、`cloudwatch:GetMetricStatistics`、`pricing:GetProducts`(Stage 0 で問題がなければ) |
  | `tag` | `sts:GetCallerIdentity`、上の Describe 系 API + `ec2:CreateTags`、`rds:AddTagsToResource`、`elbv2:AddTags` |
  | `execute`(stop / delete) | `sts:GetCallerIdentity`、上の Describe 系 API + `ec2:StopInstances`、`ec2:TerminateInstances`、`ec2:DeleteVolume`、`ec2:ReleaseAddress`、`ec2:DeleteNatGateway`、`rds:StopDBInstance`、`rds:DeleteDBInstance`、`elbv2:DeleteLoadBalancer`、`ec2:CreateTags`、`rds:AddTagsToResource`(どちらも CleanupStoppedAt 用) |
- `tag` と `execute` では、`resource-explorer-2:Search`、`cloudwatch:GetMetricStatistics`、`pricing:GetProducts` を呼ばない(許可リストに入れない)。
- タグ付けの API を呼ぶとき、フックで `Tags` のキーも確かめる。`tag` モードで許すのは `CleanupCandidate` と `CleanupReason`、`execute` モードで許すのは `CleanupStoppedAt` だけ。違反したら `DisallowedApiCallError` を出す。
- 受け入れ条件:
  - `read` モードで `ec2:RunInstances`、`cloudwatch:GetMetricData`、`ce:GetCostAndUsage`、`s3:ListObjectsV2` を呼ぶと、`DisallowedApiCallError` になる。
  - `tag` モードで `Keep` キーや `CleanupStoppedAt` キーを付けようとすると、`DisallowedApiCallError` になる。
  - `tag` モードで `ec2:TerminateInstances` を呼ぶと、`DisallowedApiCallError` になる。

### F-4 リソースの発見(`discovery.py`。scan だけが使う)
- 役割は「種類ごとに、調べるリージョンの集合を決める」ことだけにする。状態とタグの正しい値は、F-5 のコレクタが Describe 系 API で取る(Resource Explorer のインデックスには反映の遅れがあるため)。
- インターフェース:
  ```python
  @dataclass(frozen=True)
  class DiscoveryResult:
      method: Literal["resource_explorer", "explicit_regions", "fallback_all_regions"]
      regions_by_type: dict[ResourceType, frozenset[str]]
      warnings: tuple[str, ...]

  def discover(session_factory, config, clock) -> DiscoveryResult: ...
  ```
- 手順:
  1. 設定の `regions` か `--region` が指定されていれば、Resource Explorer は呼ばない。6 種類すべてについて、そのリージョンの集合を返す(`explicit_regions`)。
  2. そうでなければ、`resource_explorer_region` で、種類ごとに `Search(QueryString="resourcetype:<t>")` をページ送りしながら呼び、結果のリージョンの集合を作る。
  3. 応答の `Count.Complete` が `false`(上限の 1000 件に当たった、未確認)の種類があれば、その種類について、有効なリージョン(`ec2:DescribeRegions`。オプトインしていないリージョンは含まれない)ごとに `Search(QueryString="resourcetype:<t> region:<r>", MaxResults=1)` を呼ぶ。1 件でも返ったリージョンを集合に入れる。こうすると件数の上限に当たらない。
  4. `Search` が `ClientError` を返したとき(インデックスがない、ビューがない、など。エラーコードは問わない)は、6 種類すべてについて、有効なリージョン全部を返す(`fallback_all_regions`)。警告に `resource-explorer unavailable: <Error.Code>` を入れる。コレクタはそのリージョンで Describe 系 API を全件取得する。
- 種類と resourcetype の対応(名前は未確認。Stage 0 で `ListSupportedResourceTypes` を使って確かめる):
  | 種類 | resourcetype |
  | --- | --- |
  | EBS | `ec2:volume` |
  | EIP | `ec2:elastic-ip` |
  | NAT | `ec2:natgateway` |
  | ELB | `elasticloadbalancing:loadbalancer/app` と `/net` |
  | EC2 | `ec2:instance` |
  | RDS | `rds:db` |
- 警告は `scan` の要約にも出し、scan の結果の `discovery.warnings` にも残す。
- moto が `resource-explorer-2` に対応しているかは未確認。対応していなければ、テストでは `Stubber` と `tests/fixtures/` の応答の見本を使う。
- 受け入れ条件: (a) `Complete=true` の応答から正しいリージョンの集合が出る。(b) `Complete=false` で、リージョンごとの呼び出しに切り替わる。(c) `ClientError` で `fallback_all_regions` になる。(d) `--region` の指定で `Search` を 1 回も呼ばない。

### F-5 コレクタ(`collectors/*.py`)
各リージョンで Describe 系 API を全件取得し、`Resource` に正規化する。`Resource` が持つのは、種類、リージョン、ID、ARN、名前、全タグ、状態、作成日時(または起動日時)、種類ごとの属性(`attrs`)である。

| 種類 | API と取り出す条件 | `attrs` に入れる属性 | 作成日時に使う値 |
| --- | --- | --- | --- |
| EBS | `DescribeVolumes`。`status=available` のものだけ | `Size`、`VolumeType`、`Iops`、`Throughput` | `CreateTime` |
| EIP | `DescribeAddresses`。`AssociationId` がないものだけ。`Domain=vpc` が前提 | `PublicIp` | なし(判定に使わない) |
| NAT | `DescribeNatGateways`。`state=available` のものだけ | `ConnectivityType` | `CreateTime` |
| ELB | `DescribeLoadBalancers`。`Type` が `application` か `network` のものだけ。GWLB は `unsupported_lb_type` でスキップする。タグは `DescribeTags`、ターゲットは `DescribeTargetGroups(LoadBalancerArn=...)` → `DescribeTargetHealth` で取る | `Type`、`registered_target_count` | `CreatedTime` |
| EC2 | `DescribeInstances`。`running` のものすべてと、`stopped` で `CleanupCandidate=true` が付いたもの | `InstanceType`、`Platform` | `LaunchTime` |
| RDS | `DescribeDBInstances`。`available` のものすべてと、`stopped` で `CleanupCandidate=true` が付いたもの。`DBClusterIdentifier` があるもの、または `Engine` が `aurora` で始まるものは、`aurora` でスキップする | `Engine`、`DBInstanceClass`、`MultiAZ`、`AllocatedStorage`、`StorageType`、`Iops` | `InstanceCreateTime` |

- Keep の判定は、コレクタの直後に一か所で行う(`tags.is_protected`)。Keep=true のリソースは `skipped`(`keep_tag`)に入れ、ルールにも料金の計算にも渡さない。
- 候補済みかどうかの判定(F-7 の「候補済みのリソース」の表)は、メトリクスを取る前に行う。候補済みのリソースにはメトリクスの API を呼ばない。

### F-6 メトリクス(`metrics.py`)
- 呼ぶのは `GetMetricStatistics` だけ。`Period=86400`、期間は `period_days` 日。1 リソースあたりの呼び出しは次のとおり。
  | 種類 | 呼び出し |
  | --- | --- |
  | EC2(running で、候補でないもの) | `AWS/EC2` の `CPUUtilization`(Maximum)。`ec2_network_bytes_max >= 0` のときは、加えて `NetworkIn` と `NetworkOut`(Sum) |
  | RDS(available で、候補でないもの) | `AWS/RDS` の `DatabaseConnections`(Maximum)と `CPUUtilization`(Maximum。表示用) |
  | NAT(候補でないもの) | `AWS/NATGateway` の `BytesInFromSource` と `BytesInFromDestination`(Sum) |
- 呼んだ回数を数える。1 リソースに必要な回数を足すと `max_metric_requests` を超えるときは、そのリソースから後を `metric_budget_exhausted` でスキップする(呼ばない)。
- データポイントが 0 件のときは、値を `None` にする。ルールの側で `no_metrics` として扱う。

### F-7 判定ルール(`rules.py`、純粋関数)

**候補でないリソース**(`CleanupCandidate=true` がない)
| 種類 | 候補にする条件 | 推奨アクション | 理由コード |
| --- | --- | --- | --- |
| EBS | `available` | Delete | `ebs-unattached` |
| EIP | `AssociationId` がない | Delete | `eip-unassociated` |
| NAT | 作成から `period_days` 日以上たっていて、バイト数の合計が `nat_bytes_max` 以下 | Delete | `nat-idle` |
| ELB | すべてのターゲットグループで、登録済みのターゲットが 0 件(ターゲットグループ自体がない場合も含む) | Delete | `elb-no-targets` |
| EC2 | `running`。起動から `period_days` 日以上たっていて、最大 CPU が `ec2_cpu_max_percent` 未満。ネットワークの判定が有効なら、加えて合計が `ec2_network_bytes_max` 以下 | Stop | `ec2-low-cpu` |
| RDS | `available`。作成から `period_days` 日以上たっていて、最大接続数が `rds_connections_max` 以下 | Stop | `rds-no-connections` |

**候補済みのリソース**(`CleanupCandidate=true` がある。メトリクスは使わない)
| 種類 | 状態 | `CleanupStoppedAt` | 結果 |
| --- | --- | --- | --- |
| EC2 | `stopped` | 読めて、`stop_wait_days` 日以上たっている | Finding: Delete / `ec2-stopped-elapsed` |
| EC2 | `stopped` | 読めて、たっていない | Skipped: `stop_wait_pending` |
| EC2 | `stopped` | ない、または読めない | Skipped: `stop_not_recorded` |
| EC2 | `running` | ある | Skipped: `restarted_after_stop` |
| EC2 | `running` | ない | Skipped: `already_candidate` |
| RDS | `stopped` か `available` | 読めて、`stop_wait_days` 日以上たっている | Finding: Delete / `rds-stopped-elapsed`。`available` のときは、状態の列に「停止後に起動(自動起動の可能性)」と書く |
| RDS | `stopped` か `available` | 読めて、たっていない | Skipped: `stop_wait_pending` |
| RDS | `stopped` | ない、または読めない | Skipped: `stop_not_recorded` |
| RDS | `available` | ない | Skipped: `already_candidate` |
| EBS / EIP / NAT / ELB | — | — | Skipped: `already_candidate`(削除を待っている) |

- 境界: 「未満」と「以下」は上の表のとおりにする。経過時間はデータ 4 のとおり、秒の精度で比べる。期間に満たないものは `too_new`、条件を満たさないものは `active` でスキップする。
- 理由の文面(`reason_text`)は、素案の表の例に合わせて日本語で作る。
  - EIP は「どのインスタンスや ENI にも関連付けられていない。パブリック IPv4 アドレスは関連付けの有無によらず時間単位で課金されるため(2024 年 2 月から、未確認)、使われないまま料金だけが発生している」という趣旨にする。
  - `ec2-stopped-elapsed` / `rds-stopped-elapsed` は「候補として <CleanupStoppedAt の日付> に停止し、<N> 日経過。削除の承認を求める」という趣旨にする。
- 状態 / メトリクス列の例: `available (未接続)`、`running (最大CPU 1.2% / 7日)`、`通信量 0 Byte (7日間)`、`登録ターゲット 0 件`、`available (最大接続数 0 / 7日)`、`stopped (2026-10-08 から停止)`
- 受け入れ条件: 次の表駆動テストが通る。
  - CPU 5.0% → 候補にしない。4.99% → 候補にする。
  - 接続数 0 → 候補にする。1 → 候補にしない。
  - 起動から 7 日ちょうど → 対象。6 日 23 時間 59 分 59 秒 → `too_new`。
  - 候補済みの EC2: 停止から 7 日ちょうど → Delete。1 秒足りない → `stop_wait_pending`。タグがない → `stop_not_recorded`。running でタグがある → `restarted_after_stop`。
  - 候補済みの RDS で、`available` かつ経過済み → Delete。

### F-8 `scan` サブコマンド
```
aws-cost-cleaner scan [--profile P] [--config F] [--region R ...] [--output PATH] [しきい値の上書き]
```
- 処理の流れ: `sts:GetCallerIdentity` → F-4 → F-5 → 候補済みかどうかの判定 → F-6 → F-7 → 料金の計算 → scan の結果 JSON を書き出す。標準出力には要約を出す(候補の件数、推奨アクション別の件数、スキップの理由別の件数、メトリクス API の呼び出し回数、発見の方法と警告)。
- 件数は切り詰めない。対象になったものはすべて `findings` に入れる。件数を制御するのは収集の側(しきい値、`--region`、`max_metric_requests`)の責務にする(J-20261013-046)。
- モードは `read`。

### F-9 `report` サブコマンド
```
aws-cost-cleaner report --scan out/scan-<id>.json [--cost-csv PATH ...] [--output-dir out/]
```
- 出力: `report-<scan_id>.md` と `approval-<scan_id>.json`。承認 JSON の項目は、Markdown の表と同じ順・同じ件数にし、すべて `approved: false` にする。
- Markdown の表の列は素案のとおりにする: `リージョン | サービス | リソースID / 名前 | 状態 / メトリクス | 推定月額コスト | 推奨アクション | 判断理由`
  - リソース ID と名前は `vol-0123 / tmp-data` の形にする。名前がなければ ID だけにする。
  - 金額は `$8.00` の形。`null` なら `不明`。
  - アクションは太字(`**Delete**`)にする。
  - セルの中の `|` は `\|` に、改行は空白にエスケープする。
- 表は 2 つに分ける。
  1. 「新しい候補」: 候補でないリソースから出た Finding
  2. 「停止後の削除の承認」: `ec2-stopped-elapsed` / `rds-stopped-elapsed` の Finding。表の上に「この表の項目を承認すると、`delete` で終了・削除される。RDS は最終スナップショットなしで削除される」と注記する
- 並べ方(どちらの表も同じ):
  - CSV がないとき: (1) 推定月額の降順(`null` は最後) → (2) リージョン → (3) `resource_id`
  - CSV があるとき: (1) リソースのリージョンの `region_totals` の降順 → (2) リソースの種類の `type_totals` の降順 → (3) 推定月額の降順(`null` は最後) → (4) リージョン → (5) `resource_id`
    - CSV にないリージョンや種類の金額は 0 とみなす。
    - リージョン別の CSV だけが渡されたときは、すべての `type_totals` を 0 とみなす。サービス別だけのときは、すべての `region_totals` を 0 とみなす。結果として、渡されていない種類のキーは並べ方に効かない。
- 表の下に付けるもの:
  - 料金の注記(出典と取得した日付、概算であること、含めていない料金)
  - CSV を渡したときは、使ったファイル名と、その種類(リージョン別かサービス別か)
  - スキップの一覧(理由ごとの件数と ID)。Keep で除外したものや、停止の待ち期間中のものを、人間が確かめられるようにする
  - scan の発見の方法と警告(F-4)
- 表を作る処理では件数を切り詰めない(J-20261013-046)。
- AWS の API は呼ばない。ネットワークにも出ない。
- 受け入れ条件:
  - 3 件の scan 結果の見本から作った Markdown が、`tests/fixtures/expected_report.md` と一致する(ゴールデンテスト)。
  - 承認 JSON の項目が、すべて `approved: false` になっている。
  - リージョン別の CSV とサービス別の CSV を両方渡したときの並びが、上の (1)〜(5) の順になる(見本で確かめる)。

### F-10 `tag` サブコマンド
```
aws-cost-cleaner tag --approval out/approval-<id>.json [--profile P(AgentRole)] [--apply]
```
- 処理の流れ: 承認ファイルを検証する → アカウントを照合する → `approved: true` の項目ごとに、`lookup.py` で今の状態を取り直す(F-11 の「対象の取り直し」と同じ関数)。
- 確かめ直すのは、Describe 系 API で取れる状態とタグだけにする。**メトリクス(CPU、通信量、接続数)は取り直さない**。メトリクスの判断は承認したときのレポートに基づいており、ここで取り直すと承認したものと違う判断になるためである。
- 確かめること(どれかに当たれば、その項目は `failed`。タグは付けない):
  | 条件 | 例外 |
  | --- | --- |
  | リソースが見つからない | `StateMismatchError` |
  | Keep=true が付いている | `ProtectedResourceError` |
  | Stop の項目: EC2 が `running` でない、RDS が `available` でない(すでに `stopped` の場合を含む) | `StateMismatchError` |
  | Stop の項目: すでに `CleanupCandidate=true` が付いている | なし。`skipped`(付け直さない) |
  | Delete の項目(EBS / EIP / NAT / ELB): EBS が `available` でない、EIP に `AssociationId` がある、NAT が `available` でない | `StateMismatchError` |
  | Delete の項目(EC2 / RDS): `CleanupCandidate=true` がない | `NotCandidateError` |
  | Delete の項目(EC2): `stopped` でない。(RDS): `stopped` でも `available` でもない | `StateMismatchError` |
  | Delete の項目(EC2 / RDS): `CleanupStoppedAt` がない、読めない、待ち期間が過ぎていない | `WaitPeriodNotElapsedError` |
- Stop の項目で、すでに `stopped` になっているものを `StateMismatchError` にする理由: 承認の後で、人が手で止めた可能性がある。その場合、ツールが「低稼働だから止める」という判断を記録するのは正しくない。人間がレポートを見直し、次の scan に任せる。
- 確認を通ったものには、次のタグを付ける。
  - `CleanupCandidate=true`(Stop の項目、または EBS / EIP / NAT / ELB の Delete の項目のとき)
  - `CleanupReason=<reason_code>`(すべての項目。EC2 / RDS の Delete の項目では、`ec2-low-cpu` などから `ec2-stopped-elapsed` などに書き換わる)
- タグ付けの API: EC2 / EBS / EIP / NAT は `ec2:CreateTags`(`Resources=[<id>]`)、RDS は `rds:AddTagsToResource`(`ResourceName=<arn>`)、ELB は `elbv2:AddTags`(`ResourceArns=[<arn>]`)。
- 標準は dry-run で、付ける予定のタグの一覧(`planned`)だけを出す。`--apply` を付けたときだけ API を呼ぶ。
- 1 件で失敗しても残りは続ける。`failed` があれば終了コードは 1 にする。AWS の `ClientError` は `AwsOperationError` として記録する(`aws_error_code` に `Error.Code` を入れる)。
- モードは `tag`。

### F-11 `stop` サブコマンド
```
aws-cost-cleaner stop --approval out/approval-<id>.json [--profile P(ExecutorRole)] [--apply]
```
- **対象**: 承認ファイルの項目のうち、`approved: true` で、`recommended_action` が `Stop` のもの(EC2 / RDS)。それ以外の項目は数えるだけで無視し、要約に「Stop 以外の項目 N 件は対象外」と出す。
- **対象の取り直し**(`lookup.py`。tag / stop / delete で共通): Resource Explorer は使わない。項目の `region` で、ID を指定して Describe 系 API を **1 件ずつ** 呼ぶ(1 回の呼び出しに複数の ID を渡すと、1 件が見つからないだけで全体がエラーになるため)。
  | 種類 | 呼び出し | 見つからないときのエラーコード(未確認。moto と Stage 0 で確かめる) |
  | --- | --- | --- |
  | EBS | `DescribeVolumes(VolumeIds=[id])` | `InvalidVolume.NotFound` |
  | EIP | `DescribeAddresses(AllocationIds=[id])` | `InvalidAllocationID.NotFound` |
  | NAT | `DescribeNatGateways(NatGatewayIds=[id])` | `NatGatewayNotFound`。または `deleted` の状態で返る |
  | ELB | `DescribeLoadBalancers(LoadBalancerArns=[arn])` + `DescribeTags` + ターゲット | `LoadBalancerNotFound` |
  | EC2 | `DescribeInstances(InstanceIds=[id])` | `InvalidInstanceID.NotFound`。または `terminated` の状態で返る |
  | RDS | `DescribeDBInstances(DBInstanceIdentifier=id)` | `DBInstanceNotFound` |

  見つからないときのエラーコードと、`deleted` / `terminated` の状態は、`StateMismatchError` として `failed` にする。それ以外の `ClientError` は `AwsOperationError` として `failed` にする。
- **共通の確認**: Keep=true → `ProtectedResourceError`。`CleanupCandidate` が `"true"` でない → `NotCandidateError`。どちらも `failed`。
- **状態ごとの振る舞い**:
  | 状態 | `CleanupStoppedAt` | 振る舞い | status |
  | --- | --- | --- | --- |
  | `running` / `available` | ない | 停止する → `CleanupStoppedAt=<今の UTC>` を付ける | `done` |
  | `stopped` | ない | タグだけを付ける(`tag_stopped_at`) | `done` |
  | `stopped` | ある | 何もしない | `skipped` |
  | `running` / `available` | ある | 何もしない。停止の後で起動された(人が起動したのか、RDS が自動で起動したのか、ツールでは区別しない)。もう一度は停止せず、`CleanupStoppedAt` も上書きしない | `failed`(`StateMismatchError`) |
  | その他(`stopping`、`pending`、`starting` など) | — | 何もしない | `failed`(`StateMismatchError`) |
- 2 行目(`stopped` で `CleanupStoppedAt` がない)に当たるのは、次の 2 つの場合である。F-10 で `stopped` を拒んでいるので、承認したときにすでに止まっていた場合には当たらない。
  1. 前回の `stop --apply` で、停止には成功したが、`CleanupStoppedAt` のタグ付けに失敗した場合(そのときは `failed` として報告済み)。もう一度 `stop` を実行すると、タグだけが付いて回復する。
  2. `tag --apply` の後で、`stop` の前に、人が手で止めた場合。このときは今の時刻を記録する。実際に止めた時刻より遅くなるので、待ち期間は長めになる(安全な側)。
- 停止に成功してタグ付けに失敗したときは、停止を `done`、タグ付けを `failed`(`AwsOperationError`)として 2 行で報告する。
- RDS の停止は `StopDBInstance(DBInstanceIdentifier=id)` で、`DBSnapshotIdentifier` は渡さない(スナップショットを作らない)。
- 標準は dry-run。`--apply` を付けたときだけ実行する。モードは `execute`。

### F-12 `delete` サブコマンド
```
aws-cost-cleaner delete --approval out/approval-<id>.json [--profile P(ExecutorRole)] [--apply]
```
- **対象**: 承認ファイルの項目のうち、`approved: true` で、`recommended_action` が `Delete` のもの。EC2 / RDS の Delete の項目は、許可表(データ 2)により、`ec2-stopped-elapsed` / `rds-stopped-elapsed` の理由コード、つまり停止後の再 scan で出た項目に限られる。最初の `ec2-low-cpu` / `rds-no-connections` の承認ファイル(Stop の項目)を渡しても、何も削除されない。
- **対象の取り直しと共通の確認**: F-11 と同じ(`lookup.py`、Keep、`CleanupCandidate`)。
- **種類ごとの前提条件**(満たさないときは `failed`):
  | 種類 | 前提条件 | 満たさないときの例外 | API |
  | --- | --- | --- | --- |
  | EBS | `available` | `StateMismatchError` | `DeleteVolume(VolumeId=id)` |
  | EIP | `AssociationId` がない | `StateMismatchError` | `ReleaseAddress(AllocationId=id)` |
  | NAT | `available` | `StateMismatchError` | `DeleteNatGateway(NatGatewayId=id)`(関連付けられていた EIP は解放されずに残る。次の scan で EIP として検出される) |
  | ELB | なし | — | `DeleteLoadBalancer(LoadBalancerArn=arn)`(ターゲットグループは残す) |
  | EC2 | `stopped` | `StateMismatchError` | `TerminateInstances(InstanceIds=[id])` |
  | EC2 | `CleanupStoppedAt` が読めて、`stop_wait_days` 日以上たっている(データ 4) | `WaitPeriodNotElapsedError` | (同上) |
  | RDS | `stopped` か `available` | `StateMismatchError` | `DeleteDBInstance(DBInstanceIdentifier=id, SkipFinalSnapshot=True)` |
  | RDS | `CleanupStoppedAt` が読めて、`stop_wait_days` 日以上たっている | `WaitPeriodNotElapsedError` | (同上) |
- RDS について:
  - 停止した RDS は 7 日で自動的に起動するはずなので(未確認)、`available` も対象にする。そのときは、`Outcome.note` に「停止後に起動している(自動起動の可能性)」と書き、dry-run の出力にも出す。
  - `DeleteAutomatedBackups` は指定せず、API の標準値に任せる。
  - 最終スナップショットは作らない(Q-9)。必要なら、人間がコンソールで取ってから `--apply` する。dry-run の出力の最後にも、この注意を出す。
- **AWS のエラーの扱い**: 削除の API が `ClientError` を返したときは、`AwsOperationError` として `failed` にし、`aws_error_code` に `Error.Code` を入れて、次の項目に進む。削除保護(EC2 の `DisableApiTermination`、RDS の `DeletionProtection`、ELB の `deletion_protection.enabled`)が有効なときも、この扱いになる。ツールは保護を外さない(そのための権限もない)。削除保護のときのエラーコードは、EC2 が `OperationNotPermitted`、RDS が `InvalidParameterCombination`、ELB が `OperationNotPermitted` のはずである(未確認)。テストではエラーコードの種類に依存せず、`Stubber` で任意の `ClientError` を返して、「`failed`、`aws_error_code` が入る、ほかの項目は続く、終了コード 1」を確かめる。
- 標準は dry-run。`--apply` を付けたときだけ実行する。モードは `execute`。

### F-13 IAM テンプレート(`infra/iam-roles.yaml`)
- パラメータ:
  - `AgentTrustedPrincipalArns`(CommaDelimitedList)
  - `ExecutorTrustedPrincipalArns`(CommaDelimitedList)
  - `RoleNamePrefix`(標準値 `aws-cost-cleaner`)
- **AgentRole**(AI 用。読み取りと候補タグの付与だけ)
  - マネージドポリシー: `arn:aws:iam::aws:policy/ReadOnlyAccess`
  - インラインポリシー:
    | Sid | 内容 |
    | --- | --- |
    | `DenyPaidAndHighVolumeReadAPIs` | 素案のとおり |
    | `AllowSingleMetricCheckWithinFreeTier` | 素案のとおり。Stage 0 で問題がなければ `pricing:GetProducts` を加える |
    | `AllowTaggingForCleanupCandidate` | 素案のとおり。`aws:TagKeys` は `CleanupCandidate` と `CleanupReason` だけ |
    | `ProtectCriticalResources` | 素案のとおり |
  - 素案の `AllowStopAndDeleteOnlyWithCandidateTag` は**入れない**(Q-2)。
- **ExecutorRole**(人間だけが使う)
  - マネージドポリシー: `ReadOnlyAccess`
  - インラインポリシー:
    | Sid | 内容 |
    | --- | --- |
    | `DenyPaidAndHighVolumeReadAPIs` | AgentRole と同じ |
    | `AllowSingleMetricCheckWithinFreeTier` | AgentRole と同じ |
    | `AllowStopAndDeleteOnlyWithCandidateTag` | 素案から `ec2:DeleteSnapshot` を外したもの(Q-5) |
    | `AllowTaggingStoppedAt` | `ec2:CreateTags` と `rds:AddTagsToResource`。条件は `ForAllValues:StringEquals aws:TagKeys = ["CleanupStoppedAt"]` かつ `StringEquals aws:ResourceTag/CleanupCandidate = "true"` |
    | `ProtectCriticalResources` | AgentRole と同じ |
  - `CleanupStoppedAt` を付けられるのは ExecutorRole だけにする。AI が停止日を偽って、待ち期間を飛ばせないようにするためである。Q-8 の「許可するタグキーに加える」は、実行用ロールの許可に加えることと解釈した。
- 信頼ポリシーには、それぞれのパラメータの ARN だけを書く。
- 承認の強制について: IAM では「人間が承認したか」まではわからない(AgentRole は承認されていないリソースにも `CleanupCandidate` を付けられる)。人間の承認は、(1) 停止・削除は ExecutorRole でしかできない、(2) `stop` / `delete` は人間が渡す承認ファイルの `approved: true` の項目しか扱わない、の 2 つで守る。
- 各アクションが条件キー(`aws:ResourceTag/*`、`aws:TagKeys`)に対応しているかは未確認。Stage 0 で、IAM のサービス認可リファレンスを使って確かめる。対応していないアクションが見つかったら、ガードがすり抜ける恐れがあるので、実装を進める前に開発者に報告する。

### F-14 ドキュメント
- `docs/setup.md`: 素案 §3 の手順(Resource Explorer のクイックセットアップ、Keep タグの付け方)、CFN のデプロイ(`--capabilities CAPABILITY_NAMED_IAM`)、AI 用と実行用のプロファイルの設定例。
- `docs/agent-prompt.md`: 素案 §6 のプロンプトを書き換えたもの。要点は次のとおり。
  - 使ってよいのは、AI 用のプロファイルでの `aws-cost-cleaner scan` / `report` / `tag` だけ。`aws` CLI を直接呼ばない。
  - 承認ファイルの `approved` を書き換えない(書き換えるのは人間)。
  - `tag` は、まず dry-run の結果を見せる。人間に指示されてから `--apply` を付ける。
  - `stop` と `delete` は実行しない(人間が実行用ロールで実行する)。
  - 調査の手順: CSV があれば受け取る → `scan` → `report` → 表を見せて、指示を待つ。
  - 停止から `stop_wait_days` 日たった後に `scan` / `report` を頼まれたら、「停止後の削除の承認」の表を人間に見せる。
- `README.md`: ワークフロー(「目的と範囲」の表。誰が、どのロールで、どのコマンドを実行するか)と、安全設計の要約。

### F-15 料金の更新スクリプト(`scripts/update_prices.py`)
- `price_list.py` を使って同梱の表を作り直し、`checked_at` を今日の日付にする。
- 保守用で、CLI のサブコマンドには含めない。

---

## 既存のコードからの移行(既存のリポジトリがあるとき)

- 今のリポジトリにあるのは `README.md`(タイトル `# aws-cost-cleaner` と、1 行の説明「AWSリソース棚卸し・コスト最適化AIエージェント」)だけで、コードはない。コミットは `a8dd1a3 Initial commit` の 1 つで、ローカルのブランチは `main` と `develop` がある。
- **残すもの**: `README.md`。タイトルと説明文はそのまま残し、中身を F-14 で書き足す。
- **捨てるもの**: なし。
- **作り直す範囲**: なし。すべて新しく作る。

---

## 検証の方針(Stage 0、受け入れテストで確かめること、人間が見ること)

### Stage 0(実装の前。結果は `docs/verification-notes.md` に、URL、確認した日付、結論の形で残す)

**公式ドキュメントでの確認(J-20261008-001)**。確かめられなかった点は「未確認」と書いたまま残す。

| # | 確かめること | 結果によって変わるもの |
| --- | --- | --- |
| 1 | Resource Explorer の `Search` は無料か | 無料でなければ、F-4 を常に `fallback_all_regions`(Describe 系 API の全リージョン巡回)にすることを開発者と相談する |
| 2 | 1 回の Search で返る件数の上限(1000 件)と、`Count.Complete` の意味 | F-4 の手順 3 |
| 3 | `GetMetricStatistics` は、CloudWatch API の無料枠(月 100 万リクエスト)に入るか | `max_metric_requests` の標準値 |
| 4 | `GetMetricData` には無料枠がないか | 素案のとおり Deny を続けるか |
| 5 | Price List Query API(`pricing:GetProducts`)は無料か。ReadOnlyAccess に含まれるか。エンドポイントのリージョン | `use_price_list_api` の標準値、IAM への追加 |
| 6 | Price List のフィルタ属性名と値 | `price_list.py` の定数 |
| 7 | Cost Explorer のコンソールで、グループ化の条件を 1 つしか選べないか。出力される CSV の列の形式。サービス名(EBS、NAT、EIP がどのサービス名に入るか) | `costcsv.py` の実装と `SERVICE_MAP`。開発者に見本の CSV を出してもらう |
| 8 | パブリック IPv4 の課金(2024 年 2 月から、関連付けていても課金されるか) | EIP の理由の文面 |
| 9 | ターゲットグループは課金されないか | 対象外にしてよいか |
| 10 | 停止した RDS は 7 日で自動的に起動するか | F-7 と F-12 の RDS の扱い |
| 11 | IAM のサービス認可リファレンスで、F-13 で使うアクションが `aws:ResourceTag/CleanupCandidate`、`aws:ResourceTag/Keep`、`aws:TagKeys` に対応しているか | 対応していなければ、実装を進める前に開発者に報告する |
| 12 | `CleanupReason` などのタグ値に使える文字(EC2、RDS、ELB) | 理由コードの形式 |
| 13 | Resource Explorer の resourcetype 名(F-4 の表) | `discovery.py` の定数 |
| 14 | ID を指定した Describe 系 API で、リソースが見つからないときのエラーコード(F-11 の表)と、削除保護のときのエラーコード(F-12) | `lookup.py` の定数。報告の文面 |

**ライブラリの確認**: moto が `resource-explorer-2`、`pricing`、`rds.stop_db_instance`、`elbv2.describe_target_health`、`cloudwatch.get_metric_statistics` に対応しているか、ID を指定した Describe で見つからないときに実際と同じエラーコードを返すかを、実際に試して確かめる。対応していないものは `Stubber` に切り替える。

### 機械で確かめること(CI で毎回)
1. `uv run ruff check` と `uv run ruff format --check`(`boto3.client`、`boto3.Session`、`datetime.now` を直接使っていないことの検査を含む)
2. `uv run mypy --strict src tests`
3. `uv run cfn-lint infra/iam-roles.yaml`
4. `uv run pytest`

### pytest で確かめること

**単体テスト**
- `rules.py`: F-7 の境界値を表駆動で確かめる(候補でないもの、候補済みのもの)。
- `config.py`: 標準値、TOML での上書き、CLI での上書き、範囲外の値と未知のキーで `ConfigError` になること。
- `costcsv.py`: 見本の CSV、空のファイル、ヘッダーだけ、空のセル、BOM つき、数値にできないセル(`CostCsvError`)、リージョンとサービスが混ざった列(`CostCsvError`)、同じ種類の CSV を 2 つ渡したときの合算、`SERVICE_MAP` にないサービス名の無視。
- `approval.py`: 正常、`approved` が文字列(`ApprovalFileError`)、重複、未知の種類、許可表にない組み合わせ(`ec2_instance` + `Delete` + `ec2-low-cpu` → `ApprovalFileError`)、ELB / RDS で `arn` が空、アカウントの不一致(`AccountMismatchError`)。
- `estimate.py`: 種類ごとの計算(RDS はストレージを含む。停止中の EC2/RDS の扱い)。料金がないときに `null` になること。使う料金の優先順位(API → キャッシュ → 同梱 → 不明)。
- `guard.py`: F-3 の受け入れ条件。
- `discovery.py`: F-4 の受け入れ条件 (a)〜(d)。
- `report.py`: ゴールデンテスト。100 件を入れて 100 行が出ること(切り詰めないこと)。`|` のエスケープ。2 つの表への振り分け。CSV の有無と種類による並び。

**受け入れテスト(moto と Stubber を使い、CLI の `main()` を呼ぶ。時刻は注入した Clock で固定する)**
1. `scan`: 6 種類それぞれに、候補になるものとならないものを作ると、候補になるものだけが `findings` に入る。
2. `scan`: Keep=true のリソース(6 種類それぞれ)は `findings` に入らず、`skipped`(`keep_tag`)に入る。Keep の値が `True` でも除外される。
3. `scan`: Aurora のインスタンスと GWLB は対象外になる。
4. `scan`: `max_metric_requests=1` にすると、2 件目以降が `metric_budget_exhausted` になり、`GetMetricStatistics` の呼び出しが 1 回だけになる。
5. `scan` 全体を通して、呼んだ操作名が `read` の許可リストに含まれる(フックで記録し、集合で比べる)。`GetMetricData` と `ce:*` は一度も呼ばない。
6. `scan`: `CleanupCandidate=true` と `CleanupStoppedAt`(7 日前)が付いた `stopped` の EC2 は、Delete / `ec2-stopped-elapsed` の Finding になり、メトリクスの API を呼ばない。6 日前なら `stop_wait_pending` になる。
7. `tag`: `--apply` がないときは、`CreateTags` などを呼ばない。タグが付いていないことを、moto の状態で確かめる。
8. `tag --apply`: `approved: true` のものだけに `CleanupCandidate` と `CleanupReason` が付く。Keep のものは `ProtectedResourceError` で、タグは付かない。状態が変わったもの(EBS が in-use、Stop の項目の EC2 が `stopped`)は `StateMismatchError` になる。終了コードは 1。`GetMetricStatistics` は呼ばない。
9. `tag --apply`(Delete の再承認): 候補済みで、停止から 7 日たった EC2 の `CleanupReason` が `ec2-stopped-elapsed` に書き換わる。
10. `stop --apply`: Stop の承認ファイルで、候補の EC2/RDS が停止し、`CleanupStoppedAt` が付く。`approved: false` の項目、タグのない項目(`NotCandidateError`)、Delete の項目は変わらない。`CleanupStoppedAt` があって `running` のものは停止されない(`StateMismatchError`)。`stopped` で `CleanupStoppedAt` がないものには、タグだけが付く。
11. `delete --apply`: EBS、EIP、NAT、ELB は消える。
12. `delete --apply`: 最初の承認ファイル(EC2 が Stop / `ec2-low-cpu`)を渡しても、EC2 は終了しない(**Stop の承認だけでは削除されない**)。
13. `delete --apply`: 停止後の再 scan の承認ファイル(Delete / `ec2-stopped-elapsed`、`approved: true`)を渡すと、`CleanupStoppedAt` から 7 日たった EC2 は終了する。同じ承認ファイルでも、実行の時点で 6 日 23 時間 59 分 59 秒しかたっていなければ、`WaitPeriodNotElapsedError` で残る。RDS は `SkipFinalSnapshot=True` で呼ばれる。
14. `delete --apply`: `Stubber` で削除の API に `ClientError` を返させると、その項目は `failed` になって `aws_error_code` が入り、ほかの項目は処理され、終了コードは 1 になる。
15. `tag` / `stop` / `delete`: `resource-explorer-2:Search` を一度も呼ばない。承認ファイルにあり、実際にはないリソースは `StateMismatchError` になる。
16. `stop` と `delete`: dry-run では状態が変わらない。
17. スナップショットを作る API(`CreateSnapshot`、`CreateDBSnapshot`)と、`Run*` / `Create*`(タグ以外)を、どのコマンドでも呼ばない。

**IAM の静的テスト**(`tests/infra/`。PyYAML でテンプレートを読む)
- AgentRole のどの Allow にも、`Stop*`、`Terminate*`、`Delete*`、`Release*`、`Create*`(`ec2:CreateTags` を除く)、`Run*`、`Start*` が含まれない。
- ExecutorRole のどこにも `ec2:DeleteSnapshot` がない。停止・削除の Allow には、すべて `aws:ResourceTag/CleanupCandidate = "true"` の条件が付いている。
- 両方のロールに `DenyPaidAndHighVolumeReadAPIs` と `ProtectCriticalResources` が付いている。
- タグ付けの Allow の `aws:TagKeys` が、AgentRole では `{CleanupCandidate, CleanupReason}`、ExecutorRole では `{CleanupStoppedAt}` と完全に一致する。

### 人間が見て決めること
- レポートの Markdown の読みやすさと、理由の文面(ゴールデンファイルを見て決める)。2 つの表の分け方と注記が、削除の承認として誤解なく読めるか。
- `ec2_network_bytes_max` の標準値(-1 で判定に使わない)でよいか。
- 検証用の AWS アカウントで、`docs/setup.md` の手順どおりにデプロイし、IAM ポリシーシミュレータか実際の操作で次を確かめる。
  - AgentRole では、停止と削除が拒否される。
  - AgentRole では、Keep のリソースにタグを付けられない。`CleanupStoppedAt` を付けられない。
  - ExecutorRole では、候補タグがないリソースを削除できない。
- `docs/agent-prompt.md` を Claude Code に読ませたとき、`aws` CLI を直接呼ばないこと。承認の前に `tag --apply` を実行しないこと。`stop` / `delete` を実行しないこと。

---

## 範囲外
- 複数のアカウントと Organizations
- スナップショット(EBS と RDS)の検出と削除。Aurora、Classic LB、GWLB、ターゲットグループ、S3、そのほか素案の手順にないサービス
- ツールによるスナップショットの作成(Q-9)
- 削除保護や Keep タグの付け外し、候補タグの取り外し(CLI にはその機能を持たせない)
- `stopped` の EC2/RDS のうち、このツールが止めていないもの(`CleanupCandidate` がないもの)の検出
- Cost Explorer API の利用、CUR の取り込み
- EC2 に付いた EBS、NAT のデータ処理料金、ELB の LCU、データ転送料金を、月額に精密に入れること(概算として注記する)
- 定期実行、通知、Web UI

---

## 提案(素案にはないが、検討するとよいこと)

1. **承認ファイルの改ざんの検知**: 今の設計では、AI が承認ファイルの `approved` を `true` に書き換えると、人間が `stop` / `delete` の dry-run を見落としたときに実行されてしまう。人間が承認の後に `approval-<id>.json` のハッシュを控え、`stop` / `delete` の dry-run がそのハッシュを表示する、といった仕組みで、人間が確かめやすくできる。
2. **AgentRole に `sts:AssumeRole` の明示的な Deny を入れる**: 信頼ポリシーだけでも分離はできるが、AI 用ロールから実行用ロールに切り替える経路を、二重に塞げる。
3. **ExecutorRole の信頼ポリシーで MFA を必須にする**(`aws:MultiFactorAuthPresent`): 「人間だけが使う」ことを、IAM の側で強められる。
4. **候補の取り消しコマンド**(`untag`): 今は、承認を取り消すにはコンソールでタグを外すしかない。
5. **`tag:TagResources` を IAM から外す**: このツールはサービスごとのタグ付け API しか使わない。また、`tag:*` に `aws:ResourceTag/Keep` の Deny が効くかどうかは未確認。使わない権限を減らせる。
6. **EC2 の API の `DryRun=True` で、事前に権限を確かめる**: `stop` と `delete` の dry-run のときに、IAM で拒否されるかどうかまで表示できる(EC2 の API に限る)。
7. **RDS の自動バックアップを残すかどうかの選択**: 最終スナップショットを取らないので、`DeleteAutomatedBackups=False` にすれば、復旧の手段が残る。ただし、保持している間はストレージの料金がかかる。
8. **実行ログの保存**: `--apply` で実行したことを `out/actions-<時刻>.jsonl` に残すと、後から追える。
9. **Keep の値の揺れを警告する**: `True` や `yes` などは、コードでは守られるが IAM では守られない。`scan` で警告を出し、`true` に直すよう促す。
10. **承認ファイルの有効期限**: 古い承認ファイルで `delete` を実行しないように、`scan_id` から N 日を過ぎたものは拒む。今は、実行のときに状態を取り直すことで守っている。
