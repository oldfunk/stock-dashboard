"""R3 策略登记/版本（2026-11-28）。

S-BASE-v0 是当前母策略的**声明式快照**：7 门规则 + 五维评分权重 +
交易节奏。这里不改变筛选逻辑，只把"在跑的策略"登记成可审计版本。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.research.dao import ResearchDAO
from src.research.domain import DomainError, next_id, validate_transition
from src.utils import now_cn

BASE_STRATEGY_ID = "S-BASE-v0"

BASE_SPEC = {
    "name": "价值投资 7 门 + 五维评分",
    "version": "v0",
    "source": "src/screener/value_screener.py + config/config.yaml（2026-11-28）",
    "universe": "A 股全市场，排除 ST/次新；每日 15:30 流水线",
    "gates": [
        {"id": "exclude_st", "rule": "排除 ST/*ST"},
        {"id": "pe_range", "rule": "3 <= PE <= 20"},
        {"id": "roe", "rule": "当前 ROE >= 5%；10/5 年均 ROE >= 8%（战略投入期豁免 A）"},
        {"id": "ocf", "rule": "OCF/股 > 0；OCF/NI >= 0.7（豁免 B：高毛利+高增长+净利改善）"},
        {"id": "net_margin", "rule": "净利率 >= 5%（毛利率 <30% 时强制）"},
        {"id": "interest_coverage", "rule": "利息覆盖 >= 2"},
        {"id": "fcf_dilution", "rule": "5 年累计 FCF > 0；股本稀释 <= 20%"},
    ],
    "scoring": {
        "dimensions": ["roe", "pe", "growth", "debt", "margin"],
        "weights": {"roe": 0.30, "pe": 0.20, "growth": 0.20,
                    "debt": 0.15, "margin": 0.15},
        "range": "0-100，含一致性加分",
    },
    "portfolio_rules": {
        "max_candidates": 20,
        "single_position": "由子项目风控/仓位上限约束",
        "decision_frequency": "交易日一日一决策；母策略出信号，AI 遵循",
        "news": "新闻不进入交易；政策/事件只进周度复核清单",
    },
}


def ensure_base_strategy(dao: Optional[ResearchDAO] = None,
                         status: str = "paper") -> dict:
    """幂等登记 S-BASE-v0；已存在则原样返回。"""
    dao = dao or ResearchDAO()
    existing = dao.get_strategy(BASE_STRATEGY_ID)
    if existing:
        return existing
    now = now_cn().isoformat(timespec="seconds")
    dao.insert_strategy({
        "id": BASE_STRATEGY_ID,
        "version": 0,
        "parent_id": None,
        "spec": BASE_SPEC,
        "status": status,
        "provenance": {"source": BASE_SPEC["source"],
                       "registered_at": now,
                       "hypotheses": []},
        "created_at": now,
        "approved_at": now if status == "paper" else None,
    })
    return dao.get_strategy(BASE_STRATEGY_ID) or {}


def _supported_hypotheses(dao: ResearchDAO, ids: list[str]) -> list[str]:
    bad = []
    for hid in ids or []:
        h = dao.get_hypothesis(hid)
        if not h or str(h.get("status")) != "supported":
            bad.append(hid)
    return bad


def create_version(spec: dict, parent_id: str = BASE_STRATEGY_ID,
                   hypotheses: Optional[list[str]] = None,
                   provenance: Optional[dict] = None,
                   day: Optional[date] = None) -> dict:
    """新建 draft 版本；引用的假设必须全部 supported（R3-2）。"""
    dao = ResearchDAO()
    if parent_id and not dao.get_strategy(parent_id):
        raise DomainError(f"父策略不存在: {parent_id}")
    hypotheses = hypotheses or []
    bad = _supported_hypotheses(dao, hypotheses)
    if bad:
        raise DomainError(f"只能引用 supported 假设，非法: {bad}")
    ids = [s["id"] for s in dao.list_strategies(limit=500)]
    sid = next_id("S", day or date.today(), ids)
    now = now_cn().isoformat(timespec="seconds")
    dao.insert_strategy({
        "id": sid, "version": len(ids) + 1, "parent_id": parent_id,
        "spec": spec, "status": "draft",
        "provenance": {"hypotheses": hypotheses, **(provenance or {}),
                       "created_at": now},
        "created_at": now, "approved_at": None,
    })
    return dao.get_strategy(sid) or {}


def set_status(strategy_id: str, target: str) -> dict:
    dao = ResearchDAO()
    cur = dao.get_strategy(strategy_id)
    if not cur:
        raise DomainError(f"策略不存在: {strategy_id}")
    validate_transition("strategy", cur.get("status"), target)
    dao.update_strategy_status(strategy_id, target)
    return dao.get_strategy(strategy_id) or {}


def ensure_deployment(strategy_id: str = BASE_STRATEGY_ID,
                      capital: Optional[float] = None,
                      broker: str = "paper_trading") -> dict:
    """把纸盘账户登记为策略部署（R3-3）；已存在则返回。"""
    from src.paper_bridge.conn import connect
    from src.paper_bridge.settings import bridge_paths

    dao = ResearchDAO()
    existing = dao.list_deployments(strategy_id=strategy_id, limit=50)
    for d in existing:
        if d.get("broker") == broker:
            return d
    if not dao.get_strategy(strategy_id):
        raise DomainError(f"策略不存在: {strategy_id}")
    _, acct = bridge_paths()
    conn = connect(acct)
    if conn is None:
        raise RuntimeError("纸盘账户库不可读")
    try:
        acc = conn.execute("SELECT cash, initial_cash FROM account LIMIT 1").fetchone()
        nav = conn.execute(
            "SELECT MIN(substr(timestamp,1,10)) d FROM nav_history").fetchone()
        fill = conn.execute(
            "SELECT MIN(substr(timestamp,1,10)) d FROM fills").fetchone()
    finally:
        conn.close()
    dates = [x["d"] for x in (nav, fill) if x and x["d"]]
    start = min(dates) if dates else date.today().isoformat()
    cap = float(capital if capital is not None
                else ((acc["initial_cash"] if acc else 100000) or 100000))
    ids = [d["id"] for d in dao.list_deployments(limit=500)]
    did = next_id("D", date.today(), ids)
    now = now_cn().isoformat(timespec="seconds")
    dao.insert_deployment({
        "id": did, "strategy_id": strategy_id, "mode": "paper",
        "start_date": start, "end_date": None, "capital": cap,
        "broker": broker, "status": "running", "created_at": now,
    })
    return dao.get_deployment(did) or {}

