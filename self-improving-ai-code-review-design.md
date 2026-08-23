# Self-Improving AI Code Reviewer

## Feedback Loop を活用した自己改善型 AI Code Review Agent

---

## 1. 项目背景

普通 AI Code Review 已经可以帮助开发者发现 Bug、Security、可维护性和测试相关的问题，但在真实项目中经常存在以下问题：

- AI 不理解项目背景
- 不理解团队内部开发规范
- 同一个误报会反复出现
- 评论数量很多，但真正有价值的建议比例不高
- 每次 Review 都像第一次认识这个项目
- 无法持续吸收开发者对 Review 结果的反馈

因此，本项目的目标不是单纯构建一个 “LLM Code Review Bot”，而是构建一个可以通过开发者反馈持续改善的 AI Code Review Agent。

核心思想：

> 模型本身不一定需要持续训练，而是通过 Feedback 不断改善 Memory 和 Context。

整体循环：

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
   ↓
Better Review
```

---

## 2. 项目目标

项目暂定名称：

**Self-Improving AI Code Reviewer**

日文主题：

**Feedback Loopを活用した自己改善型AI Code Review Agent**

目标：

1. 对 Git Diff / Pull Request 自动执行 AI Code Review
2. 从多个角度发现代码问题
3. 接受开发者对 AI Review 的反馈
4. 将反馈抽象为可复用的 Engineering Knowledge
5. 在后续 Review 中自动检索相关知识
6. 降低 False Positive
7. 提升 AI Review 的有效指摘率
8. 逐渐形成项目自身的 Code Review Knowledge Base

---

## 3. 要解决的核心问题

例如 AI 第一次 Review：

```text
AI:
このメソッドには null チェックを追加することを推奨します。
```

开发者反馈：

```text
👎 False Positive

この値は Spring Framework 側で non-null が保証されているため、
追加の null チェックは不要。
```

系统不应该只保存：

```text
R001 = 👎
```

而应该将反馈转换为项目知识：

```text
Project Rule:

Spring Controller 中特定参数由 Framework 保证 non-null。

Review Instruction:

除非发现绕过 Framework 调用的路径，
否则不要提出额外 null check 建议。
```

下一次遇到类似代码时：

```text
New PR
   ↓
Retrieve related memory
   ↓
Add memory to context
   ↓
AI Review
   ↓
Do not repeat the same false positive
```

这就是项目中的 “Self-Improving”。

---

# 4. 系统整体架构

```text
                    ┌─────────────────┐
                    │   Git Diff / PR │
                    └────────┬────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │   Context Builder   │
                  │                     │
                  │ Diff                │
                  │ Source Code         │
                  │ Project Rules       │
                  │ Related Memories    │
                  └─────────┬───────────┘
                            │
          ┌─────────────────┼──────────────────┐
          ▼                 ▼                  ▼
   ┌────────────┐    ┌────────────┐     ┌────────────┐
   │ Bug Agent  │    │ Security   │     │ Quality    │
   │            │    │ Agent      │     │ Agent      │
   └─────┬──────┘    └─────┬──────┘     └─────┬──────┘
         │                 │                   │
         └─────────────────┼───────────────────┘
                           │
                           ▼
                    ┌────────────┐
                    │ Test Agent │
                    └─────┬──────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │ Review Manager   │
                 │                  │
                 │ Merge            │
                 │ Deduplicate      │
                 │ Severity         │
                 │ Confidence       │
                 │ Filter           │
                 └────────┬─────────┘
                          │
                          ▼
                   AI Review Result
                          │
                   👍 / 👎 / Comment
                          │
                          ▼
                ┌────────────────────┐
                │ Feedback Analyzer  │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Memory Candidate   │
                │     pending        │
                └─────────┬──────────┘
                          │
                   approve / merge
                          │
                          ▼
                 ┌──────────────────┐
                 │ Feedback Memory  │
                 └──────────────────┘
                          │
                          └────→ Next Review
```

---

# 5. MVP 技术栈

第一阶段尽量避免过度工程化。

推荐：

```text
Python
FastAPI
Pydantic
SQLite
LLM API
Git CLI
Simple Web UI
```

第一版暂时不强依赖：

- LangGraph
- MCP
- Vector Database
- Kubernetes
- Fine-tuning
- Complex Agent Framework

原因：

项目第一阶段真正要验证的是：

> Feedback Memory 是否能够改善 AI Code Review。

而不是：

> 使用了多少 Agent Framework。

---

# 6. 项目目录设计

```text
self-improving-code-review/
│
├── app/
│   ├── main.py
│   │
│   ├── review/
│   │   ├── reviewer.py
│   │   ├── manager.py
│   │   ├── context_builder.py
│   │   └── models.py
│   │
│   ├── agents/
│   │   ├── bug_reviewer.py
│   │   ├── security_reviewer.py
│   │   ├── quality_reviewer.py
│   │   └── test_reviewer.py
│   │
│   ├── memory/
│   │   ├── store.py
│   │   ├── retriever.py
│   │   ├── extractor.py
│   │   └── models.py
│   │
│   ├── feedback/
│   │   ├── handler.py
│   │   └── analyzer.py
│   │
│   └── llm/
│       ├── client.py
│       └── prompts.py
│
├── data/
│   └── review.db
│
├── rules/
│   └── coding_rules.md
│
├── eval/
│   ├── cases/
│   ├── baseline.py
│   └── evaluate.py
│
├── tests/
│
├── .env
├── pyproject.toml
└── README.md
```

---

# 7. Review Agent 设计

第一版使用 4 个 Reviewer Agent。

## 7.1 Bug Reviewer

主要检查：

```text
logic errors
null handling
exception handling
edge cases
concurrency
resource leaks
incorrect state transitions
```

---

## 7.2 Security Reviewer

主要检查：

```text
SQL / Command Injection
authentication
authorization
secret leakage
unsafe deserialization
input validation
permission issues
sensitive data exposure
```

---

## 7.3 Maintainability Reviewer

主要检查：

```text
complexity
duplication
readability
API design
architecture
coupling
responsibility boundaries
maintainability risk
```

---

## 7.4 Test Reviewer

主要检查：

```text
missing tests
boundary cases
failure paths
regression risks
exception paths
state transition coverage
```

---

# 8. Review Manager

Review Manager 是整个系统的重要组件。

它不负责直接找 Bug，而负责处理其他 Agent 的输出：

```text
Merge
Deduplicate
Filter
Severity
Confidence
Prioritization
```

核心原则：

```text
Only report issues that are actionable.

Do not report:
- pure stylistic preferences
- speculative problems without evidence
- duplicated findings
- issues already handled by project rules
- low-confidence noise

Prefer no comment over a low-value comment.
```

可以设置：

```text
confidence < 0.7
→ 默认不展示
```

目标不是：

```text
AI 找到 100 个问题
```

而是：

```text
AI 提出的每一条评论，
开发者都愿意认真阅读。
```

---

# 9. Review Result 数据结构

AI 不直接输出自由格式 Markdown。

统一使用结构化 JSON。

示例：

```json
{
  "reviews": [
    {
      "id": "R001",
      "file": "UserService.java",
      "line": 82,
      "category": "bug",
      "severity": "high",
      "title": "Null pointer possibility",
      "description": "Possible null access when user is not found.",
      "evidence": "user.getName() is called before null validation.",
      "suggestion": "Handle the missing-user case before accessing user fields.",
      "confidence": 0.87
    }
  ]
}
```

统一结构化输出有利于：

- Feedback
- Evaluation
- Memory Extraction
- GitHub/GitLab Comment
- Metrics
- Dashboard

---

# 10. Feedback 设计

Feedback 不能只有 👍 / 👎。

建议至少支持：

```text
👍 Useful
👎 False Positive
❓ Missing Context
♻ Duplicate
⚠ Wrong Severity
💬 Comment
```

Feedback 数据示例：

```json
{
  "review_id": "R001",
  "feedback": "false_positive",
  "reason": "Framework guarantees this value is non-null."
}
```

重要的是保存：

> 为什么这条 Review 是错的 / 对的。

而不仅仅保存结果。

---

# 11. Feedback Analyzer

Feedback Analyzer 负责将开发者反馈转换为潜在的可复用知识。

输入：

```text
Review
+
Developer Feedback
+
Code Context
+
Existing Project Rules
```

输出：

```text
Memory Candidate
```

示例：

```json
{
  "id": "MEM-001",
  "project": "payment-api",
  "category": "framework_rule",
  "scope": {
    "language": "java",
    "path": "src/controller/**"
  },
  "rule": "Controller parameters annotated with @Valid are validated by Spring before entering the method.",
  "review_instruction": "Do not suggest redundant manual validation unless a bypass path exists.",
  "source": "developer_feedback",
  "confidence": 0.85,
  "status": "pending"
}
```

---

# 12. Memory 数据模型

Memory 本质上不是聊天历史。

它应该是：

> Engineering Knowledge Memory

建议字段：

```text
id
project
category
scope
rule
review_instruction
source
confidence
status
created_at
updated_at
evidence_count
```

---

# 13. Memory 生命周期

Memory 不应该在一次反馈之后立即生效。

流程：

```text
Feedback
   ↓
Memory Extractor
   ↓
Memory Candidate
   ↓
pending
   ↓
Human Approve
   ↓
active
```

原因：

如果某开发者错误反馈：

```text
SQL Injection 不需要检查
```

系统不能立刻学习成永久规则。

因此 Memory 建议拥有：

```text
pending
active
superseded
archived
rejected
```

后续可以扩展：

```text
相同反馈出现 3 次
   ↓
evidence_count ↑
   ↓
confidence ↑
   ↓
达到阈值后建议自动 active
```

---

# 14. Memory Governance

Memory 会影响后续 Review 的判断，因此它不能只是简单追加。

需要考虑：

```text
Trust
Conflict
Expiration
Versioning
Security Override
```

## 14.1 Feedback 信任度

不是所有 Feedback 都应该同等影响 Memory。

可以考虑以下因素：

```text
feedback author role
maintainer approval
same feedback repeated count
related test / CI evidence
whether the feedback is specific enough
whether it contradicts existing active memory
```

例如：

```text
一次普通反馈
→ 生成 pending memory candidate

多次相同反馈 + maintainer approve
→ confidence ↑

与 active memory 冲突
→ 标记 conflict，要求人工确认
```

## 14.2 Memory 冲突

当新的 Memory Candidate 与既有 active Memory 冲突时，不应该直接覆盖。

示例：

```text
Existing Memory:
Controller exception is handled globally by @ControllerAdvice.

New Feedback:
This controller does not use global exception handling.
```

处理方式：

```text
mark candidate as conflict
show conflicting memory
require human decision:
  - narrow the scope
  - supersede old memory
  - reject new memory
```

## 14.3 Memory 失效

Memory 可能因为项目变化而失效。

常见原因：

```text
framework version upgrade
architecture change
directory structure change
coding rule update
dependency replacement
```

建议字段：

```text
valid_from
valid_until
applies_to_versions
last_used_at
last_confirmed_at
superseded_by
```

第一版不一定全部实现，但设计上要保留空间。

## 14.4 Security Override

对于高风险安全问题，Memory 不能直接让系统完全静默。

原则：

```text
Security finding should not be suppressed only by memory.
```

例如 SQL Injection、Authorization、Secret Leakage 等问题：

```text
Memory can lower confidence
Memory can ask reviewer to check specific evidence
Memory should not silently hide high-severity security findings
```

这样可以避免错误 Memory 把真正的安全问题过滤掉。
---

# 15. Memory Retrieval

每次 Review 之前，Context Builder 根据当前代码寻找相关 Memory。

例如当前文件：

```text
src/controller/UserController.java
```

检索条件可以包括：

```text
language = Java
framework = Spring
path = controller
category = validation
related symbols
keywords
```

找到：

```text
MEM-001
MEM-014
MEM-021
```

将 Memory 加入 Prompt：

```text
## Project-specific knowledge

1. Spring @Valid parameters are validated by the framework.
2. Controller exceptions are handled globally by @ControllerAdvice.
3. Request ID logging is handled by middleware.

Use these rules when reviewing the current code.

Do not raise findings that contradict established project knowledge
unless the current code provides clear evidence that the rule does not apply.
```

然后加入：

```text
Git Diff
+
Related Source Code
```

---

# 16. Memory Retrieval MVP Strategy

第一版不需要直接上 Vector Database。

MVP 可以使用规则检索：

```text
language
framework
path glob
category
keywords
changed symbols
file extension
```

Memory 示例：

```json
{
  "scope": {
    "language": "java",
    "framework": "spring",
    "path": "src/**/controller/**",
    "category": "validation",
    "keywords": ["@Valid", "Controller"]
  }
}
```

检索排序可以先采用简单打分：

| Signal | Weight |
|---|---:|
| path match | 3 |
| language match | 2 |
| framework match | 2 |
| category match | 2 |
| keyword match | 1 |
| confidence | 0-2 |
| recently used / confirmed | 0-1 |

Context Builder 需要限制加入的 Memory 数量。

建议：

```text
top_k = 5
only active memory
prefer exact path / framework match
include memory id in prompt
record used_memory_ids in review result
```

V2 再考虑：

```text
embeddings
pgvector
hybrid search
semantic similarity
```
---

# 17. Self-Improving 的定义

本项目第一阶段不采用：

```text
Feedback
↓
Fine-tuning
↓
New Model
```

而采用：

```text
Feedback
↓
Knowledge Extraction
↓
Memory
↓
Retrieval
↓
Context
↓
Better Review
```

核心理念：

> Model 不变，Context 持续改善。

日文表达：

> **モデルを学習させるのではなく、FeedbackからContextを成長させる。**

---

# 18. Context Builder

Context Builder 是 Review 前的重要组件。

输入可能包括：

```text
Git Diff
Changed Files
Related Source Code
Project Coding Rules
Architecture Rules
Relevant Memories
Test Code
PR Description
Issue Description
```

第一版只需要：

```text
Git Diff
Project Rules
Relevant Memory
```

以后逐步增加。

---

# 19. Context Budget

Context 不可能无限增加。

当 Diff、Rules、Memory 都很多时，Context Builder 需要明确优先级。

建议优先级：

```text
1. Current Git Diff
2. Directly related active memory
3. Project coding rules
4. Nearby source code
5. Test code
6. PR / Issue description
```

Memory 裁剪原则：

```text
prefer exact scope match
prefer higher confidence
prefer recently confirmed
prefer category related to current reviewer
drop low-confidence or stale memory
```

Prompt 中建议明确：

```text
The following memories are project-specific rules.
Use them only when their scope applies to the current diff.
If the current code contradicts a memory, explain the contradiction instead of blindly following it.
```
---

# 20. Evaluation 设计

Evaluation 决定项目是否真的有价值。

准备例如：

```text
20 个 PR / Diff Case
```

每个 Case 人工标记：

```text
真实 Bug
Security Issue
Maintainability Issue
Expected No-Issue Sections
```

然后分别运行两个版本。

## Baseline

```text
Git Diff
   ↓
LLM
   ↓
Review
```

## Self-Improving

```text
Git Diff
+
Project Rules
+
Feedback Memory
   ↓
LLM
   ↓
Review
```

---

# 21. Evaluation Labeling Guideline

为了让评估结果可信，需要先定义标注标准。

建议 Review Feedback 标签：

| Label | 含义 |
|---|---|
| Useful | 指摘真实、可行动、对项目有价值 |
| False Positive | 问题不存在，或与项目事实冲突 |
| Duplicate | 与另一条 Review 表达同一问题 |
| Wrong Severity | 问题存在，但严重度明显错误 |
| Missing Context | AI 缺少必要上下文，结论无法判断 |
| Not Actionable | 过于抽象，开发者无法据此修改 |

Precision 计算时可以先采用：

```text
Useful / All Review Comments
```

更细的版本：

```text
Accepted = Useful + Useful but Wrong Severity
Rejected = False Positive + Duplicate + Not Actionable
Needs Review = Missing Context
```

Recall 的标注需要准备 Ground Truth：

```text
known bug
known security issue
known missing test
known no-issue section
```

这样 Baseline 和 Self-Improving 的比较才不会只依赖主观印象。
---

# 22. 评估指标

建议至少统计：

| Metric | 说明 |
|---|---|
| Precision | AI 评论中真正有效的比例 |
| Recall | 已知真实问题中 AI 找到的比例 |
| False Positive | 无效或错误评论数量 |
| Developer Accept Rate | 开发者认可的评论比例 |
| Review Count | AI 总评论数量 |
| Review Time | Review 所需时间 |
| Duplicate Rate | 重复意见比例 |

最重要的指标：

## Precision

```text
有效 AI Review 数
───────────────
AI Review 总数
```

项目核心目标不是让 AI “说更多”。

而是：

> 让 AI 说得更准。

---

# 23. MVP 成功标准

这是项目目标，不是预设实验结果。

希望加入 Feedback Memory 后：

```text
False Positive ↓ 30%
```

同时：

```text
Recall 不明显下降
```

理想示例：

### Before

```text
AI Comments        20
Useful             11
False Positive      9

Precision          55%
```

### After

```text
AI Comments        15
Useful             12
False Positive      3

Precision          80%
```

最终以实际 Evaluation 数据为准。

---

# 24. Demo 设计

## Step 1 — 第一次 Review

代码：

```text
UserController.java
```

AI：

```text
⚠ UserController.java:84

Manual null validation should be added.
```

---

## Step 2 — Developer Feedback

开发者：

```text
👎 False Positive

@Valid parameter is guaranteed by Spring.
```

---

## Step 3 — Memory Candidate

系统生成：

```text
Memory Candidate Generated

Scope:
Java / Spring Controller

Rule:
@Valid parameters are already validated by Spring.

Review instruction:
Avoid redundant manual validation suggestions.
```

用户：

```text
Approve
```

---

## Step 4 — 第二个类似 PR

新的 PR 出现类似代码。

系统显示：

```text
Used Memories

✓ MEM-001 Spring validation rule
```

AI 不再提出：

```text
Add null validation
```

这就是 Demo 的核心效果。

---

# 25. 开发阶段

## Phase 1 — Baseline Reviewer

目标：

```text
git diff
↓
LLM
↓
Structured Review JSON
```

完成条件：

- 可以读取 Git Diff
- 调用 LLM
- 返回统一 JSON
- 支持基础 Bug Review

---

## Phase 2 — Multi Reviewer

加入：

```text
Bug Reviewer
Security Reviewer
Maintainability Reviewer
Test Reviewer
        ↓
Review Manager
```

完成条件：

- 多 Reviewer 独立输出
- Manager 去重
- Manager 判断 severity
- Manager 过滤低价值意见

---

## Phase 3 — Feedback

加入：

```text
👍
👎
Reason
```

完成条件：

- 每个 Review 可以被反馈
- Feedback 写入数据库
- 可以查看历史反馈

---

## Phase 4 — Memory

流程：

```text
Feedback
↓
Memory Candidate
↓
Human Approve
↓
SQLite
```

完成条件：

- Feedback Analyzer 能生成 Candidate
- Memory 支持 pending / active
- 用户可以 Approve / Reject

---

## Phase 5 — Retrieval

流程：

```text
New Diff
↓
Retrieve Relevant Memory
↓
Context Builder
↓
Reviewer
```

完成条件：

- 根据当前代码找到相关 Memory
- Memory 自动加入 Review Context
- 可以显示本次 Review 使用了哪些 Memory

到这里：

> MVP 完成。

建议把 Evaluation Skeleton 提前准备。

即使 Phase 1 只有 3-5 个样例，也可以先固定：

```text
eval/cases
expected findings
baseline output
metrics script
```

这样后续加入 Multi-Agent、Memory、Retrieval 时，能持续确认系统是否真的变好。
---

## Phase 6 — Evaluation

运行：

```text
Baseline
vs
Feedback Memory
```

生成结果：

```text
Precision
Recall
False Positive
Accept Rate
Review Time
```

---

## Phase 7 — 实际工作集成

未来接入：

```text
GitHub / GitLab Webhook
        ↓
      PR Open
        ↓
    Auto Review
        ↓
    PR Comments
```

并进一步：

```text
PR Comment
↓
Resolve / Reply / 👍 / 👎
↓
Feedback Memory
```

---

# 26. 第一阶段 CLI Contract

Phase 1 的目标是先实现最小可运行入口：

```text
python review.py
```

建议 CLI：

```bash
python review.py \
  --diff-file diff.patch \
  --rules rules/coding_rules.md \
  --output review.json
```

也可以支持直接读取当前 Git Diff：

```bash
python review.py --git-diff --output review.json
```

输出格式：

```json
{
  "reviews": [
    {
      "id": "R001",
      "file": "app/service.py",
      "start_line": 42,
      "end_line": 45,
      "category": "bug",
      "severity": "high",
      "title": "Missing error handling",
      "description": "The new code assumes the API response always contains data.",
      "evidence": "response.data is accessed before checking response.status.",
      "suggestion": "Check the response status and handle empty data before access.",
      "confidence": 0.86,
      "used_memory_ids": []
    }
  ],
  "metadata": {
    "reviewer_version": "0.1.0",
    "mode": "baseline",
    "review_time_ms": 1234
  }
}
```

第一版错误处理：

```text
0 = success
1 = invalid input
2 = LLM call failed
3 = invalid LLM output
4 = internal error
```

这样 Phase 1 可以先不做 UI、不做 DB，但已经具备后续 Evaluation 和自动化集成的基础。
---

# 27. 第一阶段推荐技术路线

## MVP

```text
Python
FastAPI
Pydantic
SQLite
LLM API
Git CLI
Simple Web UI
```

---

## V2

```text
PostgreSQL
pgvector
Embeddings
GitHub / GitLab API
LangGraph
LangMem
MCP
```

---

# 28. 后续可扩展能力

未来可以增加：

## Repository Context

AI 不只看 Diff，还可以读取：

```text
Repository
Related Classes
Tests
Configuration
Architecture Documents
```

---

## RAG

将以下内容加入知识库：

```text
Coding Rules
Past PR Reviews
Architecture Decisions
Incident Reports
Bug History
FAQ
```

---

## MCP

让 Agent 可以主动访问：

```text
GitHub
GitLab
Jira
Confluence
CI/CD
Test Results
Static Analysis
```

---

## Long-Term Memory

让 Review Agent 逐渐形成：

```text
Project-specific Engineering Knowledge
```

---

# 29. 项目的核心价值

这个项目真正研究的并不仅仅是：

```text
AI Code Review
```

而是：

> 人类 Feedback 如何转换为 AI Agent 的长期 Engineering Knowledge。

完整闭环：

```text
Human Feedback
      ↓
Knowledge
      ↓
Memory
      ↓
Context
      ↓
Better Decision
      ↓
New Feedback
```

这一思路未来可以继续扩展到：

```text
Coding Agent
Bug Investigation Agent
QA Agent
Incident Analysis
Architecture Review
Development Knowledge Agent
```

---

# 30. 社内发表时的核心信息

建议将发表重点放在：

> **普通 AI Code Review 每次都像第一次认识项目。**

而本项目：

> **利用开发者 Feedback，将项目经验持续积累成可复用的 Engineering Memory。**

核心一句：

> **モデルを学習させるのではなく、FeedbackからContextを成長させる。**

最终希望验证：

> **AI Code Review 能否通过 Feedback Loop，随着团队使用时间增加而变得越来越符合项目实际需求。**

---

# 31. 下一步

下一阶段：

## Phase 1 — Baseline Code Reviewer

实现最小闭环：

```text
Git Diff
   ↓
LLM
   ↓
Structured Review JSON
```

第一版先不做 UI、不做 Memory、不做复杂 Agent Framework。

目标是先完成：

```text
python review.py
```

输入当前 Git Diff，输出：

```json
{
  "reviews": [...]
}
```

然后再逐步增加：

```text
Multi-Agent
↓
Feedback
↓
Memory
↓
Retrieval
↓
Evaluation
```
