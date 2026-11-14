# Python 候选评审规则与开发用例

## 用途

这份开发资料用于在扩充默认规则之前核查来源、适用范围、反例与上下文不足的情形。
默认 `config/rules/` 保持原样。加入候选规则后的评审质量仍须验证。
本资料禁止用于正式 B/C 评估、公开正确答案数据或登记已批准的记忆。

## 组成

- `rules/`：独立编写的 6 份候选规则 YAML。
- `patches/`：针对各规则的阳性、阴性和上下文不足用例共 18 项，以及功能下线的阴性用例 2 项。
- `manifest.yaml`：SHA-256、来源、预期分类与未审批状态。

`finding` 表示预期报告主要问题，`no_finding` 表示预期无问题，
`needs_context` 表示预期只列出需要确认的事项。
预期值属于开发假设，仍待人工审批为正确答案。`verified_by` 和 `approved_by` 保持未设置。

## 功能下线用例的约束

两项 `feature-removal-*` 用例独立编写，用于概括本次误报情形：用户已明确要求下线代理功能。
将下线契约固定在 `trusted_case_contract` 中，分别进行提供契约的对比和只提供差异的对比。
当前 M1 请求无法自动注入任意业务契约，因此只使用常规差异运行时不会将此契约发送给 LLM。
这两项属于手动上下文评估，禁止擅自纳入阳性／阴性自动评分。
Python 候选规则不适用于 `.bat`。

## 反例的关键条件

- httpx 有默认超时，禁止仅根据参数省略判定为无限等待。
- tarfile 的默认过滤器随 Python 版本而异，阳性用例使用显式的 `fully_trusted`。
- 子类在首次使用前设置属性时，禁止仅根据基类没有赋值而报告问题。
- 使用一致的幂等键且接收方会去重时，禁止仅根据重试判定为缺陷。

## 来源与处理

在清单中保存引用 URL 和核查日期。规则文本与代码示例均由本项目独立编写。
决定是否采纳时，固定上游版本并核查其使用方式。

## 离线检查

~~~bash
uv run --frozen pytest -q tests/evaluation/test_python_rule_candidate_dataset.py
~~~

此检查验证哈希、差异、语法、严格扫描、规则选择及默认禁用状态。LLM 的判定是否符合预期仍待验证。
无需网络或 API 密钥。

## 真实模型对比

固定模型、提示词和输入，现行规则组和加入候选规则的组各运行至少 3 次。
所有调用成功后，核对主要问题、参考问题、漏报、误报、采纳的规则、截断、用量、等待时间和成本。
确定性地按位置和类型进行初步匹配，再由人员裁定。禁止仅根据措辞相似认定为同一问题。
这 20 项仅用于开发，未满足概要设计书中正式评估的总体样本量和独立性要求。

## 本地对比执行器

本次验证工作区提供 `.review/pyautotest-6b133e6-validation/evaluate_candidates.py`。
这是当前工作区专用的辅助脚本。

~~~bash
# 无需 API 密钥。预检规则集和发送内容
uv run --frozen python .review/pyautotest-6b133e6-validation/evaluate_candidates.py

# 显式比较真实模型。现行组与候选追加组分别运行 18 个用例，各重复 3 次，共 108 次调用
uv run --frozen python .review/pyautotest-6b133e6-validation/evaluate_candidates.py \
  --live --repetitions 3
~~~

真实模型对比要求执行终端提供 `REVIEW_LLM_API_KEY` 并允许外部发送，运行会产生费用。
两项功能下线用例须显式分配至手动上下文评估，不计入自动执行的 108 次调用。
结果保存至 `.review/pyautotest-6b133e6-validation/candidate-comparison-<実験ID>/`。

中途失败时标记为 `INCOMPLETE_REVIEW_RUNS` 并停止。全部成功时仍标记为
`COMPLETE_PENDING_HUMAN_ADJUDICATION`。仅按位置初步匹配所得结果不足以断定准确率或质量是否提升。
