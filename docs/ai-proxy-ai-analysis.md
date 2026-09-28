# 外部 AI 代理分析开发规范

> 创建：2026-09-18 · 上游：`architecture.md` §6 · 状态：**备用参考实现**（主力为内置自带 Key 分析，见账本 M5；本协议保留兼容）
> 目标：生产服务器只负责采集/筛选/展示；本协议描述一种外接 AI 分析的方式，结果回写生产服务器 SQLite。

## 1. 背景与结论

### 1.1 问题

OpenCode Zen 免费模型池已确认不可用（2026-09-07 起 7/7 模型全灭，403/400/500 轮换全挂）。根因：**免费模型仅限 OpenCode 内部使用**，服务端匿名调用被 FreeTierError 封死。`iteration-log.md` 已归档此结论。

### 1.2 结论

| 项 | 决策 |
|---|---|
| 生产服务器职责 | 采集 + 筛选 + 展示（AI 分析走自带 Key，见下；本地免费通道已死） |
| AI 分析执行方 | **用户自带 Key 的内置分析**（`/llm` 配置 + 队列执行，2026-09-27 上线）；本协议为备用兼容方案 |
| 结果存储 | 生产服务器 SQLite（单一真相源） |
| 触发时机 | 用户在面板触发（卡片/详情/整轮/重试）；每日 15:30 流水线只跑采集筛选 |
| 接口协议 | HTTP JSON（内置调用 OpenAI-compatible `/chat/completions`；本协议 `POST /api/analyze` 保留参考） |

## 2. 技术选型

### 2.1 内置分析调用链（现行）

用户 Key 存 `.env`（`/llm` 页写入，不进 Git）→ `AiAnalyzer` 读 env/配置 → OpenAI-compatible `/chat/completions` → `parse_ai_response` 校验 → 写库 + token 用量记账。

| 候选 | 定位 | 说明 |
|---|---|---|
| 用户自带 Key（DeepSeek/智谱/ OpenRouter 等） | **采用** | `/llm` 页配置，面板队列执行 |
| OpenRouter（付费） | 备选 | 多模型兜底（Claude/Gemini/DeepSeek），按量付费 |
| DeepSeek 直接 API | 备选 | 单价低，但单模型无轮换 |
| 本地 Ollama | 免费 | 需本机有 GPU，当前无 |

Key 在用户 `.env` 中配置，不进 Git。

### 2.2 备用：外部 AI 服务框架（参考实现）

| 候选 | 选型 | 理由 |
|---|---|---|
| FastAPI | **采用（备用）** | 生产服务器已用 FastAPI，同栈零学习成本，async 原生 |
| Flask | 不选 | 同步，无优势 |
| Raw HTTP | 不选 | 自己造轮子 |

### 2.3 LLM Provider（备用外部 AI 侧）

Provider 在外部程序自有 config 中配置，不进 Git。

### 2.4 生产服务器与外部程序通信

| 项 | 方案 |
|---|---|
| 协议 | HTTP POST JSON |
| 超时 | 300s（单只股票 LLM 分析耗时） |
| 认证 | 内网环境，暂不认证（后续可加 token） |
| 重试 | 生产服务器侧失败重试 3 次，间隔 30s |

## 3. 接口设计（备用参考）

### 3.1 触发分析（备用：生产服务器 → 外部程序）

**请求**

```
POST http://<外部程序主机>:8765/api/analyze
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
    "...": "其他 financial_summary 字段",
    "data_years": "2016-2026",
    "roe_5y_count": 5,
    "list_date": "2001-08-27",
    "is_st": false,
    "snapshot_date": "2026-09-19",
    "analysis_date": "2026-09-21"
  }
}
```

> 数据质量键（`data_years`/`roe_5y_count`/`list_date`/`is_st`/`snapshot_date`/`analysis_date`）
> 建议必传；缺失时服务端按"未知"处理、结论自动降档。面板 `/api/stocks`、
> `/api/watchlist/{code}/full` 已透传 `data_quality` 块，可直接转发。

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

### 3.2 结果写回 DB（备用：外部程序 → 生产服务器）

外部程序分析完成后，直接写入生产服务器的 SQLite（`data/db/stock_dashboard.db`）：

| 表 | 写入字段 |
|---|---|
| `stock_analysis_history` | stock_code, run_id, score, ai_analysis (JSON), ai_trade_strategy (JSON), model（必填溯源） |
| `watchlist_notes` | code, note, note_type, model（必填溯源）, created_at |
| `screening_result` | ai_analysis 回写列 + ai_failed 标记 |
| `ai_analysis_log` | run_id, code, model, tokens, cost |

**连接方式**：外部程序通过 SSH 隧道或共享 NFS 访问生产服务器的 SQLite；或外部程序提供写回接口，生产服务器暴露 DB 写权限。**具体方案按需确定（当前无外部消费方）。**

## 4. 数据流

```
15:30 流水线触发
    ↓
生产服务器: 采集 → 筛选 → screening_result 落库（20 只候选）
    ↓（用户在面板触发：卡片/详情/整轮/重试）
内置分析: 读 Key → 调 LLM → 校验 → 写库 + 用量记账
    ↓
生产服务器前端: 展示数据与 AI 笔记（列表卡片只展示数据，不内联 AI 分析，2026-09-21 方向）
```

> 现状（2026-09-27）：自带 Key 分析已上线（`/llm` 配置 + 队列执行，首只真分析跑通）；通用服务端 `src/ai_proxy/server.py`（`POST /api/analyze` + `/api/health`）保留为备用参考实现。

**周六复盘**：`WatchlistReviewer.review()` 硬规则本地执行不变；LLM 决议已随用户 Key 具备调用条件，待 10-03 周六首验。

## 5. 生产服务器侧实现清单

| 文件 | 改动 | 状态 |
|---|---|---|
| `src/ai_proxy/server.py` | **已实现**：通用 `POST /api/analyze` + `/api/health`（主/备双通道） | 2026-09-20 完成，备用保留 |
| `src/scheduler.py` | 本地免费通道自动触发已停用（2026-09-21） | 完成 |
| `src/analyzer/ai_analyzer.py` | 自带 Key 分析：面板队列 + 手动脚本均可用 | 现行主力 |
| `scripts/retry_ai.py` | 保留（手动补跑，需已配 Key） | 可用 |

## 6. 外部程序侧实现参考（规范，非本仓库代码）

| 组件 | 说明 |
|---|---|
| FastAPI 服务 | 照 `src/ai_proxy/server.py` 协议提供 `POST /api/analyze`（或自定协议，能读写生产服务器库/API 即可） |
| LLM 调用 | 外部程序自选 provider |
| Prompt 构建 | 复用 `src/analyzer/ai_analyzer.py` 中的 prompt 逻辑 |
| 结果解析 | 复用现有 `parse_ai_response` 逻辑 |
| DB 写回 | 连接生产服务器 SQLite 写入分析结果 |
| 配置 | `.env` 存 API key（不进 Git） |

## 7. 归档计划

### 7.1 原因

- OpenCode Zen 免费模型不可用（政策封死，非瞬时故障）
- 免费模型仅限 OpenCode 内部使用，外部匿名调用 403
- 自带 Key 方案已上线（2026-09-27），本协议退为备用

### 7.2 归档操作

| 文件/目录 | 操作 |
|---|---|
| `src/analyzer/ai_analyzer.py` | 现行主力：自带 Key 分析（面板队列 + 手动脚本）；不迁 `_legacy/` |
| `src/analyzer/` 下 FreeModelPool 相关 | 保留（免费通道已死，不再主动轮换） |
| `scripts/retry_ai.py` | 保留（手动补跑，需已配 Key） |
| `scripts/verify_valuation.py` | 保留（不依赖 LLM） |
| `scripts/verify_intrinsic.py` | 保留（不依赖 LLM） |
| `config/config.yaml` `ai.*` | 自带 Key 配置（`/llm` 页写入）；`fallback` 保留为应急 |

## 8. 约束与红线

- 生产服务器 LLM 调用只用用户自带 Key（资源约束：1G RAM 低内存主机，不跑本地模型）
- API key 只存用户 `.env`，**永不进 Git**
- 内网通信暂不认证，但 AI 服务不暴露到公网
- 分析结果必须写生产服务器 SQLite（单一真相源，避免数据分散）
- 触发由用户在面板发起，不另起高频自动任务

## 9. 验收标准

1. 自带 Key 可用：`/llm` 测试连接成功，单股分析跑通且用量落库（2026-09-27 已验证）
2. 分析成功后，`stock_analysis_history` 表有新记录
3. 笔记写入后，面板投资笔记/钉选股笔记正常展示（列表卡片不内联 AI 分析）
4. 全仓 pytest 零失败（基线 473 passed，只升不降）
5. 周六复盘 LLM 决议：10-03 首验（配置链完整，待实战验证）
