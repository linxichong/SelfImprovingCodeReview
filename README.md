# Self-Improving AI Code Reviewer

基于 Feedback Loop 的自我改善型 AI Code Review Agent。

本项目的目标不是单纯做一个“会调用 LLM 的 Code Review Bot”，而是探索一种可以持续吸收开发者反馈、逐步形成项目工程知识记忆的 AI Code Review 系统。

核心思想：

```text
AI Review
  ↓
Human Feedback
  ↓
Knowledge Extraction
  ↓
Feedback Memory
  ↓
Context Retrieval
  ↓
Next Review
```

也就是说：

> 不是持续训练模型，而是通过 Feedback 持续改善 Context。

日文表达：

> モデルを学習させるのではなく、Feedback から Context を成長させる。

## 当前状态

当前项目处于 Phase 1 开发阶段：Baseline Reviewer CLI。

已完成：

- 设计文档
- Python 项目基础结构
- `review.py` CLI 入口
- Git diff / diff 文件读取
- Project rules 读取
- OpenAI Responses API 客户端
- 结构化 Review JSON 输出
- 本地 mock reviewer
- 基础单元测试

主要设计文档：

- `self-improving-ai-code-review-design.md`

## Phase 1 目标

第一阶段先实现最小可运行闭环：

```text
Git Diff
  ↓
LLM
  ↓
Structured Review JSON
```

暂时不做：

- Web UI
- Feedback Memory
- Vector Database
- Multi-Agent 编排框架
- GitHub / GitLab Webhook 集成

这些会放到后续 Phase 中逐步加入。

## 快速开始

使用本地 mock reviewer 运行，无需 API Key：

```bash
python3 review.py \
  --diff-file eval/cases/todo.diff \
  --rules rules/coding_rules.md \
  --mock
```

输出示例：

```json
{
  "reviews": [
    {
      "id": "R001",
      "file": "app/service.py",
      "start_line": 2,
      "end_line": 2,
      "category": "maintainability",
      "severity": "low",
      "title": "Unresolved TODO in changed code",
      "confidence": 0.7,
      "used_memory_ids": []
    }
  ],
  "metadata": {
    "mode": "mock",
    "reviewer_version": "0.1.0"
  }
}
```

## 使用 OpenAI API

设置环境变量：

```bash
export OPENAI_API_KEY="your_api_key_here"
export OPENAI_MODEL="gpt-5"
```

Review 当前 Git diff：

```bash
python3 review.py --git-diff --output review.json
```

Review 指定 diff 文件：

```bash
python3 review.py \
  --diff-file eval/cases/todo.diff \
  --rules rules/coding_rules.md \
  --output review.json
```

## CLI 参数

```text
--git-diff        读取当前仓库的 staged diff；如果 staged diff 为空，则读取 unstaged diff
--diff-file       读取指定 diff 文件
--rules           指定项目规则文件，默认 rules/coding_rules.md
--output          输出 JSON 文件路径；不指定时输出到 stdout
--mock            使用本地 mock reviewer，不调用 OpenAI API
--model           指定 OpenAI 模型，默认读取 OPENAI_MODEL 或使用 gpt-5
```

## 返回码

```text
0 = success
1 = invalid input
2 = LLM call failed
3 = invalid LLM output
4 = internal error
```

## 测试

当前测试使用 Python 标准库 `unittest`，不依赖第三方包。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

运行语法检查：

```bash
python3 -m compileall app review.py tests
```

## 工程化方向

后续需要补充标准工程能力：

- 单元测试覆盖率
- lint
- format
- type check
- CI
- PR check
- Evaluation cases 自动化

建议后续工具：

```text
ruff      lint + format
mypy      type check
coverage  test coverage
pytest    test runner
GitHub Actions CI
```

## 分支策略

建议使用：

```text
main       稳定版本
 develop   开发集成分支
 feature/* 具体功能分支
```

当前 Phase 1 推荐分支：

```text
feature/phase1-baseline-reviewer
```

开发完成后，通过 PR 合并到：

```text
develop
```

## 后续路线

```text
Phase 1  Baseline Reviewer CLI
Phase 2  Multi Reviewer + Review Manager
Phase 3  Feedback Capture
Phase 4  Memory Candidate + Human Approval
Phase 5  Memory Retrieval
Phase 6  Evaluation
Phase 7  GitHub / GitLab Integration
```