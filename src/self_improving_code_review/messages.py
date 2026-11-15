"""アプリケーションで使用するメッセージの集中定義."""

from pathlib import Path

__all__ = ["Messages"]


class Messages:
    """利用者向けメッセージと安全な診断メッセージを管理する."""

    TEST_CASE_DUPLICATE_DESIGN: str = "设计用例 ID 重复"
    TEST_CASE_NO_DESIGN: str = "找不到设计用例 ID。"
    TEST_CASE_EMPTY_MARKER: str = "case 标记必须指定用例 ID。"
    TEST_CASE_ARGUMENTS: str = "case 标记仅接受固定字符串位置参数。"
    TEST_CASE_INVALID_ID: str = "用例 ID 无效。"
    TEST_CASE_DUPLICATE_ID: str = "case 标记内的用例 ID 重复。"

    API_KEY_REQUIRED = "未配置 AI 网关凭据。"
    HTTPS_REQUIRED = "AI 网关 URL 必须使用 HTTPS。"
    MODEL_REQUIRED = "请指定 AI 网关使用的固定模型 ID。"
    OTEL_DEPENDENCY_REQUIRED = "使用 OpenTelemetry 前，请安装相应的可选依赖。"
    OTEL_ENDPOINT_REQUIRED = "未配置 OpenTelemetry 的发送目标。"
    RUN_LEASE_CONFLICT = "另一进程正在执行该审查。"
    RUN_LEASE_LOST = "已失去审查运行的所有权，无法保存结果。"
    OTEL_HEADERS_INVALID = "OpenTelemetry 认证请求头格式无效。"

    LLM_TIMEOUT = "LLM 调用超时。"
    INVALID_LLM_OUTPUT = "LLM 响应格式无效。"
    LITELLM_RESPONSE_VALIDATION_FAILED = "LiteLLM 响应验证失败。"
    LITELLM_RESPONSE_CONTENT_MISSING = "LiteLLM 响应缺少消息正文。"
    LITELLM_RESPONSE_CONTENT_NOT_JSON_STRING = "LiteLLM 消息正文必须是 JSON 字符串。"
    LITELLM_RESPONSE_JSON_OBJECT_MISSING = "LiteLLM 消息正文缺少 JSON 对象。"
    LITELLM_RESPONSE_NOT_OBJECT = "LiteLLM 响应必须为对象。"
    EXTERNAL_SOURCE_PERMISSION_REQUIRED = (
        "发送至真实 AI 网关前，请将 security.allow_external_source 设为 true。"
    )
    DIFF_SOURCE_REQUIRED = "请指定一个差异来源。"
    DATASET_PATH_REQUIRED = "dataset 提供方需要配置 llm.dataset_path。"
    FEEDBACK_RUN_NOT_FOUND = "找不到目标审查运行。"
    FEEDBACK_REVISION_REQUIRED = "目标运行必须具备固定的源代码修订版本。"
    MISSED_ISSUE_FILE_NOT_FOUND = "目标修订中不存在漏报问题所在文件。"
    MISSED_ISSUE_LINE_OUTSIDE_FILE = "漏报问题的行号超出文件范围。"
    MISSED_ISSUE_SYMBOL_NOT_FOUND = "目标文件中不存在漏报问题对应的符号。"
    MISSED_ISSUE_LOCATION_MISMATCH = "漏报问题的行号与符号指向不同位置。"

    CLI_DESCRIPTION = "解析 Git 差异并生成经过验证的代码审查结果。"
    CLI_HELP_HELP = "显示此帮助并退出。"
    CLI_OPTIONS_TITLE = "选项"
    CLI_COMMANDS_TITLE = "命令"
    CLI_DIFF_SOURCE_TITLE = "差异来源"
    DATABASE_PATH_LABEL = "数据库路径"
    BACKUP_DATABASE_PATH_LABEL = "备份数据库路径"
    LLM_DATASET_PATH_LABEL = "LLM 数据集路径"
    GROUND_TRUTH_INPUT_LABEL = "正确答案数据输入文件"
    ADJUDICATION_OUTPUT_LABEL = "裁决输出文件"
    CLI_REVIEW_HELP = "审查指定的 Git 差异。"
    CLI_REVIEW_DESCRIPTION = (
        "选择一个差异来源，应用规则、检查敏感信息、调用 LLM 审查并执行确定性验证。"
    )
    CLI_RULES_HELP = "诊断规则的适用情况。"
    CLI_RULES_DESCRIPTION = "对项目规则执行确定性诊断。"
    CLI_RULES_CHECK_HELP = "显示指定差异命中规则的原因和预算选择结果。"
    CLI_RULES_CHECK_DESCRIPTION = (
        "跳过 LLM 调用，执行与正式审查相同的规则匹配、风险判定和 Token 预算分配。"
    )
    CLI_REPOSITORY_HELP = "要审查的 Git 仓库，默认为当前目录。"
    CLI_PROJECT_HELP = "用于覆盖配置文件中 project_id 的项目标识符。"
    CLI_CONFIG_HELP = "要读取的 YAML 配置文件。相对路径以当前目录为基准。"
    CLI_MODE_HELP = "审查模式。baseline 不使用规则，rules 使用规则，memory 使用规则和已批准的记忆。"
    CLI_OUTPUT_HELP = "结果 JSON 的保存位置。省略时输出到标准输出，指定时以原子方式保存。"
    CLI_RULE_FILE_HELP = "将诊断范围限定为指定的一个变更文件。"
    CLI_FEEDBACK_HELP = "记录审查结果的评价或漏报问题。"
    CLI_FEEDBACK_DESCRIPTION = "评价和漏报报告采用追加方式保存。订正时创建新记录。"
    CLI_FEEDBACK_FINDING_HELP = "记录对一条现有审查发现的评价。"
    CLI_FEEDBACK_MISSED_ISSUE_HELP = "记录审查漏报的问题。"
    CLI_FEEDBACK_RUN_HELP = "要评价的审查运行标识符。"
    CLI_FEEDBACK_FINDING_ID_HELP = "要评价的审查发现标识符。例如 B001-F001。"
    CLI_FEEDBACK_LABEL_HELP = "审查发现的评价标签。此处禁用 missed-issue。"
    CLI_FEEDBACK_REASON_HELP = "评价理由，审计记录必填。"
    CLI_FEEDBACK_ACTOR_HELP = "执行操作的人员标识符。"
    CLI_FEEDBACK_FILE_HELP = "漏报问题所在文件的仓库相对路径。"
    CLI_FEEDBACK_START_LINE_HELP = "漏报问题的起始行。至少指定此项或 --symbol。"
    CLI_FEEDBACK_SYMBOL_HELP = "包含漏报问题的函数或类名。"
    CLI_FEEDBACK_ISSUE_TYPE_HELP = "漏报问题的类别。例如 null-dereference。"
    CLI_FEEDBACK_SEVERITY_HELP = "漏报问题的严重级别。"
    CLI_FEEDBACK_DESCRIPTION_HELP = "漏报问题的说明。"
    CLI_FEEDBACK_EVIDENCE_HELP = "支持漏报问题的依据。"
    CLI_MEMORY_HELP = "列出、查看、批准或驳回提取的记忆候选。"
    CLI_MEMORY_DESCRIPTION = "批准和驳回属于治理操作。无法识别执行者时操作失败。"
    CLI_MEMORY_LIST_HELP = "列出指定状态的记忆。"
    CLI_MEMORY_SHOW_HELP = "显示指定记忆的当前版本。"
    CLI_MEMORY_APPROVE_HELP = "启用记忆候选。"
    CLI_MEMORY_REJECT_HELP = "驳回记忆候选。"
    CLI_MEMORY_ID_HELP = "目标记忆的标识符。"
    CLI_MEMORY_STATUS_HELP = "要列出的记忆状态。"
    CLI_MEMORY_REASON_HELP = "批准或驳回的理由，审计记录必填。"
    CLI_MEMORY_LOCK_VERSION_HELP = "乐观锁预期的当前版本，可通过 show 查看。"
    CLI_MEMORY_SCOPE_FILE_HELP = "用于缩小适用范围的 YAML 文件。"
    CLI_MEMORY_ARCHIVE_HELP = "归档有效记忆并保留历史记录。"
    CLI_MEMORY_RESTORE_HELP = "将归档记忆恢复为新的待批准版本。"
    CLI_MEMORY_SUPERSEDE_HELP = "用另一条有效记忆替换当前有效记忆。"
    CLI_MEMORY_REPLACEMENT_HELP = "替换后使用的有效记忆标识符。"
    CLI_MEMORY_CONFLICT_HELP = "列出或解决已检测到的记忆冲突。"
    CLI_MEMORY_CONFLICT_ID_HELP = "目标冲突的安全标识符。"
    CLI_MEMORY_CONFLICT_STATUS_HELP = "要列出的冲突状态。"
    CLI_MEMORY_RESOLUTION_HELP = "人工选择的冲突解决方式。"
    CLI_MEMORY_EVIDENCE_HELP = "支持治理决定的依据摘要。"
    CLI_MEMORY_REVALIDATION_HELP = "列出、重新确认或停用到期记忆。"
    CLI_MEMORY_LIFECYCLE_DISABLED = (
        "使用 M5 记忆生命周期功能前，请启用 governance.enabled 和 "
        "governance.memory_lifecycle_enabled。"
    )
    CLI_EVAL_HELP = "使用已发布的正确答案数据评估 A/B/C 比较组。"
    CLI_EVAL_ARMS_HELP = "使用逗号分隔要执行的比较组。例如 B,C。"
    CLI_EVAL_SPLIT_HELP = "正确答案数据的划分，可选 development 或 test。"
    CLI_EVAL_SET_HELP = "要使用的已发布正确答案数据集版本。"
    CLI_EVAL_REPEAT_HELP = "每个比较组的重复次数，至少为 3。"
    CLI_GROUND_TRUTH_HELP = "管理评估使用的正确答案数据集。"
    CLI_GROUND_TRUTH_PUBLISH_HELP = "将已批准的问题发布为不可变的正确答案数据集。"
    CLI_GROUND_TRUTH_INPUT_HELP = "包含待发布正确答案问题的 JSON 文件。"
    CLI_GROUND_TRUTH_ACTOR_HELP = "发布正确答案数据集的维护人员标识符。"
    CLI_ADJUDICATION_HELP = "以盲化形式导出未裁决的评估匹配项，并记录人工裁决。"
    CLI_ADJUDICATION_EXPORT_HELP = "以 JSON 导出隐藏比较组信息的未裁决项。"
    CLI_ADJUDICATION_RESOLVE_HELP = "记录指定盲化令牌对应的人工裁决。"
    CLI_ADJUDICATION_REPORT_HELP = "重新生成应用裁决后的 B/C 最终判定。"
    # 利用者向け説明文であり、認証情報を保持しない。
    CLI_ADJUDICATION_TOKEN_HELP = "盲化输出中包含的裁决令牌。"  # nosec B105
    CLI_ADJUDICATION_RESULT_HELP = "人工裁决结果。"
    CLI_ADJUDICATION_ISSUE_HELP = "对应的正确答案问题 ID。"
    INVALID_GROUND_TRUTH_DOCUMENT = "无法读取正确答案数据文档，或文档格式无效。"
    GROUND_TRUTH_DOCUMENT_NOT_FOUND = "正确答案数据输入文件不存在。"
    GROUND_TRUTH_DOCUMENT_TOO_LARGE = "正确答案数据输入文件超过 5 MiB。"
    GROUND_TRUTH_SET_ALREADY_EXISTS = "相同划分和版本的正确答案数据集已经发布。"
    EVALUATION_CANDIDATE_TARGET_EXISTS = "评估候选的生成目标已存在。"
    DUPLICATE_GROUND_TRUTH_SEQUENCE = "正确答案问题的 sequence 重复。"
    INVALID_GROUND_TRUTH_APPROVAL = "每个正确答案问题都需要与创建者不同的验证者，以及批准者。"
    GROUND_TRUTH_CASES_REQUIRED = "模式版本 2 必须指定参与评估的 cases。"
    DUPLICATE_GROUND_TRUTH_CASE = "正确答案用例的 sequence 或 change_ref 重复。"
    INVALID_GROUND_TRUTH_CASE_APPROVAL = "每个正确答案用例都需要与创建者不同的验证者，以及批准者。"
    GROUND_TRUTH_ISSUE_CASE_MISSING = "找不到与正确答案问题的 change_ref 对应的用例。"
    MEMORY_EXTRACTION_TEST_DATA_FORBIDDEN = "禁止根据测试集的评估结果生成记忆候选。"
    SCM_ACTOR_MISMATCH = "SCM 认证身份与配置的机器人账号不一致。"
    SCM_BASE_SHA_MISMATCH = "本地 HEAD 与变更请求的基准 SHA 不一致。"
    SCM_CHANGE_NOT_OPEN = "仅支持审查打开状态的变更请求。"
    SCM_DISABLED = "尚未启用 SCM 集成。"
    SCM_DRAFT_NOT_ALLOWED = "配置未允许审查草稿变更请求。"
    SCM_HEAD_SHA_CHANGED = "审查完成后，变更请求的头部 SHA 已更新。"
    SCM_FEEDBACK_COMMAND_INVALID = "SCM 反馈命令格式无效。"
    SCM_FEEDBACK_SYNC_DISABLED = "尚未启用 SCM 反馈同步。"
    SCM_FEEDBACK_TARGET_INVALID = "SCM 反馈指向的审查发现无效。"
    # 利用者向け遮断理由であり、検出値を含めない。
    SCM_PUBLICATION_SECRET_DETECTED = "在待发布的 SCM 内容中检测到疑似敏感信息。"  # nosec B105
    SPECIALIST_INPUT_BLOCKED = "向专项审查者发送前检测到疑似敏感信息。"
    SCM_PUBLISHER_REQUIRED = "尚未配置 SCM 发布处理。"
    SCM_REPOSITORY_MISMATCH = "变更请求的仓库与配置不一致。"
    SCM_REVIEW_NOT_SUCCESSFUL = "审查未成功完成，无法发布到 SCM。"
    # 利用者向け設定エラーであり、認証情報を保持しない。
    SCM_TOKEN_REQUIRED = "未配置 SCM 凭据。"  # nosec B105
    CLI_SCM_HELP = "审查 SCM 变更请求并同步结果。"
    CLI_SCM_DESCRIPTION = "将变更请求的固定 SHA 与本地 Git 核对，再执行现有审查流程。"
    CLI_SCM_REVIEW_HELP = "审查变更请求。默认关闭发布。"
    CLI_SCM_SYNC_FEEDBACK_HELP = "将已发布行评论的显式评价同步到 M2 反馈。"
    CLI_SCM_CHANGE_HELP = "要审查的 Pull Request 编号。"
    CLI_SCM_PUBLISH_HELP = "将已验证的结果发布到 SCM。还需设置 scm.publish_enabled=true。"
    CLI_DIFF_SOURCE_DESCRIPTION = "请从以下差异来源中选择一项。"
    CLI_GIT_DIFF_HELP = "使用 git diff 审查工作区的未暂存差异。"
    CLI_RANGE_HELP = "审查 BASE...HEAD 的三点比较差异。两端均须指定提交或引用。"
    CLI_COMMIT_HELP = "使用 git show 审查指定提交引入的差异。"
    CLI_DIFF_FILE_HELP = "审查 UTF-8 补丁文件。文件最大为 20 MiB。"
    CLI_GOVERNANCE_HELP = "管理 M5 项目治理。"
    CLI_GOVERNANCE_DESCRIPTION = "管理项目注册、仓库绑定与现有资源归属，并记录审计信息。"
    CLI_GOVERNANCE_PROJECT_HELP = "管理项目归属。"
    CLI_GOVERNANCE_PROJECT_LIST_HELP = "列出已注册项目。"
    CLI_GOVERNANCE_PROJECT_REGISTER_HELP = "注册项目。"
    CLI_GOVERNANCE_BIND_REPOSITORY_HELP = "将外部仓库绑定到项目。"
    CLI_GOVERNANCE_ADOPT_EXISTING_HELP = "将一项尚无归属的现有资源绑定到项目。"
    CLI_GOVERNANCE_PROJECT_NAME_HELP = "面向用户显示的项目名称。"
    CLI_GOVERNANCE_PROVIDER_HELP = "仓库提供方。例如 github。"
    CLI_GOVERNANCE_REPOSITORY_ID_HELP = "在提供方内唯一的仓库标识符。"
    CLI_GOVERNANCE_RESOURCE_TYPE_HELP = "现有资源的类型。"
    CLI_GOVERNANCE_RESOURCE_ID_HELP = (
        "现有资源标识符。SCM 资源采用 provider|repository|change 格式。"
    )
    CLI_GOVERNANCE_REASON_HELP = "治理操作的理由。仅保存用于审计的哈希。"
    CLI_GOVERNANCE_BACKUP_HELP = "管理 SQLite 备份。"
    CLI_GOVERNANCE_BACKUP_VERIFY_HELP = "检查已还原的 SQLite 备份。"
    CLI_GOVERNANCE_BACKUP_DATABASE_HELP = "要检查的已还原 SQLite 数据库路径。"
    CLI_GOVERNANCE_BACKUP_ACTOR_HELP = "执行备份检查的维护人员。"
    BACKUP_DATABASE_NOT_FOUND = "要检查的备份数据库不存在。"

    ADJUDICATION_REPORTS_REQUIRED = "需要比较组 B 和 C 的评估报告。"
    EVAL_ARMS_INVALID = "比较组只允许 A、B、C。"
    EVAL_ARMS_REQUIRED = "请至少指定一个比较组。"

    CONTEXT_SOURCE_CHANGED = "审查期间补充上下文的来源发生变化。"
    CONTEXT_SOURCE_READ_FAILED = "无法列出补充上下文的来源。"
    CONTEXT_SOURCE_TOO_LARGE = "补充上下文文件超过配置的大小上限。"
    CONTEXT_SOURCE_NOT_UTF8 = "补充上下文文件必须是 UTF-8 文本。"
    CONTEXT_REVISION_INVALID = "请为补充上下文的修订指定完整的提交 SHA。"
    FILE_DIFF_VERIFICATION_FAILED = "无法验证补丁文件与工作区的一致性。"
    FEEDBACK_REVISION_INVALID = "请为反馈目标的修订指定完整的提交 SHA。"
    FEEDBACK_REVISION_READ_FAILED = "无法读取反馈目标的修订。"
    FEEDBACK_SOURCE_TOO_LARGE = "反馈目标文件超过验证所用的大小上限。"
    FEEDBACK_SOURCE_NOT_UTF8 = "反馈目标文件必须是 UTF-8 文本。"

    PUBLISHED_GROUND_TRUTH_SET_NOT_FOUND = "找不到已发布的正确答案数据集。"
    EVALUATION_DATABASE_FAILED = "评估数据库操作失败。"
    GROUND_TRUTH_SET_NOT_PUBLISHED = "请先发布正确答案数据集，再保存。"
    ADJUDICATION_ITEM_NOT_FOUND = "找不到待裁决项。"
    ADJUDICATION_ISSUE_REQUIRED = "正确检出和重复发现需要 issue_id。"
    ADJUDICATION_ISSUE_FORBIDDEN = "误报不得指定 issue_id。"
    ADJUDICATION_ISSUE_NOT_IN_SET = "正确答案问题不属于本次评估使用的数据集。"
    ADJUDICATION_ACTOR_REQUIRED = "请指定裁决人。"
    ADJUDICATION_REASON_REQUIRED = "请指定裁决理由。"

    PROJECT_ALREADY_REGISTERED = "项目 ID 已注册。"
    REPOSITORY_ALREADY_BOUND = "该仓库已绑定到另一个项目。"
    GOVERNANCE_RESOURCE_NOT_FOUND = "找不到治理目标资源。"
    GOVERNANCE_RESOURCE_OTHER_PROJECT = "治理目标资源属于另一个项目。"
    SCM_RESOURCE_ID_INVALID = (
        "SCM 资源 ID 必须使用 provider|repository_external_id|change_external_id 格式。"
    )
    GOVERNANCE_PROJECT_REGISTER_FAILED = "无法注册项目。"
    GOVERNANCE_PROJECT_LIST_FAILED = "无法获取项目列表。"
    GOVERNANCE_PROJECT_READ_FAILED = "无法读取项目。"
    GOVERNANCE_REPOSITORY_BIND_FAILED = "无法将仓库绑定到项目。"
    GOVERNANCE_RESOURCE_ADOPT_FAILED = "无法将现有资源归入项目。"
    GOVERNANCE_CONTEXT_VALIDATION_FAILED = "无法验证项目上下文。"

    MEMORY_GOVERNANCE_TARGET_NOT_FOUND = "找不到要治理的记忆。"
    MEMORY_GOVERNANCE_INVALID_STATE = "当前状态下无法执行此记忆治理操作。"
    MEMORY_CONFLICT_NOT_FOUND = "找不到记忆冲突。"
    MEMORY_GOVERNANCE_PERSIST_FAILED = "无法保存记忆治理操作。"
    MEMORY_CONFLICT_MODIFIED_CONCURRENTLY = "记忆冲突已被并发更新。"
    MEMORY_MODIFIED_CONCURRENTLY = "记忆已被并发更新。"
    MEMORY_NEXT_REVIEW_NOT_LATER = "下次重新确认时间必须晚于确认时间。"
    MEMORY_NARROW_SCOPE_REQUIRED = "使用 narrow-scope 解决冲突时必须提供适用范围。"
    MEMORY_SCOPE_UNEXPECTED = "适用范围只能用于 narrow-scope 冲突解决方式。"
    RUN_LEASE_UPDATE_FAILED = "无法将运行租约保存到 SQLite。"

    SCM_CURSOR_READ_FAILED = "无法读取 SCM 同步游标。"
    SCM_CURSOR_SAVE_FAILED = "无法保存 SCM 同步游标。"
    SCM_EVENT_LOOKUP_FAILED = "无法查询 SCM 事件。"
    SCM_FEEDBACK_EVENT_SAVE_FAILED = "无法保存 SCM 反馈事件。"
    SCM_PUBLICATION_LOOKUP_FAILED = "无法查询 SCM 发布记录。"
    SCM_FEEDBACK_EVENT_TARGET_REQUIRED = "必须指定 SCM 反馈事件的目标。"
    SCM_PROJECT_ASSIGNMENT_FAILED = "无法保存 SCM 变更请求的项目归属。"
    SCM_PUBLICATION_PREPARATION_FAILED = "无法准备 SCM 发布记录。"
    SCM_PUBLICATION_LEASE_UPDATE_FAILED = "无法更新 SCM 发布租约。"
    SCM_PUBLICATION_LEASE_HELD = "另一个进程持有 SCM 发布租约。"
    SCM_PERSISTENCE_FAILED = "SCM 持久化操作失败。"
    SCM_INVALID_RESPONSE = "SCM API 响应无效。"
    SCM_SUMMARY_TITLE = "AI 代码审查摘要"
    SCM_SUMMARY_COUNT_LABEL = "未列入行内评论的发现"
    SCM_NO_FINDINGS = "没有通过验证的审查发现。"
    SCM_FINDING_DESCRIPTION_LABEL = "说明"
    SCM_FINDING_EVIDENCE_LABEL = "依据"
    SCM_FINDING_SUGGESTION_LABEL = "建议"
    SCM_FINDING_RULE_LABEL = "规则 ID"
    SCM_INVALID_CHANGE_ID = "GitHub Pull Request 编号必须为正整数。"
    SCM_EVENT_CURSOR_INVALID = "SCM 事件游标无效。"
    SCM_RATE_LIMIT_EXHAUSTED = "SCM API 触发限流并已用完重试次数。"
    SCM_API_AUTH_FAILED = "SCM API 认证或授权失败。"
    SCM_RESOURCE_NOT_FOUND = "找不到 SCM 资源。"
    SCM_API_REQUEST_REJECTED = "SCM API 拒绝了请求。"
    SCM_API_TEMPORARY_FAILURE_EXHAUSTED = "SCM API 出现临时故障并已用完重试次数。"
    SCM_API_REQUEST_FAILED = "SCM API 请求失败。"
    SCM_API_URL_NOT_HTTPS = "SCM API 请求 URL 必须为绝对 HTTPS URL。"
    SCM_API_RESPONSE_METADATA_INVALID = "SCM API 响应元数据无效。"
    SCM_API_RESPONSE_TOO_LARGE = "SCM API 响应超过大小上限。"
    SCM_API_CONNECTION_FAILED = "无法连接 SCM API。"

    RUN_NOT_FOUND_FOR_M3_IDENTITY = "找不到要保存 M3 标识信息的运行。"
    RUN_NOT_FOUND_FOR_CONTEXT_HASH = "找不到要保存上下文来源哈希的运行。"
    STORED_REVIEWER_METRICS_INVALID = "已保存的审查者指标无效。"
    STORED_REVIEW_METRICS_INVALID = "已保存的审查指标无效。"
    STORED_REJECTION_COUNTS_INVALID = "已保存的各排除原因计数无效。"
    DATABASE_INITIALIZATION_FAILED = "无法初始化 SQLite 数据库。"
    LLM_CALL_ID_UNAVAILABLE = "无法获取 LLM 调用标识符。"
    BUNDLE_NOT_FOUND_FOR_M3_METADATA = "找不到要保存 M3 元数据的审查单元。"
    UNIT_OF_WORK_NOT_ACTIVE = "工作单元尚未启动。"
    TRANSACTION_OPEN_FAILED = "无法开启 SQLite 事务。"
    FINDING_ID_FORMAT_INVALID = "审查发现 ID 必须采用 B<序号>-F<序号> 格式"
    FINDING_ID_SEQUENCE_NOT_NUMERIC = "审查发现 ID 的序号必须为数字"
    REPOSITORY_NOT_DIRECTORY = "仓库路径必须指向目录"
    REPOSITORY_NOT_GIT_ROOT = "仓库路径必须指向 Git 根目录"
    GIT_REVISION_INVALID = "Git 修订无效"
    PATCH_FILE_INSPECT_FAILED = "无法检查补丁文件"
    PATCH_FILE_READ_FAILED = "无法读取补丁文件"
    GIT_COMMAND_NOT_EXECUTED = "无法执行 Git 命令"
    MEMORY_VERSION_NOT_FOUND = "找不到记忆版本"
    MEMORY_CONTENT_HASH_MISMATCH = "已保存记忆的内容哈希不一致"
    RUN_NOT_FOUND_DURING_FINALIZE = "找不到要结束的运行"
    MEMORY_SCOPE_NOT_ARRAY = "记忆适用范围的字段必须为数组"
    PATCH_FILE_TOO_LARGE = "补丁文件超过大小上限"
    GIT_COMMAND_FAILED = "Git 命令执行失败"
    SCM_PUBLICATION_TRANSITION_REJECTED = "SCM 发布记录无法切换到指定状态"
    DIFF_SOURCE_ARGUMENTS_MISMATCH = "差异来源的参数缺失或多余"

    CONFIG_CONTEXT_BUDGET_REQUIRED = "启用动态上下文获取功能需要正数的 Token 预算和调用次数。"
    CONFIG_REFLECTION_BUDGET_REQUIRED = "启用自我验证至少需要 256 个输入 Token。"
    CONFIG_SCM_API_URL_INVALID = "SCM API URL 必须指向允许的 HTTPS 来源。"
    CONFIG_SCM_ACTOR_REQUIRED = "启用 SCM 必须指定机器人执行者的标识符。"
    CONFIG_SCM_REPOSITORY_REQUIRED = "启用 SCM 必须指定 owner/repository 格式的标识符。"
    CONFIG_GOVERNANCE_DISABLED = "启用 M5 下级功能前，请先启用 governance。"
    CONFIG_GOVERNANCE_HEARTBEAT_INVALID = "heartbeat_seconds 不得超过 lease_seconds 的三分之一。"
    CONFIG_GOVERNANCE_LEASE_TOO_SHORT = "lease_seconds 必须至少比 LLM 与 SCM 中较长的超时多 30 秒。"
    CONFIG_TELEMETRY_PROVIDER_MISMATCH = "遥测 provider 与启用状态不一致。"
    CONFIG_TELEMETRY_ENDPOINT_INVALID = "启用遥测必须指定允许的 HTTPS 发送目标。"
    CONFIG_INVALID = "配置无效"
    CONFIG_SCHEMA_VERSION_UNSUPPORTED = "不支持此配置 schema_version"
    CONFIG_FILE_READ_FAILED = "无法读取配置文件"
    CONFIG_FILE_YAML_INVALID = "配置文件的 YAML 无效"
    CONFIG_FILE_NOT_MAPPING = "配置文件必须是映射"
    CONFIG_CREDENTIALS_EMBEDDED = "配置文件不得包含凭据"
    CONFIG_CREDENTIALS_ENV_HINT = "请通过环境变量 {name} 提供凭据。"

    GROUND_TRUTH_SET_ALREADY_PUBLISHED = "正确答案数据集已经发布"
    GROUND_TRUTH_APPROVED_ISSUE_REQUIRED = "正确答案数据集至少需要一个已批准的问题。"
    GROUND_TRUTH_ISSUE_CASE_NOT_APPROVED = "已批准的问题关联了未经批准或不存在的用例。"
    EVALUATION_REPEAT_INVALID = "评估轮次编号必须从 1 开始。"
    FINDING_FEEDBACK_MISSED_ISSUE_FORBIDDEN = "评价已有审查发现时不得使用 missed-issue 标签。"
    FINDING_NOT_IN_RUN = "该审查发现不属于目标审查运行"
    GOVERNANCE_RESOURCE_ID_INVALID = "治理目标的资源 ID 无效。"
    PROJECT_NOT_REGISTERED = "项目尚未注册。"
    PROJECT_ARCHIVED = "项目已归档。"
    PROJECT_CONTEXT_NOT_FOUND = "找不到项目上下文。"
    GOVERNANCE_RESOURCES_UNASSIGNED = "在执行 M5 审查前，请将现有治理资源绑定到项目。"

    MEMORY_CANDIDATE_SUPPRESSES_CRITICAL_SECURITY = "记忆候选不得抑制严重的安全发现。"
    MEMORY_CANDIDATE_INJECTION_PATTERN = "记忆候选包含提示词注入内容"
    MEMORY_SUPPRESSION_FORBIDS_REPORTING = (
        "请将 suppress 记忆描述为要求更充分的依据，禁止用来阻止报告。"
    )
    MEMORY_EXTRACTION_FEEDBACK_REQUIRED = "提取记忆候选至少需要一条反馈。"
    MEMORY_EXTRACTION_FEEDBACK_MISSING = "待提取的反馈中有不存在的记录。"
    EXTRACTION_PAYLOAD_MODIFIED_AFTER_SCAN = "敏感信息检查后提取内容发生变化，已阻止发送。"

    TELEMETRY_ATTRIBUTE_COUNT_EXCEEDED = "遥测属性数量超过上限。"
    TELEMETRY_ATTRIBUTE_NAME_FORBIDDEN = "该遥测属性名不在允许列表内。"
    TELEMETRY_ATTRIBUTE_TOO_LONG = "遥测字符串属性过长。"

    AGENT_TURN_INVALID = "审查者的响应不符合封闭模式。"
    BUNDLE_DIFF_MODIFIED_AFTER_SCAN = "敏感信息检查后审查单元的差异发生变化，已阻止发送"
    SPECIALIST_TEMPLATE_NOT_CONFIGURED = "尚未配置专项审查者的提示词。"
    CONTEXT_TOOL_INTENT_INVALID = "上下文获取工具的请求无效。"
    PROMPT_TEMPLATE_READ_FAILED = "无法读取提示词模板"
    PROMPT_TEMPLATE_YAML_INVALID = "提示词模板的 YAML 无效"
    PROMPT_TEMPLATE_NOT_MAPPING = "提示词模板必须是映射"
    PROMPT_TEMPLATE_KEYS_MISSING = "提示词模板缺少必填键"
    PROMPT_TEMPLATE_VERSION_INVALID = "提示词模板的 prompt_version 必须为正整数"
    PROMPT_TEMPLATE_PROFILES_INCOMPLETE = (
        "提示词模板的 reviewer_profiles 必须包含 general/security/data/test"
    )
    PROMPT_TEMPLATE_PROFILE_EMPTY = "提示词模板的审查者配置说明不能为空"
    REFLECTION_RESPONSE_INVALID = "自我验证的响应不符合封闭模式。"
    REFLECTION_BUDGET_EXCEEDED = "自我验证的输入超过配置的预算。"
    RESPONSE_SCHEMA_VERSION_UNSUPPORTED = "不支持此响应 schema_version"
    RESPONSE_FINDINGS_NOT_ARRAY = "响应的 findings 必须为数组。"
    REVIEWER_RESULTS_EMPTY = "审查者的结果为空。"
    REVIEWER_PRIMARY_RESULT_MISSING = "审查者结果缺少主要审查者的结果。"
    RULES_DIRECTORY_OUTSIDE_REPOSITORY = "规则目录必须位于仓库内。"
    CONTEXT_TOOL_DEPENDENCY_MISSING = "尚未配置上下文获取工具的依赖。"
    REFLECTION_DEPENDENCY_MISSING = "尚未配置自我验证的依赖。"
    REVIEW_RUN_RESUME_CONFLICT = "另一进程正在恢复该审查运行。"
    REVIEW_CANCELLED_BY_BLOCKED_BUNDLE = "其他审查单元被拦截，已中止审查。"
    FILE_DIFF_WORKTREE_MISMATCH = "补丁文件的差异与当前工作区不一致。"
    SENSITIVE_CANDIDATE_BEFORE_LLM = "调用 LLM 前检测到疑似敏感信息。"
    SENSITIVE_CANDIDATE_IN_CONTEXT_TOOL = "在上下文获取工具的结果中检测到疑似敏感信息。"
    CONTEXT_SOURCE_CHANGED_DURING_REVIEW = "审查期间上下文来源发生变化。"

    DIFF_CONTAINS_NUL = "差异不得包含 NUL 字符。"
    HUNK_HEADER_MALFORMED = "差异块头部无效"
    HUNK_LINE_COUNT_MISMATCH = "差异块的行数与头部声明不一致"
    DIFF_HEADER_PATH_INVALID = "差异头部的路径无效"
    DIFF_LINE_MARKER_UNEXPECTED = "差异块包含意外的行标记"
    PROJECT_ID_LENGTH_INVALID = "项目 ID 的长度超出允许范围"
    PROJECT_ID_FORMAT_INVALID = "项目 ID 必须采用 [a-z0-9][a-z0-9_-]* 格式。"
    RUN_ID_INVALID = "运行 ID 必须是小写 UUIDv4。"
    BUNDLE_SEQUENCE_INVALID = "审查单元的序号必须从 1 开始。"
    FINDING_SEQUENCE_INVALID = "审查发现的序号必须从 1 开始。"
    PATH_EMPTY = "路径不能为空。"
    PATH_CONTAINS_NUL = "路径不得包含 NUL 字符。"
    PATH_NOT_POSIX = "路径必须使用 POSIX 分隔符"
    PATH_NOT_RELATIVE = "路径必须相对于仓库根目录"
    PATH_TRAVERSES_OUTSIDE = "路径不得指向仓库外"
    PATH_NOT_FILE = "路径必须指向文件"
    START_LINE_INVALID = "start_line 必须大于或等于 1。"
    END_LINE_INVALID = "end_line 必须大于或等于 start_line。"
    FEEDBACK_ID_INVALID = "反馈 ID 必须是小写 UUIDv4。"
    EXTRACTION_ID_INVALID = "提取 ID 必须是小写 UUIDv4。"
    MEMORY_ID_INVALID = "记忆 ID 必须是小写 UUIDv4。"
    MEMORY_VERSION_INVALID = "记忆版本必须从 1 开始。"
    GROUND_TRUTH_SPLIT_INVALID = "正确答案数据的划分无效"
    GROUND_TRUTH_SET_VERSION_INVALID = "正确答案数据集版本必须从 1 开始。"
    GROUND_TRUTH_ISSUE_SEQUENCE_INVALID = "正确答案问题的序号必须从 1 开始。"
    EVALUATION_ID_INVALID = "评估 ID 必须是小写 UUIDv4。"
    GROUND_TRUTH_CASE_SEQUENCE_INVALID = "正确答案用例的序号必须大于或等于 1。"
    GROUND_TRUTH_CASE_CHANGE_REF_REQUIRED = "正确答案用例必须包含 change_ref。"
    GROUND_TRUTH_ISSUE_LOCATION_REQUIRED = "正确答案问题必须包含 start_line 或 symbol。"
    GROUND_TRUTH_ISSUE_START_LINE_INVALID = "正确答案问题的 start_line 必须大于或等于 1。"
    GROUND_TRUTH_ISSUE_CHANGE_REF_REQUIRED = "正确答案问题必须包含 change_ref。"
    GROUND_TRUTH_ISSUE_EVIDENCE_REQUIRED = "正确答案问题必须提供依据。"
    PUBLISHED_SET_APPROVER_REQUIRED = "已发布的正确答案数据集必须包含 approved_by。"
    PUBLISHED_SET_TIME_REQUIRED = "已发布的正确答案数据集必须包含带时区的 published_at。"
    PUBLISHED_SET_UNAPPROVED_ISSUE = "已发布的数据集只能包含已批准的正确答案问题。"
    PUBLISHED_SET_UNAPPROVED_CASE = "已发布的数据集只能包含已批准的正确答案用例。"
    GROUND_TRUTH_ISSUE_CASE_UNREGISTERED = "正确答案问题必须属于已注册的用例。"
    FALSE_POSITIVE_FINDING_REQUIRED = "误报匹配项必须包含 finding_id。"
    FALSE_NEGATIVE_ISSUE_REQUIRED = "漏报匹配项必须包含 issue_id。"
    MATCH_TARGET_REQUIRED = "匹配结果必须包含 finding_id 或 issue_id。"
    MISSED_ISSUE_LOCATION_REQUIRED = "漏报问题的位置必须指定 start_line 或 symbol。"
    MISSED_ISSUE_START_LINE_INVALID = "漏报问题的 start_line 必须大于或等于 1。"
    FEEDBACK_ACTOR_REQUIRED = "反馈的 actor_id 不能为空。"
    FEEDBACK_TIME_NOT_UTC = "反馈的 created_at 必须带有 UTC 时区。"
    MISSED_ISSUE_FINDING_FORBIDDEN = "漏报问题反馈不得引用审查发现。"
    MISSED_ISSUE_FEEDBACK_LOCATION_REQUIRED = "漏报问题反馈必须包含位置。"
    MISSED_ISSUE_TYPE_REQUIRED = "漏报问题反馈必须包含 issue_type。"
    MISSED_ISSUE_SEVERITY_REQUIRED = "漏报问题反馈必须包含严重级别。"
    MISSED_ISSUE_EVIDENCE_REQUIRED = "漏报问题反馈必须提供依据。"
    FINDING_FEEDBACK_ID_REQUIRED = "审查发现反馈必须包含 finding_id。"
    FINDING_FEEDBACK_LOCATION_FORBIDDEN = "审查发现反馈不得指定漏报问题的位置。"
    FINDING_FEEDBACK_REASON_REQUIRED = "审查发现反馈必须提供理由。"
    MEMORY_GOVERNANCE_LOCK_INVALID = "记忆治理的 lock_version 必须为正数。"
    MEMORY_CONFLICT_ORDER_INVALID = "记忆冲突中的两项必须按规范顺序排列。"
    MEMORY_CONFLICT_METADATA_INVALID = "记忆冲突的元数据无效。"
    MEMORY_CONFLICT_STATE_INCONSISTENT = "记忆冲突的解决状态不一致。"
    PROJECT_NAME_INVALID = "项目名称必须由 1 至 100 个安全字符组成。"
    PROJECT_POLICY_HASH_INVALID = "项目策略哈希必须使用 SHA-256。"
    PROJECT_LOCK_INVALID = "项目的锁版本必须为正数。"
    PROJECT_TIME_NOT_UTC = "项目时间必须使用 UTC。"
    REPOSITORY_PROVIDER_INVALID = "仓库提供方无效。"
    REPOSITORY_EXTERNAL_ID_INVALID = "仓库外部 ID 无效。"
    REPOSITORY_BINDING_TIME_NOT_UTC = "仓库绑定时间必须使用 UTC。"
    GOVERNANCE_REASON_INVALID = "治理操作的理由必须由 1 至 500 个安全字符组成。"
    FRAMEWORK_CONSTRAINT_INVALID = "框架版本约束无效。"
    MEMORY_CONFIDENCE_INVALID = "记忆的置信度必须在 0.0 至 1.0 之间。"
    MEMORY_CREATOR_REQUIRED = "记忆的 created_by 不能为空。"
    MEMORY_VALIDITY_PAIR_REQUIRED = "记忆有效期的起始时间和重新确认期限必须同时指定。"
    MEMORY_VALIDITY_NOT_UTC = "记忆有效期必须带有 UTC 时区。"
    MEMORY_VALIDITY_ORDER_INVALID = "记忆的 valid_from 必须早于 review_after。"
    MEMORY_CREATED_AT_NOT_UTC = "记忆的 created_at 必须带有 UTC 时区。"
    MEMORY_FIELD_EMPTY = "记忆字段不能为空"
    MEMORY_FIELD_TOO_LONG = "记忆字段超过最大字符数"
    MEMORY_LOCK_VERSION_INVALID = "记忆的 lock_version 必须从 1 开始。"
    MEMORY_TIME_NOT_UTC = "记忆时间必须带有 UTC 时区。"
    MEMORY_TRANSITION_ACTOR_REQUIRED = "记忆状态转换的 actor_id 不能为空。"
    MEMORY_TRANSITION_REASON_REQUIRED = "记忆状态转换的理由不能为空。"
    MEMORY_TRANSITION_TIME_NOT_UTC = "记忆状态转换的 created_at 必须带有 UTC 时区。"
    MEMORY_SCOPE_GROUP_REQUIRED = "记忆适用范围至少需要一个字段组。"
    MEMORY_SCOPE_TOO_MANY_ITEMS = "记忆适用范围中的条目数量超过上限"
    MEMORY_SCOPE_EMPTY_VALUE = "记忆适用范围不得包含空值"
    MEMORY_TRANSITION_NOT_ALLOWED = "此记忆状态转换未获允许"
    MEMORY_LOCK_VERSION_MISMATCH = "记忆已被并发更新，lock_version 不一致"
    RULE_FILE_NOT_MAPPING = "规则文件必须是 YAML 映射"
    RULE_FILE_FIELDS_MISSING = "规则文件缺少必填字段"
    RULE_FILE_FIELDS_UNKNOWN = "规则文件包含未知字段"
    RULE_FILE_FIELD_NOT_STRING = "规则文件的字段必须是非空字符串"
    RULE_FILE_SEVERITY_NOT_STRING = "规则文件的 severity 必须为字符串"
    RULE_FILE_SCOPE_NOT_MAPPING = "规则文件的 scope 必须为映射"
    RULE_FILE_SCOPE_FIELDS_UNKNOWN = "规则文件的 scope 包含未知字段"
    RULE_FILE_SCOPE_NOT_ARRAY = "规则文件中 scope 的各字段必须为数组"
    RULE_ID_DUPLICATED = "规则 ID 重复"
    RULE_PREVIOUS_DEFINITION_LABEL = "已定义于"
    RULE_FILE_READ_FAILED = "无法读取规则文件"
    RULE_FILE_YAML_INVALID = "规则文件的 YAML 无效"
    RULE_FILE_SEVERITY_INVALID = "规则文件的 severity 无效"
    RULE_FILE_INVALID = "规则文件无效"
    RULE_FILE_SCOPE_EMPTY_STRING = "规则文件中 scope 的数组元素必须是非空字符串"
    RULE_SCOPE_GROUP_REQUIRED = "规则的 scope 至少需要 languages、paths 或 keywords 之一。"
    RULE_ID_FORMAT_INVALID = "规则 ID 必须采用 <DOMAIN>.<ISSUE_TYPE> 格式"
    RULE_ID_GENERAL_RESERVED = "规则 ID 的 GENERAL 命名空间保留给未使用项目规则的审查发现。"
    RULE_CATEGORY_REQUIRED = "规则必须包含 category"
    RULE_INSTRUCTION_REQUIRED = "规则必须包含 instruction"
    SCM_CHANGE_ID_EMPTY = "SCM 变更请求标识符不能为空。"
    SCM_COMMENT_BODY_EMPTY = "SCM 评论正文不能为空。"
    SCM_COMMENT_LINE_INVALID = "SCM 评论的行号必须为正数。"
    SCM_COMMENT_POSITION_INVALID = "SCM 行评论必须指定一个位置。"
    SCM_COMMENT_REVISION_INVALID = "请为 SCM 评论的修订指定完整的 Git SHA。"
    SCM_COMMENT_SIDE_INVALID = "SCM 评论只能定位到变更后的代码一侧。"
    SCM_REPOSITORY_ID_FORMAT_INVALID = "SCM 仓库标识符必须采用 owner/repository 格式。"
    SCM_REVISION_INVALID = "SCM 修订必须为 40 位小写 Git SHA。"
    SCM_CHANGE_TIME_NO_TIMEZONE = "SCM 变更请求的时间必须包含时区。"
    SCM_COMMENT_UPDATE_INVALID = "更新 SCM 评论必须提供标识符和正文。"
    SCM_PUBLICATION_TRANSITION_INVALID = "SCM 发布记录的状态转换无效"
    ACTOR_ID_REQUIRED = "授权操作前请指定 actor_id。"
    ACTOR_NOT_MAINTAINER = "执行者未被配置为此操作的维护人员"

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
        return f"{Messages.REPOSITORY_NOT_GIT_ROOT}: 预期 {expected}，实际 {actual}"

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
            f"{Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH}: {kind} 需要 {expected}。"
            f"实际参数: {present}"
        )

    @staticmethod
    def patch_file_too_large(max_bytes: int) -> str:
        """パッチファイルが上限を超える場合のメッセージを返す."""
        return f"{Messages.PATCH_FILE_TOO_LARGE}: {max_bytes} 字节"

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
        return f"无法读取 LLM 数据集，或数据集格式无效: {path}"

    @staticmethod
    def duplicate_dataset_case_id(path: Path) -> str:
        """ケース ID が重複したデータセットのメッセージを返す."""
        return f"LLM 数据集的 case_id 重复: {path}"

    @staticmethod
    def duplicate_dataset_response_key(path: Path) -> str:
        """複数ターン応答キーが重複したデータセットのメッセージを返す."""
        return f"LLM 数据集的多轮响应键重复: {path}"

    @staticmethod
    def invalid_dataset_request_label() -> str:
        """複数ターン照合に使用できない要求ラベルのメッセージを返す."""
        return "LLM 数据集的多轮请求标签无效。"

    @staticmethod
    def missing_dataset_response() -> str:
        """要求に対応する複数ターン応答がない場合のメッセージを返す."""
        return "LLM 数据集缺少与请求匹配的多轮响应。"

    @staticmethod
    def mismatched_dataset_request_hash() -> str:
        """固定要求ハッシュが一致しない場合のメッセージを返す."""
        return "LLM 数据集的请求哈希不一致。"

    @staticmethod
    def unused_dataset_responses() -> str:
        """選択済みケースに未使用応答が残る場合のメッセージを返す."""
        return "LLM 数据集的所选用例含有未使用的响应。"

    @staticmethod
    def closed_dataset_client() -> str:
        """終了後のデータセット利用を拒否するメッセージを返す."""
        return "LLM 数据集已关闭，无法使用。"

    @staticmethod
    def configured_path_outside_repository(label: str) -> str:
        """リポジトリ外を指す設定パスのメッセージを返す."""
        return f"{label}必须位于仓库内。"

    @staticmethod
    def memory_not_found(memory_id: str) -> str:
        """対象メモリが存在しない場合のメッセージを返す."""
        return f"找不到记忆: {memory_id}"

    @staticmethod
    def invalid_memory_scope_file(path: str) -> str:
        """適用範囲ファイルを読み込めない場合のメッセージを返す."""
        return f"无法读取适用范围文件，或文件格式无效: {path}"

    @staticmethod
    def feedback_run_not_found(run_id: str) -> str:
        """対象実行が存在しない場合のメッセージを返す."""
        return f"{Messages.FEEDBACK_RUN_NOT_FOUND}: {run_id}"

    @staticmethod
    def file_not_found(path: str) -> str:
        """対象ファイルが存在しない場合のメッセージを返す."""
        return f"找不到目标文件: {path}"

    @staticmethod
    def llm_request_failed(error_type: str) -> str:
        """LLM 呼び出し失敗メッセージを返す."""
        return f"LLM 调用失败: {error_type}"

    @staticmethod
    def litellm_transient_gateway_failure(error_type: str) -> str:
        """LiteLLM の一時的なゲートウェイ障害メッセージを返す."""
        return f"LiteLLM 网关出现临时故障: {error_type}"

    @staticmethod
    def litellm_gateway_request_failed(error_type: str) -> str:
        """LiteLLM の再試行不可要求エラーメッセージを返す."""
        return f"LiteLLM 网关请求失败: {error_type}"
