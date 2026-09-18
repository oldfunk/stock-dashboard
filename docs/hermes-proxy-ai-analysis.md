# Hermes 代理 AI 分析开发规范

> 创建：2026-09-18 · 上游：`architecture.md` §6 · 状态：方向确立，待排期
> 目标：pi1 只负责采集/筛选/展示，AI 分析外包给 pi2 Hermes，结果回写 pi1 SQLite。

## 1. 背景与结论

### 1.1 问题

OpenCode Zen 免费模型池已确认不可用（2026-09-07 起 7/7 模型全灭，403/400/500 轮换全挂）。根因：**免费模型仅限 OpenCode 内部使用**，服务端匿名调用被 FreeTierError 封死。`iteration-log.md` 已归档此结论。

### 1.2 结论

| 项 | 决策 |
|---|---|
| pi1 职责 | 采集 + 筛选 + 展示（不跑 LLM） |
| AI 分析执行方 | pi2 Hermes（FastAPI 服务，LLM provider 可切换） |
| 结果存储 | pi1 SQLite（单一真相源） |
| 触发时机 | 每日 15:30 流水线自动触发（现有 `schedule.daily_update_time`） |
| 接口协议 | HTTP JSON（`POST /api/analyze`） |

## 2. 技术选型

### 2.1 pi2 Hermes 服务框架

| 候选 | 选型 | 理由 |
|---|---|---|
| FastAPI | **采用** | pi1 已用 FastAPI，同栈零学习成本，async 原生 |
| Flask | 不选 | 同步，无优势 |
| Raw HTTP | 不选 | 自己造轮子 |

### 2.2 LLM Provider（pi2 侧）

| 候选 | 定位 | 说明 |
|---|---|---|
| OpenRouter（付费） | **首选** | 多模型兜底（Claude/Gemini/DeepSeek），按量付费，pi2 可访问 |
| DeepSeek 直接 API | 备选 | 单价低，但单模型无轮换 |
| 本地 Ollama | 免费 | 需 pi2 有 GPU，当前无 |

Provider 在 pi2 的 `.env` 或 Hermes config 中配置，不进 Git。

### 2.3 pi1 ↔ pi2 通信

| 项 | 方案 |
|---|---|
| 协议 | HTTP POST JSON |
| 超时 | 300s（单只股票 LLM 分析耗时） |
| 认证 | 内网环境，暂不认证（后续可加 token） |
| 重试 | pi1 侧失败重试 3 次，间隔 30s |

## 3. 接口设计

### 3.1 触发分析（pi1 → pi2）

**请求**

```
POST http://pi2:8765/api/analyze
Content-Type: application/json

{
  "code": "600519",
  "name": "贵州茅台",
  "run_id": "20260918_153000",
  "data": {
    "score": 88.5,
    "pe": 28.5,
    "pb": 9.2,
    "roe_5y_avg": 25.3,
    "gross_margin": 92.0,
    "fcf_5y_sum": 1234.5,
    "...": "其他 financial_summary 字段"
  }
}
```

**响应**

```json
{
  "status": "ok",
  "model": "claude-sonnet-5",
  "analysis": {
    "analysis": "...",
    "moat_evaluation": [...],
    "management_score": {...},
    "intrinsic_value": {...},
    "trade_strategy": {...}
  },
  "usage": {
    "tokens": 15234,
    "cost_usd": 0.03
  }
}
```

**错误响应**

```json
{
  "status": "error",
  "error": "LLM timeout after 300s",
  "retryable": true
}
```

### 3.2 结果写回 DB（pi2 → pi1）

pi2 分析完成后，直接写入 pi1 的 SQLite（`data/db/stock_dashboard.db`）：

| 表 | 写入字段 |
|---|---|
| `stock_analysis_history` | run_id, code, name, ai_analysis (JSON), model, created_at |
| `screening_result` | ai_analysis 回写列 + ai_failed 标记 |
| `ai_analysis_log` | run_id, code, model, tokens, cost |

**连接方式**：pi2 通过 SSH 隧道或共享 NFS 访问 pi1 的 SQLite；或 pi2 提供写回接口，pi1 暴露 DB 写权限。**具体方案由 pi2 侧开发时确定。**

## 4. 数据流

```
15:30 流水线触发
    ↓
pi1: 采集 → 筛选 → screening_result 落库（20 只候选）
    ↓
pi1: 逐只调 POST http://pi2:8765/api/analyze
    ↓
pi2 Hermes: 构建 prompt → 调 LLM → 解析 JSON
    ↓
pi2: 写回 pi1 SQLite（stock_analysis_history 等表）
    ↓
pi1 前端: 展示 AI 分析结果（候选卡摘要 + 详情页）
```

**周六复盘**：`WatchlistReviewer.review()` 不涉及 LLM，保持 pi1 本地执行不变。

## 5. pi1 侧实现清单

| 文件 | 改动 |
|---|---|
| `src/analyzer/hermes_client.py` | **新增**：HTTP 客户端，调 pi2 接口 |
| `src/orchestrator.py` | 改：AI 分析阶段从本地调 `AiAnalyzer` 改为调 `HermesClient` |
| `src/scheduler.py` | 不动：15:30 触发逻辑不变 |
| `src/analyzer/ai_analyzer.py` | **归档**：移到 `src/analyzer/_legacy/`，标记废弃原因 |
| `tests/analyzer/test_hermes_client.py` | **新增**：mock pi2 接口单测 |
| `config/config.yaml` | 新增 `ai.pi2_url` 配置项 |

## 6. pi2 侧实现清单（规范，非本仓库代码）

| 组件 | 说明 |
|---|---|
| FastAPI 服务 | 监听 `:8765`，提供 `POST /api/analyze` |
| LLM 调用 | 用 Hermes provider 或直接 SDK 调 OpenRouter |
| Prompt 构建 | 复用 `src/analyzer/ai_analyzer.py` 中的 `_build_prompt` 逻辑（迁往 pi2） |
| 结果解析 | 复用现有 `parse_ai_response` 逻辑 |
| DB 写回 | 连接 pi1 SQLite 写入分析结果 |
| 配置 | `.env` 存 API key（不进 Git） |

## 7. 归档计划

### 7.1 原因

- OpenCode Zen 免费模型不可用（政策封死，非瞬时故障）
- 免费模型仅限 OpenCode 内部使用，外部匿名调用 403
- 付费方案更可靠（按量计费，多模型兜底）

### 7.2 归档操作

| 文件/目录 | 操作 |
|---|---|
| `src/analyzer/ai_analyzer.py` | 移到 `src/analyzer/_legacy/` |
| `src/analyzer/` 下 FreeModelPool 相关 | 移到 `src/analyzer/_legacy/` |
| `scripts/retry_ai.py` | 保留（改为重试 pi2 接口调用） |
| `scripts/verify_valuation.py` | 保留（不依赖 LLM） |
| `scripts/verify_intrinsic.py` | 保留（不依赖 LLM） |
| `config/config.yaml` `ai.*` | 保留 `fallback` 配置作为应急，新增 `ai.pi2_url` |

## 8. 约束与红线

- pi1 不跑 LLM（资源约束：1G RAM 树莓派）
- API key 只存 pi2，**永不进 Git**
- 内网通信暂不认证，但 pi2 服务不暴露到公网
- 分析结果必须写 pi1 SQLite（单一真相源，避免数据分散）
- 触发时机跟随现有调度（15:30），不另起高频任务

## 9. 验收标准

1. pi2 Hermes 服务能接收 `POST /api/analyze` 并返回分析 JSON
2. pi1 调 pi2 接口成功后，`stock_analysis_history` 表有新记录
3. 前端候选卡 + 详情页能展示 pi2 返回的分析结果
4. 全仓 pytest 零失败（基线 313+ passed）
5. 连续 5 个交易日流水线 AI 分析成功率 ≥ 80%
