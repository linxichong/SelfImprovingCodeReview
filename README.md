# SelfImprovingCodeReview

这是一个使用 LLM 审查 Git 差异，并将通过确定性验证的结果保存到 SQLite 的 CLI。
M1 提供审查基础，M2 提供反馈记忆，M3 仅按需读取代码以自我验证候选，M4 提供 GitHub SCM 集成。
M4 支持获取变更请求、生成试运行计划、显式发帖、幂等重跑以及同步反馈。

## 已实现功能

### M1 审查基础

- 从工作树、提交、修订范围或补丁文件获取 Git 差异
- 筛选目标文件、拆分差异并控制 Token 预算
- 选择仓库规则并加入审查指令
- 外部发送前立即检查敏感信息并处理提示词边界
- 通过 LiteLLM 和配置的 AI 网关调用 LLM
- 使用封闭的 JSON 模式验证响应，格式错误时仅修复一次
- 确定性验证问题位置、引用、重复项及数量上限
- 在 SQLite 保存运行结果、问题、Token 用量、等待时间、尝试次数及可获取的成本
- 提供无需网络和 API 密钥的开发及测试数据集

### M2 反馈记忆

- 评价现有问题并补报漏检问题
- 保存反馈后提取记忆候选
- 提取前检查敏感信息，使用封闭模式校验并防止重复执行
- 列出、查看、批准或驳回记忆候选
- 通过维护人员名单限制本地审批权限，并使用乐观锁检测冲突
- 审批时缩小适用范围会创建新版本
- 确定性检索有效记忆，并按数量和 Token 预算控制注入
- 保存所用记忆 ID、版本和哈希的快照
- 重复评估比较组 A（基准）、B（规则）和 C（记忆）
- 以盲化方式导出未裁决项，支持人工裁决和重新计算指标
- 裁决后重新生成 B/C 比较组的指标和最终判定

### M3 流水线增强

- 根据差异关系图、风险和固定配置制定审查计划
- 在仓库边界、调用次数、结果数量、文件大小和 Token 预算内取得附加代码
- 将工具结果交给 LLM 前重新检查敏感信息
- 对通过确定性验证的候选执行自我验证，并保存依据状态
- 有界并行执行审查单元，保持输出顺序稳定
- 严格恢复时核对计划、审查单元和附加来源的哈希
- 使用本地数据集验证多轮交互、请求哈希及未使用响应
- 分别记录工具处理时间、原因代码、自我验证和恢复状态
- M2 评估门禁禁止将未完成审查当作空的成功结果评分

### M4 SCM 集成（已实现）

- 获取 GitHub Pull Request 的 base/head SHA、状态和草稿状态
- 核对本地 HEAD 与 base SHA，复用现有 M3 范围审查
- 根据严重程度和位置生成顺序稳定的行评论和摘要评论计划
- 默认试运行，两项配置与 CLI 参数组成发帖的三重门禁
- 通过 SQLite 唯一约束和短期租约确保评论创建与更新幂等
- 发帖前重新核对 head SHA 并检查评论正文中的敏感信息
- 仅对 GitHub API 暂时性故障执行有界重试
- 将行评论的明确评价仅一次同步至 M2 反馈与记忆候选提取
- 单独计量 SCM API 调用、重试等待、计划、发布结果和同步结果

### M5 治理增强（已实现并完成最终验收）

第一阶段实现项目注册、仓库唯一归属、现有正确答案数据集与 SCM 变更请求的显式迁移，以及审查启动时的项目边界验证。
第二阶段实现记忆归档、恢复为下一版本、替换、人工冲突解决、到期、重新确认和停用。
第三阶段为高风险变更最多增加一名安全、数据或测试专项审查者。系统以确定性方式合并其与主审查者的重复问题，
并保存各审查者用量及采纳来源。
第四阶段增加仅处理安全属性的遥测端口、默认 Noop 实现及可选的 OpenTelemetry OTLP/HTTP 实现。
第五阶段增加审查运行租约、后台心跳、单调递增的隔离令牌和 SQLite 锁等待的有界重试。同一运行并发启动或租约丢失时，
以退出代码 5 报告并发冲突，禁止保存旧所有者的结果。
第六阶段使用真实 Git、SQLite、Dataset LLM 和模拟遥测进行端到端验证，覆盖项目隔离、敏感信息拦截、
受治理的记忆生命周期、专项审查、计量、备份检查及租约丢失后的恢复。
M5 默认关闭，关闭时保留 M1～M4 的配置哈希和处理结果。
全仓 Bandit 问题已处理，detect-secrets 基准文件已审计，已配置 pre-commit 并完成全文件检查。M5 最终验收已完成。

启用 M5 时，正确答案数据的发布、评估及人工裁决，以及 SCM 变更请求、发布计划和反馈同步都使用同一项目边界。
新建正确答案数据集和 SCM 变更请求时，资源及其归属在同一事务内保存。
其他项目的资源或尚未迁移的现有资源统一返回 `NOT_FOUND`，避免泄露其存在性。

准备配置见 `config/m5.governance.example.yaml`。在审查中启用 M5 前，按以下顺序注册项目和目标仓库。

```bash
uv run --frozen review governance project register \
  --repository . \
  --config config/m5.governance.example.yaml \
  --project example-project \
  --name "Example Project" \
  --actor tanaka \
  --reason "M5 初期登録"

uv run --frozen review governance project bind-repository \
  --repository . \
  --config config/m5.governance.example.yaml \
  --project example-project \
  --provider github \
  --external-repository owner/repository \
  --actor tanaka \
  --reason "绑定目标仓库"
```

如果现有数据库包含尚未设置归属的正确答案数据集或 SCM 变更请求，使用 `adopt-existing` 逐项关联到项目，
然后设置 `governance.enabled: true`。
使用记忆治理时，再设置 `governance.memory_lifecycle_enabled: true`。
通过 `memory list` 或 `memory show` 查询目标 ID 和当前 `lock_version`。
使用运行租约时，再设置 `governance.execution_leases_enabled: true`。
`lease_seconds` 是租约有效时间，`heartbeat_seconds` 是更新间隔，
`sqlite_busy_timeout_ms` 是每个 SQLite 连接的最大锁等待时间。
`heartbeat_seconds` 必须小于 `lease_seconds`。调用外部 LLM 时不持有业务事务，
心跳由独立的短时数据库连接更新。

切换到恢复后的 SQLite 备份作为生产数据库前，可以先执行只读验证。
将目标文件放在仓库内，并在 `maintainers` 中登记具有 `verify-backup` 权限的维护人员。
检查内容包括 SQLite 一致性、外键、必需模式、项目归属，以及记忆内容、记忆快照和规则快照的哈希。

```bash
uv run --frozen review governance backup verify \
  --repository . \
  --config config/m5.governance.example.yaml \
  --database .review/restored-review.db \
  --project example-project \
  --actor tanaka \
  --output .review/backup-verification.json
```

备份有效时退出代码为 0，不一致或授权失败时为 5。检查报告仅输出判定、失败原因代码和目标项目的汇总数量。
报告排除数据库路径、SQL 及已保存正文。检查过程以只读方式运行。

```bash
uv run --frozen review memory archive <MEMORY_ID> \
  --repository . --config config/m5.governance.example.yaml \
  --project example-project --lock-version 1 \
  --actor tanaka --reason "依据已过期"

uv run --frozen review memory conflict list \
  --repository . --config config/m5.governance.example.yaml \
  --project example-project --status open

uv run --frozen review memory revalidation list \
  --repository . --config config/m5.governance.example.yaml \
  --project example-project
```

确认冲突解决与重新验证时，必须提供审计所需的 `--reason` 和 `--evidence`。
新审查的记忆检索会排除已过期的有效记忆，以及带框架版本约束但运行端版本未知的记忆。

使用专项审查时，注册项目后设置 `governance.enabled: true` 和
`governance.specialized_reviewers_enabled: true`。只有满足条件时，才依次由主审查者和辅助审查者处理同一单元。
低风险变更不增加调用。仅辅助审查者失败时，运行返回 `partial-success` 并保留主审查者的有效问题。
启用后，结果 JSON 返回 `schema_version: 2` 和 `reviewer_metrics`，
SQLite 的 `reviewer_executions` 和 `reviewer_finding_sources` 保存各审查者的运行及全部采纳来源。
遥测默认关闭，此时不使用网络。仅在启用的环境同步可选依赖，并按以下方式修改配置示例的 `telemetry`。
目标必须使用 HTTPS，且主机与 `allowed_hosts` 完全匹配。通过运行环境的密钥管理功能设置
`REVIEW_OTEL_EXPORTER_HEADERS`，禁止在配置文件写入认证头。

```bash
uv sync --frozen --group telemetry
```

```yaml
telemetry:
  enabled: true
  provider: opentelemetry
  endpoint: https://collector.example.com
  allowed_hosts:
    - collector.example.com
  service_name: self-improving-code-review
  sample_ratio: 0.1
  export_timeout_seconds: 5
  max_queue_size: 2048
```

遥测排除差异、代码、路径、提示词和凭据。发送失败只转换为安全的错误代码和丢弃条数，保留审查结果。

## 当前适用范围

| 阶段 | 状态 | 内容 |
|---|---|---|
| M1 | 已实现 | 审查基础、规则、敏感信息检查、持久化 |
| M2 | 已实现 | 反馈、记忆候选、治理、检索与注入、评估 |
| M3 | 已实现 | 动态上下文获取、自我验证、并行执行、严格恢复、多轮数据集、专项计量 |
| M4 | 已实现 | GitHub 变更请求、试运行计划、显式发帖、反馈同步 |
| M5 | 已实现并完成最终验收 | 项目隔离、记忆生命周期、冲突解决、重新验证、条件式专项审查、OpenTelemetry、运行租约、并发控制和 SQLite 备份验证 |

M2 的生产审批者管理方式尚未确定。目前仅在配置为 `memory.is_production: false` 的本地环境中提供批准和驳回。

## 安装

需要 Python 3.14.x 和项目锁定版本的 `uv`。

```bash
uv sync --frozen --all-groups
```

使用以下方式查看 CLI 命令。

```bash
uv run --frozen review --help
uv run --frozen review <COMMAND> --help
```

<a id="ローカル開発llm-トークン消費なし"></a>

## 本地开发（不消耗 LLM Token）

根目录的 `review.yaml` 使用无需访问网络的 `dataset` 供应商。
无需 API 密钥，也无需设置 `security.allow_external_source: true`。

```bash
uv run --frozen review review \
  --repository . \
  --config review.yaml \
  --diff-file datasets/development/sample_nullable_user.patch
```

- 开发固定响应：`datasets/development/review_cases.json`
- 测试固定响应：`datasets/test/review_cases.json`
- 开发配置：`config/development.yaml`
- 测试配置：`config/test.yaml`
- 无匹配用例时返回不含问题的默认响应。
- 使用首个满足以下条件的用例：`match_all` 中的全部字符串均出现在系统指令或请求正文中。
- 要用固定响应试验记忆候选提取，请在数据集中添加符合提取模式的用例。

数据集格式和添加方法见 `datasets/README.md`。本地运行结果保存到 `.review/`，该目录由 Git 忽略。

### M3 本地验证

`config/m3.local.example.yaml` 是禁止外部通信的 M3 示例配置。
配套的 `datasets/development/m3_review_cases.example.json` 可针对随附示例差异重现附加代码读取与自我验证。

```bash
uv run --frozen review review \
  --repository . \
  --config config/m3.local.example.yaml \
  --diff-file datasets/development/m3_nullable_user.patch \
  --output .review/m3-example-result.json
```

通过结果 JSON 的 `pipeline_metrics`，以及 SQLite 的 `tool_calls` 和 `reflection_results` 检查运行情况。
示例仅使用 `dataset`，不需要 API 密钥，也不消耗 LLM Token。
若要以严格多轮格式固定自有数据，参见 `datasets/README.md` 的 `schema_version: 2`。

### M3 版本化资源

- 审查提示词：`prompts/review_agent_v1.yaml`
- 自我验证提示词：`prompts/reflection_v1.yaml`

### 试用 M4 GitHub 集成

复制 `config/m4.github.example.yaml`，设置仓库 ID 和机器人名称。SCM 令牌仅通过环境变量传入。

```bash
export REVIEW_SCM_TOKEN="<GitHub token>"
uv run --frozen review scm review \
  --repository . \
  --config config/m4.github.example.yaml \
  --change 123 \
  --output .review/scm-review-123.json
```

上述操作以试运行方式执行，不修改 GitHub。仅在同时添加 `scm.publish_enabled: true` 和 `--publish` 时实际发帖。
请提前在本地获取 Pull Request 的 base/head 提交，并将工作树 HEAD 对齐到 base SHA。
系统禁止隐式 fetch 或 checkout。

使用反馈同步时，配置外部 GitHub 用户名与内部人员 ID 的映射，并启用 `feedback_sync_enabled`。

```yaml
scm:
  feedback_sync_enabled: true
  feedback_actor_mapping:
    github-reviewer: reviewer-1
```

回复已发布的行评论时，必须完全匹配以下任意一种格式。

```text
/review-feedback true-positive --reason "判定为有效的理由"
/review-feedback false-positive --reason "判定为误报的理由"
/review-feedback needs-more-evidence --reason "需要补充依据的理由"
/review-feedback not-applicable --reason "判定为不适用的理由"
```

随后执行以下命令。

```bash
uv run --frozen review scm sync-feedback \
  --repository . \
  --config config/m4.github.example.yaml \
  --change 123 \
  --output .review/scm-feedback-123.json
```

### 真实 GitHub 沙箱集成

常规测试不连接 GitHub。执行真实集成时，准备专用的测试仓库与 Pull Request，
将目标仓库的本地 `HEAD` 对齐到 Pull Request 的 base SHA，并提前获取 head SHA 的 Git 对象，再显式执行。
配置须指定 `scm.enabled: true`、实际的 `repository_external_id` 和机器人名称。
令牌须采用最小权限，并且只在环境变量中设置。

```bash
export REVIEW_SCM_TOKEN="<sandbox token>"
bash scripts/m4_github_smoke.sh \
  /path/to/sandbox-repository \
  /path/to/sandbox-repository/config/review.github.yaml \
  123
```

上述命令只执行试运行。检查 `.review/m4-github-smoke/dry-run.json` 中的目标 SHA、发帖计划和敏感信息检查结果。
若要发布同一份固定结果，在第一次试运行之前设置 `scm.publish_enabled: true`，随后保持配置文件、目标 PR、
base/head SHA、提示词、规则、模型和记忆不变。未传入 `--publish` 时，单独设置该开关不会更新 GitHub。

```bash
M4_GITHUB_ALLOW_PUBLISH=YES bash scripts/m4_github_smoke.sh \
  /path/to/sandbox-repository \
  /path/to/sandbox-repository/config/review.github.yaml \
  123
```

再次执行同一发帖命令，确认 `published.json` 中的 `created_count` 为 0，
现有评论的 `operation` 为 `reused`，或在正文改变时为 `updated`。

仅在需要同步已发布行评论的回复时，再设置 `M4_GITHUB_SYNC_FEEDBACK=YES`。
结果保存在同一输出目录。禁止在生产仓库、未核对的 Pull Request 或常规质量门禁中运行该脚本。

由机器人之外的 GitHub 用户回复已发布的行评论。概要评论不接受此类反馈。
在 `feedback_actor_mapping` 中登记该用户。

```text
/review-feedback true-positive --reason "已确认审查发现有效"
```

同步后确认 `processed_count` 为 1、`failed_count` 为 0。
再次获取同一回复会增加 `duplicate_count`，不创建新的 M2 反馈。


常规回复、未知标签、缺少理由、未登记用户以及同一评论的编辑或重复获取均不生成新反馈。
SCM 评论正文不保存到 SQLite。

SCM 审查结果的 `scm.metrics` 包含 API 调用次数、重试次数与等待时间、行评论和摘要评论的计划数，
以及创建、更新、复用、恢复、失败、过期和拦截的次数。可通过 `scm.publications[].operation` 查看单项计划的操作类别。
反馈同步保留获取、处理、拒绝、失败和重复计数，并在 `scm_feedback_sync.api_metrics` 输出 API 指标。
计量排除评论正文、完整 API 响应和 SCM 令牌。
SCM HTTP 响应最多读取 `scm.max_response_bytes`（默认 8 MiB）。声明大小或实际读取大小任一超限即安全停止。
反馈同步游标按事件提交。重复项、常规评论及未知回复目标同样推进已处理位置。
仅在处理完整页后保存页末游标，以免中途失败时跳过未处理事件。

- 资源修改规则：`prompts/README.md`
- 本地多轮格式：`datasets/README.md`

提示词版本与内容哈希保存到运行结果，并用于判断能否恢复。
封闭响应模式由代码根据配置上限和候选数量生成，无需维护静态副本。

<a id="レビューの実行"></a>

## 执行审查

智能体可通过 [`$self-improving-code-review`](.agents/skills/self-improving-code-review/SKILL.md)
调用现有 CLI 审查差异，或按流程开发提示词资源。

基本格式如下。必须从四种差异输入中选择且仅选择一种。

```bash
uv run --frozen review review [通用选项] <差异来源>
```

### 通用选项

| 参数 | 必填 | 默认值 | 功能 |
|---|---:|---|---|
| `--repository PATH` | 否 | 当前目录 | 审查目标 Git 仓库的根目录，也是数据库、提示词和数据集等仓库内受限路径的基准。 |
| `--project PROJECT_ID` | 否 | 配置的 `project_id` | 覆盖审查结果中记录的项目 ID。 |
| `--config YAML` | 否 | 内置值及环境变量 | 指定 YAML 配置文件。相对路径以 CLI 当前目录为基准。 |
| `--mode MODE` | 否 | 配置的 `review.mode` | 覆盖审查方式，取值为 `baseline`、`rules` 或 `memory`。 |
| `--output JSON` | 否 | 标准输出 | 指定结果 JSON 保存路径，通过临时文件原子保存。 |

<a id="レビュー方式"></a>

### 审查方式

| 值 | 功能 |
|---|---|
| `baseline` | 基准审查，不添加仓库规则或记忆。 |
| `rules` | 添加适用的仓库规则。默认方式。 |
| `memory` | 除规则外，还添加有效且适用的记忆。需要 `memory.is_injection_enabled: true`。 |

在 `memory` 方式下，每次运行固定构建上下文时实际使用的记忆集合。
即使检出 0 个问题也会保存快照，可通过结果 JSON 中的 `run.versions.memory_snapshot_id` 查询。
新增、更新或停用有效记忆后，同一差异也会触发新的审查运行。

### 差异输入来源

| 参数 | 功能 |
|---|---|
| `--git-diff` | 通过 `git diff` 获取工作树相对于索引的未暂存差异。 |
| `--range BASE HEAD` | 获取 `BASE...HEAD` 的三点比较差异。两者均指定可解析为提交的 Git 引用。 |
| `--commit SHA` | 通过 `git show` 获取单个提交引入的差异。 |
| `--diff-file PATCH` | 读取 UTF-8 补丁文件，上限 20 MiB。 |

只需检查规则应用情况时，使用同一差异输入执行以下命令。

```bash
uv run --frozen review rules check \
  --repository . \
  --config review.yaml \
  --diff-file datasets/development/sample_nullable_user.patch
```

`rules check` 还接受 `--file PATH` 以诊断单个文件，以及 `--output JSON` 以保存结果。

### Python 审查规则

`config/rules/python/` 参考 Alibaba OpenCodeReview 内置的 Python 规则，为本项目以日文重新组织规则。
规则侧重安全、正确性、异常处理、异步处理和资源管理，仅在差异可证实实际影响时报告问题。
由 Ruff 或 mypy 确定性检测的表述问题不重复报告。

规则列表、适用策略和来源见 `config/rules/python/README.md`。

<a id="実際の-ai-ゲートウェイを使用する場合"></a>

## 使用真实 AI 网关

复制 `config/gateway.example.yaml`，将 `llm.model` 设为网关提供的固定模型 ID。
当前用于连接验证的模型为 `gpt-5.6-luna`。实际代码会发送到外部，因此必须显式提供以下三项设置。

1. `llm.provider: ai_gateway`
2. `security.allow_external_source: true`
3. 环境变量 `REVIEW_LLM_API_KEY`

```bash
export REVIEW_LLM_API_KEY='<gateway-credential>'
uv run --frozen review review \
  --repository . \
  --config config/gateway.example.yaml \
  --git-diff
```

网关默认 URL 为 `https://aigw.ldjp-ai.link`。应用使用 OpenAI 兼容模型别名，
无需依赖背后的供应商特有 API 或 SDK。凭据仅从环境变量获取。
默认不发送 `temperature`，使用模型默认值。需要显式设置时，`llm.temperature` 范围为 0～2。
GPT-5.6 推理模型禁止指定 `temperature: 0`。

`config/gateway.example.yaml` 默认关闭 M3。使用真实 LLM 验证 M3 时，先确认发送范围与预算，
再显式将 `review.context_tools_enabled` 和 `review.reflection_enabled` 设为 `true`。

使用 [AACR-Bench](docs/AACR-Bench_质量评测.md) 作为主要质量评测。
`datasets/real_llm_smoke/` 仅保留两个已知问题和一个安全对照，用于网关连接与响应冒烟。
预期结果和操作见 `datasets/real_llm_smoke/manifest.yaml` 与 `datasets/real_llm_smoke/README.md`。

结果 JSON 的 `metrics` 除 LLM 返回的候选数量外，还包含通过解析和规则引用校验的
`accepted_candidate_count`、被排除的 `rejected_candidate_count`，以及按固定原因代码归类的 `rejection_reasons`。
禁止保存被排除候选的正文或原始 LLM 响应。
问题行通过依据引用确定性地映射到新增行。模型提供的行号不直接作为验证结果。
若能通过解析改动后片段反证 Python 语法错误候选，则将其排除。

启用 M3 动态上下文时，使用计划阶段选定的 GENERAL／SECURITY／TEST 固定审查配置。
结果 JSON 保留原有 `metrics`，新加顶层 `pipeline_metrics`。
其中包括工具调用次数、成功次数、独立于 LLM 等待时间的工具处理耗时、按原因代码计数、
动态上下文 Token、自我验证次数和恢复次数。输出排除完整提示词、工具正文和原始响应。

<a id="フィードバックの登録"></a>

## 登记反馈

反馈所针对的审查运行和问题必须已保存在同一 SQLite 数据库。
先提交并保存反馈，再提取记忆候选。提取失败不撤销已保存的反馈。

评价已报告问题的示例：

```bash
uv run --frozen review feedback finding \
  --repository . \
  --config review.yaml \
  --run <RUN_ID> \
  --finding B001-F001 \
  --label false-positive \
  --reason "该调用方已经验证了前置条件。" \
  --actor reviewer-1
```

`--label` 取 `true-positive`、`false-positive`、`needs-more-evidence` 或 `not-applicable`。

登记漏检问题的示例：

```bash
uv run --frozen review feedback missed-issue \
  --repository . \
  --config review.yaml \
  --run <RUN_ID> \
  --file src/example.py \
  --start-line 12 \
  --issue-type null-dereference \
  --severity high \
  --description "引用的值可能为 None。" \
  --evidence "第 12 行未检查该值就访问了属性。" \
  --actor reviewer-1
```

漏检位置须至少指定 `--start-line` 或 `--symbol` 之一。
`--severity` 从 `critical`、`high`、`medium` 和 `low` 中选择。

<a id="メモリの確認と統制"></a>

## 查看与治理记忆

在配置的 `memory.maintainers` 中登记能够批准和驳回的用户。
将下列内容加入现有配置，保存为本地治理配置。

```yaml
memory:
  maintainers:
    - reviewer-1
  is_production: false
```

查看候选列表及详情。

```bash
uv run --frozen review memory list \
  --repository . \
  --config <包含维护人员设置的配置文件> \
  --status pending

uv run --frozen review memory show <MEMORY_ID> \
  --repository . \
  --config <包含维护人员设置的配置文件>
```

指定列表中的 `lock_version` 执行批准或驳回。同一候选如果已被更新，乐观锁会拒绝操作。

```bash
uv run --frozen review memory approve <MEMORY_ID> \
  --repository . \
  --config <包含维护人员设置的配置文件> \
  --actor reviewer-1 \
  --reason "批准此记忆，因为它可在多次审查中复用。" \
  --lock-version 0

uv run --frozen review memory reject <MEMORY_ID> \
  --repository . \
  --config <包含维护人员设置的配置文件> \
  --actor reviewer-1 \
  --reason "这条记忆仅适用于个别案例。" \
  --lock-version 0
```

审批时指定 `--scope-file YAML`，可创建适用范围缩小的新版本。

## 使用记忆审查

默认关闭记忆注入以防误用。要把已批准的记忆加入审查，请显式启用。

```yaml
review:
  mode: memory
  memory_tokens: 1200

memory:
  is_injection_enabled: true
  max_memories: 5
  per_memory_tokens: 200
```

```bash
uv run --frozen review review \
  --repository . \
  --config <启用记忆的配置文件> \
  --git-diff
```

仅检索同一项目中当前版本的有效记忆。按路径、符号、框架、语言、风险、类别和关键词进行确定性评分，
在数量上限和 Token 预算内选择。

## 既有自建数据集的 CLI 操作

公开质量评测使用固定版本的 AACR-Bench，沿用上游专家复核标签。
下面的发布、A/B/C 评估与人工裁决命令适用于本项目既有自建数据集。

评估前，将由人员创建、由其他负责人验证、由维护人员审批的问题整理为 JSON。
输入文档包含 `schema_version`、`split`、`version` 和 `issues`。每个问题包含 `sequence`、
`change_ref`、`file`、`start_line` 或 `symbol`、`issue_type`、`severity`、`evidence`、`origin`、
`created_by`、`verified_by` 和 `approved_by`。

~~~bash
uv run --frozen review ground-truth publish \
  --repository . \
  --config review.yaml \
  --input datasets/evaluation/ground-truth-test-v1.json \
  --actor reviewer-1
~~~

发布时根据配置的 `memory.maintainers` 验证执行者。已发布的 `split` 与 `version` 不能覆盖。
订正时增加 `version` 并发布新数据集。结果显示数据集哈希、问题数量、变更引用数量、严重问题数量，
以及是否满足评估启动条件。

## A/B/C 评估

使用已发布的正确答案数据集，比较基准审查、加入规则和加入记忆的结果。

- A：基准审查
- B：加入仓库规则
- C：加入规则和记忆，评估期间自动启用记忆注入

```bash
uv run --frozen review eval \
  --repository . \
  --config review.yaml \
  --arms A,B,C \
  --split development \
  --set-version <已发布数据集版本> \
  --repeat 3
```

`--repeat` 指定每组重复次数，范围为 3～100。评估所需的正确答案数据必须已发布到同一 SQLite 数据库。
评估与常规自动测试分开执行。

如仍有自动匹配无法唯一确定的问题，判定结果包含 `ADJUDICATION_REQUIRED`。
此时 B/C 成功条件未满足。每次重复的 `review_required` 给出未裁决数量。

只要有一次评估审查的状态不是 `success`，就返回 `INCOMPLETE_REVIEW_RUNS`（退出代码 2）。
失败运行不作为 0 个问题计分，也不保存已计算的比较组报告、匹配结果、指标或最终判定。
排除故障后重新执行同一评估命令。所有审查成功后才确定评估结果。

## 未裁决项的盲化检查与最终判定

无法通过自动匹配唯一确定的项目以盲化方式输出，隐藏比较组、模型及是否使用记忆。
省略输出路径时写入标准输出。

| 参数 | 适用命令 | 功能 |
|---|---|---|
| --split development／test | export、report | 正确答案数据的目标划分。 |
| --set-version VERSION | export、report | 目标已发布数据集的版本。 |
| --output JSON | export | 盲化 JSON 的保存路径，默认写入标准输出。 |
| --token TOKEN | resolve | 盲化输出分配的项目标识符。 |
| --result RESULT | resolve | 取 true-positive、false-positive 或 duplicate。 |
| --issue-id ISSUE_ID | resolve | 真阳性或重复项对应的正确答案问题 ID。 |
| --actor ACTOR_ID | resolve | 执行裁决的维护人员。 |
| --reason TEXT | resolve | 作为判断依据保存的说明。 |

~~~bash
uv run --frozen review adjudication export \
  --repository . \
  --config review.yaml \
  --split test \
  --set-version 1 \
  --output .review/adjudication-test-v1.json
~~~

人员核对依据后，按每个输出令牌记录结果。使用 `true-positive` 或 `duplicate` 时，必须指定对应的正确答案问题 ID。
使用 `false-positive` 时禁止指定该 ID。执行者须登记在配置的 `memory.maintainers` 中。

~~~bash
uv run --frozen review adjudication resolve \
  --repository . \
  --config review.yaml \
  --token <ADJUDICATION_TOKEN> \
  --result true-positive \
  --issue-id test-v1-I001 \
  --actor reviewer-1 \
  --reason "根因和依据位置一致。"
~~~

全部裁决后，使用已保存的匹配结果重新计算指标，以 JSON 显示 B/C 比较组的每次重复和最终判定。
若仍存在未裁决、重复次数不足或严重问题检出退步等情形，退出代码为 2。

~~~bash
uv run --frozen review adjudication report \
  --repository . \
  --config review.yaml \
  --split test \
  --set-version 1
~~~

`datasets/evaluation/ground-truth-test-v1.json` 是验证操作的小规模样本，达不到正式评估的样本要求。
正式判定要求至少 20 项变更、30 个问题、5 个严重问题，以及至少 3 项包含严重问题的变更。
数据由人员创建、其他负责人验证、维护人员审批。区分方式和准备步骤见 `datasets/evaluation/README.md`。

## 退出代码

| 代码 | 含义 |
|---:|---|
| `0` | 成功 |
| `1` | CLI 输入、配置或差异等输入无效 |
| `2` | LLM 调用失败、治理冲突或处理失败 |
| `3` | LLM 响应格式验证失败 |
| `4` | 违反路径策略、外部发送或敏感信息等策略 |
| `5` | 其他领域、持久化或操作系统层故障 |

错误输出不得包含 API 密钥、检出的敏感字符串、完整差异、完整提示词或原始 LLM 响应。

## 设计文档

- 总览和外部规格：[概要设计书](docs/概要设计书.md)
- 通用类型、端口、配置、异常和持久化：[通用详细设计书](docs/详细设计书_通用.md)
- [M1 审查基础](docs/详细设计书_M1_审查基础.md)
- [M2 反馈记忆](docs/详细设计书_M2_反馈记忆.md)
- [M3 流水线增强](docs/详细设计书_M3_流水线增强.md)
- 开发规范和质量门禁：[开发标准](docs/开发标准.md)
- AI 开发简明上下文：[AGENTS.md](AGENTS.md)

## 质量检查

```bash
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy src/self_improving_code_review
uv run --frozen pytest -q --cov=self_improving_code_review --cov-branch --cov-fail-under=100
uv run --frozen pytest -q tests/e2e/test_m3_cli.py
uv run --frozen bandit -q -r src/self_improving_code_review
uv run --frozen pip-audit
uv run --frozen pre-commit run --all-files
```

禁止自动测试隐式调用真实 LLM 或外部服务。
