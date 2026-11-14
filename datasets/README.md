# 本地 LLM 数据集

`development/` 和 `test/` 的固定响应供开发和自动测试使用，无需调用真实 LLM。
`evaluation/` 的正确答案数据与真实模型连接资料有各自用途，固定响应测试通过不代表正式评估达标。
评估用途的三种分类及独立性要求见[独立性管理](../docs/评估数据_独立性管理.md)。

- `development/` 用于手动调试，可添加典型成功和失败用例。
- `test/` 仅供自动测试使用，须保持测试预期稳定。
- 禁止将测试数据用于正式评估、记忆生成或训练。
- 禁止包含真实敏感信息、个人信息或未公开的输入源代码。

## 单响应格式（schema_version 1）

```json
{
  "schema_version": 1,
  "model": "local-development-dataset-v1",
  "default_result": {
    "payload": {"schema_version": 1, "findings": []},
    "usage": {
      "input_tokens": 0,
      "output_tokens": 0,
      "latency_ms": 1,
      "cost_usd": "0"
    },
    "attempt": 1
  },
  "cases": [
    {
      "case_id": "唯一用例 ID",
      "match_all": ["请求正文必需的字符串"],
      "result": {
        "payload": {"schema_version": 1, "findings": []},
        "usage": {
          "input_tokens": 10,
          "output_tokens": 2,
          "latency_ms": 1,
          "cost_usd": "0"
        }
      }
    }
  ]
}
```

返回首个满足以下条件的用例：`match_all` 的所有元素出现在系统指令或请求正文中。
未匹配时返回 `default_result`。用例顺序与 `case_id` 必须稳定，不得重复使用 ID。

## 多轮格式（schema_version 2）

要重现 M3 的上下文获取与自我验证，请在用例中按调用顺序定义 `responses`。

```json
{
  "schema_version": 2,
  "model": "local-development-dataset-v2",
  "default_result": {
    "payload": {"schema_version": 1, "findings": []},
    "usage": {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "cost_usd": "0"}
  },
  "cases": [
    {
      "case_id": "python-context-case",
      "match_all": ["src/example.py"],
      "responses": [
        {
          "review_unit_id": "B001",
          "call_kind": "review-agent",
          "turn_number": 1,
          "request_hash": "0000000000000000000000000000000000000000000000000000000000000000",
          "result": {
            "payload": {
              "schema_version": 1,
              "action": "tool-request",
              "tool_request": {
                "tool_name": "read_file",
                "path": "src/helper.py",
                "path_scope": [],
                "query": null,
                "start_line": null,
                "end_line": null
              },
              "findings": []
            },
            "usage": {"input_tokens": 100, "output_tokens": 20, "latency_ms": 1, "cost_usd": "0"}
          }
        }
      ]
    }
  ]
}
```

- 响应键为 `(case_id, review_unit_id, call_kind, turn_number)`，禁止重复。
- `review_unit_id` 是请求标签冒号后的部分，例如 `review-agent:B001` 的 `B001`。
  `call_kind` 是冒号前的部分。审查调用使用 `review-agent`，自我验证使用 `reflection`。
- `turn_number` 是同一审查单元与调用类型内的顺序。格式修复的再次调用也记录为下一轮。
- `request_hash` 是 `system_instruction + "\\n" + user_content` 的 UTF-8 SHA-256。
  根据加入真实测试仓库工具结果后的完整请求计算，禁止将工具结果复制到数据集。
  上面的零值只展示格式，须替换为实际哈希。
- 只要用例中有一个响应已使用，结束时就必须用完该用例的所有响应，否则判为失败。
  这能检出遗漏预期工具调用或自我验证的回归。
- 只有完全未匹配到用例的请求才返回 `default_result`。匹配用例后，若响应键或请求哈希不符，立即失败。

原有单响应数据可以继续使用 `schema_version: 1`，仅需将用于验证多轮的用例迁移到第 2 版。
