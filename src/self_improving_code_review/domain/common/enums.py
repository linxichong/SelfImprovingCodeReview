"""レビューパイプライン全体で共有する列挙型.

内部メンバーには ``UPPER_SNAKE_CASE`` を使用し、``value`` には
システム設計書で定義した外部インターフェース表現を保持する.
"""

from enum import Enum

__all__ = [
    "BundleEdgeDecision",
    "BundleRelation",
    "BundleStatus",
    "ChangeType",
    "CheckpointStage",
    "ConflictResolution",
    "ConflictStatus",
    "DiffSourceKind",
    "EvaluationArm",
    "ExtractionStatus",
    "FeedbackLabel",
    "FileDecision",
    "FilterReason",
    "GroundTruthStatus",
    "LineKind",
    "MatchResult",
    "MemoryCategory",
    "MemoryPolarity",
    "MemoryStatus",
    "MetricStatus",
    "Permission",
    "ReflectionStatus",
    "RevalidationStatus",
    "ReviewMode",
    "ReviewerType",
    "RiskLevel",
    "RunStatus",
    "Severity",
    "ToolStatus",
    "ValidationStatus",
]


class DiffSourceKind(Enum):
    """レビュー対象となる統一差分の入力元."""

    WORKSPACE = "workspace"
    RANGE = "range"
    COMMIT = "commit"
    FILE = "file"


class ReviewMode(Enum):
    """実行時に有効化する知識の入力元.

    ``BASELINE`` はルールなしの評価群、``RULES`` は M1 の
    既定値、``MEMORY`` は M2 用の予約値とする.
    """

    BASELINE = "baseline"
    RULES = "rules"
    MEMORY = "memory"


class RunStatus(Enum):
    """レビュー実行のライフサイクル状態."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial-success"
    FAILED = "failed"
    BLOCKED = "blocked"


class BundleStatus(Enum):
    """単一レビュー単位の処理結果."""

    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial-success"
    FAILED = "failed"
    SKIPPED = "skipped"


class BundleRelation(Enum):
    """変更ファイル間で検出した決定的な関係."""

    IMPLEMENTATION_TEST = "implementation-test"
    INTERFACE_IMPLEMENTATION = "interface-implementation"
    SCHEMA_PROCESSING = "schema-processing"
    MODEL_MIGRATION = "model-migration"
    IMPORT_REFERENCE = "import-reference"
    SAME_DIRECTORY_STEM = "same-directory-stem"


class BundleEdgeDecision(Enum):
    """レビュー単位の関係辺を採用または切断した結果."""

    SELECTED = "selected"
    CUT_CYCLE = "cut-cycle"
    CUT_BUDGET = "cut-budget"


class CheckpointStage(Enum):
    """M3 の中断再開チェックポイント."""

    DIFF_PARSED = "diff-parsed"
    PLAN_CREATED = "plan-created"
    BUNDLE_COMPLETED = "bundle-completed"
    FINALIZED = "finalized"


class ToolStatus(Enum):
    """限定コンテキストツールの処理状態."""

    SUCCESS = "success"
    NOT_FOUND = "not-found"
    INVALID_REQUEST = "invalid-request"
    REPEATED_REQUEST = "repeated-request"
    BUDGET_EXCEEDED = "budget-exceeded"
    UNSUPPORTED_LANGUAGE = "unsupported-language"
    FAILED = "failed"
    BLOCKED = "blocked"


class ChangeType(Enum):
    """差分内のファイルに記録する変更種別."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"


class LineKind(Enum):
    """差分ブロック内の単一行の役割."""

    CONTEXT = "context"
    ADDED = "added"
    DELETED = "deleted"


class FileDecision(Enum):
    """ファイル選別処理が割り当てるレビュー判定."""

    REVIEW = "review"
    LIGHTWEIGHT = "lightweight"
    FILTER = "filter"


class FilterReason(Enum):
    """既定以外のファイル判定を説明する理由コード."""

    BINARY = "binary"
    GENERATED = "generated"
    LOCK_FILE = "lock-file"
    VENDOR = "vendor"
    BUILD_OUTPUT = "build-output"
    MINIFIED = "minified"
    EXCLUDED = "excluded"
    DOCUMENT = "document"
    DELETED = "deleted"
    NO_COMMENTABLE_LINE = "no-commentable-line"
    BUDGET_EXCEEDED = "budget-exceeded"


class RiskLevel(Enum):
    """振り分けと予算決定に使用するリスク分類."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewerType(Enum):
    """レビュー担当の専門種別. M1 では常に ``GENERAL`` を使用する."""

    GENERAL = "general"
    SECURITY = "security"
    DATA = "data"
    TEST = "test"


class Severity(Enum):
    """指摘およびルールの重大度."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        """値が大きいほど重大な指摘となる、並べ替え可能な順位を返す."""
        return _SEVERITY_RANKS[self]


_SEVERITY_RANKS: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class ValidationStatus(Enum):
    """決定的な検証処理の結果."""

    VALID = "valid"
    INVALID = "invalid"
    NEEDS_CONTEXT = "needs-context"


class ReflectionStatus(Enum):
    """指摘の反証状態.

    ``REFLECTION_SKIPPED`` は自己検証を実行していないことを示し、
    反証を確認済みであることは保証しない.
    """

    SUPPORTED = "supported"
    REFLECTION_SKIPPED = "reflection-skipped"
    NEEDS_CONTEXT = "needs-context"
    CONTRADICTED = "contradicted"
    DUPLICATE = "duplicate"


class FeedbackLabel(Enum):
    """既存指摘への評価、または見逃し問題の報告種別.

    ``MISSED_ISSUE`` は見逃し報告専用の内部ラベルであり、
    既存指摘の評価として受け付けてはならない.
    """

    TRUE_POSITIVE = "true-positive"
    FALSE_POSITIVE = "false-positive"
    NEEDS_MORE_EVIDENCE = "needs-more-evidence"
    NOT_APPLICABLE = "not-applicable"
    MISSED_ISSUE = "missed-issue"

    @property
    def is_missed_issue(self) -> bool:
        """見逃し報告専用ラベルの場合は True を返す."""
        return self is FeedbackLabel.MISSED_ISSUE


class MemoryCategory(Enum):
    """メモリが表す知識の分類."""

    FRAMEWORK = "framework"
    ARCHITECTURE = "architecture"
    DOMAIN_RULE = "domain-rule"
    SECURITY = "security"
    TESTING = "testing"
    STYLE = "style"


class MemoryPolarity(Enum):
    """メモリがレビューへ与える方向.

    ``SUPPRESS`` は指摘の削除命令ではなく、報告前に強い根拠を
    要求する指示へ正規化する.
    """

    REINFORCE = "reinforce"
    SUPPRESS = "suppress"


class MemoryStatus(Enum):
    """メモリの統制状態. 有効化には人間の承認を必須とする."""

    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"

    @property
    def is_retrievable(self) -> bool:
        """検索対象として注入できる状態の場合は True を返す."""
        return self is MemoryStatus.ACTIVE


class ConflictStatus(Enum):
    """メモリ競合の解決状態."""

    OPEN = "open"
    RESOLVED = "resolved"


class ConflictResolution(Enum):
    """人間が選択できるメモリ競合の解決方法."""

    NARROW_SCOPE = "narrow-scope"
    SUPERSEDE_LEFT = "supersede-left"
    SUPERSEDE_RIGHT = "supersede-right"
    REJECT_CANDIDATE = "reject-candidate"


class RevalidationStatus(Enum):
    """メモリ再検証の判定状態."""

    DUE = "due"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"


class ExtractionStatus(Enum):
    """メモリ候補抽出実行の状態."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"

    @property
    def is_terminal(self) -> bool:
        """再開できない終了状態の場合は True を返す."""
        return self in {
            ExtractionStatus.SUCCESS,
            ExtractionStatus.BLOCKED,
        }


class Permission(Enum):
    """実行者に要求する操作権限."""

    RECORD_FEEDBACK = "record-feedback"
    APPROVE_MEMORY = "approve-memory"
    PUBLISH_GROUND_TRUTH = "publish-ground-truth"
    ADJUDICATE_EVALUATION = "adjudicate-evaluation"
    GOVERN_MEMORY = "govern-memory"
    REVALIDATE_MEMORY = "revalidate-memory"

    MANAGE_PROJECT = "manage-project"
    VERIFY_BACKUP = "verify-backup"


class EvaluationArm(Enum):
    """評価で比較する実行条件.

    ``BASELINE`` はルールとメモリなし、``RULES`` はルールのみ、
    ``MEMORY`` は同一ルールに固定メモリを追加する.
    """

    BASELINE = "A"
    RULES = "B"
    MEMORY = "C"


class GroundTruthStatus(Enum):
    """正解データ集合および個別問題の状態."""

    DRAFT = "draft"
    VERIFIED = "verified"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class MatchResult(Enum):
    """指摘と正解データの照合結果."""

    TRUE_POSITIVE = "true-positive"
    FALSE_POSITIVE = "false-positive"
    FALSE_NEGATIVE = "false-negative"
    DUPLICATE = "duplicate"
    REVIEW_REQUIRED = "review-required"


class MetricStatus(Enum):
    """測定値の算出状態. 母数が 0 の場合は評価不能とする."""

    EVALUATED = "evaluated"
    NOT_EVALUABLE = "not-evaluable"
