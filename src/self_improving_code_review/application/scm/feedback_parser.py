"""SCM コメント内の明示フィードバックコマンドを解析する."""

from __future__ import annotations

import re

from self_improving_code_review.application.scm.models import ParsedFeedbackCommand
from self_improving_code_review.domain.common.enums import FeedbackLabel
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.feedback.validators import normalize_free_text
from self_improving_code_review.messages import Messages

_LABELS = "true-positive|false-positive|needs-more-evidence|not-applicable"
_COMMAND = re.compile(
    rf'^/review-feedback (?P<label>{_LABELS}) --reason "(?P<reason>[^"\r\n]+)"\s*$'
)


def parse_feedback_command(body: str) -> ParsedFeedbackCommand:
    """完全一致する一個の明示コマンドだけを M2 入力へ変換する."""
    match = _COMMAND.fullmatch(body)
    if match is None:
        raise InvalidInputError(Messages.SCM_FEEDBACK_COMMAND_INVALID)
    return ParsedFeedbackCommand(
        label=FeedbackLabel(match.group("label")),
        reason=normalize_free_text("reason", match.group("reason")),
    )
