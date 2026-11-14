# 提示词开发流程

- 按[提示词资源列表](../../../../prompts/README.md)选定目标。常规审查使用 `review_general_v1.yaml`，动态上下文使用 `review_agent_v1.yaml`，专项审查使用 `review_specialist_v1.yaml`，自我验证使用 `reflection_v1.yaml`。
- 阅读 [M1 详细设计第 8 节](../../../../docs/详细设计书_M1_审查基础.md#8-機密情報検査とプロンプト)和对应阶段章节，先确定用例、合法输入、拒绝条件与预期结果。区分系统指令、运行时差异等数据及 JSON Schema。行位置、依据和适用范围由现有代码确定。
- 在版本化 YAML 编写指令正文。比较候选使用 `prompts/candidates/`，升级为默认版前核对设计规定的固定条件评估和人工裁决。修改采纳的正文时，更新资源 `prompt_version` 及相关设计和评估。
- 先通过使用 `DatasetLlmClient` 的测试检查请求构造和处理回归。常规审查使用 `tests/unit/test_review_prompt_loader.py`、`tests/unit/test_review_context_builder.py` 和 `tests/e2e/test_cli_entrypoint.py`。动态上下文和自我验证还检查 `tests/e2e/test_m3_cli.py`。
- 修改指令或规则遵循[评估数据独立性管理](../../../../docs/评估数据_独立性管理.md)和[开发标准第 15.4 节](../../../../docs/开发标准.md#154-llm-評価)。以相同固定条件比较漏报、误报、严重问题 ID 保留、Token、等待时间和成本，并区分已知用例调优和独立验收。记录外部发送与实际负责人的裁决执行情况。
