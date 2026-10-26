"""Research OS 数据库 schema（2026-10-26，R0-2）。

原则：
- 研究表独立于业务表；`init_database()` 幂等创建。
- 所有 JSON 字段以 TEXT 存储（`*_json`），由 DAO 负责序列化。
- 多市场：`market`/`instrument` 从第一天存在；`kline_daily` 增加
  `market`/`instrument_id`（不改旧主键，避免 215 万行重建；新市场用
  `instrument_id` 唯一标识）。
"""

RESEARCH_SCHEMA_SQL = """
-- 市场与品种（多市场基础）
CREATE TABLE IF NOT EXISTS market (
    market_id   TEXT PRIMARY KEY,          -- CN / HK / US
    name        TEXT NOT NULL,
    currency    TEXT NOT NULL,
    timezone    TEXT NOT NULL,
    calendar_id TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instrument (
    instrument_id TEXT PRIMARY KEY,        -- CN:600519 / HK:00700 / US:AAPL
    market_id     TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    exchange      TEXT,
    name          TEXT,
    currency      TEXT,
    lot_size      REAL,
    tick_size     REAL,
    list_date     TEXT,
    delist_date   TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_instrument_market ON instrument(market_id, symbol);
CREATE INDEX IF NOT EXISTS idx_instrument_status ON instrument(status);

CREATE TABLE IF NOT EXISTS trading_calendar (
    market_id   TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    is_open     INTEGER NOT NULL,
    session_json TEXT,
    source      TEXT,
    PRIMARY KEY (market_id, trade_date)
);

CREATE TABLE IF NOT EXISTS fx_rate_daily (
    base_currency  TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    trade_date     TEXT NOT NULL,
    rate           REAL NOT NULL,
    source         TEXT,
    PRIMARY KEY (base_currency, quote_currency, trade_date)
);

-- 特征注册与值
CREATE TABLE IF NOT EXISTS feature_definition (
    feature_id     TEXT NOT NULL,
    version        INTEGER NOT NULL,
    name           TEXT NOT NULL,
    formula        TEXT,
    unit           TEXT,
    direction      TEXT,
    missing_policy TEXT,
    lookback       INTEGER,
    source         TEXT,
    owner          TEXT,
    status         TEXT NOT NULL DEFAULT 'draft',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (feature_id, version)
);

CREATE TABLE IF NOT EXISTS stock_feature_daily (
    instrument_id   TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    feature_id      TEXT NOT NULL,
    feature_version INTEGER NOT NULL,
    value           REAL,
    quality         TEXT,
    PRIMARY KEY (instrument_id, trade_date, feature_id, feature_version)
);
CREATE INDEX IF NOT EXISTS idx_feature_date ON stock_feature_daily(feature_id, trade_date);

CREATE TABLE IF NOT EXISTS universe_membership (
    universe_id   TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    status        TEXT NOT NULL,           -- in / out
    reason        TEXT,
    source        TEXT,
    PRIMARY KEY (universe_id, instrument_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_universe_date ON universe_membership(universe_id, trade_date);

CREATE TABLE IF NOT EXISTS data_quality_daily (
    dataset        TEXT NOT NULL,
    trade_date     TEXT NOT NULL,
    coverage       REAL,
    missing_rate   REAL,
    outliers       INTEGER,
    pit_violations INTEGER,
    status         TEXT NOT NULL,
    details_json   TEXT,
    PRIMARY KEY (dataset, trade_date)
);

-- 研究账本
CREATE TABLE IF NOT EXISTS research_hypothesis (
    id            TEXT PRIMARY KEY,
    version       INTEGER NOT NULL DEFAULT 1,
    prior         TEXT NOT NULL,
    statement     TEXT NOT NULL,
    universe      TEXT NOT NULL,
    signal_json   TEXT,
    horizon_days  INTEGER,
    benchmark     TEXT,
    metric        TEXT,
    threshold     TEXT,
    regimes_json  TEXT,
    costs_json    TEXT,
    kill_criteria_json TEXT,
    status        TEXT NOT NULL,
    owner         TEXT NOT NULL DEFAULT 'human',
    created_at    TEXT NOT NULL,
    locked_at     TEXT,
    review_date   TEXT,
    supersedes    TEXT
);
CREATE INDEX IF NOT EXISTS idx_hypothesis_status ON research_hypothesis(status);
CREATE INDEX IF NOT EXISTS idx_hypothesis_review ON research_hypothesis(review_date);

CREATE TABLE IF NOT EXISTS research_experiment (
    id               TEXT PRIMARY KEY,
    hypothesis_id    TEXT NOT NULL,
    plan_json        TEXT,
    data_snapshot_id TEXT NOT NULL,
    code_commit      TEXT NOT NULL,
    config_hash      TEXT NOT NULL,
    random_seed      INTEGER,
    status           TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    error            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiment_hypothesis ON research_experiment(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_experiment_status ON research_experiment(status);

CREATE TABLE IF NOT EXISTS research_result (
    id            TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    metric        TEXT NOT NULL,
    value         REAL,
    ci_low        REAL,
    ci_high       REAL,
    p_value       REAL,
    sample_size   INTEGER,
    splits_json   TEXT,
    verdict       TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_result_experiment ON research_result(experiment_id);

CREATE TABLE IF NOT EXISTS strategy_version (
    id            TEXT PRIMARY KEY,
    version       INTEGER NOT NULL,
    parent_id     TEXT,
    spec_json     TEXT NOT NULL,
    status        TEXT NOT NULL,
    provenance_json TEXT,
    created_at    TEXT NOT NULL,
    approved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_strategy_status ON strategy_version(status);

CREATE TABLE IF NOT EXISTS strategy_deployment (
    id          TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    mode        TEXT NOT NULL,
    start_date  TEXT,
    end_date    TEXT,
    capital     REAL,
    broker      TEXT,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deployment_strategy ON strategy_deployment(strategy_id);

CREATE TABLE IF NOT EXISTS strategy_attribution (
    id            TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    total_return  REAL,
    excess_return REAL,
    costs         REAL,
    factor_json   TEXT,
    sector_json   TEXT,
    stock_json    TEXT,
    regime        TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attribution_deployment ON strategy_attribution(deployment_id);

CREATE TABLE IF NOT EXISTS decision_log (
    id            TEXT PRIMARY KEY,
    decision_date TEXT NOT NULL,
    type          TEXT NOT NULL,
    target        TEXT NOT NULL,
    change        TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    evidence_json TEXT,
    expected      TEXT,
    review_date   TEXT,
    status        TEXT NOT NULL,
    outcome       TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_date ON decision_log(decision_date);
CREATE INDEX IF NOT EXISTS idx_decision_status ON decision_log(status);

-- LLM 研究助手
CREATE TABLE IF NOT EXISTS prompt_registry (
    prompt_id     TEXT NOT NULL,
    version       INTEGER NOT NULL,
    task          TEXT NOT NULL,
    template_hash TEXT NOT NULL,
    schema_json   TEXT,
    model_tier    TEXT,
    tools_json    TEXT,
    max_tokens    INTEGER,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    PRIMARY KEY (prompt_id, version)
);

CREATE TABLE IF NOT EXISTS llm_run (
    id            TEXT PRIMARY KEY,
    task          TEXT NOT NULL,
    prompt_id     TEXT,
    prompt_version INTEGER,
    model         TEXT,
    input_hash    TEXT,
    output_json   TEXT,
    schema_ok     INTEGER,
    citations_ok  INTEGER,
    tokens        INTEGER,
    latency_ms    INTEGER,
    human_feedback TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_run_task ON llm_run(task, created_at);

-- LLM 任务注册（R0-8）：任务/模型档/工具白名单/输出 schema/token 预算
CREATE TABLE IF NOT EXISTS llm_task (    task_id       TEXT NOT NULL,
    version       INTEGER NOT NULL,
    description   TEXT NOT NULL,
    model_tier    TEXT NOT NULL,           -- fast / deep
    tools_json    TEXT,
    output_schema_json TEXT NOT NULL,      -- JSON schema（缺失即不可调用）
    max_tokens    INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    PRIMARY KEY (task_id, version)
);
CREATE INDEX IF NOT EXISTS idx_llm_task_status ON llm_task(status);

-- 市场状态快照（R1-2）：每个交易日一条，metrics 缺失显式 NULL
CREATE TABLE IF NOT EXISTS market_state_daily (
    trade_date  TEXT PRIMARY KEY,
    metrics_json TEXT,
    missing_json TEXT,
    coverage    REAL,
    status      TEXT NOT NULL,
    quality_json TEXT,
    created_at  TEXT NOT NULL
);
"""
