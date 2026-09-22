# K 线图 + 技术指标 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单股详情页第 10 章节填入 K 线图（日/周/月）+ 全套技术指标（MA/MACD/KDJ/RSI），数据定时缓存到 SQLite。

**Architecture:** scheduler 每日 15:30 调 akshare 拉日K 存入 `kline_daily` 表 → 前端访问 `/api/stock/{code}/kline` 读 SQLite → klinecharts v9 CDN 库渲染。周K/月K 从日K 本地聚合，不单独存储。

**Tech Stack:** SQLite + FastAPI + akshare (`stock_zh_a_hist`) + klinecharts v9 (CDN)

**Spec:** `docs/superpowers/specs/2026-07-22-kline-chart-design.md`

---

## File Structure

| 文件 | 职责 | 操作 |
|---|---|---|
| `src/models/database.py` | `kline_daily` 建表 + `KlineDAO` 类 | 修改 |
| `src/collector/akshare_fetcher.py` | `fetch_kline_data()` 拉日K | 修改 |
| `src/scheduler.py` | 每日 15:30 追加 K 线拉取任务 | 修改 |
| `src/web/routes.py` | `GET /api/stock/{code}/kline` API + 周月聚合 | 修改 |
| `src/web/templates/stock_detail.html` | 第 10 章节替换为 klinecharts 组件 | 修改 |
| `tests/models/test_kline_dao.py` | KlineDAO 测试 | 新建 |
| `tests/collector/test_kline_fetcher.py` | fetch_kline_data 测试（mock akshare） | 新建 |
| `tests/web/test_routes_kline.py` | kline API 测试 | 新建 |

---

### Task 1: kline_daily 表 + KlineDAO

**Files:**
- Modify: `src/models/database.py`（init_database 函数 ~276 行，追加建表）
- Test: `tests/models/test_kline_dao.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/models/test_kline_dao.py
"""KlineDAO 单元测试"""
import pytest
from src.models.database import KlineDAO, init_database, db_conn, get_db_path


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("src.models.database.get_db_path", lambda: db_path)
    init_database()
    return db_path


def test_upsert_and_get_daily(tmp_db):
    """写入日K并读取，验证字段往返"""
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 12345600, "amount": 2.3e8, "turnover": 1.2},
        {"trade_date": "2025-07-22", "open": 18.5, "close": 19.0,
         "high": 19.2, "low": 18.3, "volume": 9876500, "amount": 1.9e8, "turnover": 0.9},
    ])
    records = dao.get_daily("000792")
    assert len(records) == 2
    # 升序：7-21 在前
    assert records[0]["trade_date"] == "2025-07-21"
    assert records[0]["close"] == 18.5
    assert records[1]["trade_date"] == "2025-07-22"


def test_upsert_replace(tmp_db):
    """重复写入同一天数据应覆盖"""
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 12345600, "amount": 2.3e8, "turnover": 1.2},
    ])
    # 覆盖写入
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.1, "close": 18.9,
         "high": 19.0, "low": 17.9, "volume": 15000000, "amount": 2.8e8, "turnover": 1.5},
    ])
    records = dao.get_daily("000792")
    assert len(records) == 1
    assert records[0]["close"] == 18.9


def test_get_latest_date(tmp_db):
    """获取最新已缓存日期"""
    dao = KlineDAO()
    assert dao.get_latest_date("000792") is None
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-20", "open": 18.0, "close": 18.0,
         "high": 18.0, "low": 18.0, "volume": 0, "amount": 0, "turnover": 0},
        {"trade_date": "2025-07-22", "open": 19.0, "close": 19.0,
         "high": 19.0, "low": 19.0, "volume": 0, "amount": 0, "turnover": 0},
    ])
    assert dao.get_latest_date("000792") == "2025-07-22"


def test_get_daily_empty(tmp_db):
    """无数据返回空列表"""
    dao = KlineDAO()
    records = dao.get_daily("999999")
    assert records == []


def test_get_daily_limit(tmp_db):
    """limit 参数限制返回条数"""
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": f"2025-07-{i:02d}", "open": 18.0, "close": 18.0,
         "high": 18.0, "low": 18.0, "volume": 0, "amount": 0, "turnover": 0}
        for i in range(1, 11)  # 10 条
    ])
    records = dao.get_daily("000792", limit=5)
    assert len(records) == 5
    # 仍为升序，返回最近 5 条（即 07-06 ~ 07-10）
    assert records[0]["trade_date"] == "2025-07-06"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/models/test_kline_dao.py -v`
Expected: FAIL with `ImportError: cannot import name 'KlineDAO'`

- [ ] **Step 3: Implement — init_database 追加建表**

在 `src/models/database.py` 的 `init_database()` 函数中，`print(f"[DB] 数据库初始化完成...")` 这行**之前**追加：

```python
        # 2026-07-22 新增：K线日线数据表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS kline_daily (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL,
                close REAL,
                high REAL,
                low REAL,
                volume REAL,
                amount REAL,
                turnover REAL,
                PRIMARY KEY (code, trade_date)
            )
        ''')
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_kline_code_date '
            'ON kline_daily(code, trade_date)'
        )
```

- [ ] **Step 4: Implement — KlineDAO 类**

在 `src/models/database.py` 文件末尾追加 `KlineDAO` 类（在最后一个 DAO 类之后）：

```python
class KlineDAO:
    """K线日线数据 DAO"""

    def upsert_many(self, code: str, records: list[dict]) -> int:
        """批量写入日K（INSERT OR REPLACE）

        records 字段：trade_date, open, close, high, low, volume, amount, turnover
        """
        if not records:
            return 0
        with db_conn() as conn:
            for r in records:
                conn.execute("""
                    INSERT OR REPLACE INTO kline_daily
                    (code, trade_date, open, close, high, low, volume, amount, turnover)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    code, r['trade_date'],
                    r.get('open'), r.get('close'),
                    r.get('high'), r.get('low'),
                    r.get('volume'), r.get('amount'),
                    r.get('turnover'),
                ))
        return len(records)

    def get_daily(self, code: str, limit: int = 250) -> list[dict]:
        """按日期升序返回日K（最近 limit 条）"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM kline_daily WHERE code = ? "
                "ORDER BY trade_date DESC LIMIT ?",
                (code, limit)
            ).fetchall()
        # 反转为升序（API 需要）
        return [dict(r) for r in reversed(rows)]

    def get_latest_date(self, code: str) -> str | None:
        """获取该股票最新已缓存日期"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) as max_date FROM kline_daily WHERE code = ?",
                (code,)
            ).fetchone()
        return row['max_date'] if row else None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/models/test_kline_dao.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add tests/models/test_kline_dao.py src/models/database.py
git commit -m "feat: 新增 kline_daily 表 + KlineDAO"
```

---

### Task 2: fetch_kline_data 采集方法

**Files:**
- Modify: `src/collector/akshare_fetcher.py`（文件末尾追加）
- Test: `tests/collector/test_kline_fetcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/collector/test_kline_fetcher.py
"""fetch_kline_data 单元测试（mock akshare）"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


def test_fetch_kline_data_basic():
    """基本拉取：返回正确字段"""
    fake_df = pd.DataFrame([
        {"日期": "2025-07-21", "开盘": 18.0, "收盘": 18.5, "最高": 18.8,
         "最低": 17.9, "成交量": 12345600, "成交额": 2.3e8, "换手率": 1.2},
        {"日期": "2025-07-22", "开盘": 18.5, "收盘": 19.0, "最高": 19.2,
         "最低": 18.3, "成交量": 9876500, "成交额": 1.9e8, "换手率": 0.9},
    ])

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = fake_df
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")

    assert len(records) == 2
    assert records[0]["trade_date"] == "2025-07-21"
    assert records[0]["open"] == 18.0
    assert records[0]["close"] == 18.5
    assert records[0]["volume"] == 12345600
    assert records[0]["turnover"] == 1.2


def test_fetch_kline_data_empty():
    """akshare 返回空 DataFrame"""
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")
    assert records == []


def test_fetch_kline_data_none():
    """akshare 返回 None"""
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = None
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")
    assert records == []


def test_fetch_kline_data_date_conversion():
    """start_date 的 YYYY-MM-DD 应转为 akshare 的 YYYYMMDD"""
    fake_df = pd.DataFrame([
        {"日期": "2025-07-21", "开盘": 18.0, "收盘": 18.5, "最高": 18.8,
         "最低": 17.9, "成交量": 100, "成交额": 1000.0, "换手率": 0.1},
    ])

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = fake_df
        from src.collector.akshare_fetcher import fetch_kline_data
        fetch_kline_data("000792", start_date="2025-01-01")

    # 验证传给 akshare 的 start_date 是 YYYYMMDD 格式
    call_kwargs = mock_ak.stock_zh_a_hist.call_args
    assert call_kwargs.kwargs.get("start_date") == "20250101" or \
           call_kwargs[1].get("start_date") == "20250101"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/collector/test_kline_fetcher.py -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_kline_data'`

- [ ] **Step 3: Implement — fetch_kline_data**

在 `src/collector/akshare_fetcher.py` 文件末尾追加：

```python
def fetch_kline_data(code: str, start_date: str | None = None,
                     adjust: str = "qfq") -> list[dict]:
    """拉取单只股票的日K线数据。

    参数：
    - code: 6位股票代码
    - start_date: 起始日期（YYYY-MM-DD），None 则拉最近1年
    - adjust: 复权类型，qfq=前复权（默认）

    返回：[{"trade_date", "open", "close", "high", "low", "volume", "amount", "turnover"}, ...]
    """
    import akshare as ak

    # 计算日期范围
    if start_date is None:
        start = now_cn().replace(year=now_cn().year - 1)
    else:
        # YYYY-MM-DD → YYYYMMDD
        start = __import__('datetime').datetime.strptime(start_date, "%Y-%m-%d")
    start_str = start.strftime("%Y%m%d")
    end_str = now_cn().strftime("%Y%m%d")

    # 代码转东方财富格式
    em_code = _code_to_em(code)

    try:
        df = ak.stock_zh_a_hist(
            symbol=em_code, period="daily",
            start_date=start_str, end_date=end_str, adjust=adjust
        )
    except Exception as e:
        logger.warning(f"[K线] 拉取失败 {code}: {e}")
        return []

    if df is None or df.empty:
        return []

    # akshare 返回的列名为中文，需映射
    records = []
    for _, row in df.iterrows():
        records.append({
            "trade_date": str(row["日期"])[:10],
            "open": float(row["开盘"]) if pd.notna(row["开盘"]) else None,
            "close": float(row["收盘"]) if pd.notna(row["收盘"]) else None,
            "high": float(row["最高"]) if pd.notna(row["最高"]) else None,
            "low": float(row["最低"]) if pd.notna(row["最低"]) else None,
            "volume": float(row["成交量"]) if pd.notna(row["成交量"]) else None,
            "amount": float(row["成交额"]) if pd.notna(row["成交额"]) else None,
            "turnover": float(row["换手率"]) if pd.notna(row["换手率"]) else None,
        })
    return records
```

注意：文件顶部需确保已 `import pandas as pd`（检查是否已有，若无则加）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/collector/test_kline_fetcher.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tests/collector/test_kline_fetcher.py src/collector/akshare_fetcher.py
git commit -m "feat: 新增 fetch_kline_data 采集方法"
```

---

### Task 3: scheduler 集成 K 线拉取

**Files:**
- Modify: `src/scheduler.py`（`_check_daily_pipeline` 方法 ~208 行，流水线完成后追加）
- Test: 无独立测试，通过日志和手动验证

- [ ] **Step 1: Implement — scheduler 追加 K 线拉取**

在 `src/scheduler.py` 的 `_check_daily_pipeline` 方法中，找到 `logger.info("[调度器] 每日流水线完成")` 这行（~227 行），在其**之后**追加：

```python
            # 流水线后追加 K 线数据拉取
            self._fetch_kline_daily()
```

然后在 `MarketScheduler` 类中追加新方法（在 `_check_daily_pipeline` 方法之后）：

```python
    def _fetch_kline_daily(self):
        """每日拉取观察池+候选股的K线数据（增量更新）"""
        from src.models.database import KlineDAO
        from src.collector.akshare_fetcher import fetch_kline_data
        from src.models.ai_watchlist import AiWatchlistDAO
        from src.models.database import ScreeningResultDAO

        # 合并需要拉取的代码：观察池 + 候选股 Top25
        watchlist = AiWatchlistDAO().list_all()
        codes = {w['code'] for w in watchlist}
        try:
            candidates = ScreeningResultDAO().get_latest_top(limit=25)
            codes.update(c['code'] for c in candidates)
        except Exception:
            pass

        if not codes:
            logger.info("[调度器] K线拉取：无待拉取股票")
            return

        dao = KlineDAO()
        success = 0
        for code in codes:
            latest = dao.get_latest_date(code)
            records = fetch_kline_data(code, start_date=latest)
            if records:
                dao.upsert_many(code, records)
                success += 1
        logger.info(f"[调度器] K线拉取完成: {success}/{len(codes)} 只成功")
```

- [ ] **Step 2: Verify scheduler 启动无报错**

Run: `python -c "from src.scheduler import MarketScheduler; s = MarketScheduler(); print('OK')"`
Expected: 输出 `OK`（无 ImportError / 语法错误）

- [ ] **Step 3: Commit**

```bash
git add src/scheduler.py
git commit -m "feat: scheduler 每日追加 K 线数据拉取"
```

---

### Task 4: kline API + 周月聚合

**Files:**
- Modify: `src/web/routes.py`（`stock_detail` 路由之后追加 API 路由）
- Test: `tests/web/test_routes_kline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_routes_kline.py
"""kline API 路由测试"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
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


def test_kline_api_empty(client):
    """无数据返回空 klines"""
    resp = client.get("/api/stock/000792/kline?period=daily")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "000792"
    assert data["period"] == "daily"
    assert data["klines"] == []


def test_kline_api_daily(client):
    """写入日K后 API 返回正确格式"""
    from src.models.database import KlineDAO
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 12345600, "amount": 2.3e8, "turnover": 1.2},
        {"trade_date": "2025-07-22", "open": 18.5, "close": 19.0,
         "high": 19.2, "low": 18.3, "volume": 9876500, "amount": 1.9e8, "turnover": 0.9},
    ])
    resp = client.get("/api/stock/000792/kline?period=daily")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "000792"
    assert len(data["klines"]) == 2
    k = data["klines"][0]
    assert "timestamp" in k
    assert k["open"] == 18.0
    assert k["close"] == 18.5
    assert k["volume"] == 12345600
    # timestamp 是毫秒级
    assert k["timestamp"] > 1e12


def test_kline_api_weekly_aggregation(client):
    """周K聚合：同一周的日K合并为一条"""
    from src.models.database import KlineDAO
    dao = KlineDAO()
    # 2025-07-21(周一) ~ 2025-07-25(周五) 同一周
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 10000, "amount": 1e6, "turnover": 1.0},
        {"trade_date": "2025-07-22", "open": 18.5, "close": 19.0,
         "high": 19.2, "low": 18.3, "volume": 20000, "amount": 2e6, "turnover": 2.0},
        {"trade_date": "2025-07-23", "open": 19.0, "close": 18.8,
         "high": 19.5, "low": 18.6, "volume": 15000, "amount": 1.5e6, "turnover": 1.5},
    ])
    resp = client.get("/api/stock/000792/kline?period=weekly")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["klines"]) == 1
    w = data["klines"][0]
    # 周一开盘
    assert w["open"] == 18.0
    # 最后收盘
    assert w["close"] == 18.8
    # 周内最高
    assert w["high"] == 19.5
    # 周内最低
    assert w["low"] == 17.9
    # 成交量求和
    assert w["volume"] == 45000


def test_kline_api_invalid_period(client):
    """无效 period 参数返回 400"""
    resp = client.get("/api/stock/000792/kline?period=invalid")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_routes_kline.py -v`
Expected: FAIL with 404 或路由不存在

- [ ] **Step 3: Implement — 聚合函数 + API 路由**

在 `src/web/routes.py` 中，`stock_detail` 路由函数之后追加：

```python
def _aggregate_kline(daily_records: list[dict], period: str) -> list[dict]:
    """将日K聚合为周K或月K

    period: "weekly" 按自然周（周一~周五）聚合
            "monthly" 按自然月聚合
    """
    from datetime import datetime
    import calendar

    if not daily_records:
        return []

    # 按周/月分组
    groups: dict[str, list[dict]] = {}
    for r in daily_records:
        dt = datetime.strptime(r["trade_date"], "%Y-%m-%d")
        if period == "weekly":
            # ISO 周号作为 key
            key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        else:  # monthly
            key = f"{dt.year}-{dt.month:02d}"
        groups.setdefault(key, []).append(r)

    result = []
    for key, group in groups.items():
        # 排序确保第一条是周/月第一天
        group.sort(key=lambda x: x["trade_date"])
        first = group[0]
        last = group[-1]
        result.append({
            "trade_date": first["trade_date"],  # 用周/月第一天作为日期
            "open": first.get("open"),
            "close": last.get("close"),
            "high": max((g.get("high") or 0) for g in group),
            "low": min((g.get("low") or 999999) for g in group),
            "volume": sum((g.get("volume") or 0) for g in group),
            "amount": sum((g.get("amount") or 0) for g in group),
            "turnover": sum((g.get("turnover") or 0) for g in group),
        })
    return result


def _to_klinecharts(records: list[dict]) -> list[dict]:
    """DB 记录转 klinecharts 所需格式"""
    from datetime import datetime
    result = []
    for r in records:
        dt = datetime.strptime(r["trade_date"], "%Y-%m-%d")
        result.append({
            "timestamp": int(dt.replace(hour=15).timestamp() * 1000),
            "open": r.get("open"),
            "close": r.get("close"),
            "high": r.get("high"),
            "low": r.get("low"),
            "volume": r.get("volume"),
            "turnover": r.get("turnover"),
        })
    return result


@app.get("/api/stock/{code}/kline")
async def stock_kline(code: str, period: str = "daily", limit: int = 250):
    """K线数据 API

    period: daily / weekly / monthly
    返回 klinecharts 所需格式
    """
    import re
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(status_code=404, detail="Invalid code")
    if period not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="period must be daily/weekly/monthly")

    from src.models.database import KlineDAO
    dao = KlineDAO()
    daily = dao.get_daily(code, limit=limit)

    if period != "daily":
        daily = _aggregate_kline(daily, period)

    klines = _to_klinecharts(daily)
    return {"code": code, "period": period, "klines": klines}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_routes_kline.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_routes_kline.py src/web/routes.py
git commit -m "feat: 新增 kline API + 周月聚合"
```

---

### Task 5: 前端 klinecharts 组件

**Files:**
- Modify: `src/web/templates/stock_detail.html`（第 10 章节占位替换 + head 引入 CDN + 底部 JS）

- [ ] **Step 1: Implement — head 引入 klinecharts CDN**

在 `stock_detail.html` 的 `<head>` 中，`</style>` 之前追加（或在 `</head>` 之前）：

```html
    <script src="https://cdn.jsdelivr.net/npm/klinecharts@9/dist/umd/index.min.js"></script>
```

- [ ] **Step 2: Implement — CSS 样式**

在 `stock_detail.html` 的 `<style>` 块中，`/* ========== 技术分析扩展位 ========== */` 注释区域**替换**为：

```css
        /* ========== 行情图表 ========== */
        .sd-kline-toolbar { display: flex; gap: 4px; margin-bottom: 12px; }
        .kline-period-btn {
            padding: 4px 12px; border: 1px solid var(--divider); background: transparent;
            color: var(--text-secondary); border-radius: var(--radius-sm); cursor: pointer;
            font-size: 13px; transition: background 0.15s, color 0.15s;
        }
        .kline-period-btn:hover { background: var(--bg-hover); }
        .kline-period-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .sd-kline-wrap { background: var(--bg-secondary); border-radius: var(--radius); padding: 12px; }
        #kline-chart { width: 100%; height: 500px; }
        .sd-kline-empty { padding: 48px; text-align: center; color: var(--text-tertiary); display: none; }
        .sd-kline-loading { padding: 48px; text-align: center; color: var(--text-tertiary); }
```

- [ ] **Step 3: Implement — 第 10 章节替换**

在 `stock_detail.html` 中找到第 10 章节的占位 section（搜索 `technical-analysis` 或 `技术分析扩展位`），**替换**整个 section 为：

```html
    <!-- 10. 行情图表 -->
    <section class="sd-section" id="technical-analysis">
        <h2 class="sd-section-title">行情图表</h2>
        <div class="sd-kline-toolbar">
            <button class="kline-period-btn active" data-period="daily">日K</button>
            <button class="kline-period-btn" data-period="weekly">周K</button>
            <button class="kline-period-btn" data-period="monthly">月K</button>
        </div>
        <div class="sd-kline-wrap">
            <div id="kline-chart"></div>
            <div class="sd-kline-empty" id="kline-empty">暂无K线数据</div>
            <div class="sd-kline-loading" id="kline-loading">加载中...</div>
        </div>
    </section>
```

- [ ] **Step 4: Implement — 初始化 JS**

在 `stock_detail.html` 的 `</body>` 之前追加：

```html
    <script>
    (function() {
        const code = "{{ code }}";
        let chart = null;

        function initChart() {
            if (typeof klinecharts === 'undefined') {
                document.getElementById('kline-loading').textContent = 'K线库加载失败，请刷新重试';
                return;
            }
            chart = klinecharts.init('kline-chart');
            // 主图叠加 MA 均线
            chart.createIndicator('MA', false, { id: 'candle_pane' });
            // 副图：成交量
            chart.createIndicator('VOL');
            loadKline('daily');
        }

        async function loadKline(period) {
            document.getElementById('kline-loading').style.display = 'block';
            document.getElementById('kline-empty').style.display = 'none';
            document.getElementById('kline-chart').style.display = 'none';

            try {
                const resp = await fetch(`/api/stock/${code}/kline?period=${period}`);
                const data = await resp.json();
                document.getElementById('kline-loading').style.display = 'none';

                if (!data.klines || data.klines.length === 0) {
                    document.getElementById('kline-empty').style.display = 'block';
                    return;
                }
                document.getElementById('kline-chart').style.display = 'block';
                chart.applyNewData(data.klines);
            } catch (e) {
                document.getElementById('kline-loading').textContent = '加载失败: ' + e.message;
            }
        }

        // 日/周/月切换
        document.querySelectorAll('.kline-period-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.kline-period-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                loadKline(btn.dataset.period);
            });
        });

        // 页面加载后初始化
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initChart);
        } else {
            initChart();
        }
    })();
    </script>
```

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `python -m pytest tests/web/test_routes_stock_detail.py -v`
Expected: 3 passed（已有测试不受影响）

- [ ] **Step 6: Commit**

```bash
git add src/web/templates/stock_detail.html
git commit -m "feat: 单股详情页第10章节填入 klinecharts K线图"
```

---

### Task 6: 端到端验证 + 部署生产服务器

**Files:** 无代码修改，纯验证

- [ ] **Step 1: 本地全量测试**

Run: `python -m pytest tests/models/test_kline_dao.py tests/collector/test_kline_fetcher.py tests/web/test_routes_kline.py tests/web/test_routes_stock_detail.py -v`
Expected: All passed

- [ ] **Step 2: 部署到生产服务器**

```bash
scp src/models/database.py <生产服务器>:<部署目录>/src/models/database.py
scp src/collector/akshare_fetcher.py <生产服务器>:<部署目录>/src/collector/akshare_fetcher.py
scp src/scheduler.py <生产服务器>:<部署目录>/src/scheduler.py
scp src/web/routes.py <生产服务器>:<部署目录>/src/web/routes.py
scp src/web/templates/stock_detail.html <生产服务器>:<部署目录>/src/web/templates/stock_detail.html
ssh <生产服务器> "sudo systemctl restart stock-dashboard"
```

- [ ] **Step 3: 生产服务器上手动触发首次 K 线拉取**

```bash
ssh <生产服务器> "cd <部署目录> && python -c \"
from src.models.database import init_database, KlineDAO
from src.collector.akshare_fetcher import fetch_kline_data
init_database()
# 拉取观察池 5 只
codes = ['000792', '600519', '000001', '002415', '600036']
for code in codes:
    records = fetch_kline_data(code)
    KlineDAO().upsert_many(code, records)
    print(f'{code}: {len(records)} 条')
\""
```

- [ ] **Step 4: 验证 API + 页面**

```bash
ssh <生产服务器> "curl -s http://localhost:9527/api/stock/000792/kline?period=daily | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"klines: {len(d[\"klines\"])} 条, 首条: {d[\"klines\"][0] if d[\"klines\"] else None}\")'"
ssh <生产服务器> "curl -s -o /dev/null -w '%{http_code}' http://localhost:9527/stock/000792"
```

Expected: klines 有数据，页面 200

- [ ] **Step 5: 浏览器验证 K 线渲染**

- 访问 `http://<生产服务器>:9527/stock/000792`
- 确认 K 线图区域显示蜡烛图 + 成交量
- 点击"周K"/"月K"切换
- 滚轮缩放、十字线拖拽

- [ ] **Step 6: Push GitHub**

```bash
git push origin main
```

- [ ] **Step 7: 更新 roadmap 变更记录**

在 `docs/roadmap.md` 变更记录表追加一行，标记 P0① 完成。

- [ ] **Step 8: Commit roadmap 更新**

```bash
git add docs/roadmap.md
git commit -m "docs: P0① K线图完成，更新 roadmap"
git push origin main
```
