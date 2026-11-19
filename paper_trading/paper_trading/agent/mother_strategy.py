"""母项目价值策略的引用与买入闸门（2026-11-19）。

背景：母侧实例此前只有"股票池来自母筛选"，买卖参考仍是 MA5/20——母项目自己的
分析（`screening_result.ai_trade_strategy` 的 signal/buy_zone/stop_loss）**根本
没进上下文**（`screening_detail()` 的 SELECT 里没有这一列）。实测 2026-11-19 的
计划买入 601899：母分析 **HOLD**、买入区间 **8-12**、现价 **23.88**——策略被绕开。

本模块只做两件事：

1. 把母筛选行里的 `ai_trade_strategy` 变成可读的参考文本（进 prompt）；
2. 给出**买入**的 fail-closed 判据（代码闸门，不靠模型自觉）。

卖出不在这里拦：止损/减仓是降风险动作，仍由风控与论点条件管。
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Optional

_BUY_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~—－至]\s*(\d+(?:\.\d+)?)")
_BELOW = re.compile(r"(?:低于|小于|不超过|≤|<)\s*(\d+(?:\.\d+)?)")
_ABOVE = re.compile(r"(?:高于|大于|不低于|≥|>)\s*(\d+(?:\.\d+)?)")
_BELOW_CN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元)?\s*(?:以下|以内)")


def parse_price_zone(text: Any) -> Optional[tuple[Optional[float], Optional[float]]]:
    """买入区间文本 -> `(low|None, high|None)`；不是价格区间则 None。

    认：`8-12`、`18-22元`、`低于15元`、`8元以下`、`≥20元`。
    不认（返回 None）：`300-320亿市值`（市值口径，无法对价）、空串。
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s or "亿" in s or "市值" in s:
        return None
    s = (s.replace("人民币", "").replace("元", "").replace("¥", "")
          .replace("￥", "").strip())
    m = _BUY_RANGE.search(s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (min(lo, hi), max(lo, hi))
    m = _BELOW.search(s)
    if m:
        return (None, float(m.group(1)))
    m = _ABOVE.search(s)
    if m:
        return (float(m.group(1)), None)
    m = _BELOW_CN.search(s)
    if m:
        return (None, float(m.group(1)))
    return None


def _as_trade_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except ValueError:
            return {}
    return {}


def refs_from_screening(block: Optional[dict]) -> dict[str, dict]:
    """`screening_detail()` 的 block -> `{code: {signal, buy_zone, ...}}`。

    兼容两种形状：block 包装（`{"source","asof","data":{"rows"}}`）与裸行（`{"rows"}`）。
    """
    block = block or {}
    rows = block.get("rows") or (block.get("data") or {}).get("rows") or []
    out: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        trade = _as_trade_dict(row.get("ai_trade_strategy"))
        out[code] = {
            "signal": str(trade.get("signal") or "").upper(),
            "buy_zone": str(trade.get("buy_zone") or ""),
            "stop_loss": str(trade.get("stop_loss") or ""),
            "target_price": str(trade.get("target_price") or ""),
            "take_profit": str(trade.get("take_profit") or ""),
            "score": row.get("score"),
            "ai_failed": bool(row.get("ai_failed")),
        }
    return out


def ref_text(refs: dict[str, dict], symbols: list[str]) -> str:
    """给 prompt 的母策略参考段（只列池内标的）。"""
    lines = ["母项目价值策略（权威）：signal 只有 BUY 才允许新开仓；"
             "HOLD/AVOID 不买；买入区间按现价校验。"]
    for code in symbols:
        r = refs.get(str(code).zfill(6))
        if not r:
            lines.append(f"{code}：母库无该标的最新分析")
            continue
        if r.get("ai_failed") or not r.get("signal"):
            lines.append(f"{code}：母库分析缺失/失败")
            continue
        lines.append(
            f"{code}：{r['signal']}｜买入区间 {r['buy_zone'] or '-'}｜"
            f"止损 {r['stop_loss'] or '-'}｜目标 {r['target_price'] or '-'}｜"
            f"评分 {r.get('score')}")
    return "\n".join(lines)


def clean_mother_actions(actions: list, refs: dict[str, dict], *,
                         asof: Optional[str] = None,
                         today: Optional[str] = None,
                         available: Optional[dict[str, int]] = None,
                         max_orders: Optional[int] = None,
                         ) -> tuple[list, list, list]:
    """母方案的计划整备：过滤违规买入、把 AVOID 持仓的 hold 换成卖出。

    返回 `(可以直接落计划的动作, 被拒动作(带 reject_reason), 被强制离场的代码)`。

    为什么在计划阶段就做：执行层闸门只是兜底；计划里留着"必被拒的买单"或
    "hold 与 sell 并列"会让面板与审计自相矛盾（2026-11-19 实测踩过）。
    """
    available = available or {}
    clean: list = []
    rejected: list = []
    forced: list[str] = []
    seen: set[str] = set()
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        act = str(a.get("action", "")).lower()
        sym = str(a.get("symbol") or "").zfill(6)
        if act == "buy" and sym:
            try:
                price = float(a.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                rejected.append({**a, "reject_reason": "无有效价格（无法校验买入区间）"})
                continue
            why = buy_violation(sym, price, refs, asof=asof, today=today)
            if why:
                rejected.append({**a, "reject_reason": why})
                continue
        if (act == "hold" and sym
                and str((refs.get(sym) or {}).get("signal") or "").upper() == "AVOID"
                and int(available.get(sym, 0)) > 0):
            clean.append(_sell_action(sym, int(available[sym])))
            forced.append(sym)
            seen.add(sym)
            continue
        if act == "sell":
            seen.add(sym)
        clean.append(a)
    for sym, vol in available.items():
        sym = str(sym).zfill(6)
        if sym in seen or int(vol or 0) <= 0:
            continue
        if str((refs.get(sym) or {}).get("signal") or "").upper() != "AVOID":
            continue
        clean.append(_sell_action(sym, int(vol)))
        forced.append(sym)
    if max_orders is not None:
        # 笔数上限在**计划阶段**就对齐执行层（卖出优先，降风险不能被买单挤掉）。
        n_sell = sum(1 for a in clean if str(a.get("action")).lower() == "sell")
        room = max(0, int(max_orders) - n_sell)
        out: list = []
        kept = 0
        for a in clean:
            if str(a.get("action")).lower() == "buy":
                if kept >= room:
                    rejected.append({**a, "reject_reason": (
                        f"超单轮笔数上限（母方案 max_orders_per_run={max_orders}，卖出优先）")})
                    continue
                kept += 1
            out.append(a)
        clean = out
    return clean, rejected, forced


def _sell_action(sym: str, volume: int) -> dict:
    return {
        "action": "sell", "symbol": sym, "volume": volume,
        "reason": "母策略 AVOID：代码层强制离场",
        "basis": ["screening.ai_trade_strategy.signal"],
        "thesis": "母项目最新一轮分析为 AVOID，按母策略纪律离场",
        "risk_stop": None, "confidence": 100, "expected_hold_days": 0,
    }


def buy_violation(code: str, price: float, refs: dict[str, dict], *,
                  asof: Optional[str] = None,
                  today: Optional[str] = None,
                  max_age_days: int = 3) -> Optional[str]:
    """母策略视角下这笔买入是否违规；违规返回原因，放行返回 None。

    fail-closed：没有分析、分析过期、信号非 BUY、价格不在区间内，都拒绝。
    市值口径的区间（含"亿市值"）无法对价，只校验 signal。
    """
    r = refs.get(str(code).zfill(6))
    if not r or r.get("ai_failed") or not r.get("signal"):
        return "母策略无可用分析（fail-closed）"
    if asof and today:
        try:
            age = (date.fromisoformat(str(today)[:10])
                   - date.fromisoformat(str(asof)[:10])).days
            if age > int(max_age_days):
                return f"母策略分析过期（asof={str(asof)[:10]}，{age} 天前）"
        except ValueError:
            pass
    sig = str(r.get("signal") or "").upper()
    if sig != "BUY":
        return f"母策略信号={sig}（仅 BUY 允许买入）"
    zone = parse_price_zone(r.get("buy_zone"))
    if zone and price and price > 0:
        lo, hi = zone
        if (lo is not None and price < lo) or (hi is not None and price > hi):
            rng = f"{'' if lo is None else lo}-{'' if hi is None else hi}"
            return f"现价 {price:.2f} 不在母策略买入区间 {rng}"
    return None
