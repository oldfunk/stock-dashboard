"""Stock Dashboard - 数据模型与数据库层
SQLite 本地存储，无外部依赖
"""

import json
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

    2026-10-23：``PRAGMA journal_mode=WAL`` 需要写锁，调度器写库期间读连接
    （如首页轮询 /api/indices）会抛 ``database is locked``。改为：
    busy_timeout 等待锁 + 仅在非 WAL 时才设置（读连接不再抢写锁）。
    """
    conn = sqlite3.connect(get_db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
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
    dividend_yield REAL,              -- 股息率 %（年报现金分红/现价）
    payout_ratio REAL,                -- 分红率 %（年报现金分红/EPS）
    dividend_year TEXT,               -- 股息对应的年报年份（如 2025）
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
    dividend_yield REAL,              -- 股息率 %（2026-10-21）
    payout_ratio REAL,                -- 分红率 %
    dividend_year TEXT,               -- 年报年份
    reason TEXT,                      -- 符合哪些条件
    ai_analysis TEXT,                 -- AI 选股分析 JSON
    ai_investment_strategy TEXT,      -- AI 投资策略
    ai_trade_strategy TEXT,           -- AI 买卖策略
    status TEXT DEFAULT 'active',     -- active / eliminated（后续排除）
    eliminated_date TEXT,             -- 排除日期
    eliminated_reason TEXT,           -- 排除原因
    score_detail TEXT,                -- 评分拆解 JSON（五维子分+一致性加分，透明化）
    strategy_tags TEXT,               -- 命中策略 JSON 数组（如 ["growth","dividend"]，多策略模式）
    ai_failed INTEGER DEFAULT 0,      -- 本轮 AI 分析是否失败（1=失败，0=成功/未触发）
    ai_failure_reason TEXT            -- AI 分析失败原因（模型耗尽/解析失败/超时等）
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
    model TEXT,                     -- 本次分析所用 AI 模型（2026-08-23 起落库，透明化模型归属）
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
    -- 10年拓展字段（2026-07-08 新增，用于长期一致性评估）
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

-- AI 观察池（当前 5 只状态）
CREATE TABLE IF NOT EXISTS ai_watchlist (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    added_at        TEXT NOT NULL,
    added_reason    TEXT,
    ai_confidence   TEXT,
    last_reviewed   TEXT,
    review_count    INTEGER DEFAULT 1,
    status          TEXT DEFAULT 'core',  -- core/watch/dropped（B6a 状态机）
    status_reason   TEXT,                 -- 状态变更原因（调出/观察理由）
    watch_until     TEXT                  -- 观察期限 YYYY-MM-DD（watch 态用）
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

-- 钉选股票投资笔记（AI 或用户提交）
CREATE TABLE IF NOT EXISTS watchlist_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    note            TEXT NOT NULL,
    note_type       TEXT NOT NULL DEFAULT 'weekly',  -- weekly/analysis/user
    model           TEXT,                             -- 撰写方模型（外部 AI 必填溯源，旧行 NULL）
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_notes_code ON watchlist_notes(code);

-- 论文表 M-x（2026-09-22 内化）：论点+可验证假设+红线+卖出条件，复盘时更新假设状态
CREATE TABLE IF NOT EXISTS watchlist_thesis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    core_thesis     TEXT NOT NULL,        -- 论点一句话（200字内）
    assumptions     TEXT NOT NULL DEFAULT '[]',   -- JSON [{content, verify_method, verify_freq, status: 成立/证伪/未验证}]
    red_lines       TEXT NOT NULL DEFAULT '[]',   -- JSON [{condition, action, triggered: 0/1}]
    sell_conditions TEXT NOT NULL DEFAULT '[]',   -- JSON 买入前写下的卖出条件
    source          TEXT NOT NULL DEFAULT 'ai_analysis',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_thesis_code ON watchlist_thesis(code);

-- 新闻舆情（2026-10-19 新增）：滚动条 + AI 每日摘要的事实底座。
-- code 为空串 = 市场级新闻；同一 URL 只存一条（UNIQUE 去重，重复拉取不膨胀）。
CREATE TABLE IF NOT EXISTS news_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL DEFAULT '',   -- 关联股票代码，'' = 市场级
    name            TEXT,
    title           TEXT NOT NULL,
    summary         TEXT,
    url             TEXT,
    source          TEXT,
    published_at    TEXT,                       -- 源站发布时间（可能为空）
    fetched_at      TEXT NOT NULL,
    sentiment       TEXT,                       -- AI 判定：利好/中性/利空（未摘要为 NULL）
    ai_brief        TEXT,                       -- AI 一句话解读
    summarized_at   TEXT                        -- 摘要时间，未摘要为 NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_unique ON news_item(COALESCE(url,''), title);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_item(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_code ON news_item(code, published_at DESC);

-- 新闻抓取尝试日志（2026-10-19）：冷却判定按"尝试"而非"有无内容"，
-- 否则抓不到内容的票每轮都会重打数据源。
CREATE TABLE IF NOT EXISTS news_fetch_log (
    code        TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    ok_count    INTEGER DEFAULT 0
);

-- AI 舆情日报（每日一条，正文 Markdown；与 ai_journal 分开，避免污染复盘对比）
CREATE TABLE IF NOT EXISTS news_digest (
    digest_date     TEXT PRIMARY KEY,
    scope           TEXT,                       -- 覆盖范围描述
    overall         TEXT,                       -- 市场情绪结论一句话
    content_md      TEXT NOT NULL,
    model           TEXT,
    item_count      INTEGER DEFAULT 0,
    usage_json      TEXT,                       -- {prompt_tokens, completion_tokens}
    created_at      TEXT NOT NULL
);

-- 基准指数日线（2026-10-21）：信号前向收益对比用；只存收盘价，价格指数不含分红。
CREATE TABLE IF NOT EXISTS benchmark_daily (
    code            TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    close           REAL NOT NULL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_benchmark_daily_code_date
    ON benchmark_daily(code, trade_date);

-- 年度分红（2026-10-21）：东财 stock_fhps_em 落库，股息率/分红率由本表 + 行情现价计算。
CREATE TABLE IF NOT EXISTS dividend_annual (
    stock_code      TEXT NOT NULL,
    report_date     TEXT NOT NULL,          -- 年报期末 YYYY-MM-DD
    cash_per_share  REAL,                   -- 每股现金分红（元）
    eps             REAL,                   -- 年报每股收益（元）
    reported_yield  REAL,                   -- 东财披露股息率（%），仅对账参考
    source          TEXT DEFAULT 'eastmoney',
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (stock_code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_dividend_report_date
    ON dividend_annual(report_date);

-- 分析信号前向收益（2026-10-21）：每条分析 × 每个基准一行；未到期/缺行情一律 NULL。
CREATE TABLE IF NOT EXISTS analysis_outcome (
    analysis_id     INTEGER NOT NULL,
    benchmark_code  TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    stock_name      TEXT,
    analysis_date   TEXT NOT NULL,
    signal          TEXT,                   -- BUY / HOLD / AVOID
    confidence      TEXT,                   -- 高 / 中 / 低
    verdict         TEXT,                   -- 通过 / 不通过 / 灰色地带
    entry_date      TEXT,                   -- T+1 交易日（分析日之后第一个交易日）
    entry_price     REAL,                   -- 前复权收盘价
    mos_at_entry    REAL,                   -- 买入时安全边际 %（2026-10-23 P0-9，模型可复现）
    ret_20          REAL,                   -- 个股收益 %（前复权，含分红）
    ret_60          REAL,
    ret_120         REAL,
    bench_20        REAL,                   -- 基准收益 %（价格指数，不含分红）
    bench_60        REAL,
    bench_120       REAL,
    excess_20       REAL,                   -- 个股 - 基准（百分点）
    excess_60       REAL,
    excess_120      REAL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (analysis_id, benchmark_code)
);
CREATE INDEX IF NOT EXISTS idx_analysis_outcome_date
    ON analysis_outcome(analysis_date);
CREATE INDEX IF NOT EXISTS idx_analysis_outcome_signal
    ON analysis_outcome(signal, confidence);

-- 注：M4a 纸盘五表（paper_account/orders/trades/positions/nav）已随 2026-09-20
-- 路线调整删除（git 历史可查）。存量库残留表不自动删（只读历史），新库不再建。
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
        _add_column_if_not_exists(conn, 'screening_result', 'score_detail', 'TEXT')
        _add_column_if_not_exists(conn, 'screening_result', 'strategy_tags', 'TEXT')
        _add_column_if_not_exists(conn, 'screening_result', 'ai_failed', 'INTEGER')
        _add_column_if_not_exists(conn, 'screening_result', 'ai_failure_reason', 'TEXT')
        # 2026-10-21：真实股息数据（红利策略 fail-closed 的数据底座）
        _add_column_if_not_exists(conn, 'screening_result', 'dividend_yield', 'REAL')
        _add_column_if_not_exists(conn, 'screening_result', 'payout_ratio', 'REAL')
        _add_column_if_not_exists(conn, 'screening_result', 'dividend_year', 'TEXT')
        _add_column_if_not_exists(conn, 'stock_snapshot', 'payout_ratio', 'REAL')
        _add_column_if_not_exists(conn, 'stock_snapshot', 'dividend_year', 'TEXT')
        _add_column_if_not_exists(conn, 'stock_snapshot', 'dividend_years', 'INTEGER')  # 2026-10-23 连续分红年数
        _add_column_if_not_exists(conn, 'analysis_outcome', 'mos_at_entry', 'REAL')  # 2026-10-23 P0-9 买入安全边际
        _add_column_if_not_exists(conn, 'stock_analysis_history', 'model', 'TEXT')
        _add_column_if_not_exists(conn, 'ai_watchlist', 'status', 'TEXT')
        _add_column_if_not_exists(conn, 'ai_watchlist', 'status_reason', 'TEXT')
        _add_column_if_not_exists(conn, 'ai_watchlist', 'watch_until', 'TEXT')
        _add_column_if_not_exists(conn, 'ai_watchlist', 'monitor_condition', 'TEXT')
        _add_column_if_not_exists(conn, 'watchlist_notes', 'model', 'TEXT')
        _add_column_if_not_exists(conn, 'financial_history', 'interest_coverage', 'REAL')
        _add_column_if_not_exists(conn, 'financial_history', 'cash', 'REAL')  # 2026-10-23 P0-3 货币资金
        _add_column_if_not_exists(conn, 'financial_history', 'interest_bearing_debt', 'REAL')  # 有息负债
        _add_column_if_not_exists(conn, 'financial_history', 'goodwill', 'REAL')  # 商誉
        _add_column_if_not_exists(conn, 'financial_history', 'total_assets', 'REAL')
        _add_column_if_not_exists(conn, 'financial_history', 'total_equity', 'REAL')
        _add_column_if_not_exists(conn, 'financial_history', 'capex', 'REAL')  # 2026-10-23 P0-4 购建固定资产等支付现金
        _add_column_if_not_exists(conn, 'financial_history', 'net_operate_cashflow', 'REAL')  # 经营现金流净额
        _add_column_if_not_exists(conn, 'financial_history', 'notice_date', 'TEXT')  # 2026-10-23 P0-10 披露日期
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
        # 回填进度表（2026-10-24）：全市场回填的断点续跑标记。
        # 动机：老股票有 20 年年报、上游只回 10 年，未填的老年份会让"按 NULL 判定"
        # 的目标集永远不缩小 → 无限重复处理。进度表让每只股票只处理一次。
        conn.execute('''
            CREATE TABLE IF NOT EXISTS backfill_progress (
                task         TEXT NOT NULL,
                code         TEXT NOT NULL,
                updated_rows INTEGER DEFAULT 0,
                status       TEXT NOT NULL,      -- done / partial / no_data
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (task, code)
            )
        ''')
        # 2026-10-25：记录"已覆盖到哪个报告期"。只按 (task, code) 排除会让
        # 未来新增的年报永远进不了目标集（审计实证：插入 2026 年报后目标集仍为空）；
        # 现在只有 covered_report_date >= 当前最新年报时才排除该股票。
        _add_column_if_not_exists(conn, 'backfill_progress', 'covered_report_date', 'TEXT')
        conn.execute('''
            UPDATE backfill_progress SET covered_report_date = (
                SELECT MAX(f.report_date) FROM financial_history f
                WHERE f.stock_code = backfill_progress.code
            ) WHERE covered_report_date IS NULL
        ''')
        # 注：2026-09-15 的 M4a 纸盘列守卫已随路线调整删除（表不再建，守卫会 crash 新库）。
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


def _avg(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _median(vals: list) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def _round(v, nd: int):
    return round(v, nd) if isinstance(v, (int, float)) else None


class StockSnapshotDAO:
    def save_batch(self, records: list[dict]):
        with db_conn() as conn:
            for r in records:
                # sector 保值：上游 dict 无板块时不清已有值
                # （INSERT OR REPLACE 整行覆盖，否则每日快照都会把回填的板块擦成 NULL）
                sector = r.get('sector')
                if not sector:
                    prev = conn.execute(
                        "SELECT sector FROM stock_snapshot WHERE code = ?",
                        (r['code'],)).fetchone()
                    sector = prev["sector"] if prev else None
                conn.execute("""
                    INSERT OR REPLACE INTO stock_snapshot
                    (code, name, market, sector, pe, pb, ps, market_cap, circulating_cap,
                     roe, revenue, revenue_growth, profit, profit_growth, debt_ratio,
                     dividend_yield, payout_ratio, dividend_year, dividend_years,
                     current_price, high_52w, low_52w, is_st, list_date, snapshot_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (r['code'], r['name'], r.get('market', 'A'),
                      sector, r.get('pe'), r.get('pb'), r.get('ps'),
                      r.get('market_cap'), r.get('circulating_cap'),
                      r.get('roe'), r.get('revenue'), r.get('revenue_growth'),
                      r.get('profit'), r.get('profit_growth'), r.get('debt_ratio'),
                      r.get('dividend_yield'), r.get('payout_ratio'),
                      r.get('dividend_year'), r.get('dividend_years'),
                      r.get('current_price'),
                      r.get('high_52w'), r.get('low_52w'),
                      1 if r.get('is_st') else 0, r.get('list_date'),
                      r['snapshot_date']))

    def backfill_sectors(self, sector_map: dict) -> int:
        """S8 行业回填：按 {code: sector} 刷新表内已有行（含行业再分类修正）。

        只碰 map 内的 code，map 为 {} 时不动任何行（上游失败不清旧值）。返回更新行数。
        """
        if not sector_map:
            return 0
        items = [(sector, code) for code, sector in sector_map.items()
                 if code and sector]
        if not items:
            return 0
        with db_conn() as conn:
            conn.executemany(
                "UPDATE stock_snapshot SET sector = ? WHERE code = ?", items)
            return conn.total_changes

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

    def get_by_code(self, code: str) -> Optional[dict]:
        """按股票代码查最新快照（单条）"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM stock_snapshot WHERE code = ?", (code,)
            ).fetchone()
        return dict(row) if row else None

    @classmethod
    def get_sector_averages(cls) -> dict:
        """行业参照（P0-7）：{sector: {avg_pe, median_pe, avg_roe, median_roe,
        avg_pb, median_pb, n}}。

        pe/pb 仅取正值样本（亏损股不拉低），roe 全样本；仅 A 股且板块非空。
        只读展示，不参与评分。
        """
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT sector, pe, roe, pb FROM stock_snapshot "
                "WHERE market = 'A' AND sector IS NOT NULL AND TRIM(sector) <> '' "
            ).fetchall()
        groups: dict[str, dict] = {}
        for r in rows:
            g = groups.setdefault(r["sector"], {"pe": [], "roe": [], "pb": [], "n": 0})
            g["n"] += 1
            if r["pe"] is not None and r["pe"] > 0:
                g["pe"].append(r["pe"])
            if r["roe"] is not None:
                g["roe"].append(r["roe"])
            if r["pb"] is not None and r["pb"] > 0:
                g["pb"].append(r["pb"])
        out = {}
        for sector, g in groups.items():
            out[sector] = {
                "avg_pe": _round(_avg(g["pe"]), 1), "median_pe": _round(_median(g["pe"]), 1),
                "avg_roe": _round(_avg(g["roe"]), 2), "median_roe": _round(_median(g["roe"]), 2),
                "avg_pb": _round(_avg(g["pb"]), 2), "median_pb": _round(_median(g["pb"]), 2),
                "n": g["n"],
            }
        return out

    @classmethod
    def get_sector_percentile(cls, sector: str, value, metric: str = "pe"):
        """个股在某行业内的分位（<= value 的比例，越低越便宜）。缺值返回 None。"""
        if value is None or sector is None or metric not in ("pe", "pb", "roe"):
            return None
        with db_conn() as conn:
            rows = conn.execute(
                f"SELECT {metric} AS v FROM stock_snapshot "
                f"WHERE market='A' AND sector=? AND {metric} IS NOT NULL AND {metric} > 0",
                (sector,),
            ).fetchall()
        vals = [r["v"] for r in rows]
        if not vals:
            return None
        return round(100.0 * sum(1 for v in vals if v <= value) / len(vals), 1)

    @classmethod
    def get_sector_map(cls) -> dict:
        """{code: sector}（A 股且板块非空），供列表页给卡片挂板块归属。"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT code, sector FROM stock_snapshot "
                "WHERE market = 'A' AND sector IS NOT NULL AND TRIM(sector) <> ''"
            ).fetchall()
        return {r["code"]: r["sector"] for r in rows}


class ScreeningResultDAO:
    def save_batch(self, records: list[dict]):
        with db_conn() as conn:
            for r in records:
                conn.execute("""
                    INSERT INTO screening_result
                    (run_id, run_date, code, name, score, pe, pb, roe,
                     gross_margin, net_margin, ocf_per_share,
                     revenue_growth, profit_growth, debt_ratio, market_cap,
                     dividend_yield, payout_ratio, dividend_year, reason,
                     score_detail, strategy_tags)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (r['run_id'], r['run_date'], r['code'], r['name'],
                      r.get('score'), r.get('pe'), r.get('pb'), r.get('roe'),
                      r.get('gross_margin'), r.get('net_margin'), r.get('ocf_per_share'),
                      r.get('revenue_growth'), r.get('profit_growth'),
                      r.get('debt_ratio'), r.get('market_cap'),
                      r.get('dividend_yield'), r.get('payout_ratio'),
                      r.get('dividend_year'), r.get('reason'),
                      r.get('score_detail'), r.get('strategy_tags')))

    def update_ai_analysis(self, run_id: str, code: str, analysis: str, strategy: str, trade_strategy: str):
        with db_conn() as conn:
            conn.execute("""
                UPDATE screening_result
                SET ai_analysis = ?, ai_investment_strategy = ?, ai_trade_strategy = ?,
                    ai_failed = 0, ai_failure_reason = NULL
                WHERE run_id = ? AND code = ?
            """, (analysis, strategy, trade_strategy, run_id, code))

    def mark_ai_failure(self, run_id: str, code: str, reason: str):
        """标记本轮 AI 分析失败（落库原因，便于前端/复盘可见）。"""
        with db_conn() as conn:
            conn.execute("""
                UPDATE screening_result
                SET ai_failed = 1, ai_failure_reason = ?
                WHERE run_id = ? AND code = ?
            """, (reason or 'unknown', run_id, code))

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

    def get_latest_for_code(self, code: str) -> Optional[dict]:
        """取某只股票最新一轮 screening_result 行（含 score_detail）。"""
        with db_conn() as conn:
            row = conn.execute("""
                SELECT * FROM screening_result
                WHERE code = ?
                ORDER BY run_date DESC LIMIT 1
            """, (code,)).fetchone()
        return dict(row) if row else None

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

    def get_by_run(self, run_id: str) -> list:
        """某轮 token 用量明细（供 /api/llm/usage 展示）。"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT code, model, prompt_tokens, completion_tokens, cost,"
                " created_at FROM ai_analysis_log WHERE run_id = ? ORDER BY id",
                (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]


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
        """最新已完成的*筛选*轮（排除周六复盘 run：run_id 以 _review 结尾，
        否则周六之后所有调用方都会拿到空集合）。"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT run_id FROM run_log WHERE status='completed' "
                "AND run_id NOT LIKE '%_review' "
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
             ai_analysis: str, ai_trade_strategy: str, model: str = None):
        now = now_cn().isoformat()
        with db_conn() as conn:
            conn.execute("""
                INSERT INTO stock_analysis_history
                (stock_code, run_id, analysis_date, score, ai_analysis, ai_trade_strategy, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (stock_code, run_id, now[:10], score, ai_analysis, ai_trade_strategy, model, now))

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

    def get_all_latest(self, days: Optional[int] = None) -> list[dict]:
        """获取所有分析过的股票的最新一条记录（按 code 去重）。

        Args:
            days: 可选，只返回最近 N 天内有分析的股票（按 analysis_date 过滤）

        Returns:
            list[dict]，每条包含 stock_code 的最新 analysis_date / score / ai_analysis / ai_trade_strategy。
            注意：不包含 screening_result 的字段（如 pe / pb / roe），需在调用处合并。
        """
        with db_conn() as conn:
            if days is not None:
                rows = conn.execute("""
                    SELECT h.* FROM stock_analysis_history h
                    INNER JOIN (
                        SELECT stock_code, MAX(id) AS max_id
                        FROM stock_analysis_history
                        WHERE analysis_date >= date('now', ?)
                        GROUP BY stock_code
                    ) latest ON h.id = latest.max_id
                    ORDER BY h.analysis_date DESC, h.stock_code
                """, (f'-{days} days',)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT h.* FROM stock_analysis_history h
                    INNER JOIN (
                        SELECT stock_code, MAX(id) AS max_id
                        FROM stock_analysis_history
                        GROUP BY stock_code
                    ) latest ON h.id = latest.max_id
                    ORDER BY h.analysis_date DESC, h.stock_code
                """).fetchall()
        return [dict(r) for r in rows]

    def list_all(self, limit: Optional[int] = None) -> list[dict]:
        """按日期升序返回全部分析记录（供信号前向收益批量刷新）。"""
        sql = "SELECT * FROM stock_analysis_history ORDER BY analysis_date, id"
        args: list = []
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


class FinancialHistoryDAO:
    """财务历史数据 DAO（时序表 — 按日累积）"""

    def batch_save(self, records: list[dict]):
        """批量保存财务历史（UPSERT：新值为空时保留旧值）。

        **2026-10-25 修**：旧实现用 ``INSERT OR REPLACE`` 整行替换，重采集时
        缺省字段会把回填的 ``cash/capex/notice_date`` 等重置为 NULL（审计已用
        临时库复现）。现在改为 ``ON CONFLICT`` 逐列 ``COALESCE``：采集器没提供
        的字段保持原值；``created_at`` 保留首次写入时间（审计溯源）。
        """
        now = now_cn().isoformat()
        data_cols = (
            "roe", "gross_margin", "net_margin", "ocf_per_share", "debt_ratio",
            "revenue_growth", "profit_growth", "net_profit", "interest_coverage",
            "fcf", "total_shares", "roic", "eps", "cash", "interest_bearing_debt",
            "goodwill", "total_assets", "total_equity", "capex",
            "net_operate_cashflow", "notice_date",
        )
        updates = ", ".join(f"{c} = COALESCE(excluded.{c}, {c})" for c in data_cols)
        sql = f"""
            INSERT INTO financial_history
            (stock_code, report_date, report_type, roe, gross_margin, net_margin,
             ocf_per_share, debt_ratio, revenue_growth, profit_growth,
             net_profit, interest_coverage, fcf, total_shares, roic, eps,
             cash, interest_bearing_debt, goodwill, total_assets, total_equity,
             capex, net_operate_cashflow, notice_date,
             data_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, report_date) DO UPDATE SET
                report_type = excluded.report_type,
                data_source = COALESCE(excluded.data_source, data_source),
                {updates}
        """
        with db_conn() as conn:
            for r in records:
                conn.execute(sql, (
                    r['stock_code'], r['report_date'],
                    'A' if r['report_date'].endswith('12-31') else 'Q',
                    r.get('roe'), r.get('gross_margin'), r.get('net_margin'),
                    r.get('ocf_per_share'), r.get('debt_ratio'),
                    r.get('revenue_growth'), r.get('profit_growth'),
                    r.get('net_profit'), r.get('interest_coverage'),
                    r.get('fcf'), r.get('total_shares'), r.get('roic'), r.get('eps'),
                    r.get('cash'), r.get('interest_bearing_debt'), r.get('goodwill'),
                    r.get('total_assets'), r.get('total_equity'),
                    r.get('capex'), r.get('net_operate_cashflow'), r.get('notice_date'),
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

    # 补充字段白名单（P0-3/4/10 + C2.5 兜底）：只允许这些列被回填
    EXTRA_FIELDS = ("cash", "interest_bearing_debt", "goodwill", "total_assets",
                    "total_equity", "capex", "net_operate_cashflow",
                    "notice_date", "fcf", "roic", "total_shares")

    def update_extra_fields(self, code: str, report_date: str,
                            values: dict) -> int:
        """只填 NULL 的补充字段（``COALESCE`` 保序），返回受影响行数。

        - 列白名单（``EXTRA_FIELDS``）：防止拼错列名写坏库。
        - 已有值**不覆盖**（幂等；重跑安全）。
        - 只作用于年报行（``report_type='A'``）。
        """
        cols = [k for k in self.EXTRA_FIELDS if values.get(k) is not None]
        if not cols:
            return 0
        sets = ", ".join(f"{c} = COALESCE({c}, ?)" for c in cols)
        params = [values[c] for c in cols] + [code, report_date]
        with db_conn() as conn:
            cur = conn.execute(
                f"UPDATE financial_history SET {sets} "
                "WHERE stock_code = ? AND report_date = ? AND report_type = 'A'",
                params)
            return cur.rowcount

    def get_codes_missing_extra(self, limit: int | None = None,
                                offset: int = 0, task: str | None = None,
                                retry_no_data: bool = False,
                                retry_partial: bool = False) -> list[str]:
        """返回"年报补充字段仍有 NULL"的股票代码（按 code 升序，可断点续跑）。

        判定：任一字段（cash / capex / notice_date）在该股任一年报为 NULL。

        - ``task``：给了就用 ``backfill_progress`` 排除**已处理过**的股票
          （否则老股票因上游只回 10 年、老年份永远 NULL 而永不消失）。
          只有进度记录覆盖到该股当前最新年报时才排除；新报告期到达后自动重新入选。
        - ``retry_no_data`` / ``retry_partial``：把上次对应状态的股票重新纳入
          （``partial`` = 至少一个数据源失败或返回空，字段可能没填全）。
        """
        sql = "SELECT stock_code FROM financial_history WHERE report_type = 'A'"
        params: list = []
        if task:
            skip = ["done"]
            if not retry_no_data:
                skip.append("no_data")
            if not retry_partial:
                skip.append("partial")
            marks = ", ".join("?" for _ in skip)
            # 只有进度记录覆盖到"当前最新年报"时才排除；新报告期到达后自动重新入选
            sql += (" AND NOT EXISTS (SELECT 1 FROM backfill_progress p"
                    " WHERE p.task = ? AND p.code = financial_history.stock_code"
                    f" AND p.status IN ({marks})"
                    " AND COALESCE(p.covered_report_date, '') >="
                    " (SELECT MAX(f2.report_date) FROM financial_history f2"
                    "  WHERE f2.stock_code = financial_history.stock_code"
                    "    AND f2.report_type = 'A'))")
            params.append(task)
            params.extend(skip)
        sql += """
            GROUP BY stock_code
            HAVING SUM(cash IS NULL) > 0
                OR SUM(capex IS NULL) > 0
                OR SUM(notice_date IS NULL) > 0
            ORDER BY stock_code
        """
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        with db_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [r["stock_code"] for r in rows]

    def mark_backfill_done(self, task: str, code: str, updated_rows: int = 0,
                           status: str = "done",
                           covered_report_date: str | None = None) -> None:
        """记录回填进度（``task``+``code`` 唯一，幂等覆盖）。

        status：``done``（三源都返回数据）/ ``partial``（至少一源失败或空，
        字段可能漏填，需重试）/ ``no_data``（整只无数据）。

        ``covered_report_date`` 缺省取该股当前最新年报日期；查询目标集时只有
        覆盖到最新报告期才排除该股票 —— 否则未来年报会被进度表永久锁死。
        """
        with db_conn() as conn:
            if covered_report_date is None:
                row = conn.execute(
                    "SELECT MAX(report_date) AS rd FROM financial_history"
                    " WHERE stock_code = ? AND report_type = 'A'",
                    (code,)).fetchone()
                covered_report_date = row["rd"] if row else None
            conn.execute(
                "INSERT OR REPLACE INTO backfill_progress"
                " (task, code, updated_rows, status, covered_report_date, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (task, code, updated_rows, status, covered_report_date,
                 now_cn().isoformat()))

    def backfill_progress_summary(self, task: str) -> dict:
        """某任务的进度汇总：{done, partial, no_data, updated_rows}。"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT SUM(status = 'done') AS done,"
                "       SUM(status = 'partial') AS partial,"
                "       SUM(status = 'no_data') AS no_data,"
                "       SUM(updated_rows) AS updated_rows"
                " FROM backfill_progress WHERE task = ?", (task,)).fetchone()
        return {"done": row["done"] or 0, "partial": row["partial"] or 0,
                "no_data": row["no_data"] or 0,
                "updated_rows": row["updated_rows"] or 0}

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
                "s.profit_growth, s.debt_ratio, s.dividend_yield, s.dividend_years, "
                "s.dividend_year, s.market_cap, s.current_price as snapshot_price "
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

    def add_note(self, code: str, note: str, note_type: str = "weekly",
                 model: str = None) -> bool:
        """添加投资笔记（AI 或用户）。model 为撰写方模型标识（外部 AI 必填溯源）。"""
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT INTO watchlist_notes (code, note, note_type, model, created_at) VALUES (?, ?, ?, ?, ?)",
                (code, note, note_type, model, now_cn().isoformat())
            )
            return cur.rowcount > 0

    def list_notes(self, code: str, limit: int = 10) -> list[dict]:
        """获取某只股票的投资笔记列表"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist_notes WHERE code = ? ORDER BY created_at DESC LIMIT ?",
                (code, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def clear(self):
        """清空钉选"""
        with db_conn() as conn:
            conn.execute("DELETE FROM watchlist")


class KlineDAO:
    """K线日线数据 DAO（2026-09-28 从 WatchlistDAO 迁回：历史重构误删类，致 routes/scheduler ImportError）"""

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


class BenchmarkDailyDAO:
    """基准指数日线（价格指数，不含分红）。"""

    def upsert_many(self, code: str, records: list[dict]) -> int:
        rows = [(code, r['trade_date'], r['close']) for r in records
                if r.get('trade_date') and r.get('close') is not None]
        if not rows:
            return 0
        with db_conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO benchmark_daily "
                "(code, trade_date, close) VALUES (?, ?, ?)", rows)
        return len(rows)

    def get_series(self, code: str) -> list[dict]:
        """按日期升序返回全部日线；无数据返回 []。"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT trade_date, close FROM benchmark_daily "
                "WHERE code = ? ORDER BY trade_date", (code,)).fetchall()
        return [dict(r) for r in rows]

    def latest_date(self, code: str) -> Optional[str]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) AS d FROM benchmark_daily WHERE code = ?",
                (code,)).fetchone()
        return row['d'] if row else None


class DividendDAO:
    """年度现金分红（东财 stock_fhps_em 落库）。"""

    def upsert_many(self, records: list[dict]) -> int:
        now = now_cn().isoformat()
        rows = [(
            r['stock_code'], r['report_date'], r.get('cash_per_share'),
            r.get('eps'), r.get('reported_yield'),
            r.get('source', 'eastmoney'), now,
        ) for r in records if r.get('stock_code') and r.get('report_date')]
        if not rows:
            return 0
        with db_conn() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO dividend_annual
                (stock_code, report_date, cash_per_share, eps,
                 reported_yield, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)
        return len(rows)

    def latest_by_codes(self, codes: list[str],
                        report_date: str | None = None) -> dict[str, dict]:
        """批量取分红；report_date 为空时取每只股票最新一期。"""
        if not codes:
            return {}
        out: dict[str, dict] = {}
        uniq = list(dict.fromkeys(codes))
        for i in range(0, len(uniq), 450):  # SQLite 变量上限 999
            chunk = uniq[i:i + 450]
            marks = ','.join('?' * len(chunk))
            with db_conn() as conn:
                if report_date:
                    sql = (f"SELECT * FROM dividend_annual WHERE report_date = ? "
                           f"AND stock_code IN ({marks})")
                    rows = conn.execute(sql, [report_date, *chunk]).fetchall()
                else:
                    sql = (f"SELECT * FROM dividend_annual WHERE stock_code IN ({marks}) "
                           f"AND report_date = (SELECT MAX(report_date) FROM dividend_annual x "
                           f"WHERE x.stock_code = dividend_annual.stock_code)")
                    rows = conn.execute(sql, chunk).fetchall()
            for r in rows:
                out[r['stock_code']] = dict(r)
        return out

    def get_latest(self, code: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM dividend_annual WHERE stock_code = ? "
                "ORDER BY report_date DESC LIMIT 1", (code,)).fetchone()
        return dict(row) if row else None

    def count(self, report_date: str | None = None) -> int:
        with db_conn() as conn:
            if report_date:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM dividend_annual WHERE report_date = ?",
                    (report_date,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM dividend_annual").fetchone()
        return row['c'] if row else 0

    def last_updated(self) -> Optional[str]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT MAX(updated_at) AS u FROM dividend_annual").fetchone()
        return row['u'] if row else None


class AnalysisOutcomeDAO:
    """AI 分析信号的前向收益（每条分析 × 每个基准一行）。"""

    _FIELDS = (
        'analysis_id', 'benchmark_code', 'stock_code', 'stock_name',
        'analysis_date', 'signal', 'confidence', 'verdict',
        'entry_date', 'entry_price', 'mos_at_entry',
        'ret_20', 'ret_60', 'ret_120',
        'bench_20', 'bench_60', 'bench_120',
        'excess_20', 'excess_60', 'excess_120',
    )

    def upsert_many(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        now = now_cn().isoformat()
        values = [tuple(r.get(k) for k in self._FIELDS) + (now,) for r in rows]
        marks = ','.join('?' * (len(self._FIELDS) + 1))
        with db_conn() as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO analysis_outcome "
                f"({','.join(self._FIELDS)}, updated_at) VALUES ({marks})",
                values)
        return len(values)

    def get_all(self, benchmark_code: str | None = None) -> list[dict]:
        sql = "SELECT * FROM analysis_outcome"
        args: list = []
        if benchmark_code:
            sql += " WHERE benchmark_code = ?"
            args.append(benchmark_code)
        sql += " ORDER BY analysis_date, analysis_id"
        with db_conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get(self, analysis_id: int, benchmark_code: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_outcome WHERE analysis_id = ? "
                "AND benchmark_code = ?", (analysis_id, benchmark_code)).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 50,
                    benchmark_code: str | None = None) -> list[dict]:
        sql = "SELECT * FROM analysis_outcome"
        args: list = []
        if benchmark_code:
            sql += " WHERE benchmark_code = ?"
            args.append(benchmark_code)
        sql += " ORDER BY analysis_date DESC, analysis_id DESC LIMIT ?"
        args.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def delete_without_signal(self) -> int:
        """清理无 Signal 的占位行（老分析落库为空 JSON，无法验证方向）。"""
        with db_conn() as conn:
            cur = conn.execute(
                "DELETE FROM analysis_outcome "
                "WHERE signal IS NULL OR TRIM(signal) = ''")
            return cur.rowcount


class WatchlistThesisDAO:
    """论文（论点+假设+红线+卖出条件）：每股一行，复盘更新假设状态。"""

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "code": row["code"],
            "core_thesis": row["core_thesis"],
            "assumptions": json.loads(row["assumptions"] or "[]"),
            "red_lines": json.loads(row["red_lines"] or "[]"),
            "sell_conditions": json.loads(row["sell_conditions"] or "[]"),
            "source": row["source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def upsert(code: str, core_thesis: str, assumptions: list, red_lines: list,
               sell_conditions: list, source: str = "ai_analysis") -> dict:
        now = now_cn().isoformat(timespec="seconds")
        a_json = json.dumps(assumptions or [], ensure_ascii=False)
        r_json = json.dumps(red_lines or [], ensure_ascii=False)
        s_json = json.dumps(sell_conditions or [], ensure_ascii=False)
        with db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM watchlist_thesis WHERE code=?", (code,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE watchlist_thesis SET core_thesis=?, assumptions=?, "
                    "red_lines=?, sell_conditions=?, source=?, updated_at=? "
                    "WHERE code=?",
                    (core_thesis, a_json, r_json, s_json, source, now, code),
                )
            else:
                conn.execute(
                    "INSERT INTO watchlist_thesis (code, core_thesis, assumptions, "
                    "red_lines, sell_conditions, source, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (code, core_thesis, a_json, r_json, s_json, source, now, now),
                )
        return WatchlistThesisDAO.get(code)

    @staticmethod
    def get(code: str) -> Optional[dict]:
        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM watchlist_thesis WHERE code=?", (code,)
            ).fetchone()
        return WatchlistThesisDAO._row_to_dict(row) if row else None

    @staticmethod
    def get_many(codes: list) -> dict:
        """按 code 批量取论文（复盘注入用），返回 {code: thesis}。"""
        if not codes:
            return {}
        marks = ",".join("?" * len(codes))
        with db_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM watchlist_thesis WHERE code IN ({marks})", codes
            ).fetchall()
        return {r["code"]: WatchlistThesisDAO._row_to_dict(r) for r in rows}

    @staticmethod
    def apply_updates(code: str, assumptions: Optional[list],
                      red_lines: Optional[list]) -> Optional[dict]:
        """复盘写回：替换假设/红线状态（无论文时返回 None，不新建）。"""
        current = WatchlistThesisDAO.get(code)
        if not current:
            return None
        now = now_cn().isoformat(timespec="seconds")
        with db_conn() as conn:
            if assumptions is not None:
                conn.execute(
                    "UPDATE watchlist_thesis SET assumptions=?, updated_at=? WHERE code=?",
                    (json.dumps(assumptions, ensure_ascii=False), now, code),
                )
            if red_lines is not None:
                conn.execute(
                    "UPDATE watchlist_thesis SET red_lines=?, updated_at=? WHERE code=?",
                    (json.dumps(red_lines, ensure_ascii=False), now, code),
                )
        return WatchlistThesisDAO.get(code)


# 注：Paper*DAO（M4a 纸盘）已随 2026-09-20 路线调整删除（git 历史可查）。
