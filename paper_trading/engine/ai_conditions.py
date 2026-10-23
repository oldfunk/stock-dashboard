"""AI 条件单设计器：LLM 只出"触发条件 + 有效期 + 理由"，引擎盯盘触发执行。

分工（关键）：
- LLM：今天的判断 → 变成**可验证的条件**（例：宁德时代 跌破 260 买 1 万，
  3 个交易日内有效，理由写清）
- 引擎：盘中每分钟核对实时价，触发即过风控下单；不触发就 0 成本

好处：AI 调用频率可以低（一天 1~2 次），但盘中覆盖是连续的；
且每条条件都留痕（触发/未触发/被风控拒），可复盘 AI 准不准。
"""
from __future__ import annotations

import json as _json
import math
from datetime import date, datetime
from typing import Optional

COND_RULES = """条件单规则（违反即作废该条）：
1. 只输出 JSON：{"conditions":[{"symbol":"6位代码","side":"buy|sell",
   "cond":{"type":"price_below|price_above|pct_below|pct_above","value":数字},
   "value":买入金额(元,buy必填),"volume":股数(可选),
   "expiry":"YYYY-MM-DD","reason":"一句话理由"}],"summary":"一句话总结"}
2. 条件必须是**可验证的数字**（触发价或涨跌幅%），不许写"跌多了就买"这类模糊话。
   price_below/price_above 是**绝对价格**；pct_below/pct_above 是**当日涨跌幅%**
   （相对昨收，例如 pct_below 3 = 当日跌 3%），不是从峰值回撤。
   要表达"从高点回撤后买入"，请用 price_below 并给出具体价格。
3. 触发价要与当前价有合理距离（不要贴着现价设条件，那等于立刻成交）
4. 最多 {max_conds} 条；没有看好的机会就输出空数组
5. expiry 最长 {max_days} 天
6. 只用给定股票池里的代码；数量必须 100 的整数倍
7. 买入金额 value 是人民币元，不是股数：不写 volume 时必须在
   {min_ticket}~{max_value} 元之间（低于 {min_ticket} 元会因手续费地板被拒）；
   写 volume 时按 100 股整数倍，且按触发价估算的金额也要 ≥{min_ticket} 元。
   卖出 volume 可省略=全部可用。
8. 全部中文。
9. 卖出条件（side=sell）只能挂在**当前持仓**的代码上：没有持仓的代码给 sell
   条件会直接作废（提示词与校验层双重保证，不是靠自觉）。"""


def build_prompt(pool: list[str], quotes: dict, positions: list[dict],
                 scheme_txt: str, asof: str, max_conds: int = 5,
                 max_days: int = 3, event_txt: str = "",
                 min_ticket: float = 1000.0,
                 max_value: float = 20000.0) -> tuple[str, str]:
    """构造（system, user）prompt：只给必要上下文，控制 token。"""
    q_lines = []
    for s in pool[:20]:
        q = quotes.get(s) or {}
        px = q.get("price")
        chg = q.get("change_pct")
        if px:
            q_lines.append(f"- {s} 现价 {px}（{chg:+.2f}%）" if chg is not None
                           else f"- {s} 现价 {px}")
    pos_txt = ("、".join(f"{p['stock']}{p['total']}股@{p['avg_cost']}"
                          for p in positions) or "空仓")
    event_line = ""
    if event_txt.strip():
        event_line = ("\n本次盘中异动（触发复盘；条件单仍由引擎盘中执行）：\n"
                      + event_txt.strip()[:1200] + "\n")
    user = (f"数据截至 {asof}\n股票池实时价：\n" + ("\n".join(q_lines) or "（无）")
            + f"\n当前持仓：{pos_txt}\n{scheme_txt}\n{event_line}"
            + f"请给出最多 {max_conds} 条条件单（最长 {max_days} 天有效）。")
    # 不能用 str.format：规则正文里的 JSON 示例本身含大括号（{"conditions": …}），
    # format 会把它当占位符 → KeyError: '"conditions"'（真机上「AI 设计」一直 500）
    rules = (COND_RULES.replace("{max_conds}", str(max_conds))
                        .replace("{max_days}", str(max_days))
                        .replace("{min_ticket}", f"{float(min_ticket):g}")
                        .replace("{max_value}", f"{float(max_value):g}"))
    return rules, user


def parse_conditions_verbose(raw: Optional[str], pool: list[str],
                             max_conds: int = 5, max_value: float = 20000.0,
                             min_ticket: float = 1000.0,
                             max_days: int = 30,
                             holdings: Optional[list] = None
                             ) -> tuple[list[dict], list[dict]]:
    """解析并校验 LLM 条件单，返回 (合法条目, 被拒条目+原因)。

    不能只返回合法条目：模型给 2000 元买入（低于手续费地板）时，
    静默丢弃会让面板显示“解析 0 条”却看不到原因。拒单原因必须可审计。

    `holdings` 传入真实持仓代码集时，卖出条件只允许挂在持仓上（无持仓即拒）；
    为 None 时保持旧行为（不校验持仓），仅供手工/兼容调用。
    """
    from paper_trading.engine.conditional import CONDITION_TYPES

    rejected: list[dict] = []

    def _reject(c, why: str) -> None:
        item = {"why": why}
        if isinstance(c, dict):
            cond = c.get("cond") if isinstance(c.get("cond"), dict) else {}
            item.update({
                "symbol": str(c.get("symbol") or ""),
                "side": str(c.get("side") or ""),
                "cond_type": str(cond.get("type") or ""),
                "cond_value": cond.get("value"),
                "value": c.get("value"),
                "volume": c.get("volume"),
            })
        rejected.append(item)

    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        obj = _json.loads(raw[start:end])
    except Exception:
        _reject(None, "输出不是合法 JSON（缺少完整对象）")
        return [], rejected
    conds = obj.get("conditions") if isinstance(obj, dict) else None
    if not isinstance(conds, list):
        _reject(None, "输出缺少 conditions 数组")
        return [], rejected

    allow = set(pool or [])
    held = ({str(s).strip() for s in holdings}
            if holdings is not None else None)
    out: list[dict] = []
    for c in conds:
        if len(out) >= max_conds:
            _reject(c, f"超过单轮条件数上限 {max_conds}")
            continue
        if not isinstance(c, dict):
            _reject(c, "条件不是对象")
            continue
        sym = str(c.get("symbol") or "").strip()
        side = str(c.get("side") or "").strip().lower()
        cond = c.get("cond") if isinstance(c.get("cond"), dict) else {}
        ctype = str(cond.get("type") or "").strip().lower()
        try:
            cval = float(cond.get("value"))
        except (TypeError, ValueError):
            _reject(c, "触发值不是数字")
            continue
        if not math.isfinite(cval) or cval <= 0:
            _reject(c, "触发值必须为正数")
            continue
        try:
            vol = int(float(c.get("volume") or 0))
        except (TypeError, ValueError):
            _reject(c, "数量不是整数")
            continue
        try:
            val = float(c.get("value") or 0)
        except (TypeError, ValueError):
            _reject(c, "买入金额不是数字")
            continue
        if not math.isfinite(val) or val < 0:
            _reject(c, "买入金额必须是非负数")
            continue
        if len(sym) != 6 or not sym.isdigit():
            _reject(c, "代码不是 6 位数字")
            continue
        if sym not in allow:
            _reject(c, "不在给定股票池")
            continue
        if side not in ("buy", "sell"):
            _reject(c, "side 必须是 buy/sell")
            continue
        if side == "sell" and held is not None and sym not in held:
            _reject(c, "卖出条件必须对应真实持仓（当前无该代码持仓）")
            continue
        if ctype not in CONDITION_TYPES:
            _reject(c, "非法条件类型")
            continue
        expiry = str(c.get("expiry") or "").strip()
        if expiry:
            try:
                exp_date = datetime.fromisoformat(expiry[:10]).date()
            except (TypeError, ValueError):
                _reject(c, "expiry 不是 YYYY-MM-DD")
                continue
            if max_days and max_days > 0:
                days = (exp_date - date.today()).days
                if days > int(max_days):
                    _reject(c, f"有效期 {days} 天超过上限 {int(max_days)} 天")
                    continue
        if side == "buy":
            if vol and vol % 100 != 0:
                _reject(c, "买入数量必须是 100 的整数倍")
                continue
            if not vol and not (min_ticket <= val <= max_value):
                _reject(c, f"买入金额 {val:g} 不在 {min_ticket:g}~{max_value:g} 元")
                continue
            if vol and ctype == "price_below" and min_ticket > 0 \
                    and vol * cval < min_ticket:
                _reject(c, f"买入数量 {vol} 股按触发价 {cval:g} 估算 "
                           f"{vol * cval:g} 元，低于最小金额 {min_ticket:g}")
                continue
            if vol:
                val = 0.0
        if side == "sell" and vol and vol % 100 != 0:
            _reject(c, "卖出数量必须是 100 的整数倍")
            continue
        out.append({"symbol": sym, "side": side, "cond_type": ctype,
                    "cond_value": cval, "volume": vol, "value": val,
                    "expiry": expiry,
                    "note": str(c.get("reason") or "")[:200],
                    "source": "ai"})
    return out, rejected


def parse_conditions(raw: Optional[str], pool: list[str],
                     max_conds: int = 5, max_value: float = 20000.0,
                     min_ticket: float = 1000.0,
                     max_days: int = 30,
                     holdings: Optional[list] = None) -> list[dict]:
    """兼容旧调用：只要合法条目；需要审计原因时用 parse_conditions_verbose。"""
    valid, _rejected = parse_conditions_verbose(
        raw, pool, max_conds=max_conds, max_value=max_value,
        min_ticket=min_ticket, max_days=max_days, holdings=holdings)
    return valid
