"""交易所归属（6 位代码 → sh/sz/bj）的**唯一**口径（T60）。

为什么要单独一个模块：这件事仓里独立判过四遍——

| 位置 | 旧写法 | `920156`（北交所新代码段）的实际结果 |
|---|---|---|
| `data/minute.py::_market_prefix` | 只看首字符 `"4"/"8"` → bj，其余兜底 sh | `sh920156` → 新浪 **0 行** ⇒ 盘中采集每 15 分钟报"源无这些 bar" |
| `data/realtime.py::tc_symbol` | 只看首字符 `8`/`4` → bj，其余 sz | `sz920156` → 腾讯**空响应** ⇒ 报价拿不到 |
| `integration/rq_engine/store.py::to_order_book_id` | 首字符 `6`/`9` → XSHG | `920156.XSHG` → **挂错交易所** |
| `integration/rq_engine/bands.py::band_for` | 首字符 `4`/`8` → 30% | 落主板 **10%** ⇒ band 由我们供（rqalpha 只读 `limit_up/limit_down`），档错=撮合价被错判涨跌停 |

2026-11-03 现网实测（同一时刻同一标的，三源对照）：
`bj920156` 新浪实时=39 字段（海昌智能 36.10）/腾讯实时=87 字段/新浪 5 分钟线=48 根；
`sh920156`、`sz920156` 三个口全部取不到。**920 段必须判北交所，且必须只判一次。**

规则本身：`90xxxx` 是沪市 B 股（10% 档），所以**不能**按"首字符 9"判市——
先比前两位命中北交所段（43/83/87/88/92），剩下的 9 字头才归沪。
"""
from __future__ import annotations

SH = "sh"
SZ = "sz"
BJ = "bj"

#: 北交所（含 2024 年起启用的 920xxx 新段）。按**前两位**判，避免与沪市 B 股 900xxx 混淆。
BJ_SEGMENTS = ("43", "83", "87", "88", "92")
#: 沪市股票/基金段首字符（60/68 主板与科创、90 B 股、50 基金）
SH_FIRST = ("6", "9", "5")

_SEGMENT_BY_EXCHANGE = {SH: "XSHG", SZ: "XSHE", BJ: "BJ"}


def normalize(symbol) -> str:
    """6 位数字代码（`002192.SZ`/`2192` 之类都归一到 6 位）。"""
    return str(symbol or "").strip().split(".")[0].zfill(6)


def exchange_of(symbol) -> str:
    """6 位代码 → 'sh' | 'sz' | 'bj'。

    认不出来（空/非数字）一律按 `sh`——沿用 `minute._market_prefix` 的历史契约，
    让这次收敛**不附带**任何行为变化；真取不到数据就是取不到（fail-closed），
    不许在这里编造市场。
    """
    raw = str(symbol or "").strip().split(".")[0]
    if not raw or not raw.isdigit():
        return SH
    code = normalize(symbol)
    if code[:2] in BJ_SEGMENTS:
        return BJ
    if code[0] in SH_FIRST:
        return SH
    return SZ


def is_bse(symbol) -> bool:
    return exchange_of(symbol) == BJ


def sina_symbol(symbol) -> str:
    """新浪/腾讯行情口：`sh600519` / `sz000858` / `bj920156`。"""
    return exchange_of(symbol) + normalize(symbol)


def order_book_id(symbol) -> str:
    """rqalpha 的 `order_book_id`：`600519.XSHG` / `000651.XSHE` / `920156.BJ`。"""
    code = normalize(symbol)
    return f"{code}.{_SEGMENT_BY_EXCHANGE[exchange_of(code)]}"


def from_order_book_id(order_book_id_: str) -> str:
    """`920156.BJ` → `920156`（rqalpha 成交回报回到我们的 6 位码）。"""
    return normalize(str(order_book_id_ or "").split(".")[0])
