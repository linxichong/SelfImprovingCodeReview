# M2 评估用正确答案数据

本目录保存既有 M2 比较组评估使用的自建数据及回归样本。固定 LLM 响应和真实 AI 连接冒烟数据分别存放。
日常公开质量评测使用 [AACR-Bench](../../docs/AACR-Bench_质量评测.md)，直接使用上游专家复核标签。

## 评估用途与独立性

用途分为开发调优、已知缺陷回归和冻结的独立验收，保留现有 dev/test 格式及审批状态。
test-v9 和 test-v10 是基于已知结果的订正候选，归入已知缺陷回归。
即使是独立候选，也须核对创建、查看、调优历史及人工验证与审批，才能判定独立验收完成。
`is_evaluation_ready` 表示技术准备状态，独立性与人工验收各需对应证据。
提示词和规则变更需按每次重复记录漏报、误报、严重问题 ID 保留情况、Token、等待时间及成本。
详见[独立性管理](../../docs/评估数据_独立性管理.md)和[比较记录](../../docs/templates/评估变更_比较记录.md)。

## 当前文件

- `ground-truth-test-v1.json`：用于检查发布、样本不足判定及 CLI 操作的小型集成样本。
- `prepare_formal_candidate.py`：生成包含 20 项变更和 30 个问题的独立 Git 评估候选。
- `prepare_test_v3_candidate.py`：生成包含 20 项有问题变更和 8 项无问题变更的独立 v3 候选。
- `prepare_test_v7_candidate.py`：生成在上述基础上新增 4 个 HTTP 对照用例的未批准 v7 候选，异常路径同样释放资源。

## 修正 HTTP 对照用例的 v7 候选

`uv run --frozen python datasets/evaluation/prepare_test_v7_candidate.py` 在新目录
`.review/formal-evaluation-v7-repository/` 生成 32 项变更、30 个问题及其中 8 个严重问题的候选。
保留现有 v3～v6 数据集、提交、数据库和历史判断。目标目录已存在时生成失败。
新增 HTTP 用例使用 `with urlopen(...)`，在正常、`TimeoutError` 和 UTF-8 解码失败路径均关闭资源。
对旧版判为无问题的 HTTP 用例，异常路径歧义通过新候选处理，保留正式历史结果。
与 v3 相同，各项创建者与验证者初始值相同，审批者为 `REVIEW_REQUIRED`。
完成独立核对及审批后才能发布。随后用新数据集重新运行全部比较组及全部重复。
不使用网络的资源释放测试位于 `tests/evaluation/test_http_lifecycle_cases.py`。

v1 仅含 2 个问题，无法用于正式 B/C 成功判定。`eval` 返回 `insufficient-population`，并在调用真实 LLM 前停止。

## 生成正式规模候选

在项目根目录执行以下命令。

~~~bash
uv run --frozen python datasets/evaluation/prepare_formal_candidate.py
~~~

命令生成以下独立仓库。

~~~text
.review/formal-evaluation-repository/
├── config/rules/
├── prompts/
├── src/
├── datasets/evaluation/ground-truth-test-v2.candidate.json
└── evaluation.yaml
~~~

候选包含 20 个确定性生成的提交、30 个问题和 9 个严重问题，严重问题分布在多个提交中。
提交时间、作者和内容固定，因此对空目录重新生成时 SHA 相同。

为防止误发布，候选 JSON 的 `created_by` 和 `verified_by` 都设为 `benchmark-preparer`。
另一位人员逐项核对提交、文件、行号、根因与严重程度后，将 `verified_by` 和 `approved_by` 改为实际负责人 ID。
批量替换身份值不能替代逐项验证。

核对后将候选保存为 `ground-truth-test-v2.json`，以生成的仓库为目标发布。
`--actor` 指定与 `approved_by` 相同的维护人员 ID，并在 `evaluation.yaml` 的
`memory.maintainers` 中登记该 ID。

~~~bash
uv run --frozen review ground-truth publish \
  --repository .review/formal-evaluation-repository \
  --config .review/formal-evaluation-repository/evaluation.yaml \
  --input datasets/evaluation/ground-truth-test-v2.json \
  --actor '<保守担当者ID>'
~~~

目标已存在时工具不会覆盖。重新生成前先确认如何保留既有评估结果，再指定不同的 `--repository`，或移走现有目录。

## 正式评估数据集的条件

独立 test-v8 候选与固定第 8 版的 B/C 运行计划见[独立验收准备](../../docs/P0_独立验收准备.md)。
生成器为 `prepare_independent_v8.py`，新增代码定义在 `independent_v8/catalog.json`。
候选生成使用共享模板 `config/evaluation.example.yaml`。本地配置可保存在 `config/evaluation.local.yaml`。
各项均为独立人工验证和审批前的候选，保留现有已发布集合及默认提示词。

test-v9 候选订正 test-v8 裁决发现的金额转换漏标和 ZIP 的 POSIX 契约，
使用 `prepare_test_v9_candidate.py` 和 `test_v9/catalog.json` 生成。
包含 32 项变更、31 个问题、8 个严重问题和 11 个安全对照。保留旧版 30 个问题，新增 1 个金额问题。
该订正版基于已知测试结果，归入已知缺陷回归。待审批，禁止自动发布或调用真实 LLM。
生成示例、哈希和核对材料见 [test-v9 候选准备](../../docs/P0_test-v9候选准备.md)。

未批准的 test-v10 候选为 test-v9 增加 ZIP 父目录缺失问题，使用
`prepare_test_v10_candidate.py` 和 `test_v10/catalog.json` 生成。
包含 32 项变更、32 个问题、8 个严重问题和 11 个安全对照。辅助目录的可选 `sequence`
保留旧版 31 个问题的编号，将新根因编号设为 32。创建目标前拒绝重复、非正数和缺号。
正式发布 JSON 的模式仍为第 2 版。该订正版同样归入已知缺陷回归。
改动历史、位置和技术检查范围见 [test-v10 候选准备](../../docs/P0_test-v10候选准备.md)。

正式 `test` 数据集必须满足以下全部要求。

- 至少 20 个不同的 `change_ref`。
- 至少 30 个问题。
- 至少 5 个严重程度为 `critical` 的问题。
- `critical` 问题至少分布在 3 个不同的 `change_ref` 中。
- 每个 `change_ref` 都是评估仓库内的完整提交 SHA。
- 每个问题指向目标提交的改动文件及新增行或符号。
- 创建者与验证者不同，且已由维护人员审批。
- 人员已核对根因与可复现证据。
- 模式第 2 版的 `cases` 包含无问题变更，并记录与创建者不同的验证者和审批者。

在模式第 2 版中，`cases` 表示全部待评估变更，`issues` 只引用存在问题的用例。
无问题用例不含 `issues`。AI 对这些变更报告的问题可计为假阳性。
旧模式第 1 版作为兼容输入，从各问题的 `change_ref` 补全用例。正式的 v3 及之后数据集使用第 2 版。

禁止仅为满足数量而复制数据、捏造问题或给同一变更另设标识符。
这些做法会扭曲指标，使比较组 C 的改进无法验证。

## 创建步骤

1. 在 development 数据集中确认问题类型、位置和证据的记录方式。
2. 从人工审查、修正提交、测试故障和静态分析等渠道收集候选。
3. 由创建者之外的人员验证目标提交的差异和根因。
4. 由维护人员批准纳入 test 数据集。
5. 使用未占用的 `version` 创建 JSON，执行 `ground-truth publish`。
6. 确认输出的 `is_evaluation_ready` 为 `true`。
7. 比较组 B/C 各运行至少 3 次，并以盲化方式裁决未裁决项。
8. 用 `adjudication report` 核对裁决后的指标和最终判定。

已发布数据集不可覆盖。订正时保留原 JSON 和数据库记录，递增 `version` 并创建新数据集。

## test-v3 候选

v3 在独立仓库生成，保留 v2 评估结果及数据库。
它包含 20 项有问题变更、8 项无问题变更和 30 个正确答案问题，其中 8 个为严重问题。
通过模式第 2 版的 `cases` 将无问题变更纳入误报评估。

~~~bash
uv run --frozen python datasets/evaluation/prepare_test_v3_candidate.py
~~~

生成位置和候选文件如下。

~~~text
.review/formal-evaluation-v3-repository/
└── datasets/evaluation/ground-truth-test-v3.candidate.json
~~~

候选的 `created_by` 和 `verified_by` 有意设为同一值，`approved_by` 为 `REVIEW_REQUIRED`。
对有问题用例检查差异、行号、根因和严重程度。对无问题用例检查完整变更是否存在审查范围内的问题。
核对后才把各 `cases` 和 `issues` 的 `verified_by`、`approved_by` 改为实际负责人，并保存为
`ground-truth-test-v3.json`。
