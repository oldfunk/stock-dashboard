"""市场状态向量定义（2026-10-27，R1-1）。

五块：**估值 / 基本面 / 广度 / 流动性 / 质量**。
- 字段注册表：``name / block / formula / source / missing_policy / unit / direction``。
- 缺失策略：``null``（保留 NULL，不补零）或 ``fail_closed``（该字段缺失则向量 invalid）。
- 计算：纯函数，从业务库读取；缺失显式 NULL，绝不补零/造数。

字段新增须注册（未注册字段不进向量），便于审计"每个数字从哪来"。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from src.models.database import db_conn
from src.research.dao import ResearchDAO

BLOCKS = ("valuation", "fundamentals", "breadth", "liquidity", "quality")
MISSING_POLICIES = ("null", "fail_closed")
DIRECTIONS = ("higher_better", "lower_better", "neutral")


class StateError(ValueError):
    """字段注册/校验失败。"""


@dataclass
class StateField:
    name: str
    block: str
    formula: str
    source: str
    missing_policy: str = "null"
    unit: str = ""
    direction: str = "neutral"
    implemented: bool = True   # False = 已注册但尚未实现（不计入覆盖率分母）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketStateVector:
    trade_date: str
    metrics: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    coverage: float = 0.0
    status: str = "pass"
    regime: str = ""
    regime_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "metrics": self.metrics,
            "missing": self.missing,
            "quality": self.quality,
            "coverage": self.coverage,
            "status": self.status,
            "regime": self.regime,
            "regime_confidence": self.regime_confidence,
        }

    @property
    def fail_closed_missing(self) -> list[str]:
        return self.quality.get("fail_closed_missing", [])


# ── 字段注册 ────────────────────────────────────────────────────────────────

def default_fields() -> list[StateField]:
    """五块字段定义（公式/来源/缺失策略）。"""
    return [
        # 估值
        StateField("pe_median", "valuation", "全市场 PE(TTM) 中位数",
                   "stock_snapshot.pe", "null", "x", "lower_better"),
        StateField("pb_median", "valuation", "全市场 PB 中位数",
                   "stock_snapshot.pb", "null", "x", "lower_better"),
        StateField("pe_percentile_5y", "valuation",
                   "当前 PE 在过去 5 年的分位", "kline_daily.close_raw + financial_history",
                   "null", "%", "lower_better", implemented=False),
        # 基本面
        StateField("roe_median", "fundamentals", "全市场 ROE 中位数",
                   "stock_snapshot.roe", "null", "%", "higher_better"),
        StateField("revenue_growth_median", "fundamentals",
                   "全市场营收增速中位数", "stock_snapshot.revenue_growth",
                   "null", "%", "higher_better"),
        StateField("profit_growth_median", "fundamentals",
                   "全市场净利增速中位数", "stock_snapshot.profit_growth",
                   "null", "%", "higher_better"),
        StateField("gross_margin_median", "fundamentals",
                   "全市场毛利率中位数（最新年报）",
                   "financial_history.gross_margin", "null", "%", "higher_better"),
        # 广度
        StateField("pct_above_ma20", "breadth",
                   "收盘价站上 20 日均线的股票占比", "kline_daily.close_adj",
                   "null", "%", "higher_better"),
        StateField("pct_above_ma60", "breadth",
                   "收盘价站上 60 日均线的股票占比", "kline_daily.close_adj",
                   "null", "%", "higher_better"),
        StateField("adv_dec_ratio", "breadth", "上涨家数/下跌家数",
                   "kline_daily.close_adj", "null", "x", "higher_better"),
        # 流动性
        StateField("total_turnover_yi", "liquidity",
                   "全市场当日成交额（亿元）", "kline_daily.amount", "null", "亿元",
                   "neutral"),
        StateField("avg_turnover_20d_yi", "liquidity",
                   "全市场 20 日均成交额（亿元）", "kline_daily.amount", "null", "亿元",
                   "neutral"),
        # 质量
        StateField("debt_ratio_median", "quality", "全市场资产负债率中位数",
                   "stock_snapshot.debt_ratio", "null", "%", "lower_better"),
        StateField("st_ratio", "quality", "ST/*ST 占比",
                   "stock_snapshot.is_st", "null", "%", "lower_better"),
    ]


class StateSchema:
    """状态字段注册表。"""

    def __init__(self, fields: Iterable[StateField] | None = None):
        self._fields: dict[str, StateField] = {}
        for f in (fields if fields is not None else default_fields()):
            self.register(f)

    def register(self, f: StateField) -> StateField:
        if f.block not in BLOCKS:
            raise StateError(f"非法分块: {f.block}")
        if f.missing_policy not in MISSING_POLICIES:
            raise StateError(f"非法缺失策略: {f.missing_policy}")
        if f.direction not in DIRECTIONS:
            raise StateError(f"非法方向: {f.direction}")
        if f.name in self._fields:
            raise StateError(f"字段已注册: {f.name}")
        self._fields[f.name] = f
        return f

    def get(self, name: str) -> StateField | None:
        return self._fields.get(name)

    def list(self, block: str | None = None) -> list[StateField]:
        fields = list(self._fields.values())
        return [f for f in fields if block is None or f.block == block]

    def names(self) -> list[str]:
        return list(self._fields)

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [f.to_dict() for f in self.list()],
                "blocks": list(BLOCKS)}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def compute_vector(trade_date: str,
                   schema: StateSchema | None = None) -> MarketStateVector:
    """计算某日状态向量（纯函数；缺失显式 NULL）。

    R1-1 先落地估值/基本面/质量（来自最新快照）与流动性（当日成交额）；
    广度（MA20/MA60、涨跌比）在 R1-2 快照中接入。
    """
    schema = schema or StateSchema()
    metrics: dict[str, float] = {}
    # 初始：所有已注册字段都视为缺失；算出来的再移除（未实现字段也显式 missing）
    missing: list[str] = list(schema.names())
    fail_closed_missing: list[str] = []

    def _set(name: str, value: float | None) -> None:
        if value is None:
            if schema.get(name) and schema.get(name).missing_policy == "fail_closed":
                fail_closed_missing.append(name)
        else:
            metrics[name] = value
            if name in missing:
                missing.remove(name)

    # 估值/基本面/质量：trade_date 当日或之前最新快照（避免前视）
    with db_conn() as conn:
        row = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM stock_snapshot "
            "WHERE snapshot_date <= ?", (trade_date,)).fetchone()
        snapshot_date = row["d"] if row else None
        for name in ("pe_median", "pb_median", "roe_median",
                     "revenue_growth_median", "profit_growth_median",
                     "debt_ratio_median"):
            col = {
                "pe_median": "pe", "pb_median": "pb", "roe_median": "roe",
                "revenue_growth_median": "revenue_growth",
                "profit_growth_median": "profit_growth",
                "debt_ratio_median": "debt_ratio",
            }[name]
            values = [r[0] for r in conn.execute(
                f"SELECT {col} FROM stock_snapshot WHERE {col} IS NOT NULL "
                "AND snapshot_date = ?", (snapshot_date,)).fetchall()] \
                if snapshot_date else []
            _set(name, _median(values))
        # 毛利率：最新年报（financial_history，JOIN 取每只股票最新年报）
        gm = [r[0] for r in conn.execute(
            "SELECT h.gross_margin FROM financial_history h "
            "JOIN (SELECT stock_code, MAX(report_date) AS md "
            "      FROM financial_history WHERE report_type = 'A' "
            "      GROUP BY stock_code) m "
            "  ON m.stock_code = h.stock_code AND m.md = h.report_date "
            "WHERE h.report_type = 'A' AND h.gross_margin IS NOT NULL").fetchall()]
        _set("gross_margin_median", _median(gm))
        st_total = conn.execute(
            "SELECT COUNT(*) AS n FROM stock_snapshot WHERE snapshot_date = ?",
            (snapshot_date,)).fetchone()["n"] if snapshot_date else 0
        st_count = conn.execute(
            "SELECT COUNT(*) AS n FROM stock_snapshot WHERE snapshot_date = ? "
            "AND is_st = 1", (snapshot_date,)).fetchone()["n"] if snapshot_date else 0
    _set("st_ratio", round(100.0 * st_count / st_total, 4) if st_total else None)

    # 流动性：当日成交额
    with db_conn() as conn:
        row = conn.execute(
            "SELECT SUM(amount) AS s FROM kline_daily WHERE trade_date = ?",
            (trade_date,)).fetchone()
        _set("total_turnover_yi",
             round(row["s"] / 1e8, 4) if row["s"] is not None else None)
        row20 = conn.execute(
            "SELECT AVG(s) AS a FROM (SELECT SUM(amount) AS s FROM kline_daily "
            "WHERE trade_date <= ? GROUP BY trade_date "
            "ORDER BY trade_date DESC LIMIT 20)", (trade_date,)).fetchone()
        _set("avg_turnover_20d_yi",
             round(row20["a"] / 1e8, 4) if row20["a"] is not None else None)

    # 广度：MA20/MA60 占比、涨跌比（窗口 61 个交易日）
    # 优化：先取最近 61 个交易日，只扫这些行（避免全表窗口函数）
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT code, trade_date, close_adj FROM kline_daily "
            "WHERE close_adj IS NOT NULL AND trade_date IN ("
            "  SELECT DISTINCT trade_date FROM kline_daily "
            "  WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 61)",
            (trade_date,)).fetchall()
    series: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        series.setdefault(r["code"], []).append((r["trade_date"], r["close_adj"]))
    above20 = above60 = adv = dec = total = 0
    for pts in series.values():
        pts.sort()   # 窗口查询是 DESC，这里升序以便取最新
        if len(pts) < 60 or pts[-1][0] != trade_date:
            continue   # 窗口不足 60 日 -> 不计算（不做过短均线的近似）
        total += 1
        last = pts[-1][1]
        ma20 = sum(p[1] for p in pts[-20:]) / 20
        ma60 = sum(p[1] for p in pts[-60:]) / 60
        if last > ma20:
            above20 += 1
        if last > ma60:
            above60 += 1
        if last > pts[-2][1]:
            adv += 1
        elif last < pts[-2][1]:
            dec += 1
    _set("pct_above_ma20", round(100.0 * above20 / total, 4) if total else None)
    _set("pct_above_ma60", round(100.0 * above60 / total, 4) if total else None)
    _set("adv_dec_ratio", round(adv / dec, 4) if dec else None)

    # 波动率：近 20 个交易日**等权日收益**的年化波动（regime 的波动规则用）
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, AVG(close_adj) AS avg_close FROM kline_daily "
            "WHERE close_adj IS NOT NULL AND trade_date IN ("
            "  SELECT DISTINCT trade_date FROM kline_daily "
            "  WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 21) "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 21",
            (trade_date,)).fetchall()
    if len(rows) >= 2:
        closes = [r["avg_close"] for r in rows][::-1]
        rets = [closes[i] / closes[i - 1] - 1.0
                for i in range(1, len(closes)) if closes[i - 1]]
        if len(rets) >= 2:
            import statistics
            vol = statistics.pstdev(rets) * (252 ** 0.5)
            _set("volatility_20d", round(vol, 4))
        else:
            _set("volatility_20d", None)
    else:
        _set("volatility_20d", None)

    # 覆盖率：只算"已实现"字段（未实现的不计入分母）
    implemented = [n for n in schema.names()
                   if schema.get(n) and schema.get(n).implemented]
    present = [n for n in implemented if n in metrics]
    coverage = round(len(present) / len(implemented), 4) if implemented else 0.0
    status = "invalid" if fail_closed_missing else "pass"
    return MarketStateVector(
        trade_date=trade_date, metrics=metrics, missing=missing,
        quality={"snapshot_date": snapshot_date,
                 "fail_closed_missing": fail_closed_missing},
        coverage=coverage, status=status)


# ── 快照持久化（R1-2） ──────────────────────────────────────────────────────

COVERAGE_MIN = 0.8   # 状态向量覆盖率下限（低于则 invalid，不生成叙事）


def assess_vector(vector: MarketStateVector,
                  coverage_min: float = COVERAGE_MIN) -> MarketStateVector:
    """按覆盖率判定状态：不足则 invalid（fail-closed，不生成叙事）。"""
    vector.quality["coverage_min"] = coverage_min
    if vector.coverage < coverage_min:
        vector.status = "invalid"
        vector.quality["fail_closed_missing"] = vector.fail_closed_missing
    return vector


def record_state_quality(vector: MarketStateVector,
                         dao: ResearchDAO | None = None) -> None:
    """把状态向量质量写 data_quality_daily（供 R0-5 质量门复用）。"""
    dao = dao or ResearchDAO()
    dao.insert_quality({
        "dataset": "market_state_daily", "trade_date": vector.trade_date,
        "coverage": vector.coverage,
        "missing_rate": round(1.0 - vector.coverage, 4),
        "outliers": 0, "pit_violations": 0,
        "status": vector.status, "details": {"missing": vector.missing},
    })


def save_vector(vector: MarketStateVector, dao: ResearchDAO | None = None) -> None:
    dao = dao or ResearchDAO()
    dao.insert_market_state(vector.to_dict())


def get_vector(trade_date: str,
               dao: ResearchDAO | None = None) -> MarketStateVector | None:
    dao = dao or ResearchDAO()
    row = dao.get_market_state(trade_date)
    if row is None:
        return None
    return MarketStateVector(
        trade_date=row["trade_date"], metrics=row["metrics"],
        missing=row["missing"], quality=row["quality"],
        coverage=row["coverage"], status=row["status"],
        regime=row.get("regime") or "",
        regime_confidence=row.get("regime_confidence") or 0.0)


def list_vectors(limit: int = 30,
                 dao: ResearchDAO | None = None) -> list[dict]:
    dao = dao or ResearchDAO()
    return dao.list_market_states(limit)


def trading_dates(start: str, end: str,
                  dao: ResearchDAO | None = None) -> list[str]:
    dao = dao or ResearchDAO()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM kline_daily "
            "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (start, end)).fetchall()
    return [r["trade_date"] for r in rows]


def snapshot_range(start: str, end: str, dry_run: bool = False,
                   dao: ResearchDAO | None = None) -> dict:
    """对 [start, end] 内每个交易日计算并落库状态向量（可重跑）。"""
    dao = dao or ResearchDAO()
    dates = trading_dates(start, end, dao)
    written = 0
    for day in dates:
        vector = assess_vector(compute_vector(day))
        # regime 由透明规则计算（本地导入避免与 regime.py 循环依赖）
        from src.research.regime import classify_regime
        regime = classify_regime(vector)
        vector.regime = regime.regime
        vector.regime_confidence = regime.confidence
        if not dry_run:
            record_state_quality(vector, dao)
            save_vector(vector, dao)
        written += 1
    return {"start": start, "end": end, "dates": len(dates),
            "written": written, "dry_run": dry_run}
