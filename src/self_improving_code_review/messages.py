"""アプリケーションで使用するメッセージの集中定義."""

from pathlib import Path

__all__ = ["Messages"]


class Messages:
    """利用者向けメッセージと安全な診断メッセージを管理する."""

    TEST_CASE_DUPLICATE_DESIGN: str = "設計ケース ID が重複しています"
    TEST_CASE_NO_DESIGN: str = "設計ケース ID が見つかりません。"
    TEST_CASE_EMPTY_MARKER: str = "case マーカーにはケース ID の指定が必要です。"
    TEST_CASE_ARGUMENTS: str = "case マーカーには固定文字列の位置引数だけを指定してください。"
    TEST_CASE_INVALID_ID: str = "case マーカーのケース ID が不正です。"
    TEST_CASE_DUPLICATE_ID: str = "case マーカー内でケース ID が重複しています。"

    API_KEY_REQUIRED = "AI ゲートウェイの認証情報が設定されていません。"
    HTTPS_REQUIRED = "AI ゲートウェイの URL には HTTPS を指定してください。"
    MODEL_REQUIRED = "AI ゲートウェイで使用する固定モデル ID を指定してください。"
    OTEL_DEPENDENCY_REQUIRED = "OpenTelemetry を使用するには任意依存を導入してください。"
    OTEL_ENDPOINT_REQUIRED = "OpenTelemetry の送信先が設定されていません。"
    RUN_LEASE_CONFLICT = "対象のレビュー実行は別の処理が実行中です。"
    RUN_LEASE_LOST = "レビュー実行の所有権を失ったため、結果を保存できません。"
    OTEL_HEADERS_INVALID = "OpenTelemetry の認証ヘッダー形式が不正です。"

    LLM_TIMEOUT = "LLM の呼び出しがタイムアウトしました。"
    INVALID_LLM_OUTPUT = "LLM の応答形式が正しくありません。"
    LITELLM_RESPONSE_VALIDATION_FAILED = "LiteLLM の応答検証に失敗しました。"
    LITELLM_RESPONSE_CONTENT_MISSING = "LiteLLM の応答にメッセージ本文がありません。"
    LITELLM_RESPONSE_CONTENT_NOT_JSON_STRING = (
        "LiteLLM のメッセージ本文は JSON 文字列である必要があります。"
    )
    LITELLM_RESPONSE_JSON_OBJECT_MISSING = (
        "LiteLLM のメッセージ本文に JSON オブジェクトがありません。"
    )
    LITELLM_RESPONSE_NOT_OBJECT = "LiteLLM の応答はオブジェクトである必要があります。"
    EXTERNAL_SOURCE_PERMISSION_REQUIRED = (
        "実際の AI ゲートウェイへ送信するには、"
        "security.allow_external_source を true にしてください。"
    )
    DIFF_SOURCE_REQUIRED = "差分の入力元を一つ指定してください。"
    DATASET_PATH_REQUIRED = "dataset プロバイダーには llm.dataset_path が必要です。"
    FEEDBACK_RUN_NOT_FOUND = "対象のレビュー実行が見つかりません。"
    FEEDBACK_REVISION_REQUIRED = "対象実行には固定されたソース改訂が必要です。"
    MISSED_ISSUE_FILE_NOT_FOUND = "対象改訂に見逃し問題のファイルが存在しません。"
    MISSED_ISSUE_LINE_OUTSIDE_FILE = "見逃し問題の行番号がファイル範囲外です。"
    MISSED_ISSUE_SYMBOL_NOT_FOUND = "見逃し問題のシンボルが対象ファイルに存在しません。"
    MISSED_ISSUE_LOCATION_MISMATCH = "見逃し問題の行番号とシンボルが同じ位置を示していません。"

    CLI_DESCRIPTION = "Git 差分を解析し、検証済みのコードレビュー結果を生成します。"
    CLI_HELP_HELP = "このヘルプを表示して終了します。"
    CLI_REVIEW_HELP = "指定した Git 差分をレビューします。"
    CLI_REVIEW_DESCRIPTION = (
        "差分入力元を一つ選び、ルール適用、機密情報検査、LLM レビュー、決定的検証を実行します。"
    )
    CLI_RULES_HELP = "ルールの適用状態を診断します。"
    CLI_RULES_DESCRIPTION = "プロジェクトルールの決定的な診断コマンドです。"
    CLI_RULES_CHECK_HELP = "指定差分に対するルールの命中理由と予算選択を表示します。"
    CLI_RULES_CHECK_DESCRIPTION = (
        "LLM を呼ばず、正式レビューと同じ照合、リスク判定、Token 予算選択を実行します。"
    )
    CLI_REPOSITORY_HELP = "レビュー対象の Git リポジトリ。既定値は現在のディレクトリです。"
    CLI_PROJECT_HELP = "設定ファイルの project_id を上書きするプロジェクト識別子です。"
    CLI_CONFIG_HELP = "読み込む YAML 設定ファイル。相対パスは現在のディレクトリ基準です。"
    CLI_MODE_HELP = (
        "レビュー方式。baseline はルールなし、rules はルールあり、memory は M2 用の予約値です。"
    )
    CLI_OUTPUT_HELP = (
        "結果 JSON の保存先。省略時は標準出力へ表示し、指定時はアトミックに保存します。"
    )
    CLI_RULE_FILE_HELP = "診断対象を指定した変更ファイル一件に限定します。"
    CLI_FEEDBACK_HELP = "レビュー結果への評価、または見逃し問題を記録します。"
    CLI_FEEDBACK_DESCRIPTION = (
        "評価と見逃し報告は追記のみで保存します。訂正は新しいレコードで行います。"
    )
    CLI_FEEDBACK_FINDING_HELP = "既存の指摘一件に対する評価を記録します。"
    CLI_FEEDBACK_MISSED_ISSUE_HELP = "レビューが報告しなかった問題を記録します。"
    CLI_FEEDBACK_RUN_HELP = "評価対象のレビュー実行識別子です。"
    CLI_FEEDBACK_FINDING_ID_HELP = "評価対象の指摘識別子です。例: B001-F001。"
    CLI_FEEDBACK_LABEL_HELP = "指摘に対する評価ラベルです。missed-issue は指定できません。"
    CLI_FEEDBACK_REASON_HELP = "評価理由です。監査記録として必須です。"
    CLI_FEEDBACK_ACTOR_HELP = "操作を実行する担当者の識別子です。"
    CLI_FEEDBACK_FILE_HELP = "見逃し問題が存在するリポジトリ相対パスです。"
    CLI_FEEDBACK_START_LINE_HELP = "見逃し問題の開始行です。--symbol と少なくとも一方が必須です。"
    CLI_FEEDBACK_SYMBOL_HELP = "見逃し問題を含む関数またはクラス名です。"
    CLI_FEEDBACK_ISSUE_TYPE_HELP = "見逃し問題の種別です。例: null-dereference。"
    CLI_FEEDBACK_SEVERITY_HELP = "見逃し問題の重大度です。"
    CLI_FEEDBACK_DESCRIPTION_HELP = "見逃し問題の説明です。"
    CLI_FEEDBACK_EVIDENCE_HELP = "見逃し問題を裏付ける根拠です。"
    CLI_MEMORY_HELP = "抽出されたメモリ候補を一覧、表示、承認、却下します。"
    CLI_MEMORY_DESCRIPTION = "承認と却下は統制操作です。実行者を特定できない場合は失敗します。"
    CLI_MEMORY_LIST_HELP = "指定した状態のメモリを一覧表示します。"
    CLI_MEMORY_SHOW_HELP = "メモリ一件の現在バージョンを表示します。"
    CLI_MEMORY_APPROVE_HELP = "メモリ候補を有効化します。"
    CLI_MEMORY_REJECT_HELP = "メモリ候補を却下します。"
    CLI_MEMORY_ID_HELP = "対象メモリの識別子です。"
    CLI_MEMORY_STATUS_HELP = "一覧表示するメモリの状態です。"
    CLI_MEMORY_REASON_HELP = "承認または却下の理由です。監査記録として必須です。"
    CLI_MEMORY_LOCK_VERSION_HELP = (
        "楽観ロックで想定する現在のロックバージョンです。show で確認できます。"
    )
    CLI_MEMORY_SCOPE_FILE_HELP = "適用範囲を縮小する場合の YAML ファイルです。"
    CLI_MEMORY_ARCHIVE_HELP = "有効メモリを履歴を保持したまま保管します。"
    CLI_MEMORY_RESTORE_HELP = "保管メモリを新しい承認待ち版として復元します。"
    CLI_MEMORY_SUPERSEDE_HELP = "有効メモリを別の有効メモリで置換します。"
    CLI_MEMORY_REPLACEMENT_HELP = "置換先となる有効メモリの識別子です。"
    CLI_MEMORY_CONFLICT_HELP = "検出済みメモリ競合を一覧または解決します。"
    CLI_MEMORY_CONFLICT_ID_HELP = "対象競合の安全な識別子です。"
    CLI_MEMORY_CONFLICT_STATUS_HELP = "一覧表示する競合状態です。"
    CLI_MEMORY_RESOLUTION_HELP = "人間が選択した競合解決方法です。"
    CLI_MEMORY_EVIDENCE_HELP = "統制判断を裏付ける根拠要約です。"
    CLI_MEMORY_REVALIDATION_HELP = "期限到来メモリを一覧、再確認、無効化します。"
    CLI_MEMORY_LIFECYCLE_DISABLED = (
        "M5 メモリ生命周期を使用するには governance.enabled と "
        "governance.memory_lifecycle_enabled を有効にしてください。"
    )
    CLI_EVAL_HELP = "発行済み正解データで比較群 A/B/C を評価します。"
    CLI_EVAL_ARMS_HELP = "実行する比較群をカンマ区切りで指定します。例: B,C。"
    CLI_EVAL_SPLIT_HELP = "正解データの区分です。development または test。"
    CLI_EVAL_SET_HELP = "使用する発行済み正解データ集合のバージョンです。"
    CLI_EVAL_REPEAT_HELP = "比較群ごとの反復回数です。3 回以上を指定します。"
    CLI_GROUND_TRUTH_HELP = "評価用の正解データ集合を管理します。"
    CLI_GROUND_TRUTH_PUBLISH_HELP = "承認済み問題を不変の正解データ集合として発行します。"
    CLI_GROUND_TRUTH_INPUT_HELP = "発行する正解問題を記述した JSON ファイルです。"
    CLI_GROUND_TRUTH_ACTOR_HELP = "正解データ集合を発行する保守担当者の識別子です。"
    CLI_ADJUDICATION_HELP = "未裁定の評価照合を盲化して出力し、人工裁定を記録します。"
    CLI_ADJUDICATION_EXPORT_HELP = "比較群を伏せた未裁定項目を JSON 出力します。"
    CLI_ADJUDICATION_RESOLVE_HELP = "盲化トークンに対する人工裁定を記録します。"
    CLI_ADJUDICATION_REPORT_HELP = "裁定反映後の B/C 最終判定を再生成します。"
    # 利用者向け説明文であり、認証情報を保持しない。
    CLI_ADJUDICATION_TOKEN_HELP = "盲化出力に含まれる裁定トークンです。"  # nosec B105
    CLI_ADJUDICATION_RESULT_HELP = "人工裁定の結果です。"
    CLI_ADJUDICATION_ISSUE_HELP = "対応する正解問題 ID です。"
    INVALID_GROUND_TRUTH_DOCUMENT = "正解データ文書を読み込めないか、形式が不正です。"
    GROUND_TRUTH_DOCUMENT_NOT_FOUND = "正解データ入力ファイルが存在しません。"
    GROUND_TRUTH_DOCUMENT_TOO_LARGE = "正解データ入力ファイルが 5 MiB を超えています。"
    GROUND_TRUTH_SET_ALREADY_EXISTS = "同じ区分とバージョンの正解データ集合は発行済みです。"
    EVALUATION_CANDIDATE_TARGET_EXISTS = "評価候補の生成先がすでに存在します。"
    DUPLICATE_GROUND_TRUTH_SEQUENCE = "正解問題の sequence が重複しています。"
    INVALID_GROUND_TRUTH_APPROVAL = "各正解問題には作成者と異なる検証者、および承認者が必要です。"
    GROUND_TRUTH_CASES_REQUIRED = "スキーマ版 2 では評価対象の cases が必要です。"
    DUPLICATE_GROUND_TRUTH_CASE = "正解ケースの sequence または change_ref が重複しています。"
    INVALID_GROUND_TRUTH_CASE_APPROVAL = (
        "各正解ケースには作成者と異なる検証者、および承認者が必要です。"
    )
    GROUND_TRUTH_ISSUE_CASE_MISSING = "正解問題の change_ref に対応するケースがありません。"
    MEMORY_EXTRACTION_TEST_DATA_FORBIDDEN = (
        "Test 正解データの評価結果からメモリ候補を作成することはできません。"
    )
    SCM_ACTOR_MISMATCH = "SCM の認証主体が設定済みボットと一致しません。"
    SCM_BASE_SHA_MISMATCH = "ローカル HEAD が変更要求の基準 SHA と一致しません。"
    SCM_CHANGE_NOT_OPEN = "開いている変更要求だけをレビューできます。"
    SCM_DISABLED = "SCM 連携が有効化されていません。"
    SCM_DRAFT_NOT_ALLOWED = "下書きの変更要求は設定で許可されていません。"
    SCM_HEAD_SHA_CHANGED = "レビュー後に変更要求の先端 SHA が更新されました。"
    SCM_FEEDBACK_COMMAND_INVALID = "SCM フィードバックコマンドの形式が不正です。"
    SCM_FEEDBACK_SYNC_DISABLED = "SCM フィードバック同期が有効化されていません。"
    SCM_FEEDBACK_TARGET_INVALID = "SCM フィードバックの対象指摘が不正です。"
    # 利用者向け遮断理由であり、検出値を含めない。
    SCM_PUBLICATION_SECRET_DETECTED = "SCM 投稿内容から機密情報候補を検出しました。"  # nosec B105
    SPECIALIST_INPUT_BLOCKED = "専門レビュー担当への送信前に機密情報候補を検出しました。"
    SCM_PUBLISHER_REQUIRED = "SCM 投稿処理が構成されていません。"
    SCM_REPOSITORY_MISMATCH = "変更要求のリポジトリが設定と一致しません。"
    SCM_REVIEW_NOT_SUCCESSFUL = "レビューが正常完了していないため SCM へ投稿できません。"
    # 利用者向け設定エラーであり、認証情報を保持しない。
    SCM_TOKEN_REQUIRED = "SCM の認証情報が設定されていません。"  # nosec B105
    CLI_SCM_HELP = "SCM の変更要求をレビューし、結果を連携します。"
    CLI_SCM_DESCRIPTION = (
        "変更要求の固定 SHA をローカル Git と照合し、既存のレビュー処理を実行します。"
    )
    CLI_SCM_REVIEW_HELP = "変更要求をレビューします。既定では投稿しません。"
    CLI_SCM_SYNC_FEEDBACK_HELP = "公開済み行コメントへの明示評価を M2 フィードバックへ同期します。"
    CLI_SCM_CHANGE_HELP = "レビュー対象の Pull Request 番号です。"
    CLI_SCM_PUBLISH_HELP = "検証済み結果を SCM へ投稿します。scm.publish_enabled=true も必要です。"
    CLI_DIFF_SOURCE_DESCRIPTION = "次の差分入力元から一つだけ指定してください。"
    CLI_GIT_DIFF_HELP = "git diff による作業ツリーの未ステージ差分をレビューします。"
    CLI_RANGE_HELP = (
        "BASE...HEAD の三点比較差分をレビューします。両方にコミットまたは参照を指定します。"
    )
    CLI_COMMIT_HELP = "指定したコミット一件が導入した差分を git show でレビューします。"
    CLI_DIFF_FILE_HELP = "UTF-8 のパッチファイルをレビューします。最大サイズは 20 MiB です。"
    CLI_GOVERNANCE_HELP = "M5 のプロジェクト統制を管理します。"
    CLI_GOVERNANCE_DESCRIPTION = (
        "プロジェクト登録、リポジトリ対応および既存資源の所属を監査付きで管理します。"
    )
    CLI_GOVERNANCE_PROJECT_HELP = "プロジェクト所属を管理します。"
    CLI_GOVERNANCE_PROJECT_LIST_HELP = "登録済みプロジェクトを一覧表示します。"
    CLI_GOVERNANCE_PROJECT_REGISTER_HELP = "プロジェクトを登録します。"
    CLI_GOVERNANCE_BIND_REPOSITORY_HELP = "外部リポジトリをプロジェクトへ関連付けます。"
    CLI_GOVERNANCE_ADOPT_EXISTING_HELP = "所属のない既存資源一件を関連付けます。"
    CLI_GOVERNANCE_PROJECT_NAME_HELP = "利用者向けのプロジェクト名称です。"
    CLI_GOVERNANCE_PROVIDER_HELP = "リポジトリ提供元です。例: github。"
    CLI_GOVERNANCE_REPOSITORY_ID_HELP = "提供元内で一意なリポジトリ識別子です。"
    CLI_GOVERNANCE_RESOURCE_TYPE_HELP = "既存資源の種別です。"
    CLI_GOVERNANCE_RESOURCE_ID_HELP = (
        "既存資源の識別子です。SCM は provider|repository|change の形式です。"
    )
    CLI_GOVERNANCE_REASON_HELP = "統制操作の理由です。監査用ハッシュだけを保存します。"
    CLI_GOVERNANCE_BACKUP_HELP = "SQLite バックアップを管理します。"
    CLI_GOVERNANCE_BACKUP_VERIFY_HELP = "復元済み SQLite バックアップを検査します。"
    CLI_GOVERNANCE_BACKUP_DATABASE_HELP = "検査する復元済み SQLite DB のパスです。"
    CLI_GOVERNANCE_BACKUP_ACTOR_HELP = "バックアップ検査を実行する保守担当者です。"
    BACKUP_DATABASE_NOT_FOUND = "検査対象のバックアップデータベースが存在しません。"

    ADJUDICATION_REPORTS_REQUIRED = "比較群 B と C の評価報告が必要です。"
    EVAL_ARMS_INVALID = "比較群には A、B、C だけを指定できます。"
    EVAL_ARMS_REQUIRED = "比較群を一つ以上指定してください。"

    CONTEXT_SOURCE_CHANGED = "レビュー中に追加コンテキストの入力元が変更されました。"
    CONTEXT_SOURCE_READ_FAILED = "追加コンテキストの入力元を列挙できませんでした。"
    CONTEXT_SOURCE_TOO_LARGE = "追加コンテキストのファイルが設定済みサイズ上限を超えています。"
    CONTEXT_SOURCE_NOT_UTF8 = "追加コンテキストのファイルは UTF-8 テキストである必要があります。"
    CONTEXT_REVISION_INVALID = "追加コンテキストの改訂には完全なコミット SHA を指定してください。"
    FILE_DIFF_VERIFICATION_FAILED = "パッチファイルと作業ツリーの整合を検証できませんでした。"
    FEEDBACK_REVISION_INVALID = (
        "フィードバック対象の改訂には完全なコミット SHA を指定してください。"
    )
    FEEDBACK_REVISION_READ_FAILED = "フィードバック対象の改訂を読み込めませんでした。"
    FEEDBACK_SOURCE_TOO_LARGE = "フィードバック対象のファイルが検証用サイズ上限を超えています。"
    FEEDBACK_SOURCE_NOT_UTF8 = "フィードバック対象のファイルは UTF-8 テキストである必要があります。"

    PUBLISHED_GROUND_TRUTH_SET_NOT_FOUND = "発行済みの正解データ集合が見つかりません。"
    EVALUATION_DATABASE_FAILED = "評価データベースの操作に失敗しました。"
    GROUND_TRUTH_SET_NOT_PUBLISHED = "正解データ集合は保存前に発行してください。"
    ADJUDICATION_ITEM_NOT_FOUND = "未裁定の裁定対象が見つかりません。"
    ADJUDICATION_ISSUE_REQUIRED = "真陽性と重複には issue_id が必要です。"
    ADJUDICATION_ISSUE_FORBIDDEN = "偽陽性には issue_id を指定できません。"
    ADJUDICATION_ISSUE_NOT_IN_SET = "正解問題が評価対象の正解データ集合に属していません。"
    ADJUDICATION_ACTOR_REQUIRED = "裁定者を指定してください。"
    ADJUDICATION_REASON_REQUIRED = "裁定理由を指定してください。"

    PROJECT_ALREADY_REGISTERED = "プロジェクト ID は登録済みです。"
    REPOSITORY_ALREADY_BOUND = "リポジトリは別のプロジェクトに関連付け済みです。"
    GOVERNANCE_RESOURCE_NOT_FOUND = "統制対象の資源が見つかりません。"
    GOVERNANCE_RESOURCE_OTHER_PROJECT = "統制対象の資源は別のプロジェクトに所属しています。"
    SCM_RESOURCE_ID_INVALID = (
        "SCM 資源 ID は provider|repository_external_id|change_external_id の形式で"
        "指定してください。"
    )
    GOVERNANCE_PROJECT_REGISTER_FAILED = "プロジェクトを登録できませんでした。"
    GOVERNANCE_PROJECT_LIST_FAILED = "プロジェクト一覧を取得できませんでした。"
    GOVERNANCE_PROJECT_READ_FAILED = "プロジェクトを読み込めませんでした。"
    GOVERNANCE_REPOSITORY_BIND_FAILED = "リポジトリをプロジェクトへ関連付けできませんでした。"
    GOVERNANCE_RESOURCE_ADOPT_FAILED = "既存資源をプロジェクトへ関連付けできませんでした。"
    GOVERNANCE_CONTEXT_VALIDATION_FAILED = "プロジェクトコンテキストを検証できませんでした。"

    MEMORY_GOVERNANCE_TARGET_NOT_FOUND = "メモリ統制の対象が見つかりません。"
    MEMORY_GOVERNANCE_INVALID_STATE = "現在の状態ではこのメモリ統制操作を実行できません。"
    MEMORY_CONFLICT_NOT_FOUND = "メモリ競合が見つかりません。"
    MEMORY_GOVERNANCE_PERSIST_FAILED = "メモリ統制操作を保存できませんでした。"
    MEMORY_CONFLICT_MODIFIED_CONCURRENTLY = "メモリ競合が同時に更新されました。"
    MEMORY_MODIFIED_CONCURRENTLY = "メモリが同時に更新されました。"
    MEMORY_NEXT_REVIEW_NOT_LATER = "次回再確認日時は確認日時より後にしてください。"
    MEMORY_NARROW_SCOPE_REQUIRED = "narrow-scope による解決には適用範囲が必要です。"
    MEMORY_SCOPE_UNEXPECTED = "適用範囲は narrow-scope による解決でだけ指定できます。"
    RUN_LEASE_UPDATE_FAILED = "実行リースを SQLite に保存できませんでした。"

    SCM_CURSOR_READ_FAILED = "SCM 同期カーソルを読み込めませんでした。"
    SCM_CURSOR_SAVE_FAILED = "SCM 同期カーソルを保存できませんでした。"
    SCM_EVENT_LOOKUP_FAILED = "SCM イベントを照会できませんでした。"
    SCM_FEEDBACK_EVENT_SAVE_FAILED = "SCM フィードバックイベントを保存できませんでした。"
    SCM_PUBLICATION_LOOKUP_FAILED = "SCM 出版物を照会できませんでした。"
    SCM_FEEDBACK_EVENT_TARGET_REQUIRED = "SCM フィードバックイベントの対象が必要です。"
    SCM_PROJECT_ASSIGNMENT_FAILED = "SCM 変更要求のプロジェクト所属を保存できませんでした。"
    SCM_PUBLICATION_PREPARATION_FAILED = "SCM 出版物を準備できませんでした。"
    SCM_PUBLICATION_LEASE_UPDATE_FAILED = "SCM 出版物のリースを更新できませんでした。"
    SCM_PUBLICATION_LEASE_HELD = "SCM 出版物のリースは別の処理が保持しています。"
    SCM_PERSISTENCE_FAILED = "SCM の保存処理に失敗しました。"
    SCM_INVALID_RESPONSE = "SCM API の応答が不正です。"
    SCM_INVALID_CHANGE_ID = "GitHub の Pull Request 番号は正の整数で指定してください。"
    SCM_EVENT_CURSOR_INVALID = "SCM イベントカーソルが不正です。"
    SCM_RATE_LIMIT_EXHAUSTED = "SCM API のレート制限が再試行上限を超えました。"
    SCM_API_AUTH_FAILED = "SCM API の認証または認可に失敗しました。"
    SCM_RESOURCE_NOT_FOUND = "SCM の資源が見つかりません。"
    SCM_API_REQUEST_REJECTED = "SCM API が要求を拒否しました。"
    SCM_API_TEMPORARY_FAILURE_EXHAUSTED = "SCM API の一時的な障害が再試行上限を超えました。"
    SCM_API_REQUEST_FAILED = "SCM API の要求に失敗しました。"
    SCM_API_URL_NOT_HTTPS = "SCM API の要求 URL には絶対 HTTPS URL を指定してください。"
    SCM_API_RESPONSE_METADATA_INVALID = "SCM API の応答メタデータが不正です。"
    SCM_API_RESPONSE_TOO_LARGE = "SCM API の応答がサイズ上限を超えました。"
    SCM_API_CONNECTION_FAILED = "SCM API に接続できませんでした。"

    RUN_NOT_FOUND_FOR_M3_IDENTITY = "M3 識別情報の保存対象となる実行が見つかりません。"
    RUN_NOT_FOUND_FOR_CONTEXT_HASH = (
        "コンテキスト入力元ハッシュの保存対象となる実行が見つかりません。"
    )
    STORED_REVIEWER_METRICS_INVALID = "保存済みの担当別測定値が不正です。"
    STORED_REVIEW_METRICS_INVALID = "保存済みのレビュー測定値が不正です。"
    STORED_REJECTION_COUNTS_INVALID = "保存済みの除外理由別件数が不正です。"
    DATABASE_INITIALIZATION_FAILED = "SQLite データベースを初期化できませんでした。"
    LLM_CALL_ID_UNAVAILABLE = "LLM 呼び出し識別子を取得できませんでした。"
    BUNDLE_NOT_FOUND_FOR_M3_METADATA = "M3 メタデータの保存対象となるレビュー単位が見つかりません。"
    UNIT_OF_WORK_NOT_ACTIVE = "作業単位が開始されていません。"
    TRANSACTION_OPEN_FAILED = "SQLite トランザクションを開始できませんでした。"
    FINDING_ID_FORMAT_INVALID = "指摘 ID は B<連番>-F<連番> の形式で指定してください"
    FINDING_ID_SEQUENCE_NOT_NUMERIC = "指摘 ID の連番は数字で指定してください"
    REPOSITORY_NOT_DIRECTORY = "リポジトリのパスがディレクトリではありません"
    REPOSITORY_NOT_GIT_ROOT = "リポジトリのパスには Git ルートを指定してください"
    GIT_REVISION_INVALID = "Git の改訂が不正です"
    PATCH_FILE_INSPECT_FAILED = "パッチファイルを確認できません"
    PATCH_FILE_READ_FAILED = "パッチファイルを読み込めません"
    GIT_COMMAND_NOT_EXECUTED = "Git コマンドを実行できませんでした"
    MEMORY_VERSION_NOT_FOUND = "メモリ版が見つかりません"
    MEMORY_CONTENT_HASH_MISMATCH = "保存済みメモリの内容ハッシュが一致しません"
    RUN_NOT_FOUND_DURING_FINALIZE = "最終化対象の実行が見つかりません"
    MEMORY_SCOPE_NOT_ARRAY = "メモリ適用範囲の項目は配列で指定してください"
    PATCH_FILE_TOO_LARGE = "パッチファイルがサイズ上限を超えています"
    GIT_COMMAND_FAILED = "Git コマンドが失敗しました"
    SCM_PUBLICATION_TRANSITION_REJECTED = "SCM 出版物の状態を遷移できません"
    DIFF_SOURCE_ARGUMENTS_MISMATCH = "差分入力元の引数が不足または過剰です"

    CONFIG_CONTEXT_BUDGET_REQUIRED = (
        "動的コンテキスト取得を有効にするには、正のトークン予算と呼び出し回数が必要です。"
    )
    CONFIG_REFLECTION_BUDGET_REQUIRED = (
        "自己検証を有効にするには 256 以上の入力トークンが必要です。"
    )
    CONFIG_SCM_API_URL_INVALID = "SCM API の URL には許可済みの HTTPS 送信元を指定してください。"
    CONFIG_SCM_ACTOR_REQUIRED = "SCM を有効にするにはボットの実行者識別子が必要です。"
    CONFIG_SCM_REPOSITORY_REQUIRED = (
        "SCM を有効にするには owner/repository 形式の識別子が必要です。"
    )
    CONFIG_GOVERNANCE_DISABLED = "governance が無効な状態では M5 の下位機能を有効にできません。"
    CONFIG_GOVERNANCE_HEARTBEAT_INVALID = (
        "heartbeat_seconds は lease_seconds の 3 分の 1 以下にしてください。"
    )
    CONFIG_GOVERNANCE_LEASE_TOO_SHORT = (
        "lease_seconds は LLM と SCM の長い方のタイムアウトに 30 秒を加えた値以上にしてください。"
    )
    CONFIG_TELEMETRY_PROVIDER_MISMATCH = "遠隔測定の provider が有効状態と一致しません。"
    CONFIG_TELEMETRY_ENDPOINT_INVALID = (
        "遠隔測定を有効にするには許可済みの HTTPS 送信先が必要です。"
    )
    CONFIG_INVALID = "設定が不正です"
    CONFIG_SCHEMA_VERSION_UNSUPPORTED = "未対応の設定 schema_version です"
    CONFIG_FILE_READ_FAILED = "設定ファイルを読み込めません"
    CONFIG_FILE_YAML_INVALID = "設定ファイルの YAML が不正です"
    CONFIG_FILE_NOT_MAPPING = "設定ファイルは対応表である必要があります"
    CONFIG_CREDENTIALS_EMBEDDED = "設定ファイルに認証情報を含めないでください"

    GROUND_TRUTH_SET_ALREADY_PUBLISHED = "正解データ集合は発行済みです"
    GROUND_TRUTH_APPROVED_ISSUE_REQUIRED = "正解データ集合には承認済み問題が一件以上必要です。"
    GROUND_TRUTH_ISSUE_CASE_NOT_APPROVED = (
        "承認済み問題が未承認または存在しないケースに属しています。"
    )
    EVALUATION_REPEAT_INVALID = "評価の反復番号は 1 から始めてください。"
    FINDING_FEEDBACK_MISSED_ISSUE_FORBIDDEN = (
        "既存指摘への評価には missed-issue ラベルを使用できません。"
    )
    FINDING_NOT_IN_RUN = "指摘が対象のレビュー実行に属していません"
    GOVERNANCE_RESOURCE_ID_INVALID = "統制対象の資源 ID が不正です。"
    PROJECT_NOT_REGISTERED = "プロジェクトが登録されていません。"
    PROJECT_ARCHIVED = "プロジェクトは保管済みです。"
    PROJECT_CONTEXT_NOT_FOUND = "プロジェクトコンテキストが見つかりません。"
    GOVERNANCE_RESOURCES_UNASSIGNED = (
        "M5 レビューの前に既存の統制対象資源をプロジェクトへ関連付けてください。"
    )

    MEMORY_CANDIDATE_SUPPRESSES_CRITICAL_SECURITY = (
        "メモリ候補で重大なセキュリティ指摘を抑制することはできません。"
    )
    MEMORY_CANDIDATE_INJECTION_PATTERN = "メモリ候補に命令注入の表現が含まれています"
    MEMORY_SUPPRESSION_FORBIDS_REPORTING = (
        "suppress メモリは報告の禁止ではなく、より強い根拠の要求として記述してください。"
    )
    MEMORY_EXTRACTION_FEEDBACK_REQUIRED = "メモリ候補の抽出には一件以上のフィードバックが必要です。"
    MEMORY_EXTRACTION_FEEDBACK_MISSING = (
        "メモリ候補の抽出対象に存在しないフィードバックが含まれています。"
    )
    EXTRACTION_PAYLOAD_MODIFIED_AFTER_SCAN = (
        "機密情報検査後に抽出送信内容が変更されたため、未検査の内容を送信しません。"
    )

    TELEMETRY_ATTRIBUTE_COUNT_EXCEEDED = "遠隔測定の属性数が上限を超えています。"
    TELEMETRY_ATTRIBUTE_NAME_FORBIDDEN = "遠隔測定の属性名は許可されていません。"
    TELEMETRY_ATTRIBUTE_TOO_LONG = "遠隔測定の文字列属性が長すぎます。"

    AGENT_TURN_INVALID = "レビュー担当の応答が閉じたスキーマと一致しません。"
    BUNDLE_DIFF_MODIFIED_AFTER_SCAN = (
        "機密情報検査後にレビュー単位の差分が変更されたため、未検査の内容を送信しません"
    )
    SPECIALIST_TEMPLATE_NOT_CONFIGURED = "専門レビュー担当のプロンプトが構成されていません。"
    CONTEXT_TOOL_INTENT_INVALID = "コンテキスト取得ツールの要求が不正です。"
    PROMPT_TEMPLATE_READ_FAILED = "プロンプトテンプレートを読み込めません"
    PROMPT_TEMPLATE_YAML_INVALID = "プロンプトテンプレートの YAML が不正です"
    PROMPT_TEMPLATE_NOT_MAPPING = "プロンプトテンプレートは対応表である必要があります"
    PROMPT_TEMPLATE_KEYS_MISSING = "プロンプトテンプレートに必須キーがありません"
    PROMPT_TEMPLATE_VERSION_INVALID = (
        "プロンプトテンプレートの prompt_version は正の整数にしてください"
    )
    PROMPT_TEMPLATE_PROFILES_INCOMPLETE = (
        "プロンプトテンプレートの reviewer_profiles には general/security/data/test が必要です"
    )
    PROMPT_TEMPLATE_PROFILE_EMPTY = "プロンプトテンプレートの担当別記述は空にできません"
    REFLECTION_RESPONSE_INVALID = "自己検証の応答が閉じたスキーマと一致しません。"
    REFLECTION_BUDGET_EXCEEDED = "自己検証の入力が設定済み予算を超えています。"
    RESPONSE_SCHEMA_VERSION_UNSUPPORTED = "未対応の応答 schema_version です"
    RESPONSE_FINDINGS_NOT_ARRAY = "応答の findings は配列である必要があります。"
    REVIEWER_RESULTS_EMPTY = "レビュー担当の結果が空です。"
    REVIEWER_PRIMARY_RESULT_MISSING = "レビュー担当の結果に主担当がありません。"
    RULES_DIRECTORY_OUTSIDE_REPOSITORY = "ルールディレクトリはリポジトリ内を指定してください。"
    CONTEXT_TOOL_DEPENDENCY_MISSING = "コンテキスト取得ツールの依存関係が構成されていません。"
    REFLECTION_DEPENDENCY_MISSING = "自己検証の依存関係が構成されていません。"
    REVIEW_RUN_RESUME_CONFLICT = "レビュー実行は別の処理が再開中です。"
    REVIEW_CANCELLED_BY_BLOCKED_BUNDLE = "別のレビュー単位が遮断されたためレビューを中止しました。"
    FILE_DIFF_WORKTREE_MISMATCH = "パッチファイルの差分が現在の作業ツリーと一致しません。"
    SENSITIVE_CANDIDATE_BEFORE_LLM = "LLM 呼び出し前に機密情報候補を検出しました。"
    SENSITIVE_CANDIDATE_IN_CONTEXT_TOOL = (
        "コンテキスト取得ツールの結果から機密情報候補を検出しました。"
    )
    CONTEXT_SOURCE_CHANGED_DURING_REVIEW = "レビュー中にコンテキストの入力元が変更されました。"

    DIFF_CONTAINS_NUL = "差分に NUL 文字を含めることはできません。"
    HUNK_HEADER_MALFORMED = "差分ブロックのヘッダーが不正です"
    HUNK_LINE_COUNT_MISMATCH = "差分ブロックの行数がヘッダーと一致しません"
    DIFF_HEADER_PATH_INVALID = "差分ヘッダーのパスが不正です"
    DIFF_LINE_MARKER_UNEXPECTED = "差分ブロックに想定外の行記号があります"
    PROJECT_ID_LENGTH_INVALID = "プロジェクト ID の文字数が範囲外です"
    PROJECT_ID_FORMAT_INVALID = "プロジェクト ID は [a-z0-9][a-z0-9_-]* の形式で指定してください。"
    RUN_ID_INVALID = "実行 ID は小文字の UUIDv4 で指定してください。"
    BUNDLE_SEQUENCE_INVALID = "レビュー単位の連番は 1 から始めてください。"
    FINDING_SEQUENCE_INVALID = "指摘の連番は 1 から始めてください。"
    PATH_EMPTY = "パスを空にすることはできません。"
    PATH_CONTAINS_NUL = "パスに NUL 文字を含めることはできません。"
    PATH_NOT_POSIX = "パスには POSIX 区切り文字を使用してください"
    PATH_NOT_RELATIVE = "パスはリポジトリ相対で指定してください"
    PATH_TRAVERSES_OUTSIDE = "パスでリポジトリ外へ移動することはできません"
    PATH_NOT_FILE = "パスにはファイルを指定してください"
    START_LINE_INVALID = "start_line は 1 以上にしてください。"
    END_LINE_INVALID = "end_line は start_line 以上にしてください。"
    FEEDBACK_ID_INVALID = "フィードバック ID は小文字の UUIDv4 で指定してください。"
    EXTRACTION_ID_INVALID = "抽出 ID は小文字の UUIDv4 で指定してください。"
    MEMORY_ID_INVALID = "メモリ ID は小文字の UUIDv4 で指定してください。"
    MEMORY_VERSION_INVALID = "メモリ版は 1 から始めてください。"
    GROUND_TRUTH_SPLIT_INVALID = "正解データの区分が不正です"
    GROUND_TRUTH_SET_VERSION_INVALID = "正解データ集合の版は 1 から始めてください。"
    GROUND_TRUTH_ISSUE_SEQUENCE_INVALID = "正解問題の連番は 1 から始めてください。"
    EVALUATION_ID_INVALID = "評価 ID は小文字の UUIDv4 で指定してください。"
    GROUND_TRUTH_CASE_SEQUENCE_INVALID = "正解ケースの連番は 1 以上にしてください。"
    GROUND_TRUTH_CASE_CHANGE_REF_REQUIRED = "正解ケースには change_ref が必要です。"
    GROUND_TRUTH_ISSUE_LOCATION_REQUIRED = "正解問題には start_line または symbol が必要です。"
    GROUND_TRUTH_ISSUE_START_LINE_INVALID = "正解問題の start_line は 1 以上にしてください。"
    GROUND_TRUTH_ISSUE_CHANGE_REF_REQUIRED = "正解問題には change_ref が必要です。"
    GROUND_TRUTH_ISSUE_EVIDENCE_REQUIRED = "正解問題には根拠が必要です。"
    PUBLISHED_SET_APPROVER_REQUIRED = "発行済み正解データ集合には approved_by が必要です。"
    PUBLISHED_SET_TIME_REQUIRED = (
        "発行済み正解データ集合にはタイムゾーン付きの published_at が必要です。"
    )
    PUBLISHED_SET_UNAPPROVED_ISSUE = "発行済み正解データ集合には承認済み問題だけを含めてください。"
    PUBLISHED_SET_UNAPPROVED_CASE = "発行済み正解データ集合には承認済みケースだけを含めてください。"
    GROUND_TRUTH_ISSUE_CASE_UNREGISTERED = "正解問題は登録済みのケースに属している必要があります。"
    FALSE_POSITIVE_FINDING_REQUIRED = "偽陽性の照合には finding_id が必要です。"
    FALSE_NEGATIVE_ISSUE_REQUIRED = "偽陰性の照合には issue_id が必要です。"
    MATCH_TARGET_REQUIRED = "照合結果には finding_id または issue_id が必要です。"
    MISSED_ISSUE_LOCATION_REQUIRED = "見逃し問題の位置には start_line または symbol が必要です。"
    MISSED_ISSUE_START_LINE_INVALID = "見逃し問題の start_line は 1 以上にしてください。"
    FEEDBACK_ACTOR_REQUIRED = "フィードバックの actor_id を空にすることはできません。"
    FEEDBACK_TIME_NOT_UTC = "フィードバックの created_at はタイムゾーン付き UTC にしてください。"
    MISSED_ISSUE_FINDING_FORBIDDEN = "見逃し問題のフィードバックは指摘を参照できません。"
    MISSED_ISSUE_FEEDBACK_LOCATION_REQUIRED = "見逃し問題のフィードバックには位置が必要です。"
    MISSED_ISSUE_TYPE_REQUIRED = "見逃し問題のフィードバックには issue_type が必要です。"
    MISSED_ISSUE_SEVERITY_REQUIRED = "見逃し問題のフィードバックには重大度が必要です。"
    MISSED_ISSUE_EVIDENCE_REQUIRED = "見逃し問題のフィードバックには根拠が必要です。"
    FINDING_FEEDBACK_ID_REQUIRED = "指摘へのフィードバックには finding_id が必要です。"
    FINDING_FEEDBACK_LOCATION_FORBIDDEN = (
        "指摘へのフィードバックに見逃し問題の位置は指定できません。"
    )
    FINDING_FEEDBACK_REASON_REQUIRED = "指摘へのフィードバックには理由が必要です。"
    MEMORY_GOVERNANCE_LOCK_INVALID = "メモリ統制の lock_version は正の値にしてください。"
    MEMORY_CONFLICT_ORDER_INVALID = "メモリ競合の組は正規化された順序で指定してください。"
    MEMORY_CONFLICT_METADATA_INVALID = "メモリ競合のメタデータが不正です。"
    MEMORY_CONFLICT_STATE_INCONSISTENT = "メモリ競合の解決状態が整合していません。"
    PROJECT_NAME_INVALID = "プロジェクト名は 1 から 100 文字の安全な文字で指定してください。"
    PROJECT_POLICY_HASH_INVALID = "プロジェクト方針ハッシュは SHA-256 で指定してください。"
    PROJECT_LOCK_INVALID = "プロジェクトのロック版は正の値にしてください。"
    PROJECT_TIME_NOT_UTC = "プロジェクトの時刻は UTC にしてください。"
    REPOSITORY_PROVIDER_INVALID = "リポジトリ提供元が不正です。"
    REPOSITORY_EXTERNAL_ID_INVALID = "リポジトリ外部 ID が不正です。"
    REPOSITORY_BINDING_TIME_NOT_UTC = "リポジトリ関連付けの時刻は UTC にしてください。"
    GOVERNANCE_REASON_INVALID = "統制操作の理由は 1 から 500 文字の安全な文字で指定してください。"
    FRAMEWORK_CONSTRAINT_INVALID = "フレームワークの版制約が不正です。"
    MEMORY_CONFIDENCE_INVALID = "メモリの確信度は 0.0 以上 1.0 以下にしてください。"
    MEMORY_CREATOR_REQUIRED = "メモリの created_by を空にすることはできません。"
    MEMORY_VALIDITY_PAIR_REQUIRED = "メモリの有効期間は開始と再確認期限を同時に指定してください。"
    MEMORY_VALIDITY_NOT_UTC = "メモリの有効期間はタイムゾーン付き UTC にしてください。"
    MEMORY_VALIDITY_ORDER_INVALID = "メモリの valid_from は review_after より前にしてください。"
    MEMORY_CREATED_AT_NOT_UTC = "メモリの created_at はタイムゾーン付き UTC にしてください。"
    MEMORY_FIELD_EMPTY = "メモリの項目を空にすることはできません"
    MEMORY_FIELD_TOO_LONG = "メモリの項目が最大文字数を超えています"
    MEMORY_LOCK_VERSION_INVALID = "メモリの lock_version は 1 から始めてください。"
    MEMORY_TIME_NOT_UTC = "メモリの時刻はタイムゾーン付き UTC にしてください。"
    MEMORY_TRANSITION_ACTOR_REQUIRED = "メモリ状態遷移の actor_id を空にすることはできません。"
    MEMORY_TRANSITION_REASON_REQUIRED = "メモリ状態遷移の理由を空にすることはできません。"
    MEMORY_TRANSITION_TIME_NOT_UTC = (
        "メモリ状態遷移の created_at はタイムゾーン付き UTC にしてください。"
    )
    MEMORY_SCOPE_GROUP_REQUIRED = "メモリ適用範囲には一つ以上の項目群が必要です。"
    MEMORY_SCOPE_TOO_MANY_ITEMS = "メモリ適用範囲の項目数が上限を超えています"
    MEMORY_SCOPE_EMPTY_VALUE = "メモリ適用範囲に空の値を含めることはできません"
    MEMORY_TRANSITION_NOT_ALLOWED = "メモリの状態遷移は許可されていません"
    MEMORY_LOCK_VERSION_MISMATCH = "メモリが同時に更新されました。lock_version が一致しません"
    RULE_FILE_NOT_MAPPING = "ルールファイルは YAML の対応表である必要があります"
    RULE_FILE_FIELDS_MISSING = "ルールファイルに必須項目がありません"
    RULE_FILE_FIELDS_UNKNOWN = "ルールファイルに未知の項目があります"
    RULE_FILE_FIELD_NOT_STRING = "ルールファイルの項目は空でない文字列にしてください"
    RULE_FILE_SEVERITY_NOT_STRING = "ルールファイルの severity は文字列にしてください"
    RULE_FILE_SCOPE_NOT_MAPPING = "ルールファイルの scope は対応表にしてください"
    RULE_FILE_SCOPE_FIELDS_UNKNOWN = "ルールファイルの scope に未知の項目があります"
    RULE_FILE_SCOPE_NOT_ARRAY = "ルールファイルの scope の項目は配列にしてください"
    RULE_ID_DUPLICATED = "ルール ID が重複しています"
    RULE_FILE_READ_FAILED = "ルールファイルを読み込めません"
    RULE_FILE_YAML_INVALID = "ルールファイルの YAML が不正です"
    RULE_FILE_SEVERITY_INVALID = "ルールファイルの severity が不正です"
    RULE_FILE_INVALID = "ルールファイルが不正です"
    RULE_FILE_SCOPE_EMPTY_STRING = (
        "ルールファイルの scope の項目には空でない文字列を指定してください"
    )
    RULE_SCOPE_GROUP_REQUIRED = (
        "ルールの scope には languages、paths、keywords のいずれかが必要です。"
    )
    RULE_ID_FORMAT_INVALID = "ルール ID は <DOMAIN>.<ISSUE_TYPE> の形式で指定してください"
    RULE_ID_GENERAL_RESERVED = (
        "ルール ID の GENERAL 名前空間はプロジェクトルールなしの指摘用に予約されています。"
    )
    RULE_CATEGORY_REQUIRED = "ルールには category が必要です"
    RULE_INSTRUCTION_REQUIRED = "ルールには instruction が必要です"
    SCM_CHANGE_ID_EMPTY = "SCM 変更要求の識別子を空にすることはできません。"
    SCM_COMMENT_BODY_EMPTY = "SCM コメント本文を空にすることはできません。"
    SCM_COMMENT_LINE_INVALID = "SCM コメントの行番号は正の値にしてください。"
    SCM_COMMENT_POSITION_INVALID = "SCM の行コメントには位置を一つだけ指定してください。"
    SCM_COMMENT_REVISION_INVALID = "SCM コメントの改訂には完全な Git SHA を指定してください。"
    SCM_COMMENT_SIDE_INVALID = "SCM コメントは変更後側だけに指定できます。"
    SCM_REPOSITORY_ID_FORMAT_INVALID = (
        "SCM リポジトリ識別子は owner/repository の形式で指定してください。"
    )
    SCM_REVISION_INVALID = "SCM の改訂は小文字 40 桁の Git SHA で指定してください。"
    SCM_CHANGE_TIME_NO_TIMEZONE = "SCM 変更要求の時刻にはタイムゾーンが必要です。"
    SCM_COMMENT_UPDATE_INVALID = "SCM コメントの更新には識別子と本文が必要です。"
    SCM_PUBLICATION_TRANSITION_INVALID = "SCM 出版物の状態遷移が不正です"
    ACTOR_ID_REQUIRED = "操作を認可するには actor_id を指定してください。"
    ACTOR_NOT_MAINTAINER = "実行者はこの操作の保守担当者として設定されていません"

    @staticmethod
    def invalid_finding_id_format(raw_finding_id: str) -> str:
        """指摘 ID の形式が不正な場合のメッセージを返す."""
        return f"{Messages.FINDING_ID_FORMAT_INVALID}: {raw_finding_id}"

    @staticmethod
    def finding_id_sequence_not_numeric(raw_finding_id: str) -> str:
        """指摘 ID の連番が数字でない場合のメッセージを返す."""
        return f"{Messages.FINDING_ID_SEQUENCE_NOT_NUMERIC}: {raw_finding_id}"

    @staticmethod
    def memory_scope_not_array(name: str) -> str:
        """メモリ適用範囲の項目が配列でない場合のメッセージを返す."""
        return f"{Messages.MEMORY_SCOPE_NOT_ARRAY}: {name}"

    @staticmethod
    def repository_not_directory(path: Path) -> str:
        """リポジトリのパスがディレクトリでない場合のメッセージを返す."""
        return f"{Messages.REPOSITORY_NOT_DIRECTORY}: {path}"

    @staticmethod
    def repository_not_git_root(expected: Path, actual: Path) -> str:
        """リポジトリのパスが Git ルートでない場合のメッセージを返す."""
        return f"{Messages.REPOSITORY_NOT_GIT_ROOT}: 期待値 {expected}、指定値 {actual}"

    @staticmethod
    def invalid_git_revision(revision: str) -> str:
        """Git の改訂が不正な場合のメッセージを返す."""
        return f"{Messages.GIT_REVISION_INVALID}: {revision!r}"

    @staticmethod
    def diff_source_arguments_mismatch(
        kind: str,
        expected: list[str],
        present: list[str],
    ) -> str:
        """差分入力元の引数が不足または過剰な場合のメッセージを返す."""
        return (
            f"{Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH}: {kind} には {expected} が必要です。"
            f"指定値: {present}"
        )

    @staticmethod
    def patch_file_too_large(max_bytes: int) -> str:
        """パッチファイルが上限を超える場合のメッセージを返す."""
        return f"{Messages.PATCH_FILE_TOO_LARGE}: {max_bytes} バイト"

    @staticmethod
    def cannot_inspect_patch_file(path: Path, error: OSError) -> str:
        """パッチファイルの属性を取得できない場合のメッセージを返す."""
        return f"{Messages.PATCH_FILE_INSPECT_FAILED}: {path}: {error}"

    @staticmethod
    def cannot_read_patch_file(path: Path, error: Exception) -> str:
        """パッチファイルを読み込めない場合のメッセージを返す."""
        return f"{Messages.PATCH_FILE_READ_FAILED}: {path}: {error}"

    @staticmethod
    def git_command_not_executed(subcommand: str) -> str:
        """Git コマンドを起動できない場合のメッセージを返す."""
        return f"{Messages.GIT_COMMAND_NOT_EXECUTED}: {subcommand}"

    @staticmethod
    def git_command_failed(subcommand: str, detail: str) -> str:
        """Git コマンドが失敗した場合のメッセージを返す."""
        return f"{Messages.GIT_COMMAND_FAILED}: {subcommand}: {detail}"

    @staticmethod
    def memory_modified_concurrently(memory_id: str) -> str:
        """メモリの楽観ロックが競合した場合のメッセージを返す."""
        return f"{Messages.MEMORY_MODIFIED_CONCURRENTLY.rstrip('。')}: {memory_id}"

    @staticmethod
    def memory_version_not_found(memory_id: str, version: str) -> str:
        """メモリ版が存在しない場合のメッセージを返す."""
        return f"{Messages.MEMORY_VERSION_NOT_FOUND}: {memory_id}@{version}"

    @staticmethod
    def memory_content_hash_mismatch(memory_id: str, version: str) -> str:
        """保存済みメモリの内容ハッシュが一致しない場合のメッセージを返す."""
        return f"{Messages.MEMORY_CONTENT_HASH_MISMATCH}: {memory_id}@{version}"

    @staticmethod
    def scm_publication_transition_rejected(target: str) -> str:
        """SCM 出版物の状態遷移が許可されない場合のメッセージを返す."""
        return f"{Messages.SCM_PUBLICATION_TRANSITION_REJECTED}: {target}"

    @staticmethod
    def run_not_found_during_finalize(run_id: str) -> str:
        """最終化対象の実行が存在しない場合のメッセージを返す."""
        return f"{Messages.RUN_NOT_FOUND_DURING_FINALIZE}: {run_id}"

    @staticmethod
    def invalid_llm_dataset(path: Path) -> str:
        """読み込みまたは検証に失敗したデータセットのメッセージを返す."""
        return f"LLM データセットを読み込めないか、形式が不正です: {path}"

    @staticmethod
    def duplicate_dataset_case_id(path: Path) -> str:
        """ケース ID が重複したデータセットのメッセージを返す."""
        return f"LLM データセットの case_id が重複しています: {path}"

    @staticmethod
    def duplicate_dataset_response_key(path: Path) -> str:
        """複数ターン応答キーが重複したデータセットのメッセージを返す."""
        return f"LLM データセットの複数ターン応答キーが重複しています: {path}"

    @staticmethod
    def invalid_dataset_request_label() -> str:
        """複数ターン照合に使用できない要求ラベルのメッセージを返す."""
        return "LLM データセットの複数ターン要求ラベルが不正です。"

    @staticmethod
    def missing_dataset_response() -> str:
        """要求に対応する複数ターン応答がない場合のメッセージを返す."""
        return "LLM データセットに要求と一致する複数ターン応答がありません。"

    @staticmethod
    def mismatched_dataset_request_hash() -> str:
        """固定要求ハッシュが一致しない場合のメッセージを返す."""
        return "LLM データセットの要求ハッシュが一致しません。"

    @staticmethod
    def unused_dataset_responses() -> str:
        """選択済みケースに未使用応答が残る場合のメッセージを返す."""
        return "LLM データセットの選択済みケースに未使用応答があります。"

    @staticmethod
    def closed_dataset_client() -> str:
        """終了後のデータセット利用を拒否するメッセージを返す."""
        return "終了済みの LLM データセットは使用できません。"

    @staticmethod
    def configured_path_outside_repository(label: str) -> str:
        """リポジトリ外を指す設定パスのメッセージを返す."""
        return f"{label}はリポジトリ内を指定してください。"

    @staticmethod
    def memory_not_found(memory_id: str) -> str:
        """対象メモリが存在しない場合のメッセージを返す."""
        return f"メモリが見つかりません: {memory_id}"

    @staticmethod
    def invalid_memory_scope_file(path: str) -> str:
        """適用範囲ファイルを読み込めない場合のメッセージを返す."""
        return f"適用範囲ファイルを読み込めないか、形式が不正です: {path}"

    @staticmethod
    def feedback_run_not_found(run_id: str) -> str:
        """対象実行が存在しない場合のメッセージを返す."""
        return f"{Messages.FEEDBACK_RUN_NOT_FOUND}: {run_id}"

    @staticmethod
    def file_not_found(path: str) -> str:
        """対象ファイルが存在しない場合のメッセージを返す."""
        return f"対象ファイルが見つかりません: {path}"

    @staticmethod
    def llm_request_failed(error_type: str) -> str:
        """LLM 呼び出し失敗メッセージを返す."""
        return f"LLM の呼び出しに失敗しました: {error_type}"

    @staticmethod
    def litellm_transient_gateway_failure(error_type: str) -> str:
        """LiteLLM の一時的なゲートウェイ障害メッセージを返す."""
        return f"LiteLLM の一時的なゲートウェイ障害です: {error_type}"

    @staticmethod
    def litellm_gateway_request_failed(error_type: str) -> str:
        """LiteLLM の再試行不可要求エラーメッセージを返す."""
        return f"LiteLLM のゲートウェイ要求に失敗しました: {error_type}"
