# AWSリソース棚卸し・コスト最適化AIエージェント 設計書

## 1. 概要と目的
開発・検証用AWSアカウントにおいて、把握しきれなくなった放置リソースを特定し、安全に停止・削除することで継続的なコスト発生を防ぐ。
AIエージェントを活用して調査・仕分けを自動化するにあたり、**「AIの暴走によるリソース大量作成」「重要環境の誤削除」「調査API自体の課金（Cost Explorer API等）」の3大リスクをシステムレベル（IAMおよびワークフロー）で完全に排除すること**を目的とする。

---

## 2. コア設計原則（ガードレール）

1. **作成権限の完全排除（No-Create）**
   AIエージェント用のIAMには、いかなるリソースの `Create` / `Run` / `Start` 権限も付与しない。
2. **調査APIの完全無料化（Zero-API-Cost）**
   有料の `ce:*` (Cost Explorer API) や、大量課金リスクのある `s3:ListObjects*` 等をIAMレベルで明示的に拒否（Deny）する。コストの全体像は人間がコンソールから出力したCSVで与え、リソース検索には無料の **AWS Resource Explorer** および各サービスの `Describe` 系APIを使用する。
3. **タグベースの2段階削除（Tag-Gated Deletion）**
   AIが直接リソースを削除することは許可しない。AIは「削除候補タグ（`CleanupCandidate: true`）」を付与するのみとし、実際の停止・削除はそのタグが付いたリソースに対してのみ実行可能とする（ABAC: 属性ベースのアクセス制御）。
4. **保護リソースの絶対防衛（Explicit Deny）**
   `Keep: true` タグが付与されたリソースに対する変更・削除操作は、いかなる場合もIAMで明示的に拒否する。

---

## 3. 事前準備（初期セットアップ）

AIエージェントを稼働させる前に、人間（管理者）が1回だけ以下の設定を行う。

1. **AWS Resource Explorer の有効化（無料）**
   * AWSマネジメントコンソールから Resource Explorer を開き、「クイックセットアップ」で全リージョンのインデックス作成とアグリゲーターリージョン（例：`ap-northeast-1`）を設定する。
   * これにより、AIが全リージョンのリソースを1回の無料APIコールで横断検索できるようになる。
2. **絶対に残すリソースへの保護タグ付与（任意だが推奨）**
   * 消えては困る本番相当のリソースや共通基盤に `Keep: true` タグを付与しておく。
3. **AIエージェント用IAMロール（またはユーザー）の作成**
   * 次項「4. IAMポリシー設計」に従い、権限を絞ったIAMを用意する。

---

## 4. IAMポリシー設計

### 4.1. アタッチするAWSマネージドポリシー
* `ReadOnlyAccess` （基本の閲覧権限として利用。ただし下記のカスタムポリシーで有料APIを塞ぐ）

### 4.2. インラインポリシー（カスタムガードレール）
以下のJSONをインラインポリシーとしてアタッチする。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyPaidAndHighVolumeReadAPIs",
      "Effect": "Deny",
      "Action": [
        "ce:*",
        "s3:ListBucket",
        "s3:ListObjects*",
        "s3:GetObject*",
        "cloudwatch:GetMetricData",
        "logs:FilterLogEvents",
        "logs:StartQuery"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowSingleMetricCheckWithinFreeTier",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricStatistics",
        "resource-explorer-2:Search"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowTaggingForCleanupCandidate",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateTags",
        "rds:AddTagsToResource",
        "elasticloadbalancing:AddTags",
        "tag:TagResources"
      ],
      "Resource": "*",
      "Condition": {
        "ForAllValues:StringEquals": {
          "aws:TagKeys": ["CleanupCandidate", "CleanupReason"]
        }
      }
    },
    {
      "Sid": "AllowStopAndDeleteOnlyWithCandidateTag",
      "Effect": "Allow",
      "Action": [
        "ec2:StopInstances",
        "ec2:TerminateInstances",
        "ec2:DeleteVolume",
        "ec2:ReleaseAddress",
        "ec2:DeleteNatGateway",
        "ec2:DeleteSnapshot",
        "rds:StopDBInstance",
        "rds:DeleteDBInstance",
        "elasticloadbalancing:DeleteLoadBalancer"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/CleanupCandidate": "true"
        }
      }
    },
    {
      "Sid": "ProtectCriticalResources",
      "Effect": "Deny",
      "Action": [
        "ec2:*",
        "rds:*",
        "elasticloadbalancing:*",
        "s3:*",
        "tag:*"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/Keep": "true"
        }
      }
    }
  ]
}
```
> **設計のポイント:**
> * `cloudwatch:GetMetricData`（一括取得・課金されやすい）をDenyし、古い仕様だが単発取得向けの `cloudwatch:GetMetricStatistics` のみを許可することで、月100万リクエストの無料枠内で安全にCPU使用率などを確認させる。
> * タグ付け権限も `CleanupCandidate` と `CleanupReason` キーのみに制限し、既存の管理タグを上書きできないようにしている。

---

## 5. 運用ワークフロー（4ステップ）

### Step 1: 課金データのインプット【担当：人間 / コスト：$0】
1. AWSマネジメントコンソールで **Cost Explorer** を開く。
2. 期間を「過去1ヶ月（または直近数日）」、粒度を「日別または月別」、グループ化の条件を **「サービス」** および **「リージョン」** に設定し、**CSVをダウンロード**する。
3. ダウンロードしたCSVをAIエージェントに渡し、調査フェーズを開始する。

### Step 2: 放置リソースの特定と分析【担当：AI / コスト：$0】
AIエージェントはCSVから「課金が発生しているリージョンとサービス」に狙いを定め、以下の順序で無料のCLI/APIを実行して実態を調査する。

1. **全体把握（Resource Explorer）**
   ```bash
   aws resource-explorer-2 search --query-string "region:ap-northeast-1"
   ```
2. **「存在するだけで課金される放置リソース」の即時検出（Describe系API）**
   * **未アタッチのEBSボリューム:**
     `aws ec2 describe-volumes --filters Name=status,Values=available`
   * **未使用のElastic IP (EIP):**
     `aws ec2 describe-addresses` （`AssociationId` が存在しないものを抽出）
   * **放置されたNAT Gateway:**
     `aws ec2 describe-nat-gateways --filter Name=state,Values=available`
   * **ターゲットが空のALB/NLB:**
     `aws elbv2 describe-load-balancers` & `describe-target-health`
3. **稼働中インスタンスの利用実態調査（最小限のCloudWatch API）**
   * `running` 状態のEC2や `available` 状態のRDSに限り、`cloudwatch get-metric-statistics` を叩き、直近7日間の最大CPU使用率・ネットワーク通信量・DBコネクション数を確認する。

### Step 3: 削除候補の提案とタグ付け【担当：AI ➔ 人間承認】
1. AIエージェントは調査結果を以下のフォーマット（Markdown表）で人間へレポートする。

   | リージョン | サービス | リソースID / 名前 | 状態 / メトリクス | 推定月額コスト | 推奨アクション | 判断理由 |
   | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
   | ap-northeast-1 | EC2 (EBS) | vol-0123456789 | available (未接続) | $8.00 | **Delete** | どのEC2にもアタッチされておらず課金のみ発生 |
   | ap-northeast-1 | VPC (NAT) | nat-0987654321 | 通信量 0 Byte (7日間) | $45.00 | **Delete** | 直近1週間トラフィックなし。高額課金原因 |
   | us-east-1 | EC2 | i-111122223333 | running (最大CPU 1.2%) | $15.00 | **Stop** | 放置検証機の可能性大。まずは停止を推奨 |

2. 人間がレポートを確認し、対象リストに対して承認を出す。
3. AIは承認されたリソースに対し、`CleanupCandidate: true` タグを付与する。

### Step 4: 段階的クリーンアップの実行【担当：AI】
タグが付与されたリソースに対してのみ、AIの削除・停止権限が解放される。

1. **一次対応（Stop）:** EC2やRDSなど停止が可能なものは、まず `Stop` を実行し、1週間程度システムに異常が出ないか様子を見る。
2. **二次対応（Delete / Terminate）:** 未アタッチEBS、未使用EIP、NAT Gateway、および停止後に不要と確定したインスタンスを完全に削除する（必要に応じて削除前に最終スナップショットを取得する指示を人間から出す）。

---

## 6. AIエージェント用 システムプロンプト（テンプレート）

AIエージェント（Claude Code, Cursor, カスタムスクリプト等）に最初に読み込ませる指示書のテンプレート。

```text
あなたはAWSコスト最適化およびリソース整理を担当するクラウドインフラエージェントです。
現在、あなたには「ReadOnly権限」と「特定タグ（CleanupCandidate: true）が付いたリソースのみを停止・削除できる制限付きIAM権限」が付与されています。

【絶対遵守ルール】
1. 課金回避のため、`aws ce` (Cost Explorer) コマンド、`aws s3 ls s3://bucket-name --recursive`、`aws cloudwatch get-metric-data` は絶対に実行しないでください（IAMでも拒否されます）。
2. リソースを新規作成するコマンド（run-instances, create-* 等）は実行しないでください。
3. いきなり削除・停止コマンドを実行せず、必ず調査レポートを出力して人間の承認を得てから、まず対象に `CleanupCandidate=true` タグを付与してください。

【調査の手順】
1. 私が提供するCost ExplorerのCSVデータを分析し、コストが発生している「リージョン」と「サービス」を特定してください。
2. 該当リージョンに対して `aws resource-explorer-2 search` または各サービスの `describe-*` コマンドを実行し、特に以下の「放置されやすい課金リソース」を洗い出してください。
   - statusが `available` のEBSボリューム
   - インスタンスに紐付いていないElastic IP
   - 稼働中のNAT Gateway
   - 稼働中のEC2インスタンス、RDSインスタンス、ELB
3. 稼働中のEC2やRDSについては、必要なものに絞って `aws cloudwatch get-metric-statistics` を使用し、過去7日間のCPUUtilizationやDatabaseConnectionsの最大値を確認してください。
4. 調査結果をもとに、「リソースID」「推奨アクション（Stop または Delete）」「不要と判断した根拠」を一覧表にして提示し、私の指示を待ってください。
```