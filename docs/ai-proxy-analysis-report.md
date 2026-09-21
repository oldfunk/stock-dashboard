# 外部 AI 代理分析报告（原 Hermes 代理报告，2026-09-21 通用化）

> 日期：2026-09-18 · 作者：Hermes（作者自用的外部 AI 实例；下文 "Hermes" 均指该实例，非唯一指定实现）· 状态：已评审，方向采纳
>
> 2026-09-21 修订：外部 AI 不绑定具体实现；通用服务端 `src/ai_proxy/` 已实现；本地 AI 触发已停用。

## 背景

OpenCode Zen 免费模型池已确认不可用（7/7 模型全灭，403/400/500 轮换全挂）。用户决定：
- 不再依赖 OpenCode 免费模型
- 用外部 AI 接管 AI 分析工作（作者自用 Hermes）
- 外部 AI 承担分析任务，pi1 只负责采集/筛选/展示

## 两种路径对比

### 路径 A：Hermes 直接调 LLM API（推荐）

**架构：**
```
pi1（采集/筛选）→ HTTP API → pi2 Hermes（LLM 分析）→ 写回 DB
```

**优点：**
- 职责清晰：pi1 只做数据采集和筛选，AI 分析完全外包
- Hermes 可用任意 LLM provider（付费/本地/多模型轮换）
- pi1 资源占用低（1G RAM 不跑 LLM）
- 分析逻辑集中在一处，易维护

**缺点：**
- 需要 pi2 常开（或定时唤醒）
- 网络依赖（pi1 → pi2 HTTP）
- 需要设计接口和认证

### 路径 B：Hermes 做编排调度

**架构：**
```
pi1（采集/筛选）→ 任务队列 → Hermes 轮询拉取 → 分析 → 写回 DB
```

**优点：**
- 解耦更彻底（pi1 不知道 Hermes 存在）
- Hermes 可批量处理多只股票
- 任务可重试、可优先级排序

**缺点：**
- 需要任务队列（Redis/SQLite 轮询）
- 实时性差（轮询间隔）
- 复杂度高，调试困难

## 推荐：路径 A

理由：
1. **简单可靠**：HTTP API 直接调用，无中间件
2. **实时性好**：pi1 筛选完立即触发分析，秒级响应
3. **Hermes 天然支持**：Hermes 本身就是 agent 框架，调 LLM 是核心能力
4. **pi2 资源充足**：pi2 是开发机，RAM/CPU 都够跑 LLM

## 接口设计草案

### pi1 侧（调用方）

```python
# src/analyzer/hermes_client.py（新文件）
import httpx

def trigger_analysis(stock_code: str, stock_data: dict) -> bool:
    """触发 Hermes 分析一只股票"""
    resp = httpx.post(
        "http://pi2:8765/api/analyze",
        json={"code": stock_code, "data": stock_data},
        timeout=300.0
    )
    return resp.status_code == 200
```

### pi2 侧（Hermes 服务）

```python
# pi2 上运行的 FastAPI 服务（新文件）
from fastapi import FastAPI
app = FastAPI()

@app.post("/api/analyze")
async def analyze_stock(req: dict):
    """接收股票数据，调 LLM 分析，写回 DB"""
    # 1. 构建 prompt（复用现有 ai_analyzer.py 的 prompt 逻辑）
    # 2. 调 LLM（Hermes 的 provider）
    # 3. 解析结果
    # 4. 写回 pi1 的 SQLite（或直接写 pi2 的副本）
    return {"status": "ok", "model": "..."}
```

### 数据流

```
pi1 筛选出 20 只候选
    ↓
pi1 逐只调 POST http://pi2:8765/api/analyze
    ↓
pi2 Hermes 收到 → 构建 prompt → 调 LLM → 解析 JSON
    ↓
pi2 写回 stock_analysis_history（或推回 pi1）
    ↓
pi1 前端展示分析结果
```

## 归档计划

旧 AI 分析代码（`src/analyzer/ai_analyzer.py` + `src/analyzer/` 下的 FreeModelPool 等）：
- 不删除，移到 `src/analyzer/_legacy/`
- 记录归档原因：OpenCode Zen 免费模型不可用，改用 Hermes 代理
- 保留 `scripts/verify_valuation.py` 等验算闸（不依赖 LLM）

## 待确认

1. **pi2 的 Hermes 服务怎么部署？** 手动启动还是 systemd？
2. **LLM provider 选什么？** 付费（DeepSeek/Claude）还是本地（Ollama）？
3. **分析结果写哪？** 直接写 pi1 的 DB（需要 pi2 能访问 pi1 的 SQLite）还是 pi2 本地副本？
4. **触发时机？** 每日 15:30 流水线自动触发，还是手动触发？

---

**结论**：推荐路径 A（Hermes 直接调 LLM API），简单可靠，职责清晰。等待用户确认细节后实施。
