# 外部 AI 代理分析开发规范

> 创建：2026-09-18 · 上游：`architecture.md` §6 · 状态：服务端已实现（`src/ai_proxy/`），消费方排期待定
> 目标：生产服务器只负责采集/筛选/展示，AI 分析外包给外部 AI，结果回写生产服务器 SQLite。
> 说明：外部 AI 不绑定具体实现；作者自用 Hermes 接入（历史文档中的 "Hermes" 均指作者的这一实例）。

## 1. 背景与结论

### 1.1 问题

OpenCode Zen 免费模型池已确认不可用（2026-09-07 起 7/7 模型全灭，403/400/500 轮换全挂）。根因：**免费模型仅限 OpenCode 内部使用**，服务端匿名调用被 FreeTierError 封死。`iteration-log.md` 已归档此结论。

### 1.2 结论

| 项 | 决策 |
|---|---|
| 生产服务器职责 | 采集 + 筛选 + 展示（不跑 LLM，本地 AI 触发已于 2026-09-21 停用） |
| AI 分析执行方 | 外部 AI（FastAPI 服务，LLM provider 可切换；作者自用 Hermes） |
| 结果存储 | 生产服务器 SQLite（单一真相源） |
| 触发时机 | 每日 15:30 流水线自动触发（现有 `schedule.daily_update_time`） |
| 接口协议 | HTTP JSON（`POST /api/analyze`） |

## 2. 技术选型

### 2.1 外部 AI 服务框架（作者侧由 Hermes 承担）

| 候选 | 选型 | 理由 |
|---|---|---|
| FastAPI | **采用** | 生产服务器已用 FastAPI，同栈零学习成本，async 原生 |
| Flask | 不选 | 同步，无优势 |
| Raw HTTP | 不选 | 自己造轮子 |

### 2.2 LLM Provider（外部 AI 侧）

| 候选 | 定位 | 说明 |
|---|---|---|
| OpenRouter（付费） | **首选** | 多模型兜底（Claude/Gemini/DeepSeek），按量付费 |
| DeepSeek 直接 API | 备选 | 单价低，但单模型无轮换 |
| 本地 Ollama | 免费 | 需本机有 GPU，当前无 |

Provider 在外部 AI 侧的 `.env` 或自有 config 中配置，不进 Git。

### 2.3 生产服务器与外部 AI 通信

| 项 | 方案 |
|---|---|
| 协议 | HTTP POST JSON |
| 超时 | 300s（单只股票 LLM 分析耗时） |
| 认证 | 内网环境，暂不认证（后续可加 token） |
| 重试 | 生产服务器侧失败重试 3 次，间隔 30s |

## 3. 接口设计

### 3.1 触发分析（生产服务器 → 外部 AI，待消费方排期）

**请求**

```
POST http://<外部AI主机>:8765/api/analyze
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

### 3.2 结果写回 DB（外部 AI → 生产服务器）

外部 AI 分析完成后，直接写入生产服务器的 SQLite（`data/db/stock_dashboard.db`）：

| 表 | 写入字段 |
|---|---|
| `stock_analysis_history` | stock_code, run_id, score, ai_analysis (JSON), ai_trade_strategy (JSON), model（必填溯源） |
| `watchlist_notes` | code, note, note_type, model（必填溯源）, created_at |
| `screening_result` | ai_analysis 回写列 + ai_failed 标记 |
| `ai_analysis_log` | run_id, code, model, tokens, cost |

**连接方式**：外部 AI 通过 SSH 隧道或共享 NFS 访问生产服务器的 SQLite；或外部 AI 提供写回接口，生产服务器暴露 DB 写权限。**具体方案由消费方开发时确定。**

## 4. 数据流

```
15:30 流水线触发
    ↓
生产服务器: 采集 → 筛选 → screening_result 落库（20 只候选）
    ↓（待消费方排期：外部 AI 定时调接口取数）
外部 AI: 读生产服务器数据 API（/api/stocks、/api/watchlist/{code}/full 等）→ 调 LLM
    ↓
外部 AI: 写回生产服务器 SQLite（stock_analysis_history、watchlist_notes、ai_journal 等表）
    ↓
生产服务器前端: 展示数据与外部 AI 笔记（列表卡片只展示数据，不内联 AI 分析，2026-09-21 方向）
```

> 现状（2026-09-21）：通用服务端 `src/ai_proxy/server.py`（`POST /api/analyze` + `/api/health`）已实现，
> 任何外部 AI 实现均可照此协议提供服务；生产服务器侧消费接线（定时取数/触发）待排期；本地 AI 触发已停用。

**周六复盘**：`WatchlistReviewer.review()` 不涉及 LLM，保持生产服务器本地执行不变。

## 5. 生产服务器侧实现清单

| 文件 | 改动 | 状态 |
|---|---|---|
| `src/ai_proxy/server.py` | **已实现**：通用 `POST /api/analyze` + `/api/health`（主/备双通道） | 2026-09-20 完成 |
| `src/scheduler.py` | 本地 AI 自动触发已停用（2026-09-21），等外部 AI 消费方 | 完成 |
| `src/analyzer/ai_analyzer.py` | 保留：手动脚本（retry_ai.py 等）仍可用；pipeline 不再调用 | 保留 |
| 消费方接线（外部 AI 定时取数/写回） | 待排期 | 未开始 |

## 6. 外部 AI 侧实现清单（规范，非本仓库代码；作者侧跑在 Hermes 上）

| 组件 | 说明 |
|---|---|
| FastAPI 服务 | 照 `src/ai_proxy/server.py` 协议提供 `POST /api/analyze`（或自定协议，能读写生产服务器库/API 即可） |
| LLM 调用 | 外部 AI 自选 provider（作者侧用 Hermes provider / OpenRouter） |
| Prompt 构建 | 复用 `src/analyzer/ai_analyzer.py` 中的 `_build_prompt` 逻辑（迁往外部 AI 侧） |
| 结果解析 | 复用现有 `parse_ai_response` 逻辑 |
| DB 写回 | 连接生产服务器 SQLite 写入分析结果 |
| 配置 | `.env` 存 API key（不进 Git） |

## 7. 归档计划

### 7.1 原因

- OpenCode Zen 免费模型不可用（政策封死，非瞬时故障）
- 免费模型仅限 OpenCode 内部使用，外部匿名调用 403
- 付费方案更可靠（按量计费，多模型兜底）

### 7.2 归档操作

| 文件/目录 | 操作 |
|---|---|
| `src/analyzer/ai_analyzer.py` | 保留：pipeline 已停用本地触发，仅手动脚本（retry_ai.py 等）可用；不迁 `_legacy/` |
| `src/analyzer/` 下 FreeModelPool 相关 | 保留（随上，不再主动轮换） |
| `scripts/retry_ai.py` | 保留（手动补跑本地分析；外部 AI 上线后改为重试消费方接口） |
| `scripts/verify_valuation.py` | 保留（不依赖 LLM） |
| `scripts/verify_intrinsic.py` | 保留（不依赖 LLM） |
| `config/config.yaml` `ai.*` | 保留 `fallback` 配置作为应急（本地触发已停用） |

## 8. 约束与红线

- 生产服务器不跑 LLM（资源约束：1G RAM 低内存主机）
- API key 只存外部 AI 侧，**永不进 Git**
- 内网通信暂不认证，但外部 AI 服务不暴露到公网
- 分析结果必须写生产服务器 SQLite（单一真相源，避免数据分散）
- 触发时机跟随现有调度（15:30），不另起高频任务

## 9. 验收标准

1. 外部 AI 服务能接收 `POST /api/analyze` 并返回分析 JSON（`src/ai_proxy/server.py` 为参考实现）
2. 外部 AI 调接口成功后，`stock_analysis_history` 表有新记录
3. 外部 AI 笔记写入后，面板投资笔记/钉选股笔记正常展示（列表卡片不内联 AI 分析）
4. 全仓 pytest 零失败（基线 396 passed，只升不降）
5. 外部 AI 消费方上线后：连续 5 个交易日流水线 AI 分析成功率 ≥ 80%
