"""股票指标单一事实源（2026-10-25）。

**为什么存在**：同一组指标曾在 5 个表面各写一遍——候选卡模板、AI 观察池模板、
手动钉选卡 JS、个股详情、观察池详情。结果标签（市盈率 / PE / 营收增）、
格式（+15.0% vs 15.0%）、好中差阈值、缺值行为全部漂移；每次只修一个表面，
其余继续坏（股息率/连分年数就是实例）。

**规则**：
- 指标定义、格式、阈值、tooltip 只写在这里。
- 所有表面通过 ``metric_items()`` 取数据，通过 ``_metric_cells.html`` 渲染。
- 新增指标 → 只改 ``CARD_METRICS``；``tests/web/test_ui_metric_parity.py``
  会强制所有卡片表面都出现它，漏一个就红。
"""

from __future__ import annotations

from dataclasses import dataclass

MISSING = "--"


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    fmt: str          # price / 1f / 2f / pct1 / pct1signed / pct2 / yi / int
    tone: str         # pe / pb / roe / debt / growth / margin / ocf / yield / years / none
    title: str = ""


# 卡片与详情快照条共用的一组指标（顺序即展示顺序）
CARD_METRICS = (
    MetricSpec("pe", "PE", "1f", "pe"),
    MetricSpec("pb", "PB", "2f", "pb"),
    MetricSpec("roe", "ROE", "pct1", "roe"),
    MetricSpec("debt_ratio", "负债率", "pct1", "debt"),
    MetricSpec("revenue_growth", "营收增长", "pct1signed", "growth"),
    MetricSpec("profit_growth", "利润增长", "pct1signed", "growth"),
    MetricSpec("market_cap", "市值", "yi", "none"),
    MetricSpec("gross_margin", "毛利率", "pct1", "margin"),
    MetricSpec("net_margin", "净利率", "pct1", "net_margin"),
    MetricSpec("ocf_per_share", "OCF/股", "2f", "ocf"),
    MetricSpec("dividend_yield", "股息率", "pct2", "yield"),
    MetricSpec("dividend_years", "连分年数", "int", "years"),
)

_BY_KEY = {m.key: m for m in CARD_METRICS}


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_value(spec: MetricSpec, value) -> str:
    v = _num(value)
    if v is None:
        return MISSING
    if spec.fmt == "price":
        return f"{v:.2f}"
    if spec.fmt == "1f":
        return f"{v:.1f}"
    if spec.fmt == "2f":
        return f"{v:.2f}"
    if spec.fmt == "pct1":
        return f"{v:.1f}%"
    if spec.fmt == "pct2":
        return f"{v:.2f}%"
    if spec.fmt == "pct1signed":
        return f"{v:+.1f}%"
    if spec.fmt == "yi":
        return f"{v:.0f}亿"
    if spec.fmt == "int":
        return f"{int(round(v))}"
    return f"{v}"


def tone_for(spec: MetricSpec, value) -> str:
    """好/中/差 → good/warn/bad；无意义的比较（0/负 PE、缺失）返回空串。"""
    v = _num(value)
    if v is None:
        return ""
    t = spec.tone
    if t == "pe":
        if v <= 0:
            return "bad"          # 亏损股 PE 为负：不能因为"小于 15"就标好
        return "good" if v < 15 else ("warn" if v < 20 else "")
    if t == "pb":
        if v <= 0:
            return "bad"
        return "good" if v < 2 else ("warn" if v < 3.5 else "")
    if t == "roe":
        return "good" if v >= 15 else ("warn" if v >= 8 else "bad")
    if t == "debt":
        return "good" if v < 40 else ("warn" if v < 60 else "bad")
    if t == "growth":
        return "good" if v >= 10 else ("warn" if v >= 0 else "bad")
    if t == "margin":
        return "good" if v >= 30 else ("warn" if v >= 15 else "bad")
    if t == "net_margin":
        return "good" if v >= 15 else ("warn" if v >= 5 else "bad")
    if t == "ocf":
        return "good" if v > 0 else "bad"
    if t == "yield":
        return "good" if v >= 4 else ("warn" if v >= 2 else "")
    if t == "years":
        return "good" if v >= 5 else ("warn" if v >= 3 else "")
    return ""


def metric_items(stock: dict, exclude: tuple[str, ...] = ()) -> list[dict]:
    """返回模板可直接消费的指标列表：[{key,label,text,cls,title}]。"""
    items = []
    year = stock.get("dividend_year")
    for spec in CARD_METRICS:
        if spec.key in exclude:
            continue
        title = spec.title
        if spec.key == "dividend_yield":
            title = f"股息率 = {year or '最近'} 年报每股分红 / 现价"
        elif spec.key == "dividend_years":
            title = f"连续分红年数（截至 {year or '最新'} 年报）"
        items.append({
            "key": spec.key,
            "label": spec.label,
            "text": format_value(spec, stock.get(spec.key)),
            "cls": tone_for(spec, stock.get(spec.key)),
            "title": title,
        })
    return items


def spec_for(key: str) -> MetricSpec:
    return _BY_KEY[key]
