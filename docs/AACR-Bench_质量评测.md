# AACR-Bench 质量评测

公开基准评测使用 AACR-Bench 经专家复核的参考发现。直接沿用上游标签，无需在本项目内审批或发布为正确答案数据集。

1. 按[上游评测步骤](https://github.com/alibaba/aacr-bench/tree/main/evaluation)准备数据。记录上游提交 SHA、数据 SHA-256、受评样本的 `instance_id` 以及 base 和 head SHA。
2. 在各样本的评测仓库检出 head 后运行审查。比较时固定模型、提示词、规则和记忆配置，审查输入中排除参考发现。

   ~~~bash
   uv run --frozen review review --repository <REPO> --config <CONFIG> --range <BASE> <HEAD> --output <JSON>
   ~~~

3. 将本项目的结果 JSON 转成固定版本的评测器所接受的格式。仓库目前没有专用转换工具。按[上游指标定义](https://github.com/alibaba/aacr-bench/blob/main/docs/metrics.md)，使用相同的评分方式和判定模型比较。模拟判定只用于验证流程。
4. 记录样本总数、完成数、失败数、未执行数、Precision、Recall、F1、Token 用量、耗时和可获取的费用。分别统计缺失的结果和正常完成但无发现的结果。

公开基准分数和既有 A/B/C 评估分别报告。已用已知标签或结果进行调优的样本不能用于证明独立未见评测。独立性记录见[评估数据管理](评估数据_独立性管理.md)。
