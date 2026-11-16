# AI 分析模型：GLM-4.7-Flash 规格与调优

> 创建：2026-11-16 · 上游：智谱官方文档（docs.bigmodel.cn）· 状态：适用中
> 模型名（config/local.yaml）：`glm-4.5-flash`——**路由到 GLM-4.7-Flash**。
> 直接调 `glm-4.7-flash` 会严重拥堵（用户实测），所以用 4.5 入口。

## 1. 官方规格（2026-11-16 抓取）

| 项 | 值 |
|---|---|
| 价格 | **全免费**（输入/输出/缓存命中/缓存存储均免费） |
| 上下文 | 200K |
| 最大输出 | 模型标称 128K；**API 实际限制 `max_tokens ∈ [1, 98304]`** |
| 尺寸/定位 | 30B 级 SOTA，Agentic Coding 强化（SWE-bench、τ²-Bench 同尺寸开源 SOTA） |
| 思考模式 | **GLM-4.7 系列默认开启 Thinking**（不像 4.6 是"混合自动"）；我们仍显式传 `thinking={"type":"enabled"}` |
| 结构化输出 | 支持 `response_format={"type":"json_object"}` |
| 其他能力 | 流式、Function Calling、上下文缓存、MCP |
| 推荐场景 | Agentic Coding、复杂问题协作、长文本、中文写作/翻译 |

同族对照：`GLM-4.7-FlashX`（200K/128K，**付费** 0.5/3 元每百万 token）、
`GLM-4.7`（付费 2~4 / 8~16）、`GLM-4.5-Flash`（128K/96K，免费）。

## 2. 我们踩过的两个坑（实测）

1. **max_tokens=6000 会截断**：同一 prompt 实测单组 completion 用到
   **6328** tokens（>6000）。模型标称 128K、免费，没理由卡 6000。
   ⚠️ 但 API 实际上限是 **98304**（`131072` 会报 `400 code=1210
   max_tokens参数非法：限制数值范围[1,98304]`）——别按标称 128K 填。
2. **不开 JSON 模式时输出不合法**：约半数响应带 markdown 前言/尾注，
   `json.loads` 直接失败，只能靠兜底提取（`_extract_json`）。开启官方
   `response_format=json_object` 后稳定合法。

## 3. 参数实测（同一 prompt = 000792，8506 字符；2026-11-16 生产）

| 组 | 设置 | 延迟 | 输出长 | JSON | completion tokens |
|---|---|---|---|---|---|
| A | mt=6000, t=0.3 | 269s | 4766 | **FAIL** | 4826 |
| B | mt=16000, t=0.3 | 147s | 4410 | **FAIL** | 2905 |
| C | mt=16000, t=0.3, +json | 289s | 3360 | OK | 6328 |
| D | mt=16000, t=0.8 | 187s | 4428 | OK | 4545 |

结论：
- **JSON 合法性**与参数无稳定关系（A/B FAIL、C/D OK），所以靠
  `response_format` 强制，不靠运气。
- **延迟 147–289s 波动大**，与参数无关——是上游拥堵（用户也说直连 4.7 拥堵）。
- **temperature 0.3 vs 0.8** 未见明显质量差异；保持 0.3（更稳定、可复现）。

## 4. 已落地的调优

| 项 | 值 | 位置 |
|---|---|---|
| `max_tokens` | **98304**（API 上限；模型标称 128K 但参数被拒） | config.yaml（nec local.yaml 同步） |
| `thinking` | **true**（显式 `thinking={"type":"enabled"}`） | config.yaml |
| `json_mode` | true（`response_format=json_object`） | config.yaml，可关 |
| `temperature` | 0.3 | config.yaml |
| `daily_concurrency` | 1 | config.yaml（**上游 glm 并发上限=1**） |
| `rpm_limit` | 40 | config.yaml（进程级限速器兜底） |
| 面板队列并发 | 1 | local.yaml `ai.concurrency`（`/api/llm/concurrency`） |

代码：`src/analyzer/ai_analyzer.py`（json_mode、max_tokens 兜底）、
`src/ai_rate.py`（RPM 限速器，装在 `analyze_stock` 唯一入口）。
用例：`tests/analyzer/test_ai_json_mode_20261116.py`、`test_ai_rate_20261116.py`、
`test_ai_none_content_20261116.py`。

## 5. 待验证的调优点

1. **关掉 thinking 会不会快一倍**：4.7 系列默认开启；分析任务是否需要深度
   思考未知。可 A/B：`thinking={"type":"disabled"}` vs 默认，比延迟与
   「数字抽检偏离 / 否决触发」质量。
2. **并发**：glm 账号并发=1，RPM 限额未知；维持 1。
3. **数字偏离**：glm 的正文数字常与库不符（系统已自动标注 `numeric_mismatch`），
   可在 prompt 里要求"只引用系统给的数字"再测偏离率。
