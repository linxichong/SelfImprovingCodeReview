# M4 详细设计书：SCM 集成

## 1. 目的

M4 将 SCM 上的变更请求与 M1～M3 的既有审查流程安全连接，
发布经验证的结果，并同步人员显式给出的反馈。

M4 初版采用适合个人开发运维的最小配置，遵循以下原则。

- 优先支持 GitHub。
- 仅通过现有的 `review` CLI 启动，不新增常驻服务器。
- 由用户显式执行拉取式集成。
- 默认生成评论发布计划，仅在显式指定时对外发布。
- 通过提供商无关的端口及模拟契约覆盖 GitLab，实际适配器留待后续实现。

## 2. 范围

### 2.1 M4 初版包含的功能

1. 获取 GitHub 拉取请求信息
2. 验证本地 Git 中存在的基准和最新提交
3. 通过既有 M3 流水线执行审查
4. 以确定性方式规划行内评论与摘要评论
5. 以 JSON 输出试运行结果
6. 在显式允许时幂等创建或更新评论
7. 手动同步显式反馈命令
8. 保存发布、重试、过期及反馈同步的审计记录

### 2.2 M4 初版范围之外

- 接收 Webhook 的服务器
- 消息队列或常驻工作进程
- GitLab 实际适配器
- 从表情回应、解决操作或自由文本回复推断标签
- 自动解决或删除旧评论
- 隐式依据 SCM 执行 Git fetch 或 checkout
- Web 浏览界面

待确认 CLI 集成的运行实绩和权限方针后，再扩展 Webhook 及自动启动。

## 3. 前提与阶段门禁

### 3.1 已确定的事项

- TBD-07 已确定优先支持 GitHub。
- 初版在配置中将一个 GitHub 仓库与一个本地 Git 根目录对应。
- SCM ID 与内部的运行 ID、审查发现 ID 分开管理。

### 3.2 使用真实数据前的条件

通过现有的 `security.allow_external_source` 显式声明 TBD-08 所要求的外部发送授权。
未经授权时，仅能启动使用 Dataset 或经批准的本地 LLM 的审查。

对外发布还必须同时满足以下三个条件。

1. `scm.enabled=true`
2. `scm.publish_enabled=true`
3. CLI 指定 `--publish`

`scm.enabled=false` 时直接拒绝 SCM 命令。
即使 `scm.enabled=true`，其余两个条件有任意一个未满足时也只做试运行，
不会改变外部状态。

## 4. CLI

### 4.1 审查与发布计划

~~~bash
review scm review \
  --repository . \
  --config review.yaml \
  --change 123 \
  --output .review/scm-review-123.json
~~~

只有确实需要发布时才添加 `--publish`。

### 4.2 同步反馈

~~~bash
review scm sync-feedback \
  --repository . \
  --config review.yaml \
  --change 123
~~~

### 4.3 共通契约

| 参数 | 是否必填 | 说明 |
|---|---:|---|
| `--repository PATH` | 可选 | 本地 Git 根目录，默认为当前位置 |
| `--config YAML` | 可选 | SCM 与既有审查配置 |
| `--change ID` | 必填 | GitHub 拉取请求编号 |
| `--mode MODE` | 可选 | `baseline`、`rules`、`memory` |
| `--publish` | 可选 | 三重门禁全部满足时允许对外发布 |
| `--output JSON` | 可选 | 计划或运行结果的保存位置 |

SCM 命令沿用既有退出码。仅发布失败时，审查结果保持原样，
CLI 返回表示部分失败的退出码 2。

## 5. 配置

~~~yaml
scm:
  enabled: false
  provider: github
  repository_external_id: owner/repository
  api_base_url: https://api.github.com
  publish_enabled: false
  feedback_sync_enabled: false
  include_drafts: false
  bot_actor_external_id: self-reviewer-bot
  feedback_actor_mapping: {}
  allowed_api_hosts: [api.github.com]
  max_inline_comments: 20
  request_timeout_seconds: 30
  max_response_bytes: 8388608
  max_retries: 2
~~~

- 令牌仅从 `REVIEW_SCM_TOKEN` 环境变量获取。
- 禁止在配置、数据库、日志及异常中保存令牌、认证请求头与原始响应。
- 初版 `provider` 的唯一有效值为 `github`。
- 验证 `repository_external_id` 与预期完全匹配，区分大小写。
- API URL 必须使用 HTTPS，禁止用户信息、查询参数和片段。
- 在 HTTP 发送边界，连接前再次验证 URL 为绝对 HTTPS URL 且包含主机。
- 启动时确认认证用户 API 返回的结果与 `bot_actor_external_id` 一致。
- 使用 `feedback_actor_mapping` 将反馈提交者的外部名称映射为内部 ID。
- 仅对限流、临时 5xx 和连接故障进行重试。
- 响应体默认上限为 8 MiB，可配置范围为 1 KiB～20 MiB。预检查 `Content-Length`，
  并通过读取“上限 + 1”个字节，在声明缺失或无效时也强制执行上限。
- 通过上下文管理器释放普通响应，`HTTPError` 持有的响应也必须关闭，覆盖请求头验证及响应体读取。
  即使因响应体超限或 `Content-Length` 无效而中途失败，也不得将释放交给垃圾回收。

## 6. 架构

~~~text
CLI
→ 获取 SCM 变更请求
→ 验证本地 Git 和受信任资源
→ 既有 RunReviewUseCase
→ 生成发布计划
→ 输出试运行结果
→ 仅在显式授权时发布 SCM 评论

CLI
→ 获取 SCM 评论增量
→ 核对机器人评论与内部发布记录
→ 解析显式反馈
→ 保存 M2 反馈并提取候选
~~~

沿用既有依赖方向。

~~~text
cli / infrastructure -> application -> domain
security -> application
~~~

在基础设施边界将 SCM SDK 类型、HTTP 异常和外部 ID 转换为项目类型。
禁止向应用层和领域层暴露 GitHub 专有条件。

## 7. 提供商无关的端口

~~~python
class ScmClient(Protocol):
    def get_change(self, ref: ChangeRef) -> ScmChange: ...
    def create_comment(self, command: CreateComment) -> PublishedComment: ...
    def update_comment(self, command: UpdateComment) -> PublishedComment: ...
    def list_comment_events(
        self,
        ref: ChangeRef,
        cursor: EventCursor | None,
    ) -> EventPage: ...
~~~

`ScmChange` 只包含以下字段。

- provider
- repository_external_id
- change_external_id
- base_sha
- head_sha
- state
- is_draft
- source_repository_external_id
- updated_at

标题、正文、作者姓名等自由文本不得用作审查的可信指令。
如需使用，应作为不可信元数据处理。

初版 SCM 适配器不获取代码正文及差异。审查内容从经验证的本地 Git 对象获取，
SCM 仅负责识别变更请求、查询状态和操作评论。

## 8. 本地 Git 与可信资源

### 8.1 运行前提

开始 SCM 审查之前，确认以下条件全部满足。

1. `--repository` 指定 Git 根目录。
2. 工作树的 `HEAD` 与 SCM 的 `base_sha` 一致。
3. 本地能够将 `base_sha` 和 `head_sha` 解析为提交。
4. 配置指定的外部仓库 ID 与 SCM 响应一致。
5. 变更请求处于打开状态，并符合草案排除设置。

本地缺少最新提交时返回安全错误，禁止隐式执行 fetch。
用户须预先通过经认证的 Git 操作获取提交。

### 8.2 既有流水线的输入

复用既有差异解析器。将经验证的 `base_sha` 与 `head_sha` 作为
`DiffSourceKind.RANGE` 传给既有的 `RunReviewUseCase`。
以此沿用 M3 的差异解析、敏感信息检查、审查单元划分、补充上下文、
自我验证、并行处理和严格恢复流程。

### 8.3 信任边界

从与 `base_sha` 一致的工作树或运维人员部署的未跟踪配置中读取提示词、规则及配置。
禁止将拉取请求 `head_sha` 中的提示词、规则和配置用作可信指令。

仅允许 M3 的 `GitContextSource` 从 `head_sha` 读取代码补充上下文，
作为不可信输入重新检查后再发送给 LLM。

## 9. 处理顺序

<a id="91-レビュー"></a>

### 9.1 审查

1. 验证配置、发布三重门禁和凭据的有无。
2. 获取 SCM 变更请求，验证外部 ID、状态、基准 SHA 和最新 SHA。
3. 验证第 8.1 节的本地 Git 条件。
4. 保存或更新 SCM 请求标识符。
5. 使用 `base_sha...head_sha` 执行或复用现有 M3 审查。
6. 发布前重新获取 SCM 的最新 SHA 与状态。
7. SHA 变化时，将发布记录标记为 `STALE` 并停止发布。
8. 按稳定顺序生成发布计划。
9. 试运行仅保存及输出计划。
10. 指定 `--publish` 时，在短事务内保存发布意图，再调用外部 API。
11. 收到响应后，在短事务内更新发布状态。

<a id="92-フィードバック同期"></a>

### 9.2 同步反馈

1. 获取已保存游标之后的评论事件。
2. 按外部事件 ID 去重。
3. 仅处理机器人创建且与数据库发布记录关联的评论。
4. 验证显式命令、理由、操作者、权限及目标审查发现。
5. 调用与 M2 相同的保存及记忆候选提取流程。
6. 保存结果后推进游标。

- 匹配目标发布记录的事件，在同一事务内保存审计记录与事件时间游标。
  对于重复事件及不在审计范围内的评论，处理完成后只保存游标。
- 按更新时间与外部事件 ID 的稳定顺序处理事件，所有事件处理完成后才保存页尾游标。
  中途失败时不能跨过未处理事件推进游标。
- 对于空页，仅当提供方返回新的页尾游标时才保存，该游标缺失时不创建。

在调用外部 API 期间，禁止持有 SQLite 事务。

<a id="10-冪等性と再実行"></a>

## 10. 幂等性与重新运行

SCM 请求标识符由以下各项组成。

~~~text
provider:repository_external_id:change_external_id:head_sha
~~~

此标识符仅用于识别 SCM 请求，审查运行继续使用原有的幂等键。
既有审查键包含 M3 差异哈希、配置、模型、软件包、提示词、规则、
记忆及敏感信息检查器。

当用户显式发起重新审查时，设置既有的 `execution_nonce`。
同一次审查运行重新发布时更新已有发布记录，避免重复创建评论。

## 11. 评论计划与位置映射

### 11.1 发布范围

- 将 `main_findings` 中可映射到变更后新增行的审查发现转为行内评论。
- 将 `reference_findings` 与无法转换位置的主要审查发现放入摘要评论的引用栏。
- 摘要仅显示安全的失败计数，禁止将失败的审查单元展示为 0 条审查发现。
- 即使没有审查发现，也可以计划一条表示审查已完成的摘要评论。

行内评论按严重程度降序、路径、起始行和指纹依次排序。
超出 `max_inline_comments` 的主要审查发现移到摘要中，完整保留。

### 11.2 提供商无关的位置

~~~python
@dataclass(frozen=True, slots=True)
class CommentPosition:
    path: NormalizedPath
    line: int
    side: Literal["new"]
    head_sha: str
~~~

M1～M3 的审查发现仅限新增行，因此初版仅允许使用变更后的行。
对于删除行、上下文行、越界行或 SCM 不接受评论的位置，禁止猜测性修正。

### 11.3 评论正文

正文包含标题、严重程度、说明、依据、修复建议和规则 ID。
不得包含完整差异、完整代码、提示词、记忆正文和原始 LLM 响应。

在末尾添加以下机器可读标记。

~~~text
<!-- self-reviewer:publication=<publication-id>;head=<sha> -->
~~~

处理反馈时，除了验证标记，还必须验证外部评论 ID、机器人作者、
数据库发布记录、变更请求、SHA 及内部审查发现 ID 完全一致。

<a id="12-フィードバック契約"></a>

## 12. 反馈契约

M4 保留 M2 的外部接口值，仅接受以下显式命令。

~~~text
/review-feedback true-positive --reason "..."
/review-feedback false-positive --reason "..."
/review-feedback needs-more-evidence --reason "..."
/review-feedback not-applicable --reason "..."
~~~

- `missed-issue` 不引用现有审查发现，因此使用既有 CLI 的结构化输入。
- 理由必填，复用 M2 的自由文本规范化和字符数上限。
- 拒绝未知标签、变体写法、无理由、多条命令及通过编辑改变目标的命令。
- 仅有表情回应、解决会话或普通回复时，不生成反馈。
- 通过 `external_event_id` 唯一约束确保同一外部事件只处理一次。

从配置的允许列表将 SCM 登录名唯一映射至内部 `actor_id`，
通过 `ActorAuthorizer` 验证 `RECORD_FEEDBACK` 权限。

## 13. 发布状态机

~~~text
PLANNED
→ PUBLISHING
→ PUBLISHED

PUBLISHING → FAILED
PLANNED / PUBLISHING / PUBLISHED / UPDATING / FAILED → STALE
PLANNED / PUBLISHING / PUBLISHED / UPDATING / FAILED → BLOCKED
FAILED → PUBLISHING / UPDATING
FAILED → PUBLISHED（核对已创建但丢失响应的评论后恢复）
PLANNED → UPDATING（既有外部评论正文变更）
PUBLISHED → UPDATING → PUBLISHED
UPDATING → FAILED
PUBLISHED / PUBLISHING / UPDATING / FAILED → PLANNED（请求哈希变化时重新规划）
PUBLISHING / UPDATING / FAILED → 同一状态（租约过期后重新获取及再次失败）
~~~

- `domain/scm/transitions.py` 是状态转换表的唯一来源。持久化通过原状态作为条件的
  单条 UPDATE 语句应用转换。条件不符的更新抛出 `InvalidStateTransitionError`，
  禁止以成功状态覆盖在外部调用期间已变为 `STALE` 或 `BLOCKED` 的发布记录。
- 调用外部服务前，保存 `PUBLISHING` 或 `UPDATING` 状态和请求哈希。
- 对于相同请求哈希的未确认处理，在租约到期前禁止其他处理获取它。
- 创建响应丢失时，先核对标记及已知外部 ID，再重试。
- 认证、授权和无效请求错误不重试。
- 仅对连接故障、限流及临时 5xx 在配置上限内重试。
- 最新 SHA 变化或变更请求关闭时记为 `STALE`，不重试。
- 初版禁止自动删除或自动解决外部评论。
- `BLOCKED` 是发布正文检测到敏感信息时的终止状态。

## 14. 安全

1. 将 SCM 元数据、评论、PR 正文和用户名视为不可信输入。
2. 验证 API 响应大小、内容类型、JSON Schema、外部 ID 与 SHA 格式。
3. 记录是否为派生仓库。无论是否派生，均不向代码侧传递令牌或环境变量。
4. 禁止信任 PR 的 head 侧包含的配置、提示词和规则。
5. SCM 令牌遵循最小权限原则，条件允许时拆分读取与发布权限。
6. 禁止将认证头、URL 查询参数、原始响应及评论全文保存到日志。
7. 只持久化异常的安全分类码，排除外部响应正文。
8. 发布正文也须通过敏感信息检查。检出时停止发布并标记为 `BLOCKED`。
9. API 基础 URL 可切换的主机由配置允许列表限制。

后续若引入 Webhook，应先检查正文大小和内容类型，再使用原始正文验证提供商签名。
如果提供商没有签名时间，使用投递 ID 唯一约束与接收时间上限管理重放，
禁止增加自行设计的时间校验。

## 15. 持久化

通过新增前向迁移 `0008_m4_scm.sql` 实现，保留既有迁移。

### 15.1 `scm_changes`

| 列 | 内容 |
|---|---|
| provider | 外部接口值 |
| repository_external_id | `owner/repository` |
| change_external_id | PR 编号的字符串表示 |
| base_sha / head_sha | 经验证的完整 SHA |
| state / is_draft | 获取时的状态 |
| source_repository_external_id | 派生来源标识 |
| latest_run_id | 最近一次审查运行，可选 |
| updated_at | SCM 更新时间 |
| observed_at | 本系统确认时间 |

`(provider, repository_external_id, change_external_id)` 必须唯一。

### 15.2 `scm_publications`

| 列 | 内容 |
|---|---|
| publication_id | 内部 UUID |
| run_id / finding_id | 审查结果，摘要的 finding_id 为 NULL |
| provider / repository / change | 外部目标 |
| head_sha / fingerprint_hash | 发布单元 |
| kind | `inline` 或 `summary` |
| external_comment_id | 发布后设置 |
| request_hash | 正文与位置的安全哈希 |
| status | 第 13 章的状态 |
| attempt / lease_until | 重试及并发控制 |
| safe_error_code | 安全分类，可选 |
| created_at / updated_at | UTC |

`(provider, repository, change, head_sha, kind, fingerprint_hash)` 必须唯一。
摘要评论的指纹取固定值。

### 15.3 `scm_feedback_events`

| 列 | 内容 |
|---|---|
| provider / external_event_id | 唯一外部事件 |
| publication_id / external_comment_id | 目标发布记录 |
| actor_id | 规范化的内部操作者 |
| label | M2 外部接口值 |
| payload_hash | 规范化命令的哈希 |
| mapped_feedback_id | 已保存的反馈，可选 |
| status / safe_error_code | 处理结果 |
| observed_at / processed_at | UTC |

禁止保存评论正文。

### 15.4 `scm_event_cursors`

按 `(provider, repository, change)` 保存最后确认处理的游标和更新时间。
事件保存与推进游标在同一短事务内确认。

<a id="16-失敗分類"></a>

## 16. 失败分类

| 分类 | 重试 | 处理 |
|---|---|---|
| 配置、外部 ID、SHA、本地 Git 不一致 | 否 | 输入不合法 |
| 认证或授权失败 | 否 | `FAILED`，输出安全警告 |
| 限流 | 有上限 | 先按 `Retry-After`，再按 `X-RateLimit-Reset` 决定等待时间。适用于 `429` 和剩余次数为 0 的 `403`。请求等待超过 60 秒时直接失败 |
| 连接故障或临时 5xx | 有上限 | 只对 `500/502/503/504` 执行指数退避重试 |
| 请求或位置不合法 | 否 | 转入摘要或标记 `FAILED` |
| 发布前 SHA 变化 | 否 | `STALE` |
| 审查成功、发布失败 | 只重试发布 | 保留审查运行的成功状态 |
| 发布正文检出敏感信息 | 否 | 发布记录标记为 `BLOCKED` |

评论创建或更新发生 HTTP 失败时，将 `HTTP_403` 等状态码记录在 `safe_error_code`。
异常、数据库和 CLI 输出均不得包含外部错误正文。

## 17. 度量

运行结果或 SCM 操作结果应补充以下度量。

- SCM API 调用次数、重试次数和总等待时间
- 计划的行内评论和摘要评论数
- 创建、更新、失败、过期和拦截的数量
- 同步事件数、重复数，以及接受和拒绝的反馈数
- 重新确认最新 SHA 时中止的数量

度量排除完整评论正文、完整 API 响应和令牌。

## 18. 测试设计

自动化测试禁止隐式调用真实 SCM 或 LLM。

### 18.1 单元测试

函数级相关证据见[测试用例对应表](测试用例对应表.md)，
未确认条件与测试层级区别见[对应补充说明](测试用例对应补充说明.md)。

| ID | 对象 | 主要检查 |
|---|---|---|
| UT-M4-001 | 配置 | 默认禁用、三重门禁、HTTPS 和上限 |
| UT-M4-002 | 外部 ID 与 SHA | 合法值、空值、未知 provider 和格式不合法 |
| UT-M4-003 | 发布计划 | 稳定顺序、上限、转入摘要、0 条审查发现 |
| UT-M4-004 | 位置转换 | 新增行、重命名、删除、越界及旧 SHA |
| UT-M4-005 | 评论正文 | 必填项、标记、无敏感信息 |
| UT-M4-006 | 反馈解析 | 四个标签、理由必填、未知或多条命令 |
| UT-M4-007 | 状态机 | 所有转换、禁止的转换与租约期限 |
| UT-M4-008 | 权限映射 | 已授权人员、未知人员、空操作者 |

### 18.2 契约测试

| ID | 对象 | 主要检查 |
|---|---|---|
| CT-M4-001 | GitHub HTTP | 请求、请求头、分页、严格验证响应 |
| CT-M4-002 | 异常转换 | 认证、授权、限流、5xx 和连接失败 |
| CT-M4-003 | SQLite | 唯一约束、状态更新、租约与游标原子性 |
| CT-M4-004 | SCM 端口模拟 | 禁止暴露 GitHub 专有类型 |

### 18.3 集成测试

- 将临时 Git 仓库的 `base_sha...head_sha` 传入 M3。
- 在调用 LLM 之前拒绝工作树 HEAD 与基准 SHA 不一致的情况。
- 防止重复发布同一审查发现，正文变化时更新。
- 发布期间 head SHA 变化时标记为 `STALE`。
- SCM 命令只触发一次 M2 反馈处理和候选提取。
- 调用外部服务期间不持有 SQLite 事务。

### 18.4 端到端测试

使用模拟 SCM、真实 Git、SQLite、Dataset LLM，通过 CLI 验证以下事项。

1. 试运行的外部更新数为 0。
2. 显式发布时创建行内评论和摘要评论。
3. 使用相同输入重新运行时更新或复用评论，避免重复创建。
4. 更新 SHA 后不发布旧结果。
5. 将显式反馈保存到 M2。
6. 输出和数据库均不保存凭据或评论全文。

`tests/e2e/test_m4_cli.py` 结合真实 Git、SQLite、Dataset LLM 与有状态的模拟 SCM，
用一条 CLI 路径验证试运行、创建、重新运行时复用、同步反馈和提取 M2 候选。
另以一条路径验证审查后 head SHA 变化会中止发布。

真实 GitHub 沙箱测试采用独立任务手动显式执行，
常规质量门禁及开发人员终端均不启动该任务。

### 18.5 质量门禁

~~~bash
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy src/self_improving_code_review
uv run --frozen pytest -q --cov=self_improving_code_review --cov-branch --cov-fail-under=100
~~~

增加依赖时，还应执行 `uv lock --check`、全部组同步、Bandit、pip-audit、
detect-secrets 与 pre-commit。

## 19. 实现位置

| 位置 | 职责 |
|---|---|
| `domain/scm/` | 外部 ID、状态、发布记录与状态转换 |
| `application/scm/` | 审查协调、规划、发布及同步 |
| `application/ports/scm_client.py` | 提供商无关的端口 |
| `infrastructure/scm/github_client.py` | GitHub API 边界 |
| `infrastructure/persistence/` | SCM 存储库及工作单元 |
| `cli/scm_commands.py` | `review scm` 子命令 |
| `migrations/0008_m4_scm.sql` | 前向迁移 |
| `tests/unit/` | 纯逻辑 |
| `tests/contract/` | HTTP 与 SQLite 契约 |
| `tests/integration/` | M3、M2 与 SCM 集成 |
| `tests/e2e/test_m4_cli.py` | CLI 端到端流程 |

## 20. 完成条件

- 能比对 GitHub 变更请求与本地 Git 的固定 SHA。
- 复用 M3 既有处理执行审查。
- 默认试运行，只有满足三重门禁才能对外发布。
- 能以确定性和幂等方式规划及发布行内评论与摘要评论。
- 不向旧 SHA 发布，将历史记录保留为 `STALE`。
- 仅保存显式的 M2 标签，且只保存一次。
- 禁止将 PR head 的配置、规则和提示词用作可信指令。
- 禁止保存凭据、完整代码、完整评论和原始 API 响应。
- 常规测试禁止调用真实 SCM 或 LLM。
- 含 M4 的语句及分支覆盖率必须严格达到 100%。
- README、AGENTS.md、配置示例及 CLI 帮助均与实现一致。
