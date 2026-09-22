# AI 观察池 + 投资笔记 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现固定 5 只的 AI 观察池 + 周度投资笔记，取代 UI 上的 Top 20 表格，让 AI 主动选择观察股并写叙事性笔记。

**Architecture:** 新增 `WatchlistReviewer` 复盘引擎，混合硬规则预过滤 + LLM 自主决策。周六 00:00 由 scheduler 触发，读最近 4 周 screening_result 作为候选池，硬规则强制调出恶化股票，LLM 在剩余位置选 5 只 + 写笔记。新增 3 张表（`ai_watchlist` / `ai_watchlist_history` / `ai_journal`），3 个 DAO，7 个新路由，2 个新模板。前端 Top 20 表格被观察池卡片网格取代。

**Tech Stack:** Python 3.11 + FastAPI + Jinja2 + SQLite + httpx（复用 `FreeModelPool`）+ pytest

**Spec:** `docs/superpowers/specs/2026-07-20-ai-watchlist-design.md`

---

## 文件结构

### 新建文件

| 文件 | 职责 |
|---|---|
| `src/models/ai_watchlist.py` | 3 个 DAO：`AiWatchlistDAO` / `AiWatchlistHistoryDAO` / `AiJournalDAO` |
| `src/analyzer/watchlist_reviewer.py` | 复盘引擎 `WatchlistReviewer` + 4 条硬规则 + LLM prompt |
| `src/web/templates/_watchlist_card.html` | 观察池卡片 partial（5 只网格） |
| `src/web/templates/journal.html` | 投资笔记页（最新一篇 + 历史归档 sidebar） |
| `tests/models/test_ai_watchlist.py` | DAO 单测 |
| `tests/analyzer/test_watchlist_reviewer.py` | 复盘引擎单测（硬规则 + prompt + 落库） |
| `tests/web/test_routes_journal.py` | 路由单测 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/models/database.py` | `SCHEMA_SQL` 加 3 张表；`init_database()` 调用不变（`executescript` 自动建表） |
| `src/scheduler.py` | `MarketScheduler` 加 `_last_review_date` + `_check_weekly_review()` + `_run_review()`；`_run()` 主循环调用 |
| `src/web/routes.py` | 加 7 个路由：`/journal`、`/journal/<date>`、`/api/ai-watchlist`、`/api/ai-watchlist/history`、`/api/journal/latest`、`/api/journal/<date>`、`/api/journal/list` |
| `src/web/templates/index.html` | 移除候选池 tab 下的 `_stock_list.html`，改为 `_watchlist_card.html`；tab 改为"AI 观察池 / 钉选股票" |
| `config/config.yaml` | 新增 `ai_review` 配置段 |

---

## Task 1: 数据库 schema + DAO 层

**Files:**
- Modify: `src/models/database.py:52-236`（SCHEMA_SQL 末尾追加 3 张表）
- Create: `src/models/ai_watchlist.py`
- Create: `tests/models/test_ai_watchlist.py`
- Create: `tests/models/__init__.py`（如果不存在）

### Step 1.1: 在 SCHEMA_SQL 末尾追加 3 张表

- [ ] 编辑 `src/models/database.py`，在 `SCHEMA_SQL` 字符串末尾（`pipeline_progress` 表之后、闭合 `"""` 之前）追加：

```sql

-- AI 观察池（当前 5 只状态）
CREATE TABLE IF NOT EXISTS ai_watchlist (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    added_at        TEXT NOT NULL,
    added_reason    TEXT,
    ai_confidence   TEXT,
    last_reviewed   TEXT,
    review_count    INTEGER DEFAULT 1
);

-- AI 观察池历史调整记录
CREATE TABLE IF NOT EXISTS ai_watchlist_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    name            TEXT,
    action          TEXT NOT NULL,
    action_date    TEXT NOT NULL,
    reason          TEXT,
    review_run_id   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_history_code ON ai_watchlist_history(code);
CREATE INDEX IF NOT EXISTS idx_watchlist_history_date ON ai_watchlist_history(action_date);

-- AI 投资笔记
CREATE TABLE IF NOT EXISTS ai_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_date    TEXT NOT NULL UNIQUE,
    run_id          TEXT NOT NULL,
    title           TEXT NOT NULL,
    content_md      TEXT NOT NULL,
    market_snapshot TEXT,
    actions_summary TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_date ON ai_journal(journal_date);
```

### Step 1.2: 创建 DAO 文件

- [ ] 创建 `src/models/ai_watchlist.py`：

```python
"""AI 观察池 + 投资笔记 DAO 层

独立文件避免 database.py 继续膨胀。三张表对应三个 DAO：
- AiWatchlistDAO: 当前观察池状态（固定 5 行，由 AI 保证容量）
- AiWatchlistHistoryDAO: 历史调整记录（add/remove/keep 都记）
- AiJournalDAO: 投资笔记 CRUD
"""

from typing import Optional

from src.models.database import db_conn
from src.utils import now_cn


class AiWatchlistDAO:
    """当前观察池状态（固定 5 只，由 WatchlistReviewer 保证容量）"""

    def get_all(self) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist ORDER BY added_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_code(self, code: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_watchlist WHERE code = ?", (code,)
            ).fetchone()
        return dict(row) if row else None

    def add(self, code: str, name: str, added_reason: str = None,
            ai_confidence: str = None) -> bool:
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO ai_watchlist "
                "(code, name, added_at, added_reason, ai_confidence, last_reviewed, review_count) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                (code, name, now_cn().isoformat(), added_reason, ai_confidence,
                 now_cn().strftime("%Y-%m-%d"))
            )
            return cur.rowcount > 0

    def remove(self, code: str) -> bool:
        with db_conn() as conn:
            cur = conn.execute("DELETE FROM ai_watchlist WHERE code = ?", (code,))
            return cur.rowcount > 0

    def update_reviewed(self, code: str, confidence: str = None):
        """复盘后更新置信度 + 在池周数 + 最后复盘日期"""
        with db_conn() as conn:
            conn.execute(
                "UPDATE ai_watchlist SET last_reviewed = ?, review_count = review_count + 1"
                + (", ai_confidence = ?" if confidence else ""),
                ((now_cn().strftime("%Y-%m-%d"), confidence, code)
                 if confidence else (now_cn().strftime("%Y-%m-%d"), code))
            )

    def clear(self):
        """清空观察池（首次初始化或重置时用）"""
        with db_conn() as conn:
            conn.execute("DELETE FROM ai_watchlist")


class AiWatchlistHistoryDAO:
    """历史调整记录（add/remove/keep 都追加一条）"""

    def append(self, code: str, name: str, action: str, reason: str,
               review_run_id: str, action_date: str = None):
        if action_date is None:
            action_date = now_cn().strftime("%Y-%m-%d")
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO ai_watchlist_history "
                "(code, name, action, action_date, reason, review_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (code, name, action, action_date, reason, review_run_id)
            )

    def list_by_code(self, code: str, limit: int = 50) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist_history WHERE code = ? "
                "ORDER BY action_date DESC, id DESC LIMIT ?",
                (code, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_by_date(self, action_date: str) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist_history WHERE action_date = ? "
                "ORDER BY id ASC",
                (action_date,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 20) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist_history "
                "ORDER BY action_date DESC, id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


class AiJournalDAO:
    """投资笔记 CRUD"""

    def save(self, journal_date: str, run_id: str, title: str,
             content_md: str, market_snapshot: str = None,
             actions_summary: str = None) -> int:
        now = now_cn().isoformat()
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO ai_journal "
                "(journal_date, run_id, title, content_md, market_snapshot, "
                "actions_summary, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (journal_date, run_id, title, content_md,
                 market_snapshot, actions_summary, now)
            )
            return cur.lastrowid

    def get_latest(self) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal ORDER BY journal_date DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_by_date(self, journal_date: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal WHERE journal_date = ?",
                (journal_date,)
            ).fetchone()
        return dict(row) if row else None

    def list_all(self, limit: int = 50) -> list[dict]:
        """轻量列表（仅 date + title，用于 sidebar）"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT id, journal_date, title FROM ai_journal "
                "ORDER BY journal_date DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
```

### Step 1.3: 写 DAO 测试

- [ ] 创建 `tests/models/__init__.py`（空文件）
- [ ] 创建 `tests/models/test_ai_watchlist.py`：

```python
"""AI 观察池 DAO 单测

使用独立临时数据库，避免污染开发库。
"""

import os
import tempfile
import pytest

from src.models import database as db_mod


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """每个测试用独立临时数据库"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: path)
    db_mod.init_database()
    yield
    os.unlink(path)


def test_watchlist_add_get_all():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", "ROE 持续 30%+", "高")
    dao.add("000792", "盐湖股份", "低估值高分红", "中")

    all_items = dao.get_all()
    assert len(all_items) == 2
    codes = {item['code'] for item in all_items}
    assert codes == {"600519", "000792"}

    moutai = dao.get_by_code("600519")
    assert moutai is not None
    assert moutai['name'] == "贵州茅台"
    assert moutai['ai_confidence'] == "高"
    assert moutai['review_count'] == 1


def test_watchlist_remove():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", "测试")
    assert dao.remove("600519") is True
    assert dao.get_by_code("600519") is None
    assert dao.remove("999999") is False


def test_watchlist_update_reviewed():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", ai_confidence="中")
    dao.update_reviewed("600519", confidence="高")

    moutai = dao.get_by_code("600519")
    assert moutai['ai_confidence'] == "高"
    assert moutai['review_count'] == 2


def test_watchlist_clear():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", "测试")
    dao.add("000792", "盐湖股份", "测试")
    dao.clear()
    assert dao.get_all() == []


def test_history_append_and_list():
    from src.models.ai_watchlist import AiWatchlistHistoryDAO
    dao = AiWatchlistHistoryDAO()
    dao.append("600519", "贵州茅台", "keep", "基本面稳定", "run_001", "2026-07-20")
    dao.append("000792", "盐湖股份", "add", "新进候选池", "run_001", "2026-07-20")
    dao.append("002415", "海康威视", "remove", "Signal 转 AVOID", "run_001", "2026-07-20")

    by_date = dao.list_by_date("2026-07-20")
    assert len(by_date) == 3

    by_code = dao.list_by_code("600519")
    assert len(by_code) == 1
    assert by_code[0]['action'] == "keep"

    recent = dao.list_recent(limit=2)
    assert len(recent) == 2


def test_journal_save_and_get():
    from src.models.ai_watchlist import AiJournalDAO
    dao = AiJournalDAO()
    journal_id = dao.save(
        journal_date="2026-07-20",
        run_id="run_001",
        title="本周复盘 - 市场震荡",
        content_md="# 本周复盘\n市场震荡，观察池稳定",
        market_snapshot='{"sh": 3174}',
        actions_summary='{"add": 1, "remove": 1}'
    )
    assert journal_id > 0

    latest = dao.get_latest()
    assert latest is not None
    assert latest['title'] == "本周复盘 - 市场震荡"
    assert latest['journal_date'] == "2026-07-20"

    by_date = dao.get_by_date("2026-07-20")
    assert by_date is not None
    assert by_date['run_id'] == "run_001"

    # 不存在的日期
    assert dao.get_by_date("2020-01-01") is None


def test_journal_list_all():
    from src.models.ai_watchlist import AiJournalDAO
    dao = AiJournalDAO()
    for i in range(5):
        dao.save(
            journal_date=f"2026-07-{i+1:02d}",
            run_id=f"run_{i}",
            title=f"第{i}周",
            content_md="..."
        )
    items = dao.list_all()
    assert len(items) == 5
    # 最新在前
    assert items[0]['journal_date'] == "2026-07-05"
    # list_all 只返回轻量字段
    assert 'content_md' not in items[0]
    assert 'title' in items[0]
```

### Step 1.4: 运行测试验证

- [ ] Run: `python -m pytest tests/models/test_ai_watchlist.py -v`
- Expected: 7 tests PASS

### Step 1.5: Commit

```bash
git add src/models/database.py src/models/ai_watchlist.py tests/models/
git commit -m "feat(ai-watchlist): 数据库 schema + DAO 层

- 新增 ai_watchlist / ai_watchlist_history / ai_journal 三张表
- 新增 AiWatchlistDAO / AiWatchlistHistoryDAO / AiJournalDAO
- DAO 单测 7 个全通过"
```

---

## Task 2: 硬规则层 + 复盘引擎骨架

**Files:**
- Create: `src/analyzer/watchlist_reviewer.py`
- Create: `tests/analyzer/test_watchlist_reviewer.py`
- Create: `tests/analyzer/__init__.py`（如果不存在）

### Step 2.1: 写硬规则测试

- [ ] 创建 `tests/analyzer/test_watchlist_reviewer.py`：

```python
"""WatchlistReviewer 硬规则层单测

4 条硬规则各自触发条件 + 不触发情况。
"""

import pytest

from src.analyzer.watchlist_reviewer import (
    WatchlistReviewer,
    check_signal_avoid,
    check_roe_below,
    check_price_above_buyzone,
    check_roe_collapse,
)


def test_signal_avoid_triggered():
    """规则 1: Signal=AVOID → 强制调出"""
    stock = {"code": "002415", "name": "海康威视", "roe": 15.0}
    analysis = {"trade_strategy": {"signal": "AVOID"}}
    assert check_signal_avoid(stock, analysis) is True


def test_signal_avoid_not_triggered_when_buy():
    stock = {"code": "002415", "name": "海康威视"}
    analysis = {"trade_strategy": {"signal": "BUY"}}
    assert check_signal_avoid(stock, analysis) is False


def test_signal_avoid_not_triggered_when_no_analysis():
    stock = {"code": "002415", "name": "海康威视"}
    assert check_signal_avoid(stock, None) is False


def test_roe_below_triggered():
    """规则 2: ROE < 5% → 强制调出"""
    stock = {"code": "600000", "name": "某股", "roe": 3.5}
    analysis = {}
    assert check_roe_below(stock, analysis, threshold=5) is True


def test_roe_below_not_triggered():
    stock = {"code": "600519", "name": "贵州茅台", "roe": 30.0}
    analysis = {}
    assert check_roe_below(stock, analysis, threshold=5) is False


def test_roe_below_not_triggered_when_none():
    stock = {"code": "600000", "name": "某股", "roe": None}
    assert check_roe_below(stock, {}, threshold=5) is False


def test_price_above_buyzone_triggered():
    """规则 3: 当前价 > 买入区上限 +20% → 强制调出"""
    stock = {"code": "600519", "name": "贵州茅台", "current_price": 2000.0}
    analysis = {"trade_strategy": {"buy_zone": "1500-1700"}}
    # 买入区上限 1700, +20% = 2040, 当前 2000 < 2040, 不触发
    assert check_price_above_buyzone(stock, analysis, pct=20) is False

    stock2 = {"code": "600519", "name": "贵州茅台", "current_price": 2100.0}
    # 2100 > 2040, 触发
    assert check_price_above_buyzone(stock2, analysis, pct=20) is True


def test_price_above_buyzone_no_buyzone():
    """买入区字段缺失 → 不触发"""
    stock = {"code": "600519", "current_price": 5000.0}
    analysis = {"trade_strategy": {}}
    assert check_price_above_buyzone(stock, analysis, pct=20) is False


def test_price_above_buyzone_invalid_buyzone():
    """买入区格式异常 → 不触发（容错）"""
    stock = {"code": "600519", "current_price": 5000.0}
    analysis = {"trade_strategy": {"buy_zone": "未知"}}
    assert check_price_above_buyzone(stock, analysis, pct=20) is False


def test_roe_collapse_triggered():
    """规则 4: ROE 同比下降 > 10pp → 强制调出"""
    stock = {
        "code": "600000", "name": "某股",
        "roe": 8.0,
        "_history_roe": {"last_year": 20.0}  # 去年 20%, 今年 8%, 下降 12pp
    }
    analysis = {}
    assert check_roe_collapse(stock, analysis, pp=10) is True


def test_roe_collapse_not_triggered():
    stock = {
        "code": "600519", "name": "贵州茅台",
        "roe": 28.0,
        "_history_roe": {"last_year": 30.0}  # 下降 2pp, 未达 10pp
    }
    assert check_roe_collapse(stock, {}, pp=10) is False


def test_roe_collapse_no_history():
    """无历史 ROE → 不触发"""
    stock = {"code": "600519", "roe": 8.0, "_history_roe": None}
    assert check_roe_collapse(stock, {}, pp=10) is False


def test_apply_hard_rules_collects_all_triggers():
    """多个硬规则同时触发 → 全部收集"""
    reviewer = WatchlistReviewer({
        "ai_review": {"hard_rules": {
            "signal_avoid": True,
            "roe_below": 5,
            "price_above_buyzone_pct": 20,
            "roe_collapse_pp": 10,
        }}
    })
    current = [
        {"code": "002415", "name": "海康威视", "roe": 15.0,
         "current_price": 100.0, "_history_roe": {"last_year": 30.0}},
    ]
    analyses = {
        "002415": {"trade_strategy": {"signal": "AVOID", "buy_zone": "50-80"}},
    }
    # 002415: Signal=AVOID ✓, ROE=15 不触发, 价格=100 vs 80*1.2=96 触发, ROE 下降 15pp 触发
    forced_out = reviewer._apply_hard_rules(current, analyses)
    codes = {f['code'] for f in forced_out}
    assert "002415" in codes
    # 触发原因列表不为空
    reasons = next(f['reasons'] for f in forced_out if f['code'] == '002415')
    assert len(reasons) >= 2  # 至少触发 2 条
```

### Step 2.2: 运行测试验证失败

- [ ] Run: `python -m pytest tests/analyzer/test_watchlist_reviewer.py -v`
- Expected: FAIL（`ImportError: cannot import name 'WatchlistReviewer'`）

### Step 2.3: 实现 watchlist_reviewer.py 骨架 + 硬规则

- [ ] 创建 `src/analyzer/__init__.py`（如果不存在，空文件）
- [ ] 创建 `src/analyzer/watchlist_reviewer.py`：

```python
"""AI 观察池复盘引擎

混合机制：硬规则预过滤 + LLM 自主决策。

对外暴露：
- WatchlistReviewer 类：单次复盘入口 review()
- 4 个硬规则检查函数：check_signal_avoid / check_roe_below /
  check_price_above_buyzone / check_roe_collapse
"""

import json
import logging
import threading
from typing import Optional

from src.utils import now_cn

logger = logging.getLogger(__name__)


# ── 4 条硬规则 ──

def check_signal_avoid(stock: dict, analysis: Optional[dict]) -> bool:
    """规则 1: Signal=AVOID → 强制调出"""
    if not analysis:
        return False
    trade = analysis.get('trade_strategy', {})
    if isinstance(trade, str):
        try:
            trade = json.loads(trade)
        except (json.JSONDecodeError, TypeError):
            return False
    return trade.get('signal', '').upper() == 'AVOID'


def check_roe_below(stock: dict, analysis: Optional[dict],
                    threshold: float = 5) -> bool:
    """规则 2: ROE 跌破阈值 → 强制调出"""
    roe = stock.get('roe')
    if roe is None:
        return False
    return roe < threshold


def check_price_above_buyzone(stock: dict, analysis: Optional[dict],
                               pct: float = 20) -> bool:
    """规则 3: 当前价 > 买入区上限 +pct% → 强制调出（已涨过头）

    买入区格式形如 "1500-1700"，取上限 1700 计算。
    """
    if not analysis:
        return False
    trade = analysis.get('trade_strategy', {})
    if isinstance(trade, str):
        try:
            trade = json.loads(trade)
        except (json.JSONDecodeError, TypeError):
            return False
    buy_zone = trade.get('buy_zone')
    if not buy_zone or not isinstance(buy_zone, str):
        return False
    current_price = stock.get('current_price')
    if current_price is None:
        return False
    # 解析 "1500-1700" 或 "1500~1700"
    parts = buy_zone.replace('~', '-').split('-')
    if len(parts) != 2:
        return False
    try:
        upper = float(parts[1].strip())
    except (ValueError, IndexError):
        return False
    return current_price > upper * (1 + pct / 100)


def check_roe_collapse(stock: dict, analysis: Optional[dict],
                       pp: float = 10) -> bool:
    """规则 4: ROE 同比下降 > pp 个百分点 → 强制调出

    依赖 _history_roe.last_year 字段（由 _load_history_roe 注入）。
    """
    history = stock.get('_history_roe')
    if not history:
        return False
    last_year = history.get('last_year')
    current = stock.get('roe')
    if last_year is None or current is None:
        return False
    return (last_year - current) > pp


# ── 复盘引擎 ──

class WatchlistReviewer:
    """AI 观察池复盘引擎

    单一职责：输入数据 → 输出复盘结果 + 落库。
    模块级锁防并发，跟每日流水线锁 _pipeline_lock 独立。
    """

    _module_lock = threading.Lock()

    def __init__(self, config: dict):
        self.cfg = config or {}
        ai_review_cfg = self.cfg.get('ai_review', {})
        self.hard_rules_cfg = ai_review_cfg.get('hard_rules', {})
        self.watchlist_size = ai_review_cfg.get('watchlist_size', 5)
        self.candidate_weeks = ai_review_cfg.get('candidate_pool_weeks', 4)

    def _apply_hard_rules(self, current: list[dict],
                          analyses: dict[str, dict]) -> list[dict]:
        """对当前观察池应用 4 条硬规则，返回必须调出的股票列表。

        返回: [{"code": "002415", "name": "海康威视", "reasons": ["signal_avoid", ...]}]
        """
        forced_out = []
        for stock in current:
            code = stock['code']
            analysis = analyses.get(code)
            reasons = []

            if self.hard_rules_cfg.get('signal_avoid', True):
                if check_signal_avoid(stock, analysis):
                    reasons.append('signal_avoid')

            threshold = self.hard_rules_cfg.get('roe_below', 5)
            if check_roe_below(stock, analysis, threshold=threshold):
                reasons.append(f'roe_below_{threshold}')

            pct = self.hard_rules_cfg.get('price_above_buyzone_pct', 20)
            if check_price_above_buyzone(stock, analysis, pct=pct):
                reasons.append(f'price_above_buyzone_{pct}')

            pp = self.hard_rules_cfg.get('roe_collapse_pp', 10)
            if check_roe_collapse(stock, analysis, pp=pp):
                reasons.append(f'roe_collapse_{pp}')

            if reasons:
                forced_out.append({
                    'code': code,
                    'name': stock.get('name', ''),
                    'reasons': reasons,
                })
        return forced_out

    # 下面的方法在后续 Task 实现
    def review(self, run_id: str) -> dict:
        """复盘主入口（Task 4 实现）"""
        raise NotImplementedError("review() 在 Task 4 实现")

    def _build_prompt(self, *args, **kwargs) -> str:
        """构建 LLM prompt（Task 3 实现）"""
        raise NotImplementedError("_build_prompt() 在 Task 3 实现")

    def _call_llm(self, prompt: str) -> Optional[dict]:
        """调用 LLM 并解析 JSON（Task 3 实现）"""
        raise NotImplementedError("_call_llm() 在 Task 3 实现")

    def _validate_and_persist(self, *args, **kwargs) -> dict:
        """校验 + 落库（Task 4 实现）"""
        raise NotImplementedError("_validate_and_persist() 在 Task 4 实现")
```

### Step 2.4: 运行测试验证通过

- [ ] Run: `python -m pytest tests/analyzer/test_watchlist_reviewer.py -v`
- Expected: 13 tests PASS

### Step 2.5: Commit

```bash
git add src/analyzer/watchlist_reviewer.py tests/analyzer/test_watchlist_reviewer.py
git commit -m "feat(ai-watchlist): 4 条硬规则 + 复盘引擎骨架

- check_signal_avoid / check_roe_below / check_price_above_buyzone / check_roe_collapse
- WatchlistReviewer._apply_hard_rules 收集强制调出
- 单测 13 个全通过"
```

---

## Task 3: Prompt 构建 + LLM 调用

**Files:**
- Modify: `src/analyzer/watchlist_reviewer.py`（实现 `_build_prompt` + `_call_llm`）
- Modify: `tests/analyzer/test_watchlist_reviewer.py`（加 prompt 测试）

### Step 3.1: 写 prompt 构建测试

- [ ] 在 `tests/analyzer/test_watchlist_reviewer.py` 末尾追加：

```python
def test_build_prompt_contains_required_sections():
    """prompt 必须包含角色 / 规则 / 当前观察池 / 候选池 / 大盘 / JSON schema"""
    reviewer = WatchlistReviewer({
        "ai_review": {"hard_rules": {
            "signal_avoid": True, "roe_below": 5,
            "price_above_buyzone_pct": 20, "roe_collapse_pp": 10,
        }, "watchlist_size": 5}
    })
    current = [
        {"code": "600519", "name": "贵州茅台", "roe": 30.0,
         "added_reason": "ROE 持续 30%+", "ai_confidence": "高",
         "review_count": 3},
        {"code": "000792", "name": "盐湖股份", "roe": 15.0,
         "added_reason": "低估值", "ai_confidence": "中",
         "review_count": 1},
    ]
    candidates = [
        {"code": "002415", "name": "海康威视", "roe": 20.0, "score": 75.0},
        {"code": "300750", "name": "宁德时代", "roe": 18.0, "score": 70.0},
    ]
    analyses = {
        "600519": {"trade_strategy": {"signal": "HOLD", "buy_zone": "1500-1700"}},
        "000792": {"trade_strategy": {"signal": "BUY", "buy_zone": "15-20"}},
    }
    market = [{"index_name": "上证综指", "current_value": 3174.0,
               "change_percent": 0.5}]
    forced_out = [
        {"code": "000792", "name": "盐湖股份", "reasons": ["signal_avoid"]}
    ]

    prompt = reviewer._build_prompt(
        current, candidates, analyses, market, forced_out
    )

    # 必需内容检查
    assert "价值投资基金经理" in prompt  # 角色
    assert "600519" in prompt  # 当前观察池
    assert "002415" in prompt  # 候选池
    assert "上证综指" in prompt  # 大盘
    assert "000792" in prompt  # 强制调出
    assert "watchlist_actions" in prompt  # JSON schema
    assert "new_watchlist" in prompt
    assert "journal" in prompt
    assert "5" in prompt  # watchlist_size


def test_build_prompt_initial_mode():
    """首次启动（当前观察池为空）→ prompt 切换为初始化模式"""
    reviewer = WatchlistReviewer({"ai_review": {"hard_rules": {}}})
    candidates = [
        {"code": "600519", "name": "贵州茅台", "roe": 30.0, "score": 80.0},
    ]
    prompt = reviewer._build_prompt(
        current=[], candidates=candidates, analyses={},
        market=[], forced_out=[]
    )
    assert "初始化" in prompt
    assert "从候选池选 5 只" in prompt


def test_call_llm_parses_valid_json(monkeypatch):
    """_call_llm 正常返回 JSON 时能解析"""
    reviewer = WatchlistReviewer({"ai_review": {}, "ai": {
        "api_base": "https://example.com/v1", "model": "test-free"
    }})

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps({
                    "watchlist_actions": [],
                    "new_watchlist": [],
                    "journal": {"title": "测试", "content_md": "# 测试"}
                })}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, headers=None, json=None):
            return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)
    # 跳过 FreeModelPool acquire，直接返回 model 名
    monkeypatch.setattr(reviewer, "_resolve_model", lambda: "test-free")

    result = reviewer._call_llm("test prompt")
    assert result is not None
    assert "watchlist_actions" in result
    assert result["journal"]["title"] == "测试"
```

### Step 3.2: 运行测试验证失败

- [ ] Run: `python -m pytest tests/analyzer/test_watchlist_reviewer.py::test_build_prompt_contains_required_sections -v`
- Expected: FAIL（`NotImplementedError: _build_prompt()`）

### Step 3.3: 实现 `_build_prompt` + `_call_llm`

- [ ] 在 `src/analyzer/watchlist_reviewer.py` 的 `WatchlistReviewer` 类中，替换 `_build_prompt` / `_call_llm` 两个方法（删掉 `raise NotImplementedError`）：

```python
    def _build_prompt(self, current: list[dict], candidates: list[dict],
                      analyses: dict[str, dict], market: list[dict],
                      forced_out: list[dict]) -> str:
        """构建 LLM prompt。

        - current 为空 → 初始化模式
        - current 非空 → 复盘模式
        """
        size = self.watchlist_size
        is_initial = len(current) == 0

        if is_initial:
            role_line = (
                f"你是一位价值投资基金经理，这是首次启动观察池。"
                f"请从候选池选 {size} 只作为初始成员。"
            )
            rule_line = (
                f"观察池固定 {size} 只。全部记为 action=add。"
                f"优先选评分高、ROE 稳定、护城河宽的股票。"
            )
        else:
            role_line = (
                f"你是一位价值投资基金经理，每周复盘一次观察池（固定 {size} 只）。"
            )
            rule_line = (
                f"观察池固定 {size} 只。优先保持稳定，没有充分理由不要换。"
                f"调入决策要给出理由，调出决策也要给出理由。"
            )

        # 强制调出列表
        if forced_out:
            forced_lines = []
            for f in forced_out:
                forced_lines.append(
                    f"  - {f['code']} {f['name']}: {', '.join(f['reasons'])}"
                )
            forced_section = (
                "【本次硬规则强制调出】（你必须接受这些调出）\n"
                + "\n".join(forced_lines)
            )
        else:
            forced_section = "【本次硬规则强制调出】无"

        # 当前观察池
        if current:
            current_lines = []
            for s in current:
                analysis = analyses.get(s['code'], {})
                trade = analysis.get('trade_strategy', {}) if analysis else {}
                if isinstance(trade, str):
                    try:
                        trade = json.loads(trade)
                    except Exception:
                        trade = {}
                signal = trade.get('signal', '--') if trade else '--'
                current_lines.append(
                    f"  - {s['code']} {s.get('name', '')} | ROE={s.get('roe', 'N/A')}% "
                    f"| Signal={signal} | 置信={s.get('ai_confidence', '--')} "
                    f"| 在池{s.get('review_count', 1)}周 | 进入理由: {s.get('added_reason', '')}"
                )
            current_section = "【当前观察池】\n" + "\n".join(current_lines)
        else:
            current_section = "【当前观察池】空（首次启动）"

        # 候选池
        if candidates:
            cand_lines = []
            for c in candidates:
                cand_lines.append(
                    f"  - {c['code']} {c.get('name', '')} | 评分={c.get('score', 'N/A')} "
                    f"| ROE={c.get('roe', 'N/A')}% | PE={c.get('pe', 'N/A')} "
                    f"| 市值={c.get('market_cap', 'N/A')}亿"
                )
            cand_section = "【候选池】（最近 4 周 Top 20 去重）\n" + "\n".join(cand_lines)
        else:
            cand_section = "【候选池】空"

        # 大盘
        if market:
            mkt_lines = [
                f"  - {m['index_name']}: {m.get('current_value', 'N/A')} "
                f"({m.get('change_percent', 'N/A')}%)"
                for m in market
            ]
            mkt_section = "【大盘近况】\n" + "\n".join(mkt_lines)
        else:
            mkt_section = "【大盘近况】无数据"

        return f"""{role_line}

【规则】
{rule_line}
- 你只能在候选池中选调入，不能选候选池外的股票
- 输出严格 JSON，不包含其他任何内容

{forced_section}

{current_section}

{cand_section}

{mkt_section}

【输出 JSON schema】
{{
    "watchlist_actions": [
        {{"code": "000792", "action": "keep", "reason": "..."}},
        {{"code": "600519", "action": "add", "reason": "..."}},
        {{"code": "002415", "action": "remove", "reason": "Signal 转 AVOID"}}
    ],
    "new_watchlist": ["000792", "600519", "300750", "600276", "002415"],
    "journal": {{
        "title": "本周复盘 - 市场震荡，观察池稳定",
        "content_md": "# 本周复盘\\n## 市场观察\\n..."
    }}
}}

journal.content_md 用 Markdown，写叙事性笔记（不要只是列股票），包含：
1. 本周市场观察（大盘走势、情绪）
2. 观察池调整动作的思考过程
3. 对当前观察池的整体判断
4. 风险提示
"""

    def _call_llm(self, prompt: str) -> Optional[dict]:
        """调用 LLM 并解析 JSON。

        复用 ai_analyzer 的 FreeModelPool 做模型轮换，但独立实现重试逻辑
        （复盘只需 1 次成功调用，不需要像 analyze_batch 那样的长重试链）。
        """
        import httpx
        from src.analyzer.ai_analyzer import (
            get_model_pool, parse_ai_response, BACKOFF_SCHEDULE
        )

        ai_cfg = self.cfg.get('ai', {})
        api_base = ai_cfg.get('api_base', 'https://opencode.ai/zen/v1').rstrip('/')
        api_key = ai_cfg.get('api_key') or os.getenv('STOCK_AI_API_KEY')
        model = ai_cfg.get('model', 'deepseek-v4-flash-free')

        # 免费模型用池
        is_free = model.endswith('-free')
        pool = get_model_pool(api_base, api_key) if is_free else None

        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        url = f"{api_base}/chat/completions"
        max_retries = 3  # 复盘只重试 3 次，避免长时间阻塞

        for attempt in range(max_retries):
            current_model = pool.acquire() if pool else model
            payload = {
                'model': current_model,
                'messages': [
                    {'role': 'system',
                     'content': '你只输出JSON，不输出其他任何内容。'
                                '必须严格按照用户指定的JSON结构输出。'},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': ai_cfg.get('temperature', 0.3),
                'max_tokens': ai_cfg.get('max_tokens', 6000),
            }
            try:
                with httpx.Client(timeout=240.0) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data['choices'][0]['message']['content']
                    if not content or len(content) < 10:
                        if pool:
                            pool.mark_dead(current_model)
                        continue
                    # 复用 ai_analyzer 的健壮 JSON 解析
                    result = parse_ai_response(content)
                    if result is None:
                        logger.warning("[复盘] JSON 解析失败，重试")
                        continue
                    return result

                # 非 200 → 轮换模型 + 退避
                if pool and resp.status_code in (500, 502, 503):
                    pool.mark_dead(current_model)
                import time
                time.sleep(BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)])

            except httpx.TimeoutException:
                logger.warning(f"[复盘] 超时, 重试 {attempt + 1}/{max_retries}")
                if pool:
                    pool.mark_dead(current_model)
            except Exception as e:
                logger.warning(f"[复盘] 异常: {e}, 重试 {attempt + 1}/{max_retries}")
                import time
                time.sleep(5)

        logger.error("[复盘] 已达最大重试次数，放弃")
        return None
```

- [ ] 在文件顶部 `import` 区加 `import os`：

```python
import json
import logging
import os
import threading
from typing import Optional

from src.utils import now_cn
```

### Step 3.4: 运行测试验证通过

- [ ] Run: `python -m pytest tests/analyzer/test_watchlist_reviewer.py -v`
- Expected: 16 tests PASS（13 个硬规则 + 3 个 prompt/LLM）

### Step 3.5: Commit

```bash
git add src/analyzer/watchlist_reviewer.py tests/analyzer/test_watchlist_reviewer.py
git commit -m "feat(ai-watchlist): prompt 构建 + LLM 调用

- _build_prompt: 初始化模式 + 复盘模式双形态
- _call_llm: 复用 FreeModelPool 模型轮换，独立 3 次重试
- 复用 ai_analyzer.parse_ai_response 解析 JSON
- 单测 16 个全通过"
```

---

## Task 4: 复盘主流程 + 落库

**Files:**
- Modify: `src/analyzer/watchlist_reviewer.py`（实现 `review` + `_validate_and_persist`）
- Modify: `tests/analyzer/test_watchlist_reviewer.py`（加主流程测试）

### Step 4.1: 写主流程测试

- [ ] 在 `tests/analyzer/test_watchlist_reviewer.py` 末尾追加：

```python
def test_review_initial_mode_persists_5_stocks(monkeypatch, tmp_path):
    """首次启动：观察池为空 → AI 选 5 只 → 落库"""
    import os
    from src.models import database as db_mod
    # 用临时数据库
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    # 预置 screening_result（候选池数据源）
    from src.models.database import ScreeningResultDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": "2026-07-15", "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10,
         "roe": 30, "gross_margin": 90, "net_margin": 50,
         "ocf_per_share": 50, "revenue_growth": 15, "profit_growth": 20,
         "debt_ratio": 20, "market_cap": 2000, "reason": "测试"},
    ])

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "watchlist_size": 5,
                      "hard_rules": {}},
        "ai": {"api_base": "https://example.com", "model": "test-free"}
    })

    # mock LLM 返回
    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "add", "reason": "ROE 30%+"}
            ],
            "new_watchlist": ["600519"],
            "journal": {
                "title": "首次复盘",
                "content_md": "# 首次复盘\n初始化观察池"
            }
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_001")

    from src.models.ai_watchlist import AiWatchlistDAO, AiJournalDAO
    watchlist = AiWatchlistDAO().get_all()
    assert len(watchlist) == 1
    assert watchlist[0]['code'] == "600519"
    assert watchlist[0]['added_reason'] == "ROE 30%+"

    journal = AiJournalDAO().get_latest()
    assert journal is not None
    assert journal['title'] == "首次复盘"
    assert journal['run_id'] == "run_test_001"

    # 返回值结构
    assert "new_watchlist" in result
    assert "journal" in result
    assert result["new_watchlist"] == ["600519"]


def test_review_rejects_candidate_not_in_pool(monkeypatch, tmp_path):
    """LLM 调入未在候选池的股票 → 拒绝调入"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.models.database import ScreeningResultDAO
    from src.models.ai_watchlist import AiWatchlistDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": "2026-07-15", "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10, "roe": 30,
         "gross_margin": 90, "net_margin": 50, "ocf_per_share": 50,
         "revenue_growth": 15, "profit_growth": 20, "debt_ratio": 20,
         "market_cap": 2000, "reason": "测试"},
    ])
    # 预置当前观察池
    AiWatchlistDAO().add("600519", "贵州茅台", "测试")

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "hard_rules": {}},
        "ai": {}
    })

    # LLM 尝试调入不在候选池的 999999
    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "600519", "action": "keep", "reason": "稳定"},
                {"code": "999999", "action": "add", "reason": "未知股"}
            ],
            "new_watchlist": ["600519", "999999"],
            "journal": {"title": "T", "content_md": "C"}
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_002")

    # 999999 被拒绝
    watchlist = AiWatchlistDAO().get_all()
    codes = {w['code'] for w in watchlist}
    assert "999999" not in codes
    assert "600519" in codes


def test_review_forced_out_overrides_llm(monkeypatch, tmp_path):
    """硬规则强制调出的股票，即使 LLM 想保留，也必须调出"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.models.database import ScreeningResultDAO
    from src.models.ai_watchlist import AiWatchlistDAO
    ScreeningResultDAO().save_batch([
        {"run_id": "r1", "run_date": "2026-07-15", "code": "600519",
         "name": "贵州茅台", "score": 85, "pe": 30, "pb": 10, "roe": 30,
         "gross_margin": 90, "net_margin": 50, "ocf_per_share": 50,
         "revenue_growth": 15, "profit_growth": 20, "debt_ratio": 20,
         "market_cap": 2000, "reason": "测试"},
    ])
    # 当前观察池有一只恶化的股票 002415
    AiWatchlistDAO().add("002415", "海康威视", "测试")
    # 预置它的周五 AI 分析（Signal=AVOID）
    from src.models.database import StockAnalysisHistoryDAO
    StockAnalysisHistoryDAO().save(
        "002415", "r1", 70.0,
        json.dumps({"analysis": "测试"}),
        json.dumps({"signal": "AVOID"})
    )

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "hard_rules": {
            "signal_avoid": True, "roe_below": 5,
            "price_above_buyzone_pct": 20, "roe_collapse_pp": 10,
        }},
        "ai": {}
    })

    # LLM 想保留 002415（违反硬规则）
    def fake_call_llm(prompt):
        return {
            "watchlist_actions": [
                {"code": "002415", "action": "keep", "reason": "稳定"}
            ],
            "new_watchlist": ["002415"],
            "journal": {"title": "T", "content_md": "C"}
        }
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_003")

    # 002415 被强制调出
    watchlist = AiWatchlistDAO().get_all()
    codes = {w['code'] for w in watchlist}
    assert "002415" not in codes


def test_review_skips_when_no_candidates(monkeypatch, tmp_path):
    """候选池为空 → 跳过复盘"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()

    reviewer = WatchlistReviewer({
        "ai_review": {"candidate_pool_weeks": 4, "hard_rules": {}},
        "ai": {}
    })
    call_count = [0]
    def fake_call_llm(prompt):
        call_count[0] += 1
        return {}
    monkeypatch.setattr(reviewer, "_call_llm", fake_call_llm)

    result = reviewer.review("run_test_004")
    assert call_count[0] == 0  # 没调用 LLM
    assert result.get("skipped") is True
```

### Step 4.2: 运行测试验证失败

- [ ] Run: `python -m pytest tests/analyzer/test_watchlist_reviewer.py::test_review_initial_mode_persists_5_stocks -v`
- Expected: FAIL（`NotImplementedError: review()`）

### Step 4.3: 实现 `review` + `_validate_and_persist`

- [ ] 在 `src/analyzer/watchlist_reviewer.py` 的 `WatchlistReviewer` 类中，替换 `review` + `_validate_and_persist` 两个方法（删掉 `raise NotImplementedError`）：

```python
    def review(self, run_id: str) -> dict:
        """复盘主入口。

        流程：
        1. 加载当前观察池 + 候选池 + 周五分析 + 大盘
        2. 硬规则预过滤
        3. 调用 LLM 决策
        4. 校验 + 落库
        """
        with self._module_lock:
            from src.models.ai_watchlist import (
                AiWatchlistDAO, AiWatchlistHistoryDAO, AiJournalDAO
            )
            from src.models.database import (
                ScreeningResultDAO, MarketIndexDAO, StockAnalysisHistoryDAO
            )

            # 1. 加载数据
            current = AiWatchlistDAO().get_all()
            candidates = self._load_recent_candidates(self.candidate_weeks)
            if not candidates and not current:
                logger.info("[复盘] 候选池为空，跳过")
                return {"skipped": True, "reason": "no_candidates"}

            analyses = self._load_recent_analyses(current + candidates)
            market = MarketIndexDAO().get_latest()
            # 历史财务（用于 ROE 同比判断）
            self._inject_history_roe(current)

            # 2. 硬规则预过滤
            forced_out = self._apply_hard_rules(current, analyses)

            # 3. 调用 LLM
            prompt = self._build_prompt(
                current, candidates, analyses, market, forced_out
            )
            result = self._call_llm(prompt)
            if result is None:
                logger.warning("[复盘] LLM 调用失败，跳过本次")
                return {"skipped": True, "reason": "llm_failed"}

            # 4. 校验 + 落库
            validated = self._validate_and_persist(
                result, run_id, forced_out, current, candidates
            )
            return validated

    def _validate_and_persist(self, result: dict, run_id: str,
                              forced_out: list[dict], current: list[dict],
                              candidates: list[dict]) -> dict:
        """校验 LLM 输出 + 落库。

        校验规则：
        - 强制调出的股票必须调出（无论 LLM 想干嘛）
        - 调入的股票必须在候选池内
        - new_watchlist 不超过 watchlist_size
        """
        from src.models.ai_watchlist import (
            AiWatchlistDAO, AiWatchlistHistoryDAO, AiJournalDAO
        )

        candidate_codes = {c['code'] for c in candidates}
        forced_out_codes = {f['code'] for f in forced_out}
        current_map = {s['code']: s for s in current}

        actions = result.get('watchlist_actions', [])
        new_watchlist = result.get('new_watchlist', [])
        journal = result.get('journal', {})

        # 构建最终动作列表（强制调出 + LLM 动作，去重）
        final_actions = {}  # code → (action, reason)

        # 1. 强制调出优先
        for f in forced_out:
            final_actions[f['code']] = (
                'remove',
                f"硬规则强制调出: {', '.join(f['reasons'])}"
            )

        # 2. LLM 动作（不覆盖强制调出）
        for action in actions:
            code = action.get('code')
            if not code:
                continue
            if code in forced_out_codes:
                continue  # 强制调出已处理
            act = action.get('action', 'keep')
            reason = action.get('reason', '')
            # 调入必须在候选池
            if act == 'add' and code not in candidate_codes:
                logger.warning(
                    f"[复盘] 拒绝调入 {code}: 不在候选池"
                )
                final_actions[code] = ('remove', f"rejected: not in candidate pool")
                continue
            final_actions[code] = (act, reason)

        # 3. 落库
        action_date = now_cn().strftime("%Y-%m-%d")
        watchlist_dao = AiWatchlistDAO()
        history_dao = AiWatchlistHistoryDAO()

        for code, (action, reason) in final_actions.items():
            name = current_map.get(code, {}).get('name', '')
            if action == 'remove':
                watchlist_dao.remove(code)
            elif action == 'add':
                # 从候选池找 name
                for c in candidates:
                    if c['code'] == code:
                        name = c['name']
                        break
                watchlist_dao.add(code, name, added_reason=reason)
            elif action == 'keep':
                watchlist_dao.update_reviewed(code)

            history_dao.append(
                code=code, name=name, action=action,
                reason=reason, review_run_id=run_id, action_date=action_date
            )

        # 4. 笔记落库
        if journal and journal.get('title'):
            journal_dao = AiJournalDAO()
            journal_dao.save(
                journal_date=action_date,
                run_id=run_id,
                title=journal.get('title', ''),
                content_md=journal.get('content_md', ''),
                market_snapshot=json.dumps(
                    {'indices': [{'name': m.get('index_name'),
                                  'value': m.get('current_value')}
                                 for m in []]}
                ),
                actions_summary=json.dumps({
                    'add': sum(1 for a, _ in final_actions.values() if a == 'add'),
                    'remove': sum(1 for a, _ in final_actions.values() if a == 'remove'),
                    'keep': sum(1 for a, _ in final_actions.values() if a == 'keep'),
                })
            )

        return {
            "new_watchlist": [w['code'] for w in watchlist_dao.get_all()],
            "actions": dict(final_actions),
            "journal_date": action_date,
        }

    def _load_recent_candidates(self, weeks: int) -> list[dict]:
        """加载最近 N 周 screening_result 去重"""
        from datetime import timedelta
        from src.models.database import ScreeningResultDAO, db_conn

        cutoff = (now_cn() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_result "
                "WHERE run_date >= ? "
                "GROUP BY code "
                "ORDER BY MAX(score) DESC LIMIT 50",
                (cutoff,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _load_recent_analyses(self, stocks: list[dict]) -> dict[str, dict]:
        """加载最近一次 AI 分析（按 code）"""
        from src.models.database import StockAnalysisHistoryDAO
        dao = StockAnalysisHistoryDAO()
        analyses = {}
        for s in stocks:
            code = s.get('code')
            if not code:
                continue
            hist = dao.get_latest_for_code(code)
            if hist and hist.get('ai_analysis'):
                try:
                    parsed = json.loads(hist['ai_analysis'])
                    if parsed and parsed != {}:
                        analyses[code] = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
        return analyses

    def _inject_history_roe(self, stocks: list[dict]):
        """注入历史 ROE（用于 ROE 同比判断）"""
        from src.models.database import FinancialHistoryDAO
        dao = FinancialHistoryDAO()
        for s in stocks:
            reports = dao.get_annual_reports(s['code'])
            if len(reports) >= 2:
                # reports 按日期降序，[0] 是最新年报，[1] 是去年
                s['_history_roe'] = {
                    'last_year': reports[1].get('roe'),
                    'this_year': reports[0].get('roe'),
                }
            else:
                s['_history_roe'] = None
```

### Step 4.4: 运行测试验证通过

- [ ] Run: `python -m pytest tests/analyzer/test_watchlist_reviewer.py -v`
- Expected: 20 tests PASS

### Step 4.5: Commit

```bash
git add src/analyzer/watchlist_reviewer.py tests/analyzer/test_watchlist_reviewer.py
git commit -m "feat(ai-watchlist): 复盘主流程 + 落库

- review(): 加载数据 → 硬规则 → LLM → 校验落库
- _validate_and_persist: 强制调出优先 / 候选池校验 / 历史记录
- _load_recent_candidates / _load_recent_analyses / _inject_history_roe
- 单测 20 个全通过（含边界：候选池空 / 候选池外调入 / 硬规则覆盖 LLM）"
```

---

## Task 5: scheduler 集成

**Files:**
- Modify: `src/scheduler.py:83-271`（MarketScheduler 类）
- Modify: `src/scheduler.py:135-156`（`_run()` 主循环）

### Step 5.1: 加 _check_weekly_review + _run_review

- [ ] 在 `src/scheduler.py` 的 `MarketScheduler.__init__` 末尾（`self._last_daily_date = ...` 之后）追加：

```python
        # 周六复盘状态（避免重启后重复触发）
        self._last_review_date = self._get_last_review_date()
```

- [ ] 在 `MarketScheduler` 类中（`_get_last_completed_date` 方法之后）追加新方法：

```python
    def _get_last_review_date(self):
        """从 ai_journal 表获取最新复盘日期，避免重启后重复触发"""
        try:
            from src.models.ai_watchlist import AiJournalDAO
            latest = AiJournalDAO().get_latest()
            if latest:
                from datetime import datetime
                return datetime.strptime(
                    latest['journal_date'], "%Y-%m-%d"
                ).date()
        except Exception:
            pass
        return None
```

- [ ] 在 `_run()` 主循环中，`self._check_daily_pipeline()` 之后追加：

```python
                # 每周六 00:00 触发 AI 观察池复盘
                self._check_weekly_review()
```

- [ ] 在 `_check_daily_pipeline` 方法之后追加两个新方法：

```python
    def _check_weekly_review(self):
        """检查是否需要触发周六 AI 复盘

        周六任意时刻进程还活着且当天没跑过就触发一次。
        _last_review_date 在 _run_review 成功后才设置，
        避免失败重试时被错误跳过。
        """
        now = now_cn()
        if now.weekday() != 5:  # 周六
            return
        if self._last_review_date == now.date():
            return
        threading.Thread(target=self._run_review, daemon=True).start()

    def _run_review(self):
        """后台跑复盘，不阻塞主调度循环"""
        try:
            from src.analyzer.watchlist_reviewer import WatchlistReviewer
            from src.models.database import RunLogDAO
            from src.config import load_config

            config = load_config()
            ai_review_cfg = config.get('ai_review', {})
            if not ai_review_cfg.get('enabled', True):
                logger.info("[复盘] ai_review.enabled=False, 跳过")
                return

            run_id = now_cn().strftime("%Y%m%d_%H%M%S") + "_review"
            RunLogDAO().start_run(run_id)
            logger.info(f"[复盘] 启动周六复盘 run_id={run_id}")

            reviewer = WatchlistReviewer(config)
            result = reviewer.review(run_id)

            if result.get('skipped'):
                logger.info(f"[复盘] 跳过: {result.get('reason')}")
                RunLogDAO().complete_run(
                    run_id, 0, 0, 0, f"skipped: {result.get('reason')}"
                )
            else:
                new_count = len(result.get('new_watchlist', []))
                RunLogDAO().complete_run(
                    run_id, 0, new_count, 0, "review done"
                )
                logger.info(f"[复盘] 完成，观察池 {new_count} 只")

            # 只在成功后才标记当日已完成
            self._last_review_date = now_cn().date()
        except Exception as e:
            logger.warning(f"[复盘] 异常: {e}", exc_info=True)
```

### Step 5.2: 运行现有测试确认无回归

- [ ] Run: `python -m pytest tests/ -v`
- Expected: 所有现有测试 PASS

### Step 5.3: Commit

```bash
git add src/scheduler.py
git commit -m "feat(ai-watchlist): scheduler 周六 00:00 触发复盘

- _check_weekly_review: 周六任意时刻触发，当天只跑一次
- _run_review: 后台 daemon 线程，失败不更新 _last_review_date
- ai_review.enabled=False 可禁用
- 重启后从 ai_journal 表恢复 _last_review_date"
```

---

## Task 6: 后端路由

**Files:**
- Modify: `src/web/routes.py:16-27`（import）+ 末尾追加路由
- Create: `tests/web/__init__.py`（如果不存在）
- Create: `tests/web/test_routes_journal.py`

### Step 6.1: 写路由测试

- [ ] 创建 `tests/web/__init__.py`（空文件）
- [ ] 创建 `tests/web/test_routes_journal.py`：

```python
"""AI 观察池 + 投资笔记路由测试"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试用独立临时数据库"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    # 避免启动调度器线程
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_api_ai_watchlist_empty(client):
    """空观察池"""
    resp = client.get("/api/ai-watchlist")
    assert resp.status_code == 200
    data = resp.json()
    assert data == []


def test_api_ai_watchlist_with_data(client):
    from src.models.ai_watchlist import AiWatchlistDAO
    AiWatchlistDAO().add("600519", "贵州茅台", "ROE 30%+", "高")
    AiWatchlistDAO().add("000792", "盐湖股份", "低估值", "中")

    resp = client.get("/api/ai-watchlist")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    codes = {item['code'] for item in data}
    assert codes == {"600519", "000792"}


def test_api_ai_watchlist_history(client):
    from src.models.ai_watchlist import AiWatchlistHistoryDAO
    dao = AiWatchlistHistoryDAO()
    dao.append("600519", "贵州茅台", "add", "新进", "r1", "2026-07-20")
    dao.append("002415", "海康威视", "remove", "AVOID", "r1", "2026-07-20")

    resp = client.get("/api/ai-watchlist/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_journal_page_no_data(client):
    """无笔记时 /journal 返回空状态"""
    resp = client.get("/journal")
    assert resp.status_code == 200
    assert "暂无笔记" in resp.text or "empty" in resp.text.lower()


def test_journal_page_with_data(client):
    from src.models.ai_watchlist import AiJournalDAO
    AiJournalDAO().save(
        "2026-07-20", "r1", "本周复盘",
        "# 本周复盘\n市场震荡", None, None
    )
    resp = client.get("/journal")
    assert resp.status_code == 200
    assert "本周复盘" in resp.text


def test_api_journal_latest(client):
    from src.models.ai_watchlist import AiJournalDAO
    AiJournalDAO().save(
        "2026-07-20", "r1", "本周复盘",
        "# 内容", None, None
    )
    resp = client.get("/api/journal/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data['title'] == "本周复盘"
    assert data['journal_date'] == "2026-07-20"


def test_api_journal_latest_404(client):
    resp = client.get("/api/journal/latest")
    assert resp.status_code == 404


def test_api_journal_by_date(client):
    from src.models.ai_watchlist import AiJournalDAO
    AiJournalDAO().save(
        "2026-07-20", "r1", "标题1",
        "# 内容1", None, None
    )
    resp = client.get("/api/journal/2026-07-20")
    assert resp.status_code == 200
    assert resp.json()['title'] == "标题1"

    resp = client.get("/api/journal/2020-01-01")
    assert resp.status_code == 404


def test_api_journal_list(client):
    from src.models.ai_watchlist import AiJournalDAO
    for i in range(3):
        AiJournalDAO().save(
            f"2026-07-{i+1:02d}", f"r{i}", f"第{i}周", "# C", None, None
        )
    resp = client.get("/api/journal/list")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[0]['journal_date'] == "2026-07-03"  # 最新在前
```

### Step 6.2: 运行测试验证失败

- [ ] Run: `python -m pytest tests/web/test_routes_journal.py -v`
- Expected: FAIL（路由不存在）

### Step 6.3: 实现路由

- [ ] 在 `src/web/routes.py` 顶部 import 区追加：

```python
from src.models.ai_watchlist import (
    AiWatchlistDAO, AiWatchlistHistoryDAO, AiJournalDAO
)
```

- [ ] 在 `src/web/routes.py` 末尾（`run_server` 之前）追加 7 个路由：

```python
# ── AI 观察池 + 投资笔记 ──────────────────────────────────────

@app.get("/api/ai-watchlist")
async def api_ai_watchlist():
    """当前 AI 观察池（最多 5 只）"""
    items = AiWatchlistDAO().get_all()
    # 合并实时行情
    realtime = get_realtime_cache()
    for item in items:
        code = item['code']
        if code in realtime:
            item['current_price'] = realtime[code].get('current_price')
            item['change_percent'] = realtime[code].get('change_percent')
    return items


@app.get("/api/ai-watchlist/history")
async def api_ai_watchlist_history():
    """观察池调整历史"""
    return AiWatchlistHistoryDAO().list_recent(limit=50)


@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request):
    """投资笔记页（默认显示最新一篇）"""
    latest = AiJournalDAO().get_latest()
    history_list = AiJournalDAO().list_all()
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')
    return templates.TemplateResponse(request, "journal.html", {
        "request": request,
        "page_title": page_title,
        "journal": latest,
        "history_list": history_list,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/journal/{journal_date}", response_class=HTMLResponse)
async def journal_by_date(request: Request, journal_date: str):
    """指定日期笔记页"""
    journal = AiJournalDAO().get_by_date(journal_date)
    history_list = AiJournalDAO().list_all()
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')
    return templates.TemplateResponse(request, "journal.html", {
        "request": request,
        "page_title": page_title,
        "journal": journal,
        "history_list": history_list,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/api/journal/latest")
async def api_journal_latest():
    """最新笔记 JSON"""
    journal = AiJournalDAO().get_latest()
    if not journal:
        raise HTTPException(status_code=404, detail="无笔记")
    return journal


@app.get("/api/journal/{journal_date}")
async def api_journal_by_date(journal_date: str):
    """指定日期笔记 JSON"""
    journal = AiJournalDAO().get_by_date(journal_date)
    if not journal:
        raise HTTPException(status_code=404, detail="该日期无笔记")
    return journal


@app.get("/api/journal/list")
async def api_journal_list():
    """笔记列表（轻量，仅 date + title）"""
    return AiJournalDAO().list_all()
```

### Step 6.4: 运行测试验证通过

- [ ] Run: `python -m pytest tests/web/test_routes_journal.py -v`
- Expected: 9 tests PASS（需要先完成 Task 7.2 的 journal.html 模板）

**注意**：`test_journal_page_no_data` 和 `test_journal_page_with_data` 需要 `journal.html` 模板存在。如果还没创建，这两个测试会失败。可以先创建空模板（Task 7.2），或者跳过这两个测试先验证 API 路由。

### Step 6.5: Commit

```bash
git add src/web/routes.py tests/web/
git commit -m "feat(ai-watchlist): 7 个后端路由

- /api/ai-watchlist: 当前观察池 + 实时行情
- /api/ai-watchlist/history: 调整历史
- /journal + /journal/<date>: 笔记页 HTML
- /api/journal/latest + /api/journal/<date> + /api/journal/list: 笔记 API"
```

---

## Task 7: 前端 UI 改造

**Files:**
- Create: `src/web/templates/_watchlist_card.html`
- Create: `src/web/templates/journal.html`
- Modify: `src/web/templates/index.html:909-1098`（移除 Top 20，加观察池卡片）
- Modify: `src/web/static/watchlist.js`（或新建 `src/web/static/ai-watchlist.js`）

### Step 7.1: 创建观察池卡片 partial

- [ ] 创建 `src/web/templates/_watchlist_card.html`：

```html
{% if watchlist %}
<div class="watchlist-grid">
    {% for stock in watchlist %}
    <div class="watchlist-card" data-code="{{ stock.code }}">
        <div class="wl-card-header">
            <div class="wl-stock-code">{{ stock.code }}</div>
            <div class="wl-stock-name">{{ stock.name }}</div>
        </div>
        <div class="wl-card-body">
            {% if stock.current_price is not none %}
            <div class="wl-price" id="wl-price-{{ stock.code }}">
                ¥{{ "%.2f"|format(stock.current_price) }}
            </div>
            {% else %}
            <div class="wl-price" id="wl-price-{{ stock.code }}">--</div>
            {% endif %}
            <div class="wl-change" id="wl-chg-{{ stock.code }}">
                {% if stock.change_percent is not none %}
                <span style="color:{{ 'var(--color-up)' if stock.change_percent >= 0 else 'var(--color-down)' }}">
                    {{ "%+.2f"|format(stock.change_percent) }}%
                </span>
                {% endif %}
            </div>
        </div>
        <div class="wl-card-footer">
            {% set signal = stock.signal|upper if stock.signal else '--' %}
            {% if signal == 'BUY' %}
            <span class="wl-signal wl-signal-buy">买入</span>
            {% elif signal == 'HOLD' %}
            <span class="wl-signal wl-signal-hold">持有</span>
            {% elif signal == 'AVOID' %}
            <span class="wl-signal wl-signal-avoid">回避</span>
            {% else %}
            <span class="wl-signal wl-signal-none">{{ signal }}</span>
            {% endif %}
            <span class="wl-confidence">{{ stock.ai_confidence or '--' }}</span>
            <span class="wl-weeks">在池{{ stock.review_count or 1 }}周</span>
        </div>
        <div class="wl-reason" title="{{ stock.added_reason or '' }}">
            {{ stock.added_reason or '' }}
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="empty-state">
    <div class="icon">&#9678;</div>
    <h2>观察池为空</h2>
    <p>周六 00:00 AI 自动复盘后，将在此处展示 5 只观察股。</p>
</div>
{% endif %}
```

### Step 7.2: 创建投资笔记页

- [ ] 创建 `src/web/templates/journal.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ page_title }} - 投资笔记</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,300;14..32,400;14..32,500;14..32,600;14..32,700&family=Noto+Sans+SC:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        /* 复用 index.html 的 design tokens */
        :root {
            --font-sans: 'Inter', 'Noto Sans SC', sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
            --bg-primary: #f8f9fa;
            --bg-secondary: #ffffff;
            --bg-tertiary: #f0f1f3;
            --text-primary: #1a1c1e;
            --text-secondary: #5f6368;
            --text-tertiary: #9aa0a6;
            --accent: #1a73e8;
            --divider: #e0e3e7;
            --divider-thin: #e8eaed;
            --radius: 8px;
        }
        [data-theme="dark"] {
            --bg-primary: #1a1c1e;
            --bg-secondary: #202124;
            --bg-tertiary: #282a2d;
            --text-primary: #e8eaed;
            --text-secondary: #9aa0a6;
            --text-tertiary: #5f6368;
            --divider: #3c4043;
            --divider-thin: #303134;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: var(--font-sans);
            font-size: 14px;
            line-height: 1.7;
            background: var(--bg-primary);
            color: var(--text-primary);
        }
        .journal-app {
            max-width: 1200px;
            margin: 0 auto;
            padding: 24px 32px;
            display: grid;
            grid-template-columns: 1fr 280px;
            gap: 32px;
        }
        .journal-header {
            grid-column: 1 / -1;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--divider-thin);
            margin-bottom: 8px;
        }
        .journal-header a {
            color: var(--accent);
            text-decoration: none;
            font-size: 13px;
        }
        .journal-title {
            font-size: 24px;
            font-weight: 500;
            margin-top: 8px;
        }
        .journal-date {
            font-size: 12px;
            color: var(--text-tertiary);
            margin-top: 4px;
        }
        .journal-content {
            background: var(--bg-secondary);
            border-radius: var(--radius);
            padding: 32px 40px;
        }
        .journal-content h1 { font-size: 20px; font-weight: 500; margin: 24px 0 12px; }
        .journal-content h2 { font-size: 16px; font-weight: 500; margin: 20px 0 10px; }
        .journal-content p { margin-bottom: 12px; color: var(--text-secondary); }
        .journal-content ul, .journal-content ol { margin: 8px 0 12px 24px; }
        .journal-content li { margin-bottom: 4px; color: var(--text-secondary); }
        .journal-content strong { color: var(--text-primary); font-weight: 500; }
        .journal-sidebar {
            background: var(--bg-secondary);
            border-radius: var(--radius);
            padding: 16px;
            position: sticky;
            top: 12px;
            height: fit-content;
            max-height: calc(100vh - 24px);
            overflow-y: auto;
        }
        .journal-sidebar h3 {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--divider-thin);
        }
        .journal-history-item {
            padding: 8px 10px;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.15s;
        }
        .journal-history-item:hover { background: var(--bg-tertiary); }
        .journal-history-item.active { background: var(--accent); color: #fff; }
        .journal-history-item.active .jh-title { color: #fff; }
        .jh-date { font-size: 11px; color: var(--text-tertiary); }
        .journal-history-item.active .jh-date { color: rgba(255,255,255,0.7); }
        .jh-title { font-size: 13px; color: var(--text-primary); margin-top: 2px; }
        .journal-empty {
            text-align: center;
            padding: 80px 20px;
            color: var(--text-tertiary);
        }
        @media (max-width: 900px) {
            .journal-app { grid-template-columns: 1fr; }
            .journal-sidebar { position: static; }
        }
    </style>
</head>
<body>
<div class="journal-app">
    <div class="journal-header">
        <a href="/">&larr; 返回看板</a>
        {% if journal %}
        <h1 class="journal-title">{{ journal.title }}</h1>
        <div class="journal-date">{{ journal.journal_date }}</div>
        {% endif %}
    </div>

    <div class="journal-content">
        {% if journal %}
        <div class="journal-body">
            {{ journal.content_md | markdown_to_html | safe }}
        </div>
        {% else %}
        <div class="journal-empty">
            <div style="font-size: 32px; margin-bottom: 12px;">&#9678;</div>
            <h2>暂无笔记</h2>
            <p>周六 00:00 AI 复盘后，第一篇笔记会在此处展示。</p>
        </div>
        {% endif %}
    </div>

    <aside class="journal-sidebar">
        <h3>历史归档</h3>
        {% if history_list %}
        {% for item in history_list %}
        <div class="journal-history-item {{ 'active' if journal and item.journal_date == journal.journal_date else '' }}"
             onclick="window.location.href='/journal/{{ item.journal_date }}'">
            <div class="jh-date">{{ item.journal_date }}</div>
            <div class="jh-title">{{ item.title }}</div>
        </div>
        {% endfor %}
        {% else %}
        <div style="color: var(--text-tertiary); font-size: 12px; padding: 8px 0;">
            无历史归档
        </div>
        {% endif %}
    </aside>
</div>
<script>
    // 复用 index.html 的主题切换
    const theme = localStorage.getItem('stock-dashboard-theme') || 'light';
    document.documentElement.setAttribute('data-theme', theme);
</script>
</body>
</html>
```

### Step 7.3: 注册 markdown 模板过滤器

- [ ] 在 `src/web/routes.py` 顶部（import 之后）追加：

```python
import markdown as _markdown

def _markdown_to_html(text: str) -> str:
    """Jinja2 过滤器：Markdown → HTML"""
    if not text:
        return ''
    return _markdown.markdown(text, extensions=['extra', 'codehilite'])

# 注册到 Jinja2
templates.env.filters['markdown_to_html'] = _markdown_to_html
```

- [ ] 在 `pyproject.toml` 的 dependencies 中追加 `markdown`（如果不存在）：

```toml
dependencies = [
    # ... 已有依赖
    "markdown>=3.5",
]
```

- [ ] 在生产服务器上安装：`pip install markdown`

### Step 7.4: 改造 index.html

- [ ] 在 `src/web/templates/index.html` 的 `<style>` 块末尾（`.shimmer` 之后）追加观察池卡片样式：

```css
        /* ========== AI Watchlist Grid ========== */
        .watchlist-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }
        .watchlist-card {
            background: var(--bg-secondary);
            border-radius: var(--radius);
            padding: 16px;
            transition: background 0.15s;
            border: 1px solid var(--divider-thin);
        }
        .watchlist-card:hover { background: var(--bg-hover); }
        .wl-card-header { margin-bottom: 12px; }
        .wl-stock-code {
            font-size: 11px;
            color: var(--text-tertiary);
            letter-spacing: 0.02em;
        }
        .wl-stock-name {
            font-size: 15px;
            font-weight: 500;
            color: var(--text-primary);
            margin-top: 1px;
        }
        .wl-card-body { margin-bottom: 12px; }
        .wl-price {
            font-size: 20px;
            font-weight: 400;
            letter-spacing: -0.01em;
            color: var(--text-primary);
        }
        .wl-change { font-size: 11px; margin-top: 2px; }
        .wl-card-footer {
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
            padding-top: 8px;
            border-top: 1px solid var(--divider-thin);
        }
        .wl-signal {
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 3px;
        }
        .wl-signal-buy { background: var(--color-up); color: #fff; }
        .wl-signal-hold {
            background: transparent;
            border: 1px solid var(--text-tertiary);
            color: var(--text-secondary);
        }
        .wl-signal-avoid {
            background: transparent;
            border: 1px dashed var(--red);
            color: var(--red);
        }
        .wl-signal-none { color: var(--text-tertiary); }
        .wl-confidence {
            font-size: 10px;
            color: var(--text-tertiary);
        }
        .wl-weeks {
            font-size: 10px;
            color: var(--text-tertiary);
            margin-left: auto;
        }
        .wl-reason {
            font-size: 11px;
            color: var(--text-tertiary);
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid var(--divider-thin);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        @media (max-width: 1200px) {
            .watchlist-grid { grid-template-columns: repeat(3, 1fr); }
        }
        @media (max-width: 600px) {
            .watchlist-grid { grid-template-columns: repeat(2, 1fr); }
        }

        /* ========== Journal Link ========== */
        .journal-link {
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 12px;
            padding: 10px 16px;
            background: var(--bg-tertiary);
            border-radius: var(--radius-sm);
        }
        .journal-link a {
            color: var(--accent);
            text-decoration: none;
        }
        .journal-link a:hover { text-decoration: underline; }
```

- [ ] 修改 `index.html` 的 tab 结构（约 909-922 行）。将：

```html
    <div class="section-header">
        <div class="view-tabs">
            <button class="view-tab active" onclick="switchView('candidates', event)">候选池</button>
            <button class="view-tab" onclick="switchView('watchlist', event)">我的钉选 <span id="watchCount"></span></button>
        </div>
        <span class="section-count">{{ stocks|length }} 只</span>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="搜索代码/名称..." />
            <button onclick="searchStocks()">搜索</button>
            <div id="searchResults" class="search-results" style="display:none;"></div>
        </div>
    </div>
```

替换为：

```html
    <div class="section-header">
        <div class="view-tabs">
            <button class="view-tab active" onclick="switchView('watchlist-ai', event)">AI 观察池</button>
            <button class="view-tab" onclick="switchView('watchlist', event)">钉选股票 <span id="watchCount"></span></button>
        </div>
        <span class="section-count" id="aiWatchCount">{{ ai_watchlist|length if ai_watchlist else 0 }} 只</span>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="搜索代码/名称..." />
            <button onclick="searchStocks()">搜索</button>
            <div id="searchResults" class="search-results" style="display:none;"></div>
        </div>
    </div>
```

- [ ] 修改 `index.html` 的主体内容（约 924-930 行）。将：

```html
    <div class="dashboard-layout">
    <div class="dashboard-main">
    <div class="stock-list" id="stockList">
        {% include '_stock_list.html' %}
    </div>
    <div class="stock-list" id="watchlistContainer" style="display:none;"></div>
    </div><!-- /dashboard-main -->
```

替换为：

```html
    <div class="dashboard-layout">
    <div class="dashboard-main">
    <div id="watchlistAiContainer">
        {% with watchlist = ai_watchlist %}
        {% include '_watchlist_card.html' %}
        {% endwith %}
        {% if ai_journal_latest %}
        <div class="journal-link">
            <strong>本周笔记:</strong> {{ ai_journal_latest.title }}
            &mdash; <a href="/journal">查看全文 &rarr;</a>
        </div>
        {% endif %}
    </div>
    <div class="stock-list" id="watchlistContainer" style="display:none;"></div>
    </div><!-- /dashboard-main -->
```

- [ ] 修改 `index()` 路由（`src/web/routes.py`），加载 AI 观察池数据。在 `# 获取最新筛选结果` 之后，`return templates.TemplateResponse(...)` 之前追加：

```python
    # 获取 AI 观察池
    ai_watchlist = AiWatchlistDAO().get_all()
    # 合并实时行情 + 最近 AI Signal
    realtime = get_realtime_cache()
    hist_dao = StockAnalysisHistoryDAO()
    for item in ai_watchlist:
        code = item['code']
        if code in realtime:
            item['current_price'] = realtime[code].get('current_price')
            item['change_percent'] = realtime[code].get('change_percent')
        # 取最近 Signal
        latest_hist = hist_dao.get_latest_for_code(code)
        if latest_hist and latest_hist.get('ai_trade_strategy'):
            try:
                trade = json.loads(latest_hist['ai_trade_strategy'])
                item['signal'] = trade.get('signal')
            except (json.JSONDecodeError, TypeError):
                item['signal'] = None
        else:
            item['signal'] = None

    # 获取最新笔记摘要
    ai_journal_latest = AiJournalDAO().get_latest()
```

- [ ] 在 `index()` 路由的 `return templates.TemplateResponse(...)` 中，context dict 追加两个字段：

```python
        "ai_watchlist": ai_watchlist,
        "ai_journal_latest": ai_journal_latest,
```

- [ ] 修改 `index.html` 的 `switchView` 函数（在 `<script>` 块中找到 `function switchView`，如果没有就追加）：

```javascript
    function switchView(view, event) {
        // 切换 tab 高亮
        document.querySelectorAll('.view-tab').forEach(function(el) {
            el.classList.remove('active');
        });
        event.target.closest('.view-tab').classList.add('active');

        if (view === 'watchlist-ai') {
            document.getElementById('watchlistAiContainer').style.display = '';
            document.getElementById('watchlistContainer').style.display = 'none';
        } else if (view === 'watchlist') {
            document.getElementById('watchlistAiContainer').style.display = 'none';
            document.getElementById('watchlistContainer').style.display = '';
            loadWatchlist();  // 复用已有函数
        }
    }
```

### Step 7.5: 运行测试验证

- [ ] Run: `python -m pytest tests/web/test_routes_journal.py -v`
- Expected: 9 tests PASS

- [ ] 手动启动服务验证：`python -m src.main serve`
- 打开浏览器访问 `http://localhost:9527/`，确认：
  - tab 显示"AI 观察池 / 钉选股票"
  - 观察池为空时显示"观察池为空"提示
  - 点击"钉选股票"能切换到钉选列表
  - 访问 `/journal` 显示笔记页（无笔记时显示空状态）

### Step 7.6: Commit

```bash
git add src/web/templates/ src/web/routes.py src/web/static/
git commit -m "feat(ai-watchlist): 前端 UI 改造

- _watchlist_card.html: 5 只卡片网格 partial
- journal.html: 笔记页 + 历史归档 sidebar
- index.html: 移除 Top 20 表格，加 AI 观察池 tab
- routes.py: index 路由注入 ai_watchlist + ai_journal_latest
- markdown 库渲染笔记内容"
```

---

## Task 8: 配置 + 集成测试

**Files:**
- Modify: `config/config.yaml:65`（末尾追加 ai_review 段）

### Step 8.1: 加配置

- [ ] 在 `config/config.yaml` 末尾追加：

```yaml

# -------- AI 观察池复盘配置 --------
ai_review:
  enabled: true                    # 是否启用周六 AI 复盘
  candidate_pool_weeks: 4          # 候选池回看周数（从 screening_result 取）
  watchlist_size: 5                # 观察池固定容量
  hard_rules:
    signal_avoid: true             # Signal=AVOID 强制调出
    roe_below: 5                   # ROE 低于此值强制调出
    price_above_buyzone_pct: 20    # 越出买入区上限百分比
    roe_collapse_pp: 10            # ROE 同比下降 pp
```

### Step 8.2: 端到端集成测试

- [ ] 创建 `tests/test_integration_watchlist.py`：

```python
"""端到端集成测试：scheduler → reviewer → DAO → 路由"""

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_full_flow_empty_db(app_client):
    """空数据库：首页显示空观察池，/journal 显示无笔记"""
    resp = app_client.get("/")
    assert resp.status_code == 200
    assert "观察池为空" in resp.text or "AI 观察池" in resp.text

    resp = app_client.get("/journal")
    assert resp.status_code == 200
    assert "暂无笔记" in resp.text

    resp = app_client.get("/api/ai-watchlist")
    assert resp.status_code == 200
    assert resp.json() == []


def test_scheduler_triggers_review(monkeypatch, tmp_path):
    """模拟周六触发复盘"""
    from datetime import datetime
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()

    # mock now_cn 返回周六
    saturday = datetime(2026, 7, 25, 0, 5, 0)  # 2026-07-25 是周六
    import src.utils
    monkeypatch.setattr(src.utils, "now_cn", lambda: saturday)
    import src.scheduler
    monkeypatch.setattr(src.scheduler, "now_cn", lambda: saturday)
    import src.models.database
    # database.now_cn 也需要 mock（get_db_path 间接调用）
    monkeypatch.setattr(src.models.database, "now_cn", lambda: saturday)

    scheduler = src.scheduler.MarketScheduler()
    assert scheduler._last_review_date is None  # 初始为空

    # mock reviewer.review 返回成功
    call_count = [0]
    def fake_review(self, run_id):
        call_count[0] += 1
        return {"new_watchlist": ["600519"], "actions": {},
                "journal_date": "2026-07-25"}
    monkeypatch.setattr(
        "src.analyzer.watchlist_reviewer.WatchlistReviewer.review", fake_review
    )

    scheduler._check_weekly_review()
    # 异步线程启动，等待完成
    import time
    time.sleep(1)

    assert call_count[0] == 1
    # _last_review_date 应该在成功后设置
    assert scheduler._last_review_date is not None
```

### Step 8.3: 运行全部测试

- [ ] Run: `python -m pytest tests/ -v`
- Expected: 所有测试 PASS

### Step 8.4: Commit

```bash
git add config/config.yaml tests/test_integration_watchlist.py
git commit -m "feat(ai-watchlist): 配置 + 端到端集成测试

- config.yaml 新增 ai_review 配置段
- 集成测试：空数据库流程 + scheduler 周六触发复盘"
```

---

## 自审清单（实施前）

**Spec 覆盖：**
- [x] 数据模型 3 张表（§4）→ Task 1
- [x] DAO 层（§5.2）→ Task 1
- [x] 4 条硬规则（§5.3）→ Task 2
- [x] LLM Prompt + JSON schema（§5.4）→ Task 3
- [x] scheduler 周六触发（§5.5）→ Task 5
- [x] 前端 UI 卡片 + tab + 笔记页（§6）→ Task 7
- [x] 错误处理（§7：LLM 失败/候选池空/候选池外调入）→ Task 4 测试覆盖
- [x] 并发保护（§8：模块级锁）→ Task 4 `_module_lock`
- [x] 数据库迁移（§9：init_database 自动建表）→ Task 1
- [x] 边界情况（§10：首次启动/候选不足/手动钉选独立）→ Task 4 测试覆盖
- [x] 测试策略（§11）→ 每个 Task 都有测试
- [x] 配置项（§12）→ Task 8

**占位符扫描：** 无 "TBD" / "TODO" / "implement later"。

**类型一致性：**
- `AiWatchlistDAO.get_all()` 返回 `list[dict]`，`_apply_hard_rules` 接收 `list[dict]` ✓
- `WatchlistReviewer.__init__(config: dict)` 跟 `scheduler._run_review` 传入 `load_config()` 一致 ✓
- `_call_llm` 返回 `Optional[dict]`，`review` 内 `result is None` 判断一致 ✓
- `_validate_and_persist` 返回 `dict`，`review` 直接 `return validated` ✓
- 前端 `ai_watchlist` 字段跟 `_watchlist_card.html` 模板变量一致 ✓

---

## 执行选择

Plan complete and saved to `docs/superpowers/plans/2026-07-20-ai-watchlist.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每个 Task 派发独立 subagent 实现，task 间 review，快速迭代

**2. Inline Execution** - 当前会话内顺序执行，带 checkpoint review

Which approach?
