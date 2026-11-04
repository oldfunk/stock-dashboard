# report-t65.md — 端点门禁语义审查（Hermes）

## 方法
逐个核对 `dashboard.py` 的 POST 端点：读/写/资金/配置语义 × `_admin_ok` 保护。

## 对照表
| 端点 | 语义 | 门禁 | 结论 |
|---|---|---|---|
| /api/health | 读 | 无 | OK |
| /api/status | 读 | 无 | OK |
| /api/quotes | 读 | 无 | OK |
| /api/positions | 读 | 无 | OK |
| /api/names | 读 | 无 | OK |
| /api/schemes | 读 | 无 | OK |
| /api/engine | 读 | 无 | OK |
| /api/llm/status | 读 | 无 | OK |
| /api/llm/models | 读 | 无 | OK |
| /api/llm/ask | 只读咨询 | 无（已修） | OK |
| /api/llm/config | 配置/密钥 | nuanced（base_url/key 需鉴权） | OK |
| /api/schemes/active | 写 | _admin_ok/Key 接管 | OK |
| /api/agent/fire | 执行/资金 | _admin_ok/Key 接管 | OK |
| /api/engine/* | 回测/优化 | _admin_ok | OK |
| /api/cond* | 条件单写 | _admin_ok | OK |
| /api/agent/schedule | 已停用 | 410 | OK |

## 结论
无误伤；写端点均受保护。建议 T66 做反向漏洞扫描（该保护却未保护）。
