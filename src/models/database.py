"""
股票盯盘看板 - 数据模型与数据库层
SQLite 本地存储，无外部依赖
"""

import sqlite3
import os
from pathlib import Path
from datetime import datetime, date
from typing import Optional


def get_db_path() -> str:
    """获取数据库文件路径"""
    db_dir = Path(__file__).parent.parent.parent / "data" / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "stock_dashboard.db")


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA_SQL = """
-- 大盘指数数据
CREATE TABLE IF NOT EXISTS market_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_code TEXT NOT NULL,        -- 指数代码：上证综指 000001, 深证成指 399001, 创业板指 399006
    index_name TEXT NOT NULL,        -- 指数名称
    current_value REAL,
    change_percent REAL,            -- 涨跌幅 %
    change_amount REAL,             -- 涨跌额
    volume REAL,                    -- 成交量
    amount REAL,                    -- 成交额
    pe REAL,                        -- 市盈率
    pb REAL,                        -- 市净率
    timestamp TEXT NOT NULL,         -- ISO 时间戳
    date TEXT NOT NULL               -- 日期 YYYY-MM-DD
);

-- 股票基础数据缓存（全市场快照）
CREATE TABLE IF NOT EXISTS stock_snapshot (
    code TEXT PRIMARY KEY,            -- 股票代码 600519
    name TEXT NOT NULL,               -- 股票名称
    market TEXT NOT NULL DEFAULT 'A', -- 市场 A/HK/US
    sector TEXT,                      -- 行业板块
    pe REAL,                          -- 市盈率 TTM
    pb REAL,                          -- 市净率
    ps REAL,                          -- 市销率
    market_cap REAL,                  -- 总市值（亿）
    circulating_cap REAL,             -- 流通市值（亿）
    roe REAL,                         -- ROE %
    revenue REAL,                     -- 营收（亿）
    revenue_growth REAL,              -- 营收增长率 %
    profit REAL,                      -- 净利润（亿）
    profit_growth REAL,               -- 净利润增长率 %
    debt_ratio REAL,                  -- 资产负债率 %
    dividend_yield REAL,              -- 股息率 %
    current_price REAL,               -- 现价
    high_52w REAL,                    -- 52周最高
    low_52w REAL,                     -- 52周最低
    is_st INTEGER DEFAULT 0,          -- 是否ST
    list_date TEXT,                   -- 上市日期
    snapshot_date TEXT NOT NULL        -- 快照日期
);

-- 筛选结果（每次运行保存）
CREATE TABLE IF NOT EXISTS screening_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,             -- 运行批次 ID (YYYYMMDD_HHMMSS)
    run_date TEXT NOT NULL,           -- 运行日期
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    score REAL,                       -- 综合评分 0-100
    pe REAL,
    pb REAL,
    roe REAL,
    revenue_growth REAL,
    profit_growth REAL,
    debt_ratio REAL,
    market_cap REAL,
    reason TEXT,                      -- 符合哪些条件
    ai_analysis TEXT,                 -- AI 选股分析 JSON
    ai_investment_strategy TEXT,      -- AI 投资策略
    ai_trade_strategy TEXT,           -- AI 买卖策略
    status TEXT DEFAULT 'active',     -- active / eliminated（后续排除）
    eliminated_date TEXT,             -- 排除日期
    eliminated_reason TEXT            -- 排除原因
);

-- AI 分析历史
CREATE TABLE IF NOT EXISTS ai_analysis_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- 每日运行日志
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    start_time TEXT NOT NULL,
    end_time TEXT,
    status TEXT DEFAULT 'running',    -- running / completed / failed
    total_stocks INTEGER DEFAULT 0,
    screened_count INTEGER DEFAULT 0,
    analyzed_count INTEGER DEFAULT 0,
    error_message TEXT
);

-- 关键索引
CREATE INDEX IF NOT EXISTS idx_screening_run_date ON screening_result(run_date);
CREATE INDEX IF NOT EXISTS idx_screening_code ON screening_result(code);
CREATE INDEX IF NOT EXISTS idx_market_index_date ON market_index(date);
CREATE INDEX IF NOT EXISTS idx_stock_snapshot_sector ON stock_snapshot(sector);
"""


def init_database():
    """初始化数据库，创建表结构"""
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    print(f"[DB] 数据库初始化完成: {get_db_path()}")


# -------- 数据访问对象 --------

class MarketIndexDAO:
    def save(self, records: list[dict]):
        conn = get_connection()
        for r in records:
            conn.execute("""
                INSERT INTO market_index (index_code, index_name, current_value,
                    change_percent, change_amount, volume, amount, pe, pb, timestamp, date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (r['index_code'], r['index_name'], r.get('current_value'),
                  r.get('change_percent'), r.get('change_amount'),
                  r.get('volume'), r.get('amount'), r.get('pe'),
                  r.get('pb'), r['timestamp'], r['date']))
        conn.commit()
        conn.close()

    def get_latest(self, index_code: str = None) -> list[dict]:
        conn = get_connection()
        if index_code:
            rows = conn.execute("""
                SELECT * FROM market_index WHERE index_code = ?
                ORDER BY timestamp DESC LIMIT 1
            """, (index_code,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT m.* FROM market_index m
                INNER JOIN (
                    SELECT index_code, MAX(timestamp) as max_ts
                    FROM market_index GROUP BY index_code
                ) latest ON m.index_code = latest.index_code AND m.timestamp = latest.max_ts
            """).fetchall()
        conn.close()
        return [dict(r) for r in rows]


class StockSnapshotDAO:
    def save_batch(self, records: list[dict]):
        conn = get_connection()
        conn.execute("BEGIN")
        for r in records:
            conn.execute("""
                INSERT OR REPLACE INTO stock_snapshot
                (code, name, market, sector, pe, pb, ps, market_cap, circulating_cap,
                 roe, revenue, revenue_growth, profit, profit_growth, debt_ratio,
                 dividend_yield, current_price, high_52w, low_52w, is_st, list_date, snapshot_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (r['code'], r['name'], r.get('market', 'A'),
                  r.get('sector'), r.get('pe'), r.get('pb'), r.get('ps'),
                  r.get('market_cap'), r.get('circulating_cap'),
                  r.get('roe'), r.get('revenue'), r.get('revenue_growth'),
                  r.get('profit'), r.get('profit_growth'), r.get('debt_ratio'),
                  r.get('dividend_yield'), r.get('current_price'),
                  r.get('high_52w'), r.get('low_52w'),
                  1 if r.get('is_st') else 0, r.get('list_date'),
                  r['snapshot_date']))
        conn.commit()
        conn.close()

    def get_latest_snapshot_date(self) -> Optional[str]:
        conn = get_connection()
        row = conn.execute("SELECT snapshot_date FROM stock_snapshot ORDER BY snapshot_date DESC LIMIT 1").fetchone()
        conn.close()
        return row['snapshot_date'] if row else None

    def count(self) -> int:
        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) as cnt FROM stock_snapshot").fetchone()
        conn.close()
        return row['cnt']


class ScreeningResultDAO:
    def save_batch(self, records: list[dict]):
        conn = get_connection()
        conn.execute("BEGIN")
        for r in records:
            conn.execute("""
                INSERT INTO screening_result
                (run_id, run_date, code, name, score, pe, pb, roe,
                 revenue_growth, profit_growth, debt_ratio, market_cap, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (r['run_id'], r['run_date'], r['code'], r['name'],
                  r.get('score'), r.get('pe'), r.get('pb'), r.get('roe'),
                  r.get('revenue_growth'), r.get('profit_growth'),
                  r.get('debt_ratio'), r.get('market_cap'), r.get('reason')))
        conn.commit()
        conn.close()

    def update_ai_analysis(self, run_id: str, code: str, analysis: str, strategy: str, trade_strategy: str):
        conn = get_connection()
        conn.execute("""
            UPDATE screening_result
            SET ai_analysis = ?, ai_investment_strategy = ?, ai_trade_strategy = ?
            WHERE run_id = ? AND code = ?
        """, (analysis, strategy, trade_strategy, run_id, code))
        conn.commit()
        conn.close()

    def get_latest_results(self, limit: int = 50) -> list[dict]:
        conn = get_connection()
        rows = conn.execute("""
            SELECT sr.* FROM screening_result sr
            WHERE sr.run_date = (SELECT MAX(run_date) FROM screening_result)
            ORDER BY sr.score DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_history(self, code: str, limit: int = 10) -> list[dict]:
        conn = get_connection()
        rows = conn.execute("""
            SELECT * FROM screening_result
            WHERE code = ? AND status = 'active'
            ORDER BY run_date DESC LIMIT ?
        """, (code, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_latest_run_id(self) -> Optional[str]:
        conn = get_connection()
        row = conn.execute("SELECT MAX(run_id) as rid FROM run_log WHERE status = 'completed'").fetchone()
        conn.close()
        return row['rid'] if row and row['rid'] else None


class AiAnalysisLogDAO:
    """AI 分析日志 DAO"""
    def log(self, run_id: str, code: str, model: str,
            prompt_tokens: int, completion_tokens: int, cost: float):
        conn = get_connection()
        conn.execute("""
            INSERT INTO ai_analysis_log (run_id, code, model, prompt_tokens,
                completion_tokens, cost, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (run_id, code, model, prompt_tokens, completion_tokens,
              cost, datetime.now().isoformat()))
        conn.commit()
        conn.close()


class RunLogDAO:
    def start_run(self, run_id: str) -> str:
        conn = get_connection()
        conn.execute("""
            INSERT INTO run_log (run_id, start_time, status)
            VALUES (?, ?, 'running')
        """, (run_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return run_id

    def complete_run(self, run_id: str, total: int, screened: int, analyzed: int, error: str = None):
        conn = get_connection()
        status = 'failed' if error else 'completed'
        conn.execute("""
            UPDATE run_log SET end_time = ?, status = ?,
                total_stocks = ?, screened_count = ?, analyzed_count = ?,
                error_message = ?
            WHERE run_id = ?
        """, (datetime.now().isoformat(), status, total, screened, analyzed, error, run_id))
        conn.commit()
        conn.close()

    def get_latest_run(self) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute("""
            SELECT * FROM run_log ORDER BY start_time DESC LIMIT 1
        """).fetchone()
        conn.close()
        return dict(row) if row else None
