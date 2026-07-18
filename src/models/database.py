"""Stock Dashboard - 数据模型与数据库层
SQLite 本地存储，无外部依赖
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from src.utils import now_cn


def get_db_path() -> str:
    """获取数据库文件路径"""
    db_dir = Path(__file__).parent.parent.parent / "data" / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "stock_dashboard.db")


def get_connection() -> sqlite3.Connection:
    """获取数据库连接（裸连接，调用方需自行 close）。

    推荐改用 ``db_conn()`` 上下文管理器以避免连接泄漏。
    """
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn():
    """数据库连接上下文管理器：自动 commit / rollback / close。

    使用示例::

        with db_conn() as conn:
            conn.execute("INSERT ...", (...))
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    gross_margin REAL,                -- 毛利率 %
    net_margin REAL,                  -- 净利率 %
    ocf_per_share REAL,               -- 每股经营现金流
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

-- AI 分析日志
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

-- 股票AI分析历史（逐日累积，可追溯）
CREATE TABLE IF NOT EXISTS stock_analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    run_id TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    score REAL,
    ai_analysis TEXT,
    ai_trade_strategy TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_history_code_date ON stock_analysis_history(stock_code, analysis_date);
CREATE INDEX IF NOT EXISTS idx_analysis_history_run ON stock_analysis_history(run_id);

-- 财务历史数据（按季度/年度累积）
CREATE TABLE IF NOT EXISTS financial_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,          -- 股票代码 600519
    report_date TEXT NOT NULL,         -- 财报日期 YYYY-MM-DD
    report_type TEXT DEFAULT 'Q',      -- A=年报(12-31), Q=季报
    roe REAL,                          -- ROE %
    gross_margin REAL,                 -- 毛利率 %
    net_margin REAL,                   -- 销售净利率 % (XSJLL)
    ocf_per_share REAL,                -- 每股经营现金流
    debt_ratio REAL,                   -- 资产负债率 %
    revenue_growth REAL,               -- 营收同比增长 %
    profit_growth REAL,                -- 净利润同比增长 %
    net_profit REAL,                   -- 归母净利润（元）
    interest_coverage REAL,            -- 利息覆盖倍数 (INTSTCOVRATE)
    fcf REAL,                          -- 自由现金流（元, FCFF，年报口径）
    total_shares REAL,                 -- 总股本
    roic REAL,                         -- 投入资本回报率 %
    eps REAL,                          -- 基本每股收益
    data_source TEXT DEFAULT 'eastmoney',
    created_at TEXT NOT NULL,
    UNIQUE(stock_code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_financial_history_code ON financial_history(stock_code);
CREATE INDEX IF NOT EXISTS idx_financial_history_date ON financial_history(report_date);

-- 财务汇总快照（从 financial_history 计算的衍生指标）
CREATE TABLE IF NOT EXISTS financial_summary (
    stock_code TEXT PRIMARY KEY,
    roe_5y_avg REAL,                   -- 5年平均ROE
    roe_5y_count INTEGER,              -- 参与计算的年数
    gross_margin_5y_avg REAL,          -- 5年平均毛利率
    net_margin_5y_avg REAL,            -- 5年平均净利率
    ocf_5y_trend INTEGER,              -- OCF/股5年趋势: 1=增长, 0=波动, -1=下降
    ocf_latest REAL,                   -- 最新OCF/股
    ocf_positive_years INTEGER,        -- OCF为正的年数
    debt_ratio_latest REAL,            -- 最新负债率
    net_profit_5y_sum REAL,            -- 5年累积归母净利
    intcov_5y_avg REAL,                -- 5年平均利息覆盖倍数
    fcf_5y_sum REAL,                   -- 5年累积自由现金流
    share_dilution_5y REAL,            -- 5年股本稀释率 %
    roic_5y_avg REAL,                  -- 5年平均ROIC
    -- 10年拓展字段（2026-07-08 新增，用于巴菲特风格10年评估）
    roe_10y_avg REAL,                  -- 10年平均ROE
    net_margin_10y_avg REAL,           -- 10年平均净利率
    intcov_10y_avg REAL,               -- 10年平均利息覆盖
    fcf_10y_sum REAL,                  -- 10年累积自由现金流
    share_dilution_10y REAL,           -- 10年股本稀释率 %
    fcf_positive_years_10 INTEGER,     -- 10年中FCF为正的年数
    roe_volatility REAL,               -- 10年ROE标准差（越小越稳定）
    roe_improvement REAL,              -- 5年均ROE - 10年均ROE（正=改善）
    roic_10y_avg REAL,                 -- 10年平均ROIC
    data_years TEXT,                    -- 数据覆盖区间如 "2020-2026"
    updated_at TEXT NOT NULL
);

-- 流水线进度跟踪
CREATE TABLE IF NOT EXISTS pipeline_progress (
    run_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL DEFAULT 'idle',
    stage_label TEXT DEFAULT '',
    total_stocks INTEGER DEFAULT 0,
    processed_stocks INTEGER DEFAULT 0,
    ai_total INTEGER DEFAULT 0,
    ai_done INTEGER DEFAULT 0,
    ai_failed INTEGER DEFAULT 0,
    started_at TEXT,
    updated_at TEXT
);
"""


def init_database():
    """初始化数据库，创建表结构"""
    with db_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        # 2026-07-18 新增：watchlist 钉选表（用户主动钉选的股票，不受 Top20 限制）
        conn.execute('''
            CREATE TABLE IF NOT EXISTS watchlist (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                note TEXT,
                latest_price REAL,
                last_signal TEXT,
                last_updated TEXT
            )
        ''')
        # 向后兼容：为旧表增加新字段（如果不存在）
        _add_column_if_not_exists(conn, 'screening_result', 'gross_margin', 'REAL')
        _add_column_if_not_exists(conn, 'screening_result', 'ocf_per_share', 'REAL')
        _add_column_if_not_exists(conn, 'screening_result', 'net_margin', 'REAL')
        _add_column_if_not_exists(conn, 'financial_history', 'interest_coverage', 'REAL')
        _add_column_if_not_exists(conn, 'financial_history', 'fcf', 'REAL')
        _add_column_if_not_exists(conn, 'financial_history', 'total_shares', 'REAL')
        _add_column_if_not_exists(conn, 'financial_history', 'roic', 'REAL')
        # 2026-07-08 新增10年拓展字段
        for col in ['roe_10y_avg', 'net_margin_10y_avg', 'intcov_10y_avg',
                     'fcf_10y_sum', 'share_dilution_10y', 'fcf_positive_years_10',
                     'roe_volatility', 'roe_improvement', 'roic_10y_avg']:
            _add_column_if_not_exists(conn, 'financial_summary', col, 'REAL')
        _add_column_if_not_exists(conn, 'financial_summary', 'fcf_positive_years_10', 'INTEGER')
        _add_column_if_not_exists(conn, 'financial_history', 'eps', 'REAL')
        _add_column_if_not_exists(conn, 'financial_summary', 'intcov_5y_avg', 'REAL')
        _add_column_if_not_exists(conn, 'financial_summary', 'fcf_5y_sum', 'REAL')
        _add_column_if_not_exists(conn, 'financial_summary', 'share_dilution_5y', 'REAL')
        _add_column_if_not_exists(conn, 'financial_summary', 'roic_5y_avg', 'REAL')
    print(f"[DB] 数据库初始化完成: {get_db_path()}")


def _add_column_if_not_exists(conn, table: str, column: str, col_type: str):
    """安全添加列：检查是否存在，不存在则 ALTER TABLE ADD"""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        print(f"[DB] 表 {table} 新增列: {column} {col_type}")


# -------- 数据访问对象 --------

class MarketIndexDAO:
    def save(self, records: list[dict]):
        with db_conn() as conn:
            for r in records:
                conn.execute("""
                    INSERT INTO market_index (index_code, index_name, current_value,
                        change_percent, change_amount, volume, amount, pe, pb, timestamp, date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (r['index_code'], r['index_name'], r.get('current_value'),
                      r.get('change_percent'), r.get('change_amount'),
                      r.get('volume'), r.get('amount'), r.get('pe'),
                      r.get('pb'), r['timestamp'], r['date']))

    def get_latest(self, index_code: str = None) -> list[dict]:
        with db_conn() as conn:
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
                        FROM market_index
                        WHERE index_code LIKE 'sh%' OR index_code LIKE 'sz%'
                        GROUP BY index_code
                    ) latest ON m.index_code = latest.index_code AND m.timestamp = latest.max_ts
                    WHERE m.index_code LIKE 'sh%' OR m.index_code LIKE 'sz%'
                """).fetchall()
        return [dict(r) for r in rows]


    def cleanup_old(self, keep_days: int = 30) -> int:
        """清理 keep_days 天前的大盘数据，但保留每日每指数最后一条（用于历史回溯）。

        返回删除的行数。30 分钟轮询下，每指数每日约 16 条，保留 30 天 ≈ 480 条/指数。
        """
        from src.utils import now_cn
        from datetime import timedelta
        cutoff = (now_cn() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        with db_conn() as conn:
            # 删除 keep_days 前的记录，但保留每个 (index_code, date) 最新一条
            # 用 NOT IN 子查询保留每日最后一条
            conn.execute("""
                DELETE FROM market_index
                WHERE date < ?
                  AND id NOT IN (
                    SELECT MAX(id) FROM market_index
                    WHERE date < ?
                    GROUP BY index_code, date
                  )
            """, (cutoff, cutoff))
            deleted = conn.total_changes
        return deleted


class StockSnapshotDAO:
    def save_batch(self, records: list[dict]):
        with db_conn() as conn:
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

    def get_latest_snapshot_date(self) -> Optional[str]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT snapshot_date FROM stock_snapshot ORDER BY snapshot_date DESC LIMIT 1"
            ).fetchone()
        return row['snapshot_date'] if row else None

    def count(self) -> int:
        with db_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM stock_snapshot").fetchone()
        return row['cnt']


class ScreeningResultDAO:
    def save_batch(self, records: list[dict]):
        with db_conn() as conn:
            for r in records:
                conn.execute("""
                    INSERT INTO screening_result
                    (run_id, run_date, code, name, score, pe, pb, roe,
                     gross_margin, net_margin, ocf_per_share,
                     revenue_growth, profit_growth, debt_ratio, market_cap, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (r['run_id'], r['run_date'], r['code'], r['name'],
                      r.get('score'), r.get('pe'), r.get('pb'), r.get('roe'),
                      r.get('gross_margin'), r.get('net_margin'), r.get('ocf_per_share'),
                      r.get('revenue_growth'), r.get('profit_growth'),
                      r.get('debt_ratio'), r.get('market_cap'), r.get('reason')))

    def update_ai_analysis(self, run_id: str, code: str, analysis: str, strategy: str, trade_strategy: str):
        with db_conn() as conn:
            conn.execute("""
                UPDATE screening_result
                SET ai_analysis = ?, ai_investment_strategy = ?, ai_trade_strategy = ?
                WHERE run_id = ? AND code = ?
            """, (analysis, strategy, trade_strategy, run_id, code))

    def get_latest_results(self, limit: int = 50) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT sr.* FROM screening_result sr
                WHERE sr.run_id = (SELECT MAX(run_id) FROM screening_result)
                ORDER BY sr.score DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_history(self, code: str, limit: int = 10) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM screening_result
                WHERE code = ? AND status = 'active'
                ORDER BY run_date DESC LIMIT ?
            """, (code, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_run_id(self) -> Optional[str]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT MAX(run_id) as rid FROM run_log WHERE status = 'completed'"
            ).fetchone()
        return row['rid'] if row and row['rid'] else None

    def get_results_for_run(self, run_id: str) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_result WHERE run_id = ? ORDER BY score DESC",
                (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]


class AiAnalysisLogDAO:
    def log(self, run_id: str, code: str, model: str,
            prompt_tokens: int, completion_tokens: int, cost: float):
        with db_conn() as conn:
            conn.execute("""
                INSERT INTO ai_analysis_log (run_id, code, model, prompt_tokens,
                    completion_tokens, cost, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (run_id, code, model, prompt_tokens, completion_tokens,
                  cost, now_cn().isoformat()))


class RunLogDAO:
    def start_run(self, run_id: str) -> str:
        with db_conn() as conn:
            conn.execute("""
                INSERT INTO run_log (run_id, start_time, status)
                VALUES (?, ?, 'running')
            """, (run_id, now_cn().isoformat()))
        return run_id

    def complete_run(self, run_id: str, total: int, screened: int, analyzed: int, error: str = None):
        status = 'failed' if error else 'completed'
        with db_conn() as conn:
            conn.execute("""
                UPDATE run_log SET end_time = ?, status = ?,
                    total_stocks = ?, screened_count = ?, analyzed_count = ?,
                    error_message = ?
                WHERE run_id = ?
            """, (now_cn().isoformat(), status, total, screened, analyzed, error, run_id))

    def update_screened_count(self, run_id: str, screened: int):
        with db_conn() as conn:
            conn.execute(
                "UPDATE run_log SET screened_count = ? WHERE run_id = ?",
                (screened, run_id)
            )

    def update_analyzed_count(self, run_id: str, analyzed: int):
        with db_conn() as conn:
            conn.execute(
                "UPDATE run_log SET analyzed_count = ? WHERE run_id = ?",
                (analyzed, run_id)
            )

    def get_latest_run(self) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM run_log ORDER BY start_time DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_latest_completed_run_id(self) -> Optional[str]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT run_id FROM run_log WHERE status='completed' "
                "ORDER BY start_time DESC LIMIT 1"
            ).fetchone()
        return row['run_id'] if row else None


class PipelineProgressDAO:
    def init_run(self, run_id: str, stage: str = 'idle', stage_label: str = '',
                 total: int = 0, ai_total: int = 0):
        now = now_cn().isoformat()
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO pipeline_progress
                (run_id, stage, stage_label, total_stocks, processed_stocks, ai_total, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (run_id, stage, stage_label, total, total, ai_total, now, now))

    def update(self, run_id: str, stage: str = None, stage_label: str = None,
               processed: int = None, total: int = None, ai_total: int = None,
               ai_done: int = None, ai_failed: int = None):
        sets = []
        params = []
        if stage is not None:
            sets.append('stage = ?')
            params.append(stage)
        if stage_label is not None:
            sets.append('stage_label = ?')
            params.append(stage_label)
        if processed is not None:
            sets.append('processed_stocks = ?')
            params.append(processed)
        if total is not None:
            sets.append('total_stocks = ?')
            params.append(total)
        if ai_total is not None:
            sets.append('ai_total = ?')
            params.append(ai_total)
        if ai_done is not None:
            sets.append('ai_done = ?')
            params.append(ai_done)
        if ai_failed is not None:
            sets.append('ai_failed = ?')
            params.append(ai_failed)
        if not sets:
            return
        sets.append('updated_at = ?')
        params.append(now_cn().isoformat())
        params.append(run_id)
        with db_conn() as conn:
            conn.execute(
                f"UPDATE pipeline_progress SET {','.join(sets)} WHERE run_id = ?",
                params
            )

    def get_progress(self) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                'SELECT * FROM pipeline_progress ORDER BY started_at DESC LIMIT 1'
            ).fetchone()
        return dict(row) if row else None

    def get_progress_for_run(self, run_id: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                'SELECT * FROM pipeline_progress WHERE run_id = ?', (run_id,)
            ).fetchone()
        return dict(row) if row else None


class StockAnalysisHistoryDAO:
    def save(self, stock_code: str, run_id: str, score: float,
             ai_analysis: str, ai_trade_strategy: str):
        now = now_cn().isoformat()
        with db_conn() as conn:
            conn.execute("""
                INSERT INTO stock_analysis_history
                (stock_code, run_id, analysis_date, score, ai_analysis, ai_trade_strategy, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (stock_code, run_id, now[:10], score, ai_analysis, ai_trade_strategy, now))

    def get_history(self, code: str, limit: int = 10) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM stock_analysis_history
                WHERE stock_code = ?
                ORDER BY analysis_date DESC, id DESC LIMIT ?
            """, (code, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_for_code(self, code: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute("""
                SELECT * FROM stock_analysis_history
                WHERE stock_code = ?
                ORDER BY analysis_date DESC, id DESC LIMIT 1
            """, (code,)).fetchone()
        return dict(row) if row else None


class FinancialHistoryDAO:
    """财务历史数据 DAO（时序表 — 按日累积）"""

    def batch_save(self, records: list[dict]):
        """批量保存财务历史（UPSERT 避免重复）"""
        now = now_cn().isoformat()
        with db_conn() as conn:
            for r in records:
                conn.execute("""
                    INSERT OR REPLACE INTO financial_history
                    (stock_code, report_date, report_type, roe, gross_margin, net_margin,
                     ocf_per_share, debt_ratio, revenue_growth, profit_growth,
                     net_profit, interest_coverage, fcf, total_shares, roic, eps,
                     data_source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r['stock_code'], r['report_date'],
                    'A' if r['report_date'].endswith('12-31') else 'Q',
                    r.get('roe'), r.get('gross_margin'), r.get('net_margin'),
                    r.get('ocf_per_share'), r.get('debt_ratio'),
                    r.get('revenue_growth'), r.get('profit_growth'),
                    r.get('net_profit'), r.get('interest_coverage'),
                    r.get('fcf'), r.get('total_shares'), r.get('roic'), r.get('eps'),
                    'eastmoney', now
                ))

    def get_annual_reports(self, code: str) -> list[dict]:
        """获取某只股票的年报数据（最新在前）"""
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM financial_history
                WHERE stock_code = ? AND report_type = 'A'
                ORDER BY report_date DESC
            """, (code,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_annual(self, min_year: int = 2016) -> list[dict]:
        """获取所有股票的年报数据（默认2016起，覆盖10年）"""
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM financial_history
                WHERE report_type = 'A' AND substr(report_date, 1, 4) >= ?
                ORDER BY stock_code, report_date
            """, (str(min_year),)).fetchall()
        return [dict(r) for r in rows]

    def count_stocks_with_data(self) -> int:
        """有历史数据的股票数量"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT stock_code) as cnt FROM financial_history"
            ).fetchone()
        return row['cnt'] if row else 0

    def get_data_years(self, code: str) -> tuple:
        """数据覆盖区间"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT MIN(report_date) as min_d, MAX(report_date) as max_d "
                "FROM financial_history WHERE stock_code = ?", (code,)
            ).fetchone()
        if row and row['min_d']:
            return (row['min_d'][:4], row['max_d'][:4])
        return (None, None)

    def has_code(self, code: str) -> bool:
        """是否已有历史数据"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM financial_history WHERE stock_code = ? LIMIT 1",
                (code,)
            ).fetchone()
        return row is not None

    def get_latest_report_dates(self, codes: list[str]) -> dict[str, str]:
        """批量获取每只股票最新的报告期，返回 {code: report_date}。"""
        if not codes:
            return {}
        placeholders = ','.join('?' * len(codes))
        with db_conn() as conn:
            rows = conn.execute(
                f"""SELECT stock_code, MAX(report_date) as max_date
                    FROM financial_history
                    WHERE stock_code IN ({placeholders})
                    GROUP BY stock_code""",
                codes,
            ).fetchall()
        return {r['stock_code']: r['max_date'] for r in rows}


class FinancialSummaryDAO:
    """财务汇总快照 DAO"""

    def save(self, code: str, summary: dict):
        now = now_cn().isoformat()
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO financial_summary
                (stock_code, roe_5y_avg, roe_5y_count, gross_margin_5y_avg,
                 net_margin_5y_avg, ocf_5y_trend, ocf_latest,
                 ocf_positive_years, debt_ratio_latest, net_profit_5y_sum,
                 intcov_5y_avg, fcf_5y_sum, share_dilution_5y, roic_5y_avg,
                 roe_10y_avg, net_margin_10y_avg, intcov_10y_avg,
                 fcf_10y_sum, share_dilution_10y, fcf_positive_years_10,
                 roe_volatility, roe_improvement, roic_10y_avg,
                 data_years, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                code,
                summary.get('roe_5y_avg'),
                summary.get('roe_5y_count'),
                summary.get('gross_margin_5y_avg'),
                summary.get('net_margin_5y_avg'),
                summary.get('ocf_5y_trend'),
                summary.get('ocf_latest'),
                summary.get('ocf_positive_years'),
                summary.get('debt_ratio_latest'),
                summary.get('net_profit_5y_sum'),
                summary.get('intcov_5y_avg'),
                summary.get('fcf_5y_sum'),
                summary.get('share_dilution_5y'),
                summary.get('roic_5y_avg'),
                # 10年新增字段
                summary.get('roe_10y_avg'),
                summary.get('net_margin_10y_avg'),
                summary.get('intcov_10y_avg'),
                summary.get('fcf_10y_sum'),
                summary.get('share_dilution_10y'),
                summary.get('fcf_positive_years_10'),
                summary.get('roe_volatility'),
                summary.get('roe_improvement'),
                summary.get('roic_10y_avg'),
                summary.get('data_years'),
                now
            ))

    def get(self, code: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM financial_summary WHERE stock_code = ?", (code,)
            ).fetchone()
        return dict(row) if row else None

    def get_batch(self, codes: list[str]) -> dict[str, dict]:
        if not codes:
            return {}
        placeholders = ','.join('?' * len(codes))
        with db_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM financial_summary WHERE stock_code IN ({placeholders})",
                codes
            ).fetchall()
        return {r['stock_code']: dict(r) for r in rows}

class WatchlistDAO:
    """用户钉选的股票（不受每日 Top20 限制，用于长期跟踪已关注的标的）"""

    def add(self, code: str, name: str, note: str = None) -> bool:
        """加入钉选（已存在则忽略）"""
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO watchlist (code, name, added_at, note) VALUES (?, ?, ?, ?)",
                (code, name, now_cn().isoformat(), note)
            )
            return cur.rowcount > 0

    def remove(self, code: str) -> bool:
        """取消钉选"""
        with db_conn() as conn:
            cur = conn.execute("DELETE FROM watchlist WHERE code = ?", (code,))
            return cur.rowcount > 0

    def list_all(self) -> list[dict]:
        """返回所有钉选股票（按加入时间倒序，含 stock_snapshot 的最新行情作 fallback）"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT w.*, s.pe, s.pb, s.roe, s.revenue_growth, "
                "s.profit_growth, s.debt_ratio, s.dividend_yield, "
                "s.market_cap, s.current_price as snapshot_price "
                "FROM watchlist w "
                "LEFT JOIN stock_snapshot s ON w.code = s.code "
                "ORDER BY w.added_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def is_watched(self, code: str) -> bool:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM watchlist WHERE code = ?", (code,)
            ).fetchone()
        return row is not None

    def get_watched_codes(self) -> set:
        """返回所有已钉选的 code 集合（供批量查询用）"""
        with db_conn() as conn:
            rows = conn.execute("SELECT code FROM watchlist").fetchall()
        return {r[0] for r in rows}

    def update_price(self, code: str, price: float):
        """更新最新价（调度器每日拉行情后调用）"""
        with db_conn() as conn:
            conn.execute(
                "UPDATE watchlist SET latest_price = ?, last_updated = ? WHERE code = ?",
                (price, now_cn().isoformat(), code)
            )

    def update_signal(self, code: str, signal: str):
        """更新最近一次 AI 信号"""
        with db_conn() as conn:
            conn.execute(
                "UPDATE watchlist SET last_signal = ?, last_updated = ? WHERE code = ?",
                (signal, now_cn().isoformat(), code)
            )
