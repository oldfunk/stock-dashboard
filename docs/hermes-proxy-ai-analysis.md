# Hermes 代理 AI 分析

> 创建：2026-09-18 · 上游：architecture.md §6 · 状态：方向确立，待排期

OpenCode Zen 免费模型池已确认不可用（2026-09-07 起 7/7 模型全灭，403/400/500 轮换全挂）。根因：免费模型仅限 OpenCode 内部使用，服务端匿名调用被 FreeTierError 封死。iteration-log.md 已归档此结论。

pi1 只负责采集、筛选、展示，不跑 LLM。AI 分析外包给 pi2 Hermes，结果回写 pi1 SQLite。触发时机跟随现有调度每日 15:30 流水线自动触发。接口协议为 HTTP JSON。

## 1. 技术选型

pi2 Hermes 服务框架用 FastAPI，pi1 已用 FastAPI 同栈零学习成本。LLM Provider 首选 OpenRouter 付费按量计费多模型兜底，备选 DeepSeek 直接 API 单价低但单模型无轮换，本地 Ollama 免费但当前 pi2 无 GPU。Provider 在 pi2 的 .env 或 Hermes config 中配置不进 Git。pi1 与 pi2 通信协议 HTTP POST JSON，超时 300 秒，内网环境暂不认证后续可加 token，pi1 侧失败重试 3 次间隔 30 秒。

## 2. 接口设计

pi1 筛选出 20 只候选后逐只调 pi2 的 POST /api/analyze，请求体包含股票代码、名称、run_id、以及 score/pe/pb/roe_5y_avg/gross_margin/fcf_5y_sum 等 financial_summary 字段。pi2 返回 status 为 ok 时包含 model、analysis JSON（与现有 ai_analyzer 输出格式一致）、usage（tokens 和 cost）。status 为 error 时包含 error 描述和 retryable 标记。

pi2 分析完成后直接写入 pi1 的 SQLite，写 stock_analysis_history 表（run_id、code、name、ai_analysis JSON、model、created_at）、screening_result 的 ai_analysis 回写列和 ai_failed 标记、ai_analysis_log 的 run_id、code、model、tokens、cost。连接方式由 pi2 侧开发时确定，可选 SSH 隧道或共享 NFS。

## 3. 数据流

每日 15:30 流水线触发后，pi1 执行采集、筛选、screening_result 落库（20 只候选），然后逐只调 pi2 的 POST /api/analyze，pi2 构建 prompt 调 LLM 解析 JSON 后写回 pi1 SQLite，pi1 前端展示分析结果。周六复盘 WatchlistReviewer.review() 不涉及 LLM 保持 pi1 本地执行不变。

## 4. pi1 侧实现清单

新增 src/analyzer/hermes_client.py 作为 HTTP 客户端调 pi2 接口。src/orchestrator.py 的 AI 分析阶段从本地调 AiAnalyzer 改为调 HermesClient。src/scheduler.py 不动，15:30 触发逻辑不变。src/analyzer/ai_analyzer.py 移到 src/analyzer/_legacy/ 并标记废弃原因。新增 tests/analyzer/test_hermes_client.py 用 mock pi2 接口做单测。config/config.yaml 新增 ai.pi2_url 配置项。

## 5. pi2 侧实现清单

pi2 运行 FastAPI 服务监听 8765 端口提供 POST /api/analyze。LLM 调用用 Hermes provider 或直接 SDK 调 OpenRouter。Prompt 构建复用现有 ai_analyzer.py 的 _build_prompt 逻辑迁往 pi2。结果解析复用现有 parse_ai_response 逻辑。DB 写回连接 pi1 SQLite 写入分析结果。.env 存 API key 不进 Git。pi2 侧代码不进本仓库，由 pi2 独立开发部署。

## 6. 归档计划

归档原因：OpenCode Zen 免费模型不可用（政策封死非瞬时故障），免费模型仅限 OpenCode 内部使用外部匿名调用 403，付费方案更可靠按量计费多模型兜底。归档操作：src/analyzer/ai_analyzer.py 移到 src/analyzer/_legacy/，src/analyzer/ 下 FreeModelPool 相关同移，scripts/retry_ai.py 保留改为重试 pi2 接口调用，scripts/verify_valuation.py 和 scripts/verify_intrinsic.py 保留不依赖 LLM。config/config.yaml 的 ai.* 保留 fallback 配置作为应急，新增 ai.pi2_url。

## 7. 约束

pi1 不跑 LLM 资源约束 1G RAM 树莓派。API key 只存 pi2 永不进 Git。内网通信暂不认证但 pi2 服务不暴露到公网。分析结果必须写 pi1 SQLite 单一真相源避免数据分散。触发时机跟随现有调度 15:30 不另起高频任务。

## 8. 验收标准

pi2 Hermes 服务能接收 POST /api/analyze 并返回分析 JSON。pi1 调 pi2 接口成功后 stock_analysis_history 表有新记录。前端候选卡和详情页能展示 pi2 返回的分析结果。全仓 pytest 零失败基线 313 以上。连续 5 个交易日流水线 AI 分析成功率不低于 80%。
