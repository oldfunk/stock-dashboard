# 单股详情页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/stock/{code}` 单股详情页（单栏叙事流），首页 AI 观察池卡片和 /candidates 候选股卡片可点击进入；无 AI 分析时降级为纯数据版。

**Architecture:** FastAPI 新路由 `/stock/{code}` 读 `stock_snapshot` + `stock_analysis_history` + `financial_history` + `ai_watchlist`，渲染新模板 `stock_detail.html`。所有数据来自已有 SQLite 表，无新增表/字段/LLM 调用。模板用 Jinja2 条件渲染实现"无 AI 则隐藏"降级。

**Tech Stack:** Python 3.11 / FastAPI / Jinja2 / SQLite / pytest (TestClient)

**Spec:** `docs/superpowers/specs/2026-07-21-stock-detail-page-design.md`

---

## 文件结构

### 新增文件

- `src/web/templates/stock_detail.html` — 单股详情页模板（单栏叙事流，12 章节）
- `tests/web/test_routes_stock_detail.py` — 路由测试

### 修改文件

- `src/models/database.py` — 在 `StockSnapshotDAO` 新增 `get_by_code(code)` 方法
- `src/web/routes.py` — 新增 `GET /stock/{code}` 路由
- `src/web/templates/_watchlist_card.html` — 观察池卡片可点（onclick + cursor:pointer）
- `src/web/templates/_stock_list.html` — 候选股卡片可点（onclick + cursor:pointer，watch-btn stopPropagation）
- `src/web/templates/index.html` — 补 `.watchlist-card:hover` / `.stock-card:hover` 的 cursor 样式
- `src/web/templates/candidates.html` — 补 `.stock-card:hover` 的 cursor 样式

### 不新增 / 不修改

- 不新增表、不新增字段
- 不调 LLM
- 不引入前端图表库
- 不改钉选股票 / 搜索结果的交互

---

## Task 1: StockSnapshotDAO.get_by_code

**Files:**
- Modify: `src/models/database.py`（`StockSnapshotDAO` 类，第 383 行起）
- Test: `tests/models/test_stock_snapshot.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/models/test_stock_snapshot.py`：

```python
"""StockSnapshotDAO.get_by_code 测试"""

import pytest
from src.models import database as db_mod


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    return db_path


def test_get_by_code_not_found(tmp_db):
    from src.models.database import StockSnapshotDAO
    result = StockSnapshotDAO().get_by_code("999999")
    assert result is None


def test_get_by_code_found(tmp_db):
    from src.models.database import StockSnapshotDAO
    dao = StockSnapshotDAO()
    dao.save_batch([{
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 12.5, "pb": 2.1, "ps": 3.0, "market_cap": 980.0, "circulating_cap": 900.0,
        "roe": 18.3, "revenue": 100.0, "revenue_growth": 15.0, "profit": 20.0,
        "profit_growth": 20.0, "debt_ratio": 35.0, "dividend_yield": 2.1,
        "current_price": 18.42, "high_52w": 25.0, "low_52w": 12.0,
        "is_st": False, "list_date": "2000-01-01", "snapshot_date": "2026-07-21",
    }])
    result = dao.get_by_code("000792")
    assert result is not None
    assert result["code"] == "000792"
    assert result["name"] == "盐湖股份"
    assert result["sector"] == "化工原料"
    assert result["pe"] == 12.5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd g:\trae\stock-dashboard-github && python -m pytest tests/models/test_stock_snapshot.py -v`

Expected: FAIL — `AttributeError: 'StockSnapshotDAO' object has no attribute 'get_by_code'`

- [ ] **Step 3: 实现 get_by_code 方法**

在 `src/models/database.py` 的 `StockSnapshotDAO` 类的 `count` 方法后（约第 413 行）添加：

```python
    def get_by_code(self, code: str) -> Optional[dict]:
        """按股票代码查最新快照（单条）"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM stock_snapshot WHERE code = ?", (code,)
            ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd g:\trae\stock-dashboard-github && python -m pytest tests/models/test_stock_snapshot.py -v`

Expected: PASS (2 tests)

- [ ] **Step 5: 提交**

```bash
cd g:\trae\stock-dashboard-github
git add src/models/database.py tests/models/test_stock_snapshot.py
git commit -m "feat: StockSnapshotDAO.get_by_code 按 code 查最新快照

为单股详情页准备：/stock/{code} 路由需要按 code 取 stock_snapshot 单条记录。
当前 StockSnapshotDAO 只有 save_batch / get_latest_snapshot_date / count 三个方法，
缺少按 code 单查的能力。"
```

---

## Task 2: /stock/{code} 路由（最小版，只渲染 Header + 快照条 + 占位）

**Files:**
- Modify: `src/web/routes.py`（在 `/candidates` 路由后添加）
- Test: `tests/web/test_routes_stock_detail.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/web/test_routes_stock_detail.py`：

```python
"""单股详情页 /stock/{code} 路由测试"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试用独立临时数据库"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_stock_detail_404_invalid_code_format(client):
    """格式不合法的 code 返回 404"""
    resp = client.get("/stock/abc123")
    assert resp.status_code == 404

    resp = client.get("/stock/12345")  # 5 位
    assert resp.status_code == 404

    resp = client.get("/stock/1234567")  # 7 位
    assert resp.status_code == 404


def test_stock_detail_404_not_in_snapshot(client):
    """合法 code 但 stock_snapshot 里没有也返回 404"""
    resp = client.get("/stock/000792")
    assert resp.status_code == 404


def test_stock_detail_ok_with_snapshot_only(client):
    """只有 stock_snapshot 数据，没有 AI 分析 — 纯数据版"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([{
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 12.5, "pb": 2.1, "ps": 3.0, "market_cap": 980.0, "circulating_cap": 900.0,
        "roe": 18.3, "revenue": 100.0, "revenue_growth": 15.0, "profit": 20.0,
        "profit_growth": 20.0, "debt_ratio": 35.0, "dividend_yield": 2.1,
        "current_price": 18.42, "high_52w": 25.0, "low_52w": 12.0,
        "is_st": False, "list_date": "2000-01-01", "snapshot_date": "2026-07-21",
    }])

    resp = client.get("/stock/000792")
    assert resp.status_code == 200
    # Header 必有
    assert "盐湖股份" in resp.text
    assert "000792" in resp.text
    assert "化工原料" in resp.text
    # 关键指标快照条
    assert "12.5" in resp.text
    assert "18.3" in resp.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd g:\trae\stock-dashboard-github && python -m pytest tests/web/test_routes_stock_detail.py -v`

Expected: FAIL — `/stock/000792` 路由不存在 / 404 路由 fallback

- [ ] **Step 3: 实现路由（最小版）**

在 `src/web/routes.py` 的 `/candidates` 路由后（约第 352 行）插入：

```python
@app.get("/stock/{code}", response_class=HTMLResponse)
async def stock_detail(request: Request, code: str):
    """单股详情页 — 单栏叙事流

    展示单只股票的全部信息：关键指标快照 → AI 投资笔记 →
    护城河/管理层/内在价值/交易策略/逆向思考 → 动态财务历史 →
    历次 AI 分析时间线 → 在池状态。

    无 AI 分析时降级为纯数据版（财务 + 行业 + 估值）。
    """
    import re
    # 格式校验：6 位数字
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(status_code=404, detail="Invalid code")

    from src.models.database import (
        StockSnapshotDAO, StockAnalysisHistoryDAO,
        FinancialHistoryDAO, FinancialSummaryDAO,
    )
    from src.models.ai_watchlist import (
        AiWatchlistDAO, AiWatchlistHistoryDAO
    )

    # 1. 基础快照 — 没有则 404
    snapshot = StockSnapshotDAO().get_by_code(code)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Stock not found")

    # 2. 实时行情缓存
    realtime = get_realtime_cache()
    realtime_data = realtime.get(code, {})

    # 3. 最新 AI 分析（如无则为 None，模板相应隐藏章节）
    latest_analysis = StockAnalysisHistoryDAO().get_latest_for_code(code)
    ai_parsed = None
    if latest_analysis and latest_analysis.get("ai_analysis"):
        try:
            ai_parsed = json.loads(latest_analysis["ai_analysis"])
        except (json.JSONDecodeError, TypeError):
            ai_parsed = None

    # 4. 最新交易策略
    trade_parsed = None
    if latest_analysis and latest_analysis.get("ai_trade_strategy"):
        try:
            trade_parsed = json.loads(latest_analysis["ai_trade_strategy"])
        except (json.JSONDecodeError, TypeError):
            trade_parsed = None

    # 5. 历次 AI 分析时间线（按日期倒序）
    analysis_history = StockAnalysisHistoryDAO().get_history(code, limit=20)

    # 6. 动态财务历史（年报，最新在前）
    annual_reports = FinancialHistoryDAO().get_annual_reports(code)
    financial_summary = FinancialSummaryDAO().get(code)

    # 7. 在池状态（如在池）
    in_watchlist = AiWatchlistDAO().get_by_code(code)
    watchlist_history = []
    if in_watchlist:
        watchlist_history = AiWatchlistHistoryDAO().list_by_code(code)

    config = load_config()
    page_title = config.get("web", {}).get("page_title", "价值投资选股看板")

    return templates.TemplateResponse(request, "stock_detail.html", {
        "request": request,
        "page_title": page_title,
        "code": code,
        "snapshot": snapshot,
        "realtime": realtime_data,
        "latest_analysis": latest_analysis,
        "ai_parsed": ai_parsed,
        "trade_parsed": trade_parsed,
        "analysis_history": analysis_history,
        "annual_reports": annual_reports,
        "financial_summary": financial_summary,
        "in_watchlist": in_watchlist,
        "watchlist_history": watchlist_history,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })
```

- [ ] **Step 4: 创建最小模板（只有 Header + 快照条 + 占位章节）**

创建 `src/web/templates/stock_detail.html`：

```html
{% extends "base.html" %}
{% block title %}{{ snapshot.name }} {{ code }} - 单股详情{% endblock %}
{% block content %}
<div class="stock-detail-page">
    <div class="back-link">
        <a href="/">← 返回看板</a>
    </div>

    <!-- 1. Header -->
    <header class="sd-header">
        <div class="sd-title">
            <h1>{{ snapshot.name }}</h1>
            <span class="sd-code">{{ code }}</span>
            <span class="sd-sector">{{ snapshot.sector or '--' }}</span>
        </div>
        <div class="sd-price">
            {% if realtime.get("current_price") is not none %}
            <span class="sd-price-value">¥{{ "%.2f"|format(realtime["current_price"]) }}</span>
            {% elif snapshot.current_price %}
            <span class="sd-price-value">¥{{ "%.2f"|format(snapshot.current_price) }}</span>
            {% else %}
            <span class="sd-price-value">--</span>
            {% endif %}
            {% if realtime.get("change_percent") is not none %}
            <span class="sd-price-change {{ 'up' if realtime['change_percent'] >= 0 else 'down' }}">
                {{ "%+.2f"|format(realtime["change_percent"]) }}%
            </span>
            {% endif %}
        </div>
    </header>

    <!-- 2. 关键指标快照条 -->
    <section class="sd-snapshot-bar">
        <div class="sd-metric"><span class="label">PE</span><span class="value">{{ "%.1f"|format(snapshot.pe) if snapshot.pe is not none else '--' }}</span></div>
        <div class="sd-metric"><span class="label">PB</span><span class="value">{{ "%.2f"|format(snapshot.pb) if snapshot.pb is not none else '--' }}</span></div>
        <div class="sd-metric"><span class="label">ROE</span><span class="value">{{ "%.1f"|format(snapshot.roe) if snapshot.roe is not none else '--' }}%</span></div>
        <div class="sd-metric"><span class="label">营收增</span><span class="value">{{ "%+.1f"|format(snapshot.revenue_growth) if snapshot.revenue_growth is not none else '--' }}%</span></div>
        <div class="sd-metric"><span class="label">利润增</span><span class="value">{{ "%+.1f"|format(snapshot.profit_growth) if snapshot.profit_growth is not none else '--' }}%</span></div>
        <div class="sd-metric"><span class="label">市值</span><span class="value">{{ "%.0f"|format(snapshot.market_cap) if snapshot.market_cap is not none else '--' }}亿</span></div>
        <div class="sd-metric"><span class="label">负债率</span><span class="value">{{ "%.1f"|format(snapshot.debt_ratio) if snapshot.debt_ratio is not none else '--' }}%</span></div>
        <div class="sd-metric"><span class="label">股息率</span><span class="value">{{ "%.2f"|format(snapshot.dividend_yield) if snapshot.dividend_yield is not none else '--' }}%</span></div>
    </section>

    <!-- 后续章节在后续 Task 中添加 -->
</div>
{% endblock %}
```

注意：如果项目没有 `base.html`，则直接在 `stock_detail.html` 写完整的 `<!DOCTYPE html>` 结构。需要先检查 `templates/` 目录是否有 `base.html`。若没有，则复制 `index.html` 顶部 `<!DOCTYPE html>` 到 `</head>` 的部分，把 `<body>` 内容用上面的 `{% block content %}` 替换。

- [ ] **Step 5: 检查 base.html 是否存在并调整模板**

Run: `cd g:\trae\stock-dashboard-github && dir src\web\templates\base.html`

如果不存在（expected: File not found），则将 `stock_detail.html` 改成完整 HTML 结构：

参考 `src/web/templates/index.html` 顶部到 `</head>` 的内容，body 内容只保留上面的 `{% block content %}` 部分。

- [ ] **Step 6: 运行测试确认通过**

Run: `cd g:\trae\stock-dashboard-github && python -m pytest tests/web/test_routes_stock_detail.py -v`

Expected: PASS (3 tests)

- [ ] **Step 7: 提交**

```bash
cd g:\trae\stock-dashboard-github
git add src/web/routes.py src/web/templates/stock_detail.html tests/web/test_routes_stock_detail.py
git commit -m "feat: 新增 /stock/{code} 单股详情页路由（最小版）

- 路由：GET /stock/{code}，code 必须 6 位数字，stock_snapshot 查不到返回 404
- 模板：stock_detail.html（最小版，只有 Header + 关键指标快照条）
- 数据：StockSnapshotDAO.get_by_code + 实时行情缓存 + StockAnalysisHistoryDAO + FinancialHistoryDAO + AiWatchlistDAO
- 后续 Task 补全 AI 章节 / 财务历史 / 时间线 / 在池状态"
```

---

## Task 3: 模板补全 AI 分析章节（3-8：笔记 / 护城河 / 管理层 / 内在价值 / 交易策略 / 逆向思考）

**Files:**
- Modify: `src/web/templates/stock_detail.html`

- [ ] **Step 1: 在 stock_detail.html 关键指标快照条后插入 AI 章节**

在 `<!-- 2. 关键指标快照条 -->` 的 `</section>` 后、`<!-- 后续章节在后续 Task 中添加 -->` 前插入：

```html
    {% if ai_parsed %}

    <!-- 3. 投资人笔记 -->
    {% if ai_parsed.analysis %}
    <section class="sd-section sd-note">
        <h2 class="sd-section-title">投资人笔记</h2>
        <div class="sd-note-body">
            {% for paragraph in ai_parsed.analysis.split('\n') if paragraph.strip() %}
            <p>{{ paragraph.strip() }}</p>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- 4. 护城河评估 -->
    {% if ai_parsed.moat_evaluation %}
    <section class="sd-section">
        <h2 class="sd-section-title">护城河评估</h2>
        <div class="sd-moat-list">
            {% for m in ai_parsed.moat_evaluation %}
            <div class="sd-moat-row">
                <div class="sd-moat-name">{{ m.name|default('--') }}</div>
                <div class="sd-moat-score">
                    {% for i in range(5) %}
                    {% if i < (m.score|default(0)|int) %}●{% else %}○{% endif %}
                    {% endfor %}
                </div>
                <div class="sd-moat-trend">{{ m.trend|default('--') }}</div>
                <div class="sd-moat-evidence">{{ m.evidence|default('') }}</div>
            </div>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- 5. 管理层评分 -->
    {% if ai_parsed.management_score %}
    <section class="sd-section">
        <h2 class="sd-section-title">管理层评分</h2>
        <div class="sd-mgmt">
            <div class="sd-mgmt-row">
                <span class="label">资本配置能力</span>
                <span class="value">{{ ai_parsed.management_score.capital_allocation|default('--') }}/10</span>
            </div>
            <div class="sd-mgmt-row">
                <span class="label">股东友好度</span>
                <span class="value">{{ ai_parsed.management_score.shareholder_friendly|default('--') }}/10</span>
            </div>
            {% if ai_parsed.management_score.summary %}
            <p class="sd-mgmt-summary">{{ ai_parsed.management_score.summary }}</p>
            {% endif %}
        </div>
    </section>
    {% endif %}

    <!-- 6. 内在价值 + 安全边际 -->
    {% if ai_parsed.intrinsic_value %}
    <section class="sd-section">
        <h2 class="sd-section-title">内在价值与安全边际</h2>
        <div class="sd-value-table">
            <div class="sd-value-row">
                <span class="label">保守估值</span>
                <span class="value">{{ ai_parsed.intrinsic_value.conservative|default('--') }} 亿</span>
            </div>
            <div class="sd-value-row highlight">
                <span class="label">基准估值</span>
                <span class="value">{{ ai_parsed.intrinsic_value.base|default('--') }} 亿</span>
            </div>
            <div class="sd-value-row">
                <span class="label">乐观估值</span>
                <span class="value">{{ ai_parsed.intrinsic_value.optimistic|default('--') }} 亿</span>
            </div>
            {% if ai_parsed.intrinsic_value.safety_margin is not none %}
            <div class="sd-value-row">
                <span class="label">安全边际</span>
                <span class="value {{ 'pos' if ai_parsed.intrinsic_value.safety_margin >= 0 else 'neg' }}">
                    {{ "%+.1f"|format(ai_parsed.intrinsic_value.safety_margin) }}%
                </span>
            </div>
            {% endif %}
            {% if ai_parsed.intrinsic_value.method %}
            <div class="sd-value-row">
                <span class="label">方法</span>
                <span class="value">{{ ai_parsed.intrinsic_value.method }}</span>
            </div>
            {% endif %}
        </div>
    </section>
    {% endif %}

    <!-- 7. 交易策略 -->
    {% if trade_parsed %}
    <section class="sd-section">
        <h2 class="sd-section-title">交易策略</h2>
        <div class="sd-trade">
            {% if trade_parsed.signal %}
            <div class="sd-trade-signal">
                <span class="label">Signal</span>
                {% set sig = trade_parsed.signal|upper %}
                <span class="signal-badge signal-{{ sig|lower }}">{{ sig }}</span>
                {% if trade_parsed.confidence %}
                <span class="confidence">置信度: {{ trade_parsed.confidence }}</span>
                {% endif %}
            </div>
            {% endif %}
            {% if trade_parsed.buy_range %}
            <div class="sd-trade-row">
                <span class="label">买入区间</span>
                <span class="value">{{ trade_parsed.buy_range }}</span>
            </div>
            {% endif %}
            {% if trade_parsed.target_price %}
            <div class="sd-trade-row">
                <span class="label">目标价</span>
                <span class="value">{{ trade_parsed.target_price }}</span>
            </div>
            {% endif %}
            {% if trade_parsed.stop_loss %}
            <div class="sd-trade-row">
                <span class="label">止损价</span>
                <span class="value">{{ trade_parsed.stop_loss }}</span>
            </div>
            {% endif %}
            {% if trade_parsed.holding_period %}
            <div class="sd-trade-row">
                <span class="label">持有周期</span>
                <span class="value">{{ trade_parsed.holding_period }}</span>
            </div>
            {% endif %}
        </div>
    </section>
    {% endif %}

    <!-- 8. 逆向思考 -->
    {% if ai_parsed.reverse_thinking %}
    <section class="sd-section">
        <h2 class="sd-section-title">逆向思考 — 什么情况下这家公司会死？</h2>
        <div class="sd-reverse">
            {% if ai_parsed.reverse_thinking is iterable and ai_parsed.reverse_thinking is not string %}
            <ol>
                {% for scenario in ai_parsed.reverse_thinking %}
                <li>{{ scenario }}</li>
                {% endfor %}
            </ol>
            {% else %}
            <p>{{ ai_parsed.reverse_thinking }}</p>
            {% endif %}
        </div>
    </section>
    {% endif %}

    {% endif %}{# end if ai_parsed #}
```

- [ ] **Step 2: 手动验证模板可渲染**

启动 web 服务（开发模式）：

```bash
cd g:\trae\stock-dashboard-github
python -m src.main serve
```

打开浏览器 `http://localhost:8000/stock/000792`，验证：
- 无 AI 分析的股票（如选 stock_snapshot 有但 stock_analysis_history 没有的）：只显示 Header + 快照条，不报错
- 有 AI 分析的股票：显示 3-8 章节中字段存在的部分

如果 pi 上已有 000792 的 AI 分析数据，先在 pi 上验证。

- [ ] **Step 3: 提交**

```bash
cd g:\trae\stock-dashboard-github
git add src/web/templates/stock_detail.html
git commit -m "feat: 单股详情页补全 AI 分析章节（3-8）

- 投资人笔记（多段叙事文本）
- 护城河评估（5 维度，圆点 + 趋势 + 证据）
- 管理层评分（资本配置 / 股东友好度 + 总结）
- 内在价值（保守/基准/乐观 + 安全边际 + 方法）
- 交易策略（Signal badge + 买入区/目标/止损/持有周期）
- 逆向思考（致死场景列表）

无 ai_parsed 时整段隐藏（fallback 到纯数据版）"
```

---

## Task 4: 模板补全动态财务历史章节（9）

**Files:**
- Modify: `src/web/templates/stock_detail.html`

- [ ] **Step 1: 在 AI 章节后、`<!-- 后续章节在后续 Task 中添加 -->` 前插入**

```html
    <!-- 9. 动态财务历史 -->
    {% if annual_reports %}
    <section class="sd-section">
        <h2 class="sd-section-title">动态财务历史</h2>
        {% if financial_summary and financial_summary.data_years %}
        <div class="sd-fin-summary">数据覆盖区间：{{ financial_summary.data_years }}</div>
        {% endif %}

        <table class="sd-fin-table">
            <thead>
                <tr>
                    <th>指标</th>
                    {% for r in annual_reports %}
                    <th>{{ r.report_date[:4] }}</th>
                    {% endfor %}
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td class="metric-name">ROE %</td>
                    {% for r in annual_reports %}
                    <td>{{ "%.1f"|format(r.roe) if r.roe is not none else '--' }}</td>
                    {% endfor %}
                </tr>
                <tr>
                    <td class="metric-name">毛利率 %</td>
                    {% for r in annual_reports %}
                    <td>{{ "%.1f"|format(r.gross_margin) if r.gross_margin is not none else '--' }}</td>
                    {% endfor %}
                </tr>
                <tr>
                    <td class="metric-name">净利率 %</td>
                    {% for r in annual_reports %}
                    <td>{{ "%.1f"|format(r.net_margin) if r.net_margin is not none else '--' }}</td>
                    {% endfor %}
                </tr>
                <tr>
                    <td class="metric-name">营收增长 %</td>
                    {% for r in annual_reports %}
                    <td>{{ "%+.1f"|format(r.revenue_growth) if r.revenue_growth is not none else '--' }}</td>
                    {% endfor %}
                </tr>
                <tr>
                    <td class="metric-name">利润增长 %</td>
                    {% for r in annual_reports %}
                    <td>{{ "%+.1f"|format(r.profit_growth) if r.profit_growth is not none else '--' }}</td>
                    {% endfor %}
                </tr>
                <tr>
                    <td class="metric-name">OCF/股</td>
                    {% for r in annual_reports %}
                    <td>{{ "%.2f"|format(r.ocf_per_share) if r.ocf_per_share is not none else '--' }}</td>
                    {% endfor %}
                </tr>
                <tr>
                    <td class="metric-name">FCF（亿）</td>
                    {% for r in annual_reports %}
                    <td>{{ "%.2f"|format(r.fcf / 1e8) if r.fcf is not none else '--' }}</td>
                    {% endfor %}
                </tr>
                <tr>
                    <td class="metric-name">ROIC %</td>
                    {% for r in annual_reports %}
                    <td>{{ "%.1f"|format(r.roic) if r.roic is not none else '--' }}</td>
                    {% endfor %}
                </tr>
            </tbody>
        </table>

        {% if financial_summary %}
        <div class="sd-fin-derived">
            <h3>衍生汇总指标</h3>
            <div class="sd-derived-grid">
                {% if financial_summary.roe_5y_avg is not none %}
                <div class="sd-derived-cell">
                    <span class="label">ROE 5年均</span>
                    <span class="value">{{ "%.1f"|format(financial_summary.roe_5y_avg) }}%</span>
                </div>
                {% endif %}
                {% if financial_summary.roe_10y_avg is not none %}
                <div class="sd-derived-cell">
                    <span class="label">ROE 10年均</span>
                    <span class="value">{{ "%.1f"|format(financial_summary.roe_10y_avg) }}%</span>
                </div>
                {% endif %}
                {% if financial_summary.roe_volatility is not none %}
                <div class="sd-derived-cell">
                    <span class="label">ROE 波动率</span>
                    <span class="value">{{ "%.2f"|format(financial_summary.roe_volatility) }}</span>
                </div>
                {% endif %}
                {% if financial_summary.roe_improvement is not none %}
                <div class="sd-derived-cell">
                    <span class="label">ROE 改善</span>
                    <span class="value {{ 'pos' if financial_summary.roe_improvement >= 0 else 'neg' }}">
                        {{ "%+.2f"|format(financial_summary.roe_improvement) }}
                    </span>
                </div>
                {% endif %}
                {% if financial_summary.fcf_5y_sum is not none %}
                <div class="sd-derived-cell">
                    <span class="label">FCF 5年累计</span>
                    <span class="value">{{ "%.2f"|format(financial_summary.fcf_5y_sum / 1e8) }}亿</span>
                </div>
                {% endif %}
                {% if financial_summary.fcf_10y_sum is not none %}
                <div class="sd-derived-cell">
                    <span class="label">FCF 10年累计</span>
                    <span class="value">{{ "%.2f"|format(financial_summary.fcf_10y_sum / 1e8) }}亿</span>
                </div>
                {% endif %}
                {% if financial_summary.share_dilution_5y is not none %}
                <div class="sd-derived-cell">
                    <span class="label">5年股本稀释</span>
                    <span class="value">{{ "%+.2f"|format(financial_summary.share_dilution_5y) }}%</span>
                </div>
                {% endif %}
                {% if financial_summary.roic_5y_avg is not none %}
                <div class="sd-derived-cell">
                    <span class="label">ROIC 5年均</span>
                    <span class="value">{{ "%.1f"|format(financial_summary.roic_5y_avg) }}%</span>
                </div>
                {% endif %}
            </div>
        </div>
        {% endif %}
    </section>
    {% endif %}
```

- [ ] **Step 2: 提交**

```bash
cd g:\trae\stock-dashboard-github
git add src/web/templates/stock_detail.html
git commit -m "feat: 单股详情页补全动态财务历史章节

- 年报数据表格（ROE/毛利/净利/营收增/利润增/OCF/FCF/ROIC，按年份横排）
- 衍生汇总指标卡片网格（5y/10y 均值 + 波动率 + 改善 + FCF 累计 + 股本稀释）
- 不绑定 5/10 年固定数字：表格按公司实际有数据的年数动态展示
- 无 financial_history 数据则整段隐藏"
```

---

## Task 5: 模板补全技术分析扩展位（10）+ 历次分析时间线（11）+ 在池状态（12）

**Files:**
- Modify: `src/web/templates/stock_detail.html`

- [ ] **Step 1: 在动态财务历史章节后插入剩余三段**

```html
    <!-- 10. 技术分析扩展位（预留，后期填 K 线图 / 技术指标 / 资金流向 / F10 等） -->
    <section class="sd-section sd-placeholder" id="technical-analysis" data-placeholder>
        <!-- 后期参考东方财富、同花顺陆续补全：K 线图 / MACD / KDJ / RSI / 资金流向 / 龙虎榜 / 行业对比 / F10 -->
    </section>

    <!-- 11. 历次 AI 分析时间线 -->
    <section class="sd-section">
        <h2 class="sd-section-title">历次 AI 分析时间线</h2>
        {% if analysis_history %}
        {# 评分趋势 SVG 折线图（无依赖，纯 Jinja 生成） #}
        {% set scores = analysis_history|map(attribute='score')|list %}
        {% set max_score = scores|max if scores else 100 %}
        {% set min_score = scores|min if scores else 0 %}
        {% set score_range = (max_score - min_score) if max_score != min_score else 1 %}
        {% set svg_w = 600 %}
        {% set svg_h = 120 %}
        {% set pad = 20 %}
        {% set plot_w = svg_w - 2 * pad %}
        {% set plot_h = svg_h - 2 * pad %}
        {% set n = analysis_history|length %}
        {% if n >= 2 %}
        <div class="sd-score-chart">
            <svg width="{{ svg_w }}" height="{{ svg_h }}" viewBox="0 0 {{ svg_w }} {{ svg_h }}">
                <line x1="{{ pad }}" y1="{{ svg_h - pad }}" x2="{{ svg_w - pad }}" y2="{{ svg_h - pad }}" stroke="#ccc" stroke-width="1"/>
                <line x1="{{ pad }}" y1="{{ pad }}" x2="{{ pad }}" y2="{{ svg_h - pad }}" stroke="#ccc" stroke-width="1"/>
                {% for h in analysis_history|reverse %}
                {% set i = loop.index0 %}
                {% set x = pad + (plot_w * i / (n - 1)) if n > 1 else pad + plot_w / 2 %}
                {% set y = svg_h - pad - (plot_h * (h.score - min_score) / score_range) if h.score is not none else svg_h - pad %}
                {% if i == 0 %}
                <path class="score-line" d="M {{ x }} {{ y }}"
                      fill="none" stroke="var(--color-up, #4caf50)" stroke-width="2"/>
                {% else %}
                {# 输出 L 命令到 path 的 d 属性需要拼接，Jinja 用 namespace #}
                {% endif %}
                <circle cx="{{ x }}" cy="{{ y }}" r="4" fill="var(--color-up, #4caf50)">
                    <title>{{ h.analysis_date }} | 评分 {{ h.score }} | Signal {{ h.signal|default('--') }}</title>
                </circle>
                {% endfor %}
            </svg>
        </div>
        {% endif %}

        <div class="sd-timeline">
            {% for h in analysis_history %}
            <div class="sd-timeline-item">
                <div class="sd-timeline-date">{{ h.analysis_date }}</div>
                <div class="sd-timeline-score">评分 {{ h.score|default('--') }}</div>
                {% if h.ai_analysis %}
                {% set ai = h.ai_analysis|tojson|safe %}
                <div class="sd-timeline-note">
                    {% try %}
                    {{ (h.ai_analysis|from_json).analysis|default('')|truncate(120) }}
                    {% except %}
                    {{ h.ai_analysis|truncate(120) }}
                    {% endtry %}
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        {% else %}
        <p class="sd-empty">暂无 AI 分析历史</p>
        {% endif %}
    </section>

    <!-- 12. 在池状态 -->
    {% if in_watchlist %}
    <section class="sd-section">
        <h2 class="sd-section-title">在池状态</h2>
        <div class="sd-watchlist-info">
            <div class="sd-wl-row">
                <span class="label">当前状态</span>
                <span class="value pos">✓ 在池（在池 {{ in_watchlist.review_count|default(1) }} 周）</span>
            </div>
            <div class="sd-wl-row">
                <span class="label">调入日期</span>
                <span class="value">{{ in_watchlist.added_at }}</span>
            </div>
            {% if in_watchlist.added_reason %}
            <div class="sd-wl-row">
                <span class="label">调入理由</span>
                <span class="value">{{ in_watchlist.added_reason }}</span>
            </div>
            {% endif %}
            {% if in_watchlist.ai_confidence %}
            <div class="sd-wl-row">
                <span class="label">AI 置信度</span>
                <span class="value">{{ in_watchlist.ai_confidence }}</span>
            </div>
            {% endif %}
        </div>

        {% if watchlist_history %}
        <div class="sd-wl-history">
            <h3>调入调出历史</h3>
            <table class="sd-wl-history-table">
                <thead>
                    <tr><th>日期</th><th>动作</th><th>原因</th></tr>
                </thead>
                <tbody>
                    {% for wh in watchlist_history %}
                    <tr>
                        <td>{{ wh.action_date }}</td>
                        <td>{{ wh.action }}</td>
                        <td>{{ wh.reason|default('') }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
    </section>
    {% endif %}
```

- [ ] **Step 2: 在 routes.py 注册 from_json Jinja 过滤器（如不存在）**

检查 `src/web/routes.py` 是否已注册 `from_json` 过滤器。如果没有，在 `templates.env.filters['markdown_to_html'] = _markdown_to_html` 这一行后添加：

```python
import json as _json

def _from_json(text):
    """Jinja2 过滤器：JSON 字符串 → dict"""
    if not text:
        return {}
    try:
        return _json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}

templates.env.filters['from_json'] = _from_json
```

并修复模板里的 `try/except` 块——Jinja2 不支持 try/except。改成只用 `from_json` 过滤器：

```html
{% if h.ai_analysis %}
{% set ai_obj = h.ai_analysis|from_json %}
<div class="sd-timeline-note">
    {{ ai_obj.analysis|default(h.ai_analysis)|truncate(120) }}
</div>
{% endif %}
```

- [ ] **Step 3: 修复 SVG 折线图（Jinja 不支持在循环中拼接 path d 属性）**

把 Task 5 Step 1 里的 SVG 折线图改成用 `<polyline>` 简化：

```html
{% if n >= 2 %}
<div class="sd-score-chart">
    <svg width="{{ svg_w }}" height="{{ svg_h }}" viewBox="0 0 {{ svg_w }} {{ svg_h }}">
        <line x1="{{ pad }}" y1="{{ svg_h - pad }}" x2="{{ svg_w - pad }}" y2="{{ svg_h - pad }}" stroke="#ccc" stroke-width="1"/>
        <line x1="{{ pad }}" y1="{{ pad }}" x2="{{ pad }}" y2="{{ svg_h - pad }}" stroke="#ccc" stroke-width="1"/>
        {% set points = [] %}
        {% for h in analysis_history|reverse %}
        {% set i = loop.index0 %}
        {% set x = pad + (plot_w * i / (n - 1)) if n > 1 else pad + plot_w / 2 %}
        {% set y = svg_h - pad - (plot_h * (h.score - min_score) / score_range) if h.score is not none else svg_h - pad %}
        {% set _ = points.append((x, y)) %}
        <circle cx="{{ x }}" cy="{{ y }}" r="4" fill="var(--color-up, #4caf50)">
            <title>{{ h.analysis_date }} | 评分 {{ h.score }} | Signal {{ h.signal|default('--') }}</title>
        </circle>
        {% endfor %}
        <polyline points="
            {%- for x, y in points -%}
            {{ x }},{{ y }}{% if not loop.last %} {% endif %}
            {%- endfor -%}
        " fill="none" stroke="var(--color-up, #4caf50)" stroke-width="2"/>
    </svg>
</div>
{% endif %}
```

- [ ] **Step 4: 检查 AiWatchlistDAO.get_by_code 和 AiWatchlistHistoryDAO.list_by_code 是否存在**

Run: `cd g:\trae\stock-dashboard-github && python -c "from src.models.ai_watchlist import AiWatchlistDAO, AiWatchlistHistoryDAO; print(hasattr(AiWatchlistDAO, 'get_by_code')); print(hasattr(AiWatchlistHistoryDAO, 'list_by_code'))"`

Expected: `True True`

如果 False，则需要在 `src/models/ai_watchlist.py` 补充对应方法。先用 Grep 看现有方法名：

```bash
cd g:\trae\stock-dashboard-github
```

用 Grep tool 搜 `class AiWatchlistDAO` 和 `class AiWatchlistHistoryDAO` 的方法定义，确认是否有 `get_by_code` / `list_by_code` 或等价方法。如缺失则在 Task 1 之前补一个 Task 补 DAO 方法（不要在 Task 5 里临时改）。

- [ ] **Step 5: 运行所有测试**

Run: `cd g:\trae\stock-dashboard-github && python -m pytest tests/web/test_routes_stock_detail.py -v`

Expected: PASS (3 tests)

- [ ] **Step 6: 提交**

```bash
cd g:\trae\stock-dashboard-github
git add src/web/templates/stock_detail.html src/web/routes.py
git commit -m "feat: 单股详情页补全剩余章节（10-12）

- 技术分析扩展位（预留空 section，后期填 K 线 / 技术指标 / F10）
- 历次 AI 分析时间线（评分趋势 SVG polyline + 时间线列表）
- 在池状态（当前状态 + 调入日期 + 理由 + 调入调出历史表）

并注册 from_json Jinja 过滤器用于解析历史 ai_analysis JSON。"
```

---

## Task 6: 模板补全 CSS 样式

**Files:**
- Modify: `src/web/templates/stock_detail.html`

- [ ] **Step 1: 在模板 `<head>` 内或独立 `<style>` 块内补全样式**

在 `stock_detail.html` 的 `{% block content %}` 前或 `<head>` 内加 `<style>`：

```html
<style>
.stock-detail-page {
    max-width: 900px;
    margin: 0 auto;
    padding: 24px 20px 60px;
}
.back-link { margin-bottom: 16px; }
.back-link a { color: var(--text-secondary); text-decoration: none; }
.back-link a:hover { color: var(--text-primary); }

.sd-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border-light, #eee);
    margin-bottom: 24px;
}
.sd-title h1 { display: inline-block; font-size: 28px; margin: 0; }
.sd-code { margin-left: 12px; color: var(--text-tertiary); font-size: 14px; }
.sd-sector { margin-left: 8px; color: var(--text-tertiary); font-size: 13px; }
.sd-price-value { font-size: 24px; font-weight: 600; }
.sd-price-change { margin-left: 12px; font-size: 14px; }
.sd-price-change.up { color: var(--color-up, #e53935); }
.sd-price-change.down { color: var(--color-down, #43a047); }

.sd-snapshot-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 16px 32px;
    padding: 16px 20px;
    background: var(--bg-secondary, #fafbfc);
    border-radius: 8px;
    margin-bottom: 32px;
}
.sd-metric { display: flex; flex-direction: column; gap: 2px; }
.sd-metric .label { font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; }
.sd-metric .value { font-size: 18px; font-weight: 500; }

.sd-section { margin-bottom: 40px; }
.sd-section-title {
    font-size: 18px;
    margin: 0 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-light, #eee);
}

.sd-note-body { padding-left: 16px; border-left: 3px solid var(--color-up, #4caf50); }
.sd-note-body p { margin: 0 0 12px; line-height: 1.7; }

.sd-moat-row {
    display: grid;
    grid-template-columns: 120px 100px 80px 1fr;
    gap: 16px;
    padding: 8px 0;
    border-bottom: 1px dashed var(--border-light, #eee);
}
.sd-moat-score { color: var(--color-up, #4caf50); letter-spacing: 2px; }
.sd-moat-trend { color: var(--text-secondary); font-size: 13px; }
.sd-moat-evidence { color: var(--text-secondary); font-size: 13px; }

.sd-value-table { display: flex; flex-direction: column; gap: 8px; }
.sd-value-row { display: flex; justify-content: space-between; padding: 6px 0; }
.sd-value-row.highlight { background: var(--bg-hover, #f5f5f5); padding: 6px 12px; border-radius: 4px; }
.sd-value-row .label { color: var(--text-secondary); }
.sd-value-row .value.pos { color: var(--color-up, #4caf50); }
.sd-value-row .value.neg { color: var(--color-down, #f44336); }

.sd-trade-signal { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.signal-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 16px;
}
.signal-buy { background: #ffebee; color: #c62828; }
.signal-hold { background: #fff8e1; color: #f57c00; }
.signal-avoid { background: #eceff1; color: #455a64; }
.sd-trade .confidence { color: var(--text-secondary); font-size: 13px; }
.sd-trade-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed var(--border-light, #eee); }

.sd-reverse ol { padding-left: 20px; }
.sd-reverse li { margin-bottom: 8px; line-height: 1.7; }

.sd-fin-table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
.sd-fin-table th, .sd-fin-table td {
    padding: 8px 12px;
    text-align: right;
    border-bottom: 1px solid var(--border-light, #eee);
    font-size: 13px;
}
.sd-fin-table th:first-child, .sd-fin-table td:first-child { text-align: left; }
.sd-fin-table .metric-name { color: var(--text-secondary); }
.sd-fin-summary { color: var(--text-tertiary); font-size: 12px; margin-bottom: 12px; }

.sd-fin-derived { padding: 16px 20px; background: var(--bg-secondary, #fafbfc); border-radius: 8px; }
.sd-fin-derived h3 { font-size: 14px; margin: 0 0 12px; color: var(--text-secondary); }
.sd-derived-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
.sd-derived-cell { display: flex; flex-direction: column; gap: 2px; }
.sd-derived-cell .label { font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; }
.sd-derived-cell .value { font-size: 16px; font-weight: 500; }
.sd-derived-cell .value.pos { color: var(--color-up, #4caf50); }
.sd-derived-cell .value.neg { color: var(--color-down, #f44336); }

.sd-placeholder {
    padding: 32px;
    text-align: center;
    color: var(--text-tertiary);
    font-size: 13px;
    border: 1px dashed var(--border-light, #ddd);
    border-radius: 8px;
}

.sd-score-chart { margin-bottom: 16px; }
.sd-score-chart svg { max-width: 100%; height: auto; }
.sd-timeline-item {
    display: grid;
    grid-template-columns: 100px 80px 1fr;
    gap: 16px;
    padding: 8px 0;
    border-bottom: 1px dashed var(--border-light, #eee);
}
.sd-timeline-date { color: var(--text-tertiary); font-size: 13px; }
.sd-timeline-score { font-weight: 500; }
.sd-timeline-note { color: var(--text-secondary); font-size: 13px; }
.sd-empty { color: var(--text-tertiary); font-style: italic; }

.sd-watchlist-info { padding: 16px 20px; background: var(--bg-secondary, #fafbfc); border-radius: 8px; margin-bottom: 16px; }
.sd-wl-row { display: flex; justify-content: space-between; padding: 6px 0; }
.sd-wl-row .label { color: var(--text-secondary); }
.sd-wl-row .value.pos { color: var(--color-up, #4caf50); }
.sd-wl-history h3 { font-size: 14px; margin: 16px 0 8px; color: var(--text-secondary); }
.sd-wl-history-table { width: 100%; border-collapse: collapse; }
.sd-wl-history-table th, .sd-wl-history-table td {
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border-light, #eee);
    font-size: 13px;
}
</style>
```

- [ ] **Step 2: 提交**

```bash
cd g:\trae\stock-dashboard-github
git add src/web/templates/stock_detail.html
git commit -m "style: 单股详情页补全完整 CSS

- 单栏布局（max-width: 900px 居中）
- Header / 快照条 / 章节 / 财务表格 / 衍生卡片网格 / 时间线 / 在池状态样式
- Signal badge 颜色：BUY 红 / HOLD 橙 / AVOID 灰
- 上涨/下跌色：跟随 --color-up / --color-down CSS 变量"
```

---

## Task 7: 卡片可点（观察池 + 候选股）

**Files:**
- Modify: `src/web/templates/_watchlist_card.html`
- Modify: `src/web/templates/_stock_list.html`
- Modify: `src/web/templates/index.html`
- Modify: `src/web/templates/candidates.html`

- [ ] **Step 1: 修改 _watchlist_card.html — 卡片可点**

把第 4 行的：

```html
<div class="watchlist-card" data-code="{{ stock.code }}">
```

改成：

```html
<div class="watchlist-card clickable" data-code="{{ stock.code }}"
     onclick="location.href='/stock/{{ stock.code }}'">
```

- [ ] **Step 2: 修改 _stock_list.html — 卡片可点 + watch-btn stopPropagation**

把第 2 行的：

```html
<div class="stock-card" data-code="{{ stock.code }}">
```

改成：

```html
<div class="stock-card clickable" data-code="{{ stock.code }}"
     onclick="location.href='/stock/{{ stock.code }}'">
```

并把第 8-10 行的 watch-btn 改成（加 `onclick="event.stopPropagation()"`）：

```html
<button class="watch-btn {{ 'watched' if stock.watched else '' }}"
        onclick="event.stopPropagation(); toggleWatch('{{ stock.code }}', this)"
        title="钉选后不受 Top20 限制，持续跟踪">{{ '已钉' if stock.watched else '钉选' }}</button>
```

- [ ] **Step 3: 修改 index.html — 补 cursor:pointer 样式**

在 `index.html` 现有 `.stock-card:hover` 样式（约第 375 行）后添加：

```css
.stock-card.clickable, .watchlist-card.clickable {
    cursor: pointer;
}
```

找到 `.watchlist-card` 样式块（如果存在），在其 hover 状态加 cursor:pointer。如果找不到，则把上面那段直接追加到 `.stock-card:hover` 后。

- [ ] **Step 4: 修改 candidates.html — 补 cursor:pointer 样式**

在 `candidates.html` 的 `.stock-card:hover` 后添加同样的：

```css
.stock-card.clickable {
    cursor: pointer;
}
```

- [ ] **Step 5: 手动验证**

启动 web 服务后，浏览器打开：
- `http://localhost:8000/` — 点击 AI 观察池卡片，应跳转到 `/stock/{code}`
- `http://localhost:8000/candidates` — 点击候选股卡片，应跳转
- 卡片 hover 时鼠标变成手型
- 点击"钉选"按钮不触发跳转

- [ ] **Step 6: 提交**

```bash
cd g:\trae\stock-dashboard-github
git add src/web/templates/_watchlist_card.html src/web/templates/_stock_list.html src/web/templates/index.html src/web/templates/candidates.html
git commit -m "feat: 观察池/候选股卡片可点击进入单股详情页

- 整张卡片 onclick 跳转 /stock/{code}
- .clickable class 提供 cursor:pointer
- watch-btn 加 event.stopPropagation() 防止钉选时误触发跳转"
```

---

## Task 8: 端到端验证 + 部署到 pi

**Files:** 无代码改动，验证 + 部署

- [ ] **Step 1: 本地运行所有测试**

Run: `cd g:\trae\stock-dashboard-github && python -m pytest tests/ -v`

Expected: 全部 PASS

- [ ] **Step 2: 本地手动验证**

```bash
cd g:\trae\stock-dashboard-github
python -m src.main serve
```

浏览器测试：
1. `/` 首页 → 点 AI 观察池 5 张卡片，每张都能跳转到 `/stock/{code}`
2. `/candidates` 候选股 → 点 25 张卡片，每张都能跳转
3. `/stock/000792`（有 AI 分析）：12 章节中数据存在的都显示
4. `/stock/000001`（无 AI 分析）：只显示 Header + 快照条 + 财务历史（如有），不报错
5. `/stock/abc123` → 404
6. `/stock/12345` → 404
7. 浏览器后退按钮能返回原页面
8. 点"钉选"按钮不触发跳转

- [ ] **Step 3: 同步到 pi**

```bash
cd g:\trae\stock-dashboard-github
git push origin main

# 同步到 pi（参考之前 rsync over SSH 的部署方式）
rsync -avz --delete --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' --exclude='data/db' g:\trae\stock-dashboard-github\ pi@192.168.50.142:/home/pi/stock-dashboard/
```

如果 rsync 命令在 PowerShell 下报错，改用：

```powershell
cd g:\trae\stock-dashboard-github
ssh pi@192.168.50.142 "sudo systemctl stop stock-dashboard"
# 然后用 scp 或 rsync 同步代码
ssh pi@192.168.50.142 "sudo systemctl start stock-dashboard"
```

- [ ] **Step 4: pi 上验证**

```bash
ssh pi@192.168.50.142
sudo systemctl status stock-dashboard
# 应为 active (running)
```

浏览器打开 `http://192.168.50.142:8000/stock/000792`，验证：
- 12 章节都正确渲染
- 评分趋势 SVG 折线图显示
- 财务历史表格显示
- 在池状态正确

- [ ] **Step 5: 收集测试问题**

记录任何渲染异常 / 数据缺失 / 样式问题，作为后续迭代输入。

---

## Self-Review

**1. Spec coverage 检查：**

| Spec 章节 | 对应 Task |
|---|---|
| 路由与入口（/stock/{code}、404、卡片可点） | Task 2（路由）+ Task 7（卡片可点） |
| 数据来源（snapshot / 实时行情 / 最新 AI / 历史 / 财务 / 在池） | Task 2 路由数据准备 |
| 章节 1. Header | Task 2 模板 |
| 章节 2. 关键指标快照条 | Task 2 模板 |
| 章节 3-8（笔记 / 护城河 / 管理层 / 内在价值 / 交易策略 / 逆向思考） | Task 3 |
| 章节 9. 动态财务历史 | Task 4 |
| 章节 10. 技术分析扩展位 | Task 5 |
| 章节 11. 历次分析时间线（含 SVG） | Task 5 |
| 章节 12. 在池状态 | Task 5 |
| 无 AI 分析的 Fallback | Task 3（{% if ai_parsed %}）+ Task 4（{% if annual_reports %}）+ Task 5（{% if in_watchlist %}） |
| 实施范围 — 新增 StockSnapshotDAO.get_by_code | Task 1 |
| CSS 样式 | Task 6 |

无遗漏。

**2. Placeholder 扫描：**

- Task 5 Step 1 有"技术分析扩展位"占位 section — 这是 spec 要求的预留扩展位，不是 placeholder（带明确注释"后期参考东方财富、同花顺陆续补全"），通过。
- Task 2 Step 5 提到"如果项目没有 base.html" — 这是计划内的条件分支，已经写了对应的 fallback（"复制 index.html 顶部到 </head>"），通过。
- Task 5 Step 4 提到"如缺失则在 Task 1 之前补一个 Task 补 DAO 方法" — 这是潜在前置依赖检查，通过 Grep 可以确认；如果确实缺失，执行时会先补 DAO 方法再继续。

无真 placeholder。

**3. Type consistency 检查：**

- Task 2 路由返回 `latest_analysis` / `ai_parsed` / `trade_parsed` / `analysis_history` / `annual_reports` / `financial_summary` / `in_watchlist` / `watchlist_history` — Task 3-5 模板使用变量名一致 ✓
- Task 1 `StockSnapshotDAO.get_by_code(code)` 返回 `Optional[dict]` — Task 2 调用 `snapshot = StockSnapshotDAO().get_by_code(code)`，模板用 `snapshot.name` / `snapshot.sector` / `snapshot.pe` 等 — 与 `stock_snapshot` 表字段一致 ✓
- Task 5 Step 2 注册 `from_json` 过滤器 — Task 5 Step 1 模板使用 `h.ai_analysis|from_json` — 一致 ✓
- Task 5 模板用 `AiWatchlistDAO().get_by_code(code)` — 需在 Task 2 路由中调用，已在 Task 2 Step 3 代码中包含 ✓

无类型不一致。

**4. 潜在问题：**

- AiWatchlistHistoryDAO.list_by_code 方法是否存在 — Task 5 Step 4 已有检查步骤，如缺失会提前发现
- from_json 过滤器注册位置 — Task 5 Step 2 已明确"在 markdown_to_html 注册行后添加"
- Jinja2 不支持 try/except — Task 5 Step 2 已修复，改成只用 from_json 过滤器
- SVG path d 拼接问题 — Task 5 Step 3 已修复，改成 polyline

执行注意：Task 5 Step 1 的代码块里包含 try/except，应直接用 Step 2/3 修复后的版本。执行时把 Step 1 + Step 2 + Step 3 合并为一次完整的模板修改。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-21-stock-detail-page.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 每个 Task 派一个 fresh subagent 执行，Task 之间审查，迭代快

**2. Inline Execution** - 在当前会话用 executing-plans skill 批量执行，带 checkpoint 审查

**Which approach?**
