# 独立安全边界开发用例

本目录用于开发期检查漏检、误报和上下文判断，不是已批准的正式正解集合。
由 AI 新编写，没有从已发布 Test 集复制文件或导入问题 ID。它针对此前发现的失败模式，
不能视为盲测留出集，也不能单独满足设计中的30个开发 PR 下限。

## 用例与判断依据

每个差分包含两个独立函数，以观察只报告第一个问题的遗漏。
`manifest.json` 保存逐函数预期、精确行号、原文证据和待补上下文问题。
同一领域的三个变体使用相同的中性源码路径，差分内不出现 vulnerable/safe/context 分类名。

| 领域 | 有缺陷（2个目标） | 安全对照 | 上下文不足 |
|---|---|---|---|
| JWT | HTTP Token 的签名验证关闭，声明直接决定授权 | 同一授权逻辑要求验签、固定 RS256、audience 和 issuer | 只读取声明，未提供消费者及其他验证步骤 |
| Flask | 明示生产公开服务，强制开启调试器 | 明示隔离开发环境，仅回环监听、关闭调试器 | 启动选项、运行环境和公开范围未知 |
| XML | 外部上传进入开启实体解析、DTD 和网络访问的解析器 | 显式关闭实体解析、DTD 和网络访问 | 输入来源和注入解析器的设置未知 |
| XSS | HTTP 输入未经转义进入浏览器 HTML；两个函数分别计数 | 先使用 `html.escape(..., quote=True)` 转义再标记 HTML | 片段来源、上游无害化和使用位置未知 |

安全对照的 `no-target-findings` 只表示未包含表中目标缺陷，不能推导所有可能问题均不存在。
上下文不足的 `needs-context` 不等于安全，也不允许靠函数名或文件名断言漏洞。
`context_questions` 列出每个函数缺少的事实；真实评审需要记录上下文不足，不能把空指摘算作通过。

API 语义依据：[PyJWT](https://pyjwt.readthedocs.io/en/latest/api.html)、
[Flask 调试器](https://flask.palletsprojects.com/en/stable/debugging/)、
[lxml 解析器](https://lxml.de/parsing.html)、
[MarkupSafe](https://markupsafe.palletsprojects.com/en/stable/escaping/)。
JWT 对照限定于签名真实性；XML 对照限定于外部实体访问，并不声称解决所有资源耗尽风险。

## 离线验证

```bash
uv run --frozen pytest -q tests/evaluation/test_security_boundary_cases.py
```

检查内容：12个差分的 Python 语法、行数、问题位置和函数归属、严格秘密扫描，
以及 JWT 的授权与拒绝分支、Flask 的启动参数、XML 解析器设置传递、HTML 标签转义。
JWT、Flask、lxml 的 SDK 使用观测模桩；Markup 构造用不转义的字符串构造替代，
HTML 转义和解析实际使用标准库。没有网络、真实服务、文件实体读取、真实 JWT 验签或真实 LLM。
这些检查证明样例和调用契约一致，不能替代第三方 SDK 的实际安全测试或模型效果测量。

当前33项用例检查全部通过，包含重复引用的歧义拒绝与连续原文定位检查；
项目全量1566项测试通过，行与分支覆盖率100%。
Ruff、mypy strict 和全文件 pre-commit 通过。测试日志：`.review/security-development-tests.log`。

## 后续模型试验

在项目根目录将 `config/gateway.example.yaml` 复制为 `config/gateway.local.yaml`，
确认固定模型 ID 后，显式审查单个差异。

```bash
uv run --frozen review review --repository . --config config/gateway.local.yaml \
  --mode rules --diff-file datasets/development/security_boundaries/patches/jwt-vulnerable.patch \
  --output .review/security-jwt.json
```

真实调用仍需在所选配置中允许外部发送，并通过现有安全方式配置 API 密钥。
不要把 `manifest.json` 的答案和分类发送给被评模型；输入只有差分与固定项目配置。
候选比较应固定全部12例、顺序、模型及参数，各至少3次，保存模型、提示词和输入哈希、
逐函数命中、误报、上下文不足、Token、耗时及成本。
同一函数的近义问题名称需核对根因；跨函数的相同根因不得合并成一次命中。

只报告一个目标应计为部分检出；上下文不足不能算作零问题安全例。
本开发集及其近似变体不要再作为提示词调优之后的新盲测集合。
正式验收仍使用独立验证并获人工批准的独立集合，保留原有版本和历史测量。

已完成的版5、版7、版8开发实测见 [提示词候选验证](../../../docs/P0_提示词候选验证.md)。
