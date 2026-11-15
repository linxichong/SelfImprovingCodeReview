# AACR-Bench 质量评测

公开质量评测沿用固定版本的 AACR-Bench 专家复核标签。使用仓库根目录下的
`review benchmark`，比较 SelfImprovingCodeReview、OpenCodeReview 和 Codex。

`config/aacr-benchmark.example.json` 固定了先前用于试测的三个 Python PR、三次重复、
AACR-Bench v1 提交与数据 SHA-256、OCR Linux 二进制 SHA-256、Codex CLI 版本、
审查模型、判定模型和网关。此清单用于已知样本回归。独立验收使用另行冻结的未见样本
清单，记录用途和查看历史。

在独立目录检出清单中的上游提交。准备清单指定的 OCR 二进制和 Codex CLI，并在上游目录里的
独立虚拟环境中安装该提交的 `evaluation/requirements.txt`。以下示例沿用本次试测的本地路径。
上游仓库、二进制和安装说明见 [AACR-Bench 官方流程](https://github.com/alibaba/aacr-bench/tree/c6486859e14b686b3c2aa0176ac8376cc9b72e07/evaluation)
及 [OpenCodeReview 发布页](https://github.com/alibaba/open-code-review/releases/tag/v1.13.10)。

~~~bash
bench=.review/same-model-benchmark
uv run --frozen review benchmark \
  --manifest config/aacr-benchmark.example.json \
  --upstream "$bench/aacr-bench"
~~~

默认命令只检查固定数据并打印计划。执行时用 `REVIEW_LLM_API_KEY` 提供网关密钥。
`--execute` 会下载所选公开 PR，校验两套外部工具版本，并向清单内的网关发送公开评测内容。

~~~bash
read -rsp 'LLM API Key: ' REVIEW_LLM_API_KEY
printf '\n'
export REVIEW_LLM_API_KEY

uv run --frozen review benchmark \
  --manifest config/aacr-benchmark.example.json \
  --upstream "$bench/aacr-bench" \
  --ocr "$bench/ocr" \
  --evaluator-python "$bench/codex-eval-env/bin/python" \
  --output "$bench/aacr-comparison.json" \
  --execute
~~~

Codex 默认从 PATH 查找，可用 `--codex` 指定名为 `codex` 的可执行文件。
所有工具使用同一 Git 合并基准和 head。程序记录实际合并基准、差异哈希、项目提交、
提示词、规则和可用的 OCR 用户级配置哈希。SICR 使用当前项目的 `rules` 模式，
关闭记忆注入、动态上下文与自我验证。参考发现只进入评分阶段。

程序自动转换三套工具的结果，由固定版上游的文件级语义比较评分。JSON 和 CSV 都会
记录每次审查的状态、参考问题命中数、命中参考问题的评论数、输入与输出 Token、耗时和
可获取的成本。成功零发现计入召回率分母。超时、失败、拦截、缺失、无效输出和评分失败分别
记录。JSON 另含所有已评分结果及各工具都成功的同组结果。F1 使用上游语义命中率和召回率
计算。Token 按工具的报告值统计，包含其已计入的缓存读量。评分用量在审查 Token 之外。
有未完成试次时仍写出报告，命令返回 2。每次重跑选择新的报告名。

可把报告所在目录交给 `review dashboard --reports`，从「已生成报告」打开 JSON。
公开基准分数和项目内部 A/B/C 评估分别使用。见[评估数据独立性管理](评估数据_独立性管理.md)。
