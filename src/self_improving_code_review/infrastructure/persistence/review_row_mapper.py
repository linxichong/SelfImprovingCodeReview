"""保存済み実行と指摘の行をドメイン型へ復元する."""

from __future__ import annotations

import json
from datetime import datetime

from self_improving_code_review.application.review.models import (
    RunRecord,
)
from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
    RunStatus,
    Severity,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    LineRange,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    Finding,
    ValidationOutcome,
)


def row_to_run(row: tuple[object, ...]) -> RunRecord:
    """冪等性の報告に必要な実行フィールドを再構築する."""
    from self_improving_code_review.application.review.models import RunVersions
    from self_improving_code_review.domain.common.enums import ReviewMode
    from self_improving_code_review.domain.common.values import ProjectId

    return RunRecord(
        run_id=RunId(str(row[0])),
        project_id=ProjectId(str(row[1])),
        mode=ReviewMode(str(row[2])),
        status=RunStatus(str(row[3])),
        source_revision=str(row[4]) if row[4] is not None else None,
        model=str(row[5]),
        versions=RunVersions(
            package_version=str(row[6]),
            config_hash=str(row[7]),
            prompt_version=str(row[8]),
            prompt_hash=str(row[9]),
            rule_snapshot_id=str(row[10]),
            rule_set_hash=str(row[11]),
            is_rule_set_empty=False,
            secret_engine_version=str(row[12]),
            memory_snapshot_id=str(row[13]) if row[13] is not None else None,
            agent_prompt_version=(str(row[17]) if len(row) > 17 and row[17] is not None else None),
            agent_prompt_hash=(str(row[18]) if len(row) > 18 and row[18] is not None else None),
            reflection_prompt_version=(
                str(row[19]) if len(row) > 19 and row[19] is not None else None
            ),
            reflection_prompt_hash=(
                str(row[20]) if len(row) > 20 and row[20] is not None else None
            ),
            pipeline_config_hash=(
                str(row[23]) if len(row) > 23 and row[23] is not None else str(row[7])
            ),
        ),
        idempotency_key=str(row[14]),
        started_at=datetime.fromisoformat(str(row[15])),
        finished_at=datetime.fromisoformat(str(row[16])) if row[16] is not None else None,
        resume_count=int(str(row[21])) if len(row) > 21 else 0,
    )


def row_to_finding(run_id: RunId, row: tuple[object, ...]) -> Finding:
    """永続化された 1 件の指摘を再構築する."""
    bundle_sequence = int(str(row[2]).removeprefix("B"))
    finding_sequence = int(str(row[1]).rsplit("F", maxsplit=1)[1])
    bundle_id = BundleId(run_id, bundle_sequence)
    candidate = CandidateFinding(
        file=NormalizedPath(str(row[3])),
        line_range=LineRange(int(str(row[4])), int(str(row[5]))),
        category=str(row[6]),
        issue_type=str(row[7]),
        rule_id=str(row[8]),
        primary_entity=str(row[9]),
        severity=Severity(str(row[10])),
        title=str(row[11]),
        description=str(row[12]),
        evidence=Evidence(quote=str(row[13]), reason=str(row[14])),
        suggestion=str(row[15]),
        needs_context=str(row[20]) == ReflectionStatus.NEEDS_CONTEXT.value,
    )
    return Finding(
        finding_id=FindingId(bundle_id, finding_sequence),
        bundle_id=bundle_id,
        candidate=candidate,
        fingerprint=str(row[16]),
        enclosing_symbol=str(row[17]),
        validation=ValidationOutcome(
            position_status=ValidationStatus(str(row[18])),
            evidence_status=ValidationStatus(str(row[19])),
            reflection_status=ReflectionStatus(str(row[20])),
            reason_codes=tuple(str(item) for item in json.loads(str(row[21]))),
        ),
    )
