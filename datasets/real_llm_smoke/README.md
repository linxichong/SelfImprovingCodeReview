# 真实 LLM 连接验证数据集

该数据集用于手动验证真实 AI 网关连接、结构化输出和安全对照。
主要质量评测见 [AACR-Bench](../../docs/AACR-Bench_质量评测.md)。
保留两个已知问题（命令注入、SQL 注入）和参数化 SQL 安全对照。
`datasets/development/` 和 `datasets/test/` 则使用固定响应。

## 目标

- 验证 AI 网关连接、响应格式、Token、等待时间和成本。
- 使用 `baseline` 和 `rules` 审查同一差异，比较添加规则的效果。
- 人工核对已知问题是否漏报，以及正常代码是否被误报。

本数据集只用于小规模冒烟测试。正式 M2 评估要求已发布的正确答案数据，判定 MVP 是否成功还需满足规定的样本要求。

## 组成

- `manifest.yaml` 记录用例、预期主要问题、允许的问题类型和检查项。
- `patches/*.patch` 提供传给 `review review --diff-file` 的 UTF-8 Unified Diff。

数据不包含真实凭据、个人信息和未公开代码。每个问题用例聚焦一个根因，正常用例用于检查误报率。

## 执行前准备

复制 `config/gateway.example.yaml`，在 `llm.model` 中设置具体且固定的模型 ID。
仅通过环境变量提供凭据。

```bash
export REVIEW_LLM_API_KEY='<gateway-credential>'
```

## 逐条执行

首先用无附加规则的 `baseline` 检查连接与常规检出能力。

```bash
uv run --frozen review review \
  --repository . \
  --config <LIVE_LLM_CONFIG> \
  --mode baseline \
  --diff-file datasets/real_llm_smoke/patches/03_sql_injection.patch \
  --output .review/real-llm-smoke/03-baseline.json
```

添加 Python 规则后，用 `rules` 执行相同差异。

```bash
uv run --frozen review review \
  --repository . \
  --config <LIVE_LLM_CONFIG> \
  --mode rules \
  --diff-file datasets/real_llm_smoke/patches/03_sql_injection.patch \
  --output .review/real-llm-smoke/03-rules.json
```

## 判定方法

比较 `manifest.yaml` 与结果 JSON，记录以下项目。

1. `should_find: true` 的用例是否检出主要问题。
2. 文件与新增行是否正确对应。
3. 根因是否属于 `accepted_issue_types` 之一。
4. 严重程度是否符合预期。
5. `should_find: false` 的用例是否没有主要问题。
6. 同一问题是否重复报告。
7. 是否已保存运行状态、输入输出 Token、等待时间、尝试次数和成本。
8. 若候选未成为正式问题，检查 `rejection_reasons` 的固定原因代码。

若 `candidate_count` 至少为 1 且 `main_finding_count` 为 0，检查 `accepted_candidate_count`、
`rejected_candidate_count` 与 `rejection_reasons`，判断候选是否在解析或规则引用校验时被排除。
出于安全原因，排除候选的正文和原始响应不得保存。

baseline 使用提示词版本 `review_general_v1@5` 或更新版本，确认返回通用 ID `GENERAL.<ISSUE_TYPE>`。

报告行号由引用的证据确定性地映射到新增行。若修改后的 Python 代码片段可通过 `ast.parse` 解析，
则作为误报排除 `SYNTAX_ERROR`，并在 `rejection_reasons` 中记录 `PYTHON_SYNTAX_VALID`。

允许模型采用不同表述，结合问题位置、根因、严重程度、规则 ID 与依据判定。
比较时固定模型、提示词、配置、差异和规则快照，每个用例至少运行 3 次。
