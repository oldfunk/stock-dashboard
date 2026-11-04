"""上下文压缩：预算感知的装配后处理（T63 重写分级，T64 修"knob 无效"）。

目标不变：母库富化后上下文变大，按 `agent.context_budget` 分级压缩，
**provenance 元数据永不被截断**（排查与 AI 分析依赖它）。

为什么不沿用旧顺序（2026-11-03 现网实测）：旧实现是"长文本截断 → 收盘价留 10 根 →
丢 intraday/ops/nav"，而现网那几段本来就短：预算设 8000/6000/4000/3000，输出体积
**一字节都没变**（97,912 → 97,912）。原因不是代码坏了，是**裁错了地方**：
体积在 `value_view`（股息史 26,850 / 追踪结果 11,590 / 财务摘要 7,997 / 基本面 6,591 /
AI 分析 4,361 字符）与 `screening` 12,246、全池收盘 14,677、要闻 6,466 上，
旧阶梯一级都没碰到。所以 `context_budget` 当时是个"定义了、也读了、但改变不了行为"的
knob——正是 T64 要抓的那类断层。

现在的分级按**实测体积分布**排，并且带一条硬保证：
1. 池内非持仓标的的长史类字段先走（股息史/追踪/notes/论点/AI 分析截到只够认脸）；
2. 收盘只留持仓的 N 根 + 池内 1 根（问"最近走势"靠持仓，池内只要参考价）；
3. 筛选行按条数与理由长度截、要闻按条数与摘要长度截；
4. 仍超预算才逐级丢：池内基本面 → 要闻 → 筛选 → 池内整张卡；
5. 到最后还压不进预算 ⇒ **如实回报** `within_budget=False` + 残余体积，
   不假装成功（纪律 4：宁可响，不静默）。

`compress_context()` 的老签名保留（决策路径与既有测试都在用），新增的
`shrink_to_budget()` 才返回元信息，给 `llm_ask` 的留痕用。
"""
from __future__ import annotations

import copy
import json
import re
from typing import Optional

#: 财务摘要进上下文的字段白名单（其余丢弃，控制体积）
_FIN_KEYS = ("roe_5y_avg", "roe_10y_avg", "gross_margin_5y_avg",
             "net_margin_5y_avg", "ocf_latest", "debt_ratio_latest",
             "roic_5y_avg", "data_years")
#: 池内（非持仓）只留这几个，够模型说清"这只是不是好票"
FIN_KEYS_POOL = ("roe_5y_avg", "net_margin_5y_avg", "debt_ratio_latest")

#: 每只标的卡的字段配额：持仓用 HELD，池内用 POOL（0 = 整段丢弃，None = 原样保留）
CARD_CAPS_HELD = {"dividend": 3, "outcomes": 3, "notes": 5,
                  "ai_analysis_chars": 400, "trade_strategy_chars": 200}
CARD_CAPS_POOL = {"dividend": 0, "outcomes": 0, "notes": 0,
                  "ai_analysis_chars": 160, "trade_strategy_chars": 0}

#: 其它段的配额（数字来自 11-03 现网体积分布，不是拍脑袋）
_SCREEN_ROWS = 8
_SCREEN_REASON_CHARS = 100
_NEWS_ITEMS = 10
_NEWS_SUMMARY_CHARS = 80
_HELD_CLOSES = 5
_POOL_CLOSES = 1
_OPS = 3
_NAV = 3

#: 逐级丢弃的顺序（越靠前越先丢）
_DROP_ORDER = ("pool_fundamentals", "news", "screening", "pool_cards")


#: 估算→真实 tokenizer 的校准系数（T67 批复 3）。1.0 = 未校准。
#: 为什么放在模块里而不是配置：它不是人定的口径，是**厂商 usage 反出来的观测值**；
#: `agent.context_budget` 的单位因此是"真实 token"，首次问答拿到 usage 后自动收敛。
_TOKEN_CALIBRATION = 1.0
#: 校准的合理区间：超出就说明 prompt 文本与 usage 对不上（缓存命中、system 未计入等），
#: 宁可不校，也不让一个异常比值把预算越拉越歪。
_CALIB_MIN, _CALIB_MAX = 0.6, 2.5


def set_token_calibration(ratio: float) -> bool:
    """用 `真实 prompt_tokens / 估算 token` 校准估算器；返回是否采纳。"""
    global _TOKEN_CALIBRATION
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return False
    if not (_CALIB_MIN <= r <= _CALIB_MAX):
        return False
    _TOKEN_CALIBRATION = r
    return True


def token_calibration() -> float:
    return _TOKEN_CALIBRATION


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 约 1 token/字，其余约 4 字符/token，再乘厂商校准系数。

    系数默认 1.0（单测与首次调用就是这个口径）；11-03 现网对照 GLM 的
    `usage.prompt_tokens`，未校准的估算**低约 1.4 倍**——按它当预算会让上下文
    实际超出 `context_budget` 的字面含义，所以问答拿到 usage 后自动校准。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(round((cjk + (other + 3) // 4) * _TOKEN_CALIBRATION))


def context_tokens(ctx) -> int:
    try:
        return estimate_tokens(json.dumps(ctx, ensure_ascii=False, default=str))
    except Exception:                       # noqa: BLE001
        return estimate_tokens(str(ctx))


def size_of(obj) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:                       # noqa: BLE001
        return len(str(obj))


def truncate(text, max_chars: int, suffix: str = "…[截断]") -> str:
    s = str(text or "")
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[:max_chars] + suffix


def held_codes(ctx: dict) -> set[str]:
    """从上下文里认出持仓标的（`positions[].stock` 是"名称(代码)"格式）。

    分级必须以持仓为准，不然一压缩就把持仓的细节裁掉、把无关候选的股息史留着——
    那正好是反的。解析不出代码时返回空集 ⇒ 调用方按"没人是持仓"处理（保守：
    仍会裁，但不会因为猜错代码而裁掉真持仓）。
    """
    out: set[str] = set()
    for p in (ctx.get("positions") or []):
        for cand in (p.get("symbol"), p.get("stock")):
            m = re.search(r"(\d{6})", str(cand or ""))
            if m:
                out.add(m.group(1))
                break
    return out


def _card_key_code(key: str) -> str:
    m = re.search(r"(\d{6})", str(key or ""))
    return m.group(1) if m else str(key or "")[:6]


def _apply_card_caps(cards: dict, held: set[str], stats: dict) -> None:
    for code, card in (cards or {}).items():
        if not isinstance(card, dict):
            continue
        caps = CARD_CAPS_HELD if _card_key_code(code) in held else CARD_CAPS_POOL
        for f in ("dividend", "outcomes", "notes"):
            v = card.get(f)
            if isinstance(v, list):
                n = int(caps.get(f) or 0)
                if n <= 0:
                    if v:
                        stats[f"{f}_dropped"] = stats.get(f"{f}_dropped", 0) + 1
                    card.pop(f, None)
                elif len(v) > n:
                    card[f] = v[-n:]
        ai = card.get("ai_analysis")
        if isinstance(ai, dict):
            ai["analysis"] = truncate(ai.get("analysis"),
                                      int(caps["ai_analysis_chars"]))
            if int(caps.get("trade_strategy_chars") or 0) <= 0:
                ai.pop("trade_strategy", None)
            else:
                ai["trade_strategy"] = truncate(
                    ai.get("trade_strategy"), int(caps["trade_strategy_chars"]))
        if int(caps.get("dividend") or 0) <= 0:
            card.pop("thesis", None)
        fin = card.get("financial_summary")
        if isinstance(fin, dict):
            keep = _FIN_KEYS if _card_key_code(code) in held else FIN_KEYS_POOL
            card["financial_summary"] = {k: fin.get(k) for k in keep if k in fin}


def _caps_applied(out: dict, held: set[str], stats: dict) -> None:
    vv = out.get("value_view")
    if isinstance(vv, dict):
        _apply_card_caps(vv.get("stock_cards") or {}, held, stats)
        scr = vv.get("screening")
        if isinstance(scr, dict) and isinstance(scr.get("rows"), list):
            rows = scr["rows"]
            if len(rows) > _SCREEN_ROWS:
                stats["screening_rows"] = len(rows) - _SCREEN_ROWS
                scr["rows"] = rows[:_SCREEN_ROWS]
            for r in scr["rows"]:
                if isinstance(r, dict):
                    r["reason"] = truncate(r.get("reason"), _SCREEN_REASON_CHARS)
                    r.pop("score_detail", None)
    news = out.get("news")
    if isinstance(news, dict) and isinstance(news.get("items"), list):
        items = news["items"]
        if len(items) > _NEWS_ITEMS:
            stats["news_items"] = len(items) - _NEWS_ITEMS
            news["items"] = items[:_NEWS_ITEMS]
        for it in news["items"]:
            if isinstance(it, dict):
                it["summary"] = truncate(it.get("summary"), _NEWS_SUMMARY_CHARS)
                it["ai_brief"] = truncate(it.get("ai_brief"), _NEWS_SUMMARY_CHARS)
    closes = out.get("recent_closes")
    if isinstance(closes, dict):
        trimmed = {}
        for k, v in closes.items():
            if not isinstance(v, list):
                trimmed[k] = v
                continue
            n = _HELD_CLOSES if _card_key_code(k) in held else _POOL_CLOSES
            trimmed[k] = v[-n:] if len(v) > n else v
        out["recent_closes"] = trimmed
    for key, cap in (("recent_ops", _OPS), ("recent_nav", _NAV)):
        v = out.get(key)
        if isinstance(v, list) and len(v) > cap:
            stats[f"{key}_cut"] = len(v) - cap
            out[key] = v[:cap]
        if key == "recent_ops" and isinstance(out.get(key), list):
            for o in out[key]:
                if isinstance(o, dict):
                    o["params"] = truncate(o.get("params"), 100)
                    o["result"] = truncate(o.get("result"), 120)


def _drop_one(out: dict, which: str, held: set[str]) -> Optional[str]:
    """按顺序丢一整块；丢了就返回块名（进 meta.dropped，让人看得见丢了什么）。"""
    vv = out.get("value_view")
    if which == "pool_fundamentals" and isinstance(vv, dict):
        cards = vv.get("stock_cards") or {}
        gone = [k for k, c in cards.items()
                if isinstance(c, dict) and _card_key_code(k) not in held]
        for k in gone:
            cards[k] = {"note": "非持仓，基本面已按预算省略"}
        return which if gone else None
    if which == "news" and isinstance(out.get("news"), dict):
        out["news"] = {"note": "要闻已按预算省略", "source":
                       out["news"].get("source")}
        return which
    if which == "screening" and isinstance(vv, dict) and vv.get("screening"):
        vv["screening"] = {"note": "候选筛选已按预算省略"}
        return which
    if which == "pool_cards" and isinstance(vv, dict):
        cards = vv.get("stock_cards") or {}
        gone = [k for k in list(cards) if _card_key_code(k) not in held]
        for k in gone:
            cards.pop(k, None)
        return which if gone else None
    return None


def shrink_to_budget(ctx: dict, budget: int = 8000, *,
                     held: Optional[set[str]] = None) -> tuple[dict, dict]:
    """压到预算内并返回 `(新 ctx, 元信息)`。元信息给留痕/日志，不进请求体。

    保证的是"**要么压进预算，要么如实说压不进**"——旧实现两者都没做到：
    它裁不到点子上，又不报告，于是 `context_budget` 看起来接了线其实没用。
    """
    before_chars = size_of(ctx)
    before_tokens = context_tokens(ctx)
    meta = {"budget": int(budget or 0), "before_chars": before_chars,
            "before_tokens": before_tokens, "caps": {}, "dropped": [],
            "within_budget": True, "reason": ""}
    if not isinstance(ctx, dict) or budget <= 0 or before_tokens <= budget:
        meta["after_chars"] = before_chars
        meta["after_tokens"] = before_tokens
        meta["reason"] = ("未超预算，原样返回" if isinstance(ctx, dict)
                          and budget > 0 else "未启压缩（预算非法或空上下文）")
        return ctx, meta

    out = copy.deepcopy(ctx)
    held = held if held is not None else held_codes(out)
    _caps_applied(out, held, meta["caps"])
    meta["after_chars"] = size_of(out)
    meta["after_tokens"] = context_tokens(out)
    for which in _DROP_ORDER:
        if meta["after_tokens"] <= budget:
            break
        gone = _drop_one(out, which, held)
        if gone:
            meta["dropped"].append(gone)
            meta["after_chars"] = size_of(out)
            meta["after_tokens"] = context_tokens(out)
    meta["within_budget"] = meta["after_tokens"] <= budget
    if not meta["within_budget"]:
        meta["reason"] = (f"压到最低仍超预算：{meta['after_tokens']} > {budget}"
                          f"（持仓 {len(held)} 只的细节是刻意保留的，"
                          f"再压要动持仓字段或 provenance）")
    else:
        meta["reason"] = "已压进预算"
    return out, meta


def compress_context(ctx: dict, budget: int = 8000, *,
                     held: Optional[set[str]] = None) -> dict:
    """老签名入口（决策路径与既有测试用）：只回压缩后的 dict。"""
    out, _meta = shrink_to_budget(ctx, budget, held=held)
    return out
