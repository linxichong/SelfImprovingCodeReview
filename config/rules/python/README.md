# Python 审查规则

本目录是 Python 差异审查中 LLM 指令的唯一可信来源。每份 YAML 聚焦一种根本原因，便于在报告问题时返回稳定的规则 ID。

## 参考资料

- Alibaba OpenCodeReview `python.md`
  - https://github.com/alibaba/open-code-review/blob/main/internal/config/rules/rule_docs/python.md
  - 查阅日期：2026-09-22
  - 许可证：Apache License 2.0

本项目参考精确率优先的思路和 Python 特有的重点检查项，按项目 YAML 模式、确定性的适用范围、M1/M2 可用上下文和日文运行环境重新编写规则。规则正文为独立编写。

## 适用策略

- 仅报告能从差异核实证据的实际缺陷。
- 判定前应考虑调用方、类型契约和框架提供的安全保证。
- 可由 Ruff、mypy 或常规格式化确定性检测的纯表述问题无需添加到 LLM 规则。
- 优先检查安全和正确性，跳过仅与偏好或惯用写法有关的问题。
- `keywords` 按大小写对新增行做初步筛选，最终判定仍需证据。

可用以下命令检查规则选择结果，无需调用真实 LLM。

```bash
uv run --frozen review rules check \
  --repository . \
  --config review.yaml \
  --diff-file datasets/real_llm_smoke/patches/02_command_injection.patch
```
