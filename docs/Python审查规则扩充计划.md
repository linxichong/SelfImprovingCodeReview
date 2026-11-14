# Python 评审规则扩充计划与开发比较

## 1. 目的

规则采纳依据包括缺陷类型覆盖、误报、漏报、上下文不足和费用。
本次变更限于候选资料和开发试验，保留现有 12 条默认规则、提示词、模型、产品 CLI 和数据库 Schema。
候选规则放在 `datasets/development/python_rule_candidates/v1/rules/`，与默认加载器隔离。

## 2. 本次固定证据

| 项目 | 固定值或处理方式 |
|---|---|
| 源提交 | `6b133e60d40def53f508f9d9c7ea779aa413a48c` |
| 评审输入 | 脱敏补丁 SHA-256 `c51c906aff36c008a510e9da15a88ade949ead2a9d3f09ffaa96f6aa16f9b375` |
| baseline 运行 | `b2f67c6e-e847-4a7d-a2ab-a6f4d5bc37a5`，7 条发现 |
| rules 运行 | `32c08e69-4f9a-4b41-9c77-f43b7927f576`，4 条发现 |
| 用户确认 | baseline 5 条成立、2 条误报，rules 4 条均成立 |
| 明确误报 | baseline `B006-F001`、`B006-F002`。停用代理功能后移除资源复制符合变更意图 |
| 确认的效力 | 用户通过聊天确认。正式裁定须由独立验证者和审批者完成 |

baseline 和 rules 的问题 ID 或指纹可能随表述变化。漏报统计须比对文件、位置和根因，并由人员确认是否为同一问题。
本次成立的 5 条中，rules 未报告其中一条清理路径问题。规则的因果影响需要更多比较验证。

## 3. 独立确认

### 3.1 复现的未报告候选

`view/auto_chrome_driver.py:28` 将配置读入局部变量 `download_folder`，但在非空分支访问了尚未设置的 `self._download_folder`。

只从固定提交提取构造函数的 AST，将基类、Chrome 选项和驱动获取替换为测试实现后检查。

| 条件 | 结果 |
|---|---|
| 空配置 | 到达驱动获取前 |
| 非空配置 | `AttributeError: _download_folder` |
| 非空配置，仅将访问表达式改为局部变量作为对照 | 到达驱动获取前 |

检查在测试替身中进行，没有启动网络、真实浏览器或目标应用。源提交保持原样。
`MISS-001` 是有复现证据的候选，正确答案仍需用户审批。

### 3.2 已排除的初始假设

`common/utils_yml.py` 的删除行本身不足以推断模块或公开函数被删除。
检查固定版本的 AST 后确认 `exec_flowdata` 仍存在，因此排除了导入失败假设。

### 3.3 单靠规则无法补足的条件

- 无法看到继承关系、调用顺序、已有行或配置契约时，返回 `NEEDS_CONTEXT`，保留待确认状态。
- 功能停用的业务判断需要与固定的需求和变更意图核对，差异可能没有记录这些信息。
- 这次检查只覆盖列出的情形，完整正确答案集和召回率仍需全面人工评审。

## 4. 覆盖表

「部分」表示目前有相关单项规则，覆盖范围限于该规则定义的情形。

| 领域 | 默认规则 | 主要缺口 | 方针 |
|---|---|---|---|
| 输入与边界 | `PYTHON.NULL_DEREFERENCE`（部分） | 空集合、缺失键、除零、上游契约 | 评估有依据的候选，与类型检查分工 |
| 可变状态与比较 | `PYTHON.MUTABLE_DEFAULT_ARGUMENT`、`PYTHON.SHARED_MUTABLE_STATE`、`PYTHON.IDENTITY_COMPARISON` | 实际调用与共享范围 | 保留现有规则并补充反例 |
| 初始化、继承与清理 | 无专用规则 | 非空配置分支、子类初始化、失败后清理 | 将 `PYTHON.ATTRIBUTE_INITIALIZATION` 列为候选 |
| 异常与资源 | `PYTHON.SWALLOWED_EXCEPTION`、`PYTHON.RESOURCE_LEAK`（部分） | 原异常被掩盖、部分获取、取消 | 结合静态检查和上下文核对 |
| 注入、路径与恢复 | `SECURITY.SQL_INJECTION`、`SECURITY.COMMAND_INJECTION`、`SECURITY.PATH_TRAVERSAL`、`SECURITY.UNSAFE_DESERIALIZATION` | 输入可控性、编码、归档 | 将 `SECURITY.UNSAFE_ARCHIVE_EXTRACTION` 列为候选 |
| 加密与通信 | `SECURITY.WEAK_CRYPTOGRAPHY`（部分） | TLS 验证、可信 CA、HTTP 调用上限 | 增加 TLS 和超时候选 |
| 敏感数据与授权 | 无专用 LLM 规则 | 日志中的数据流、对象归属与权限 | 将敏感日志列为候选。授权应另行结合业务契约设计 |
| 并行与异步 | `PYTHON.ASYNC_BLOCKING_IO`、共享状态规则（部分） | 竞争、缺少 await、取消、死锁 | 须提供执行形态和共享方式的证据 |
| 外部调用与一致性 | 无专用规则 | 重试副作用、幂等性、部分更新 | 将 `PYTHON.RETRY_SIDE_EFFECT` 列为候选 |
| 需求与功能变更 | 仅通用评审 | 功能停用、替换、配置未生效 | 准备正负用例与固定契约。批处理文件的意图由契约确定 |

## 5. 候选来源和约束

| 候选 | 参考来源 | 采纳时所需证据 |
|---|---|---|
| 禁用 TLS 验证 | [CodeQL](https://codeql.github.com/codeql-query-help/python/py-request-without-cert-validation/) | 禁用参数、通信目标、运行环境 |
| 等待时间上限 | [Bandit B113](https://bandit.readthedocs.io/en/latest/plugins/b113_request_without_timeout.html)、[HTTPX](https://www.python-httpx.org/advanced/timeouts/) | 实际 API、默认值和客户端设置。报告前须确认实际超时语义 |
| 将敏感信息写入日志 | [CodeQL](https://codeql.github.com/codeql-query-help/python/py-clear-text-logging-sensitive-data/) | 从敏感输入到日志的路径及是否做掩码处理 |
| 不安全的解包 | [CodeQL](https://codeql.github.com/codeql-query-help/python/py-tarslip/)、[Python 3.12](https://docs.python.org/3.12/library/tarfile.html#extraction-filters) | 不可信输入、过滤器、Python 版本和输出位置 |
| 属性初始化 | [Python 数据模型](https://docs.python.org/3.12/reference/datamodel.html#object.__getattribute__) | 构造函数、继承、调用前提及目标路径 |
| 重试副作用 | [AWS Builders' Library](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) | 副作用、应答丢失、幂等键和接收方契约 |

规则文案和示例为独立编写，禁止复制上游的规则实现或正文。在 manifest 中保留来源 ID 和核对日期。
采纳候选前确认来源版本的固定与使用方式。对于 Ruff 或 Bandit 可确定性检出的模式，开发评审重点补充上下文和排除条件。

## 6. 开发用例

6 条规则各包含阳性、阴性和上下文不足三种用例，共 18 个。
另加 2 个功能停用的阴性用例，共 20 个。

参见[用例与规则规范](../datasets/development/python_rule_candidates/v1/README.md)
和[带哈希的 manifest](../datasets/development/python_rule_candidates/v1/manifest.yaml)。

2 个功能停用用例需要单独固定的业务契约。常规 M1 输入未提供契约时，自动评分不得假设模型已经知晓契约。
开发比较时将这些用例单列为需人工提供上下文的用例。

## 7. 验证与采纳条件

1. 检查所有候选的 YAML、与现有 ID 的重复项、适用范围，以及用例的差异、语法和严格扫描结果。
2. 使用同一输入、模型、提示词，对现有 12 条规则与加入候选后的 18 条规则各比较至少 3 次。
3. 记录已选择、已实际发送和因预算截断的规则，以区分未发送与漏报。
4. 只有所有评审都成功的实验可以进入初步核对。未完成时标记为 `INCOMPLETE_REVIEW_RUNS`。
5. 行位置只是初步匹配依据。由人员裁定问题类型、根因、误报、重复项及严重程度。
6. 核查正例检出、反例不报告、上下文不足的参考分类、现有严重问题的保留和费用。
7. 只有达标候选才能纳入默认规则。启用时同步更新设计、规则和评估。

这套小规模开发数据和聊天确认只适用于开发比较。正式 B/C 判定须另行满足概要设计第 15.2～15.3 节规定的总体样本、独立验证和审批条件。
禁止将这套开发数据复用为测试集。

## 8. 分别评估属性规则和固定上下文

单文件比较漏报了属性问题，因此对以下四组各比较三次。

| 组别 | 规则 | 固定基类源代码 |
|---|---|---|
| current | 现有 12 条 | 无 |
| attribute | 现有规则加 1 条属性初始化规则 | 无 |
| current-parent | 现有 12 条 | 有 |
| attribute-parent | 现有规则加 1 条属性初始化规则 | 有 |

目标差异限于同一个 Chrome 驱动文件，其他五个候选均不加入。
固定同一提交的 `view/auto_base_driver.py` 作为基类源代码，应用路径策略、64 KiB 上限、2,000 估算 Token 上限、严格扫描、边界转义与发送前再次核对。
记录 Git 引用、内容哈希、版本和发送请求哈希。禁止保存原文或完整提示词。

将附加上下文作为不可信数据绑定到 `prompts/candidates/diagnostics/review_general_parent_context_v1.yaml` 的候选资源。
系统指令与默认版一致。分别计算资源哈希和绑定后模板哈希，使实际内容关联到运行记录。
基类文件不加入变更目标，因此拒绝将针对该文件的发现作为主要结果。

此固定上下文开发实验维持 M1 原有功能、默认规则和默认提示词，结果仍是正式裁定前的候选。
辅助执行器位于 `.review/pyautotest-6b133e6-validation/attribute_context_ablation.py`。
仅在显式传入 `--live` 时进行合计 12 次真实模型调用。
