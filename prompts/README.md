# 提示词资源

本目录对传给 LLM 的可信指令做版本管理。禁止在应用代码中重复保存提示词正文，也禁止根据用户输入或仓库内容生成可信指令。

## 资源列表

| 文件 | 用途 |
|---|---|
| `review_general_v1.yaml` | 兼容 M1/M2 的审查 |
| `review_agent_v1.yaml` | M3 工具请求、最终候选及各类审查者的检查项 |
| `review_specialist_v1.yaml` | M5 条件式专项审查 |
| `reflection_v1.yaml` | M3 审查候选自我验证 |
| `memory_extraction_v1.yaml` | 从 M2 反馈中提取记忆候选 |

M3/M5 的审查资源包含 `general`、`security`、`data` 和 `test` 四种固定配置。
仅使用计划流程选定的一种，配置不得注入任意附加指令。

## 变更规则

1. 正文修改后提高 `prompt_version`。
2. 读取时拒绝未知项、缺少必填项和空正文。
3. 禁止将完整提示词保存到日志、结果 JSON 或 SQLite。
4. 运行结果仅保存资源名、版本与规范化后的内容哈希。
5. 修改后更新相关单元测试、端到端测试和固定评估。

封闭的响应 JSON Schema 由代码生成器唯一定义，以便在运行时固定最大问题数和候选数。
提示词中的 `{output_schema}` 由为该次调用生成的 Schema 替换。
