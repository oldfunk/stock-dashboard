"""涨跌停幅度与最小变动位（**数据供给**，不是撮合判定）。

rqalpha 从不计算 band：`bar_dict_price_board.py:33-40` 直接读 bar 的
`limit_up/limit_down` 字段，缺字段时 `price_limits.py:14-16` 判定为
"没到停" ⇒ **fail-open**（report-t13 §4.5 实测涨停照样成交）。
所以 band 由我们供，且必须完整——`store.py` 在缺 `prev_close` 时直接拒供该日。

判定（"是否触及涨跌停"）仍然在 rqalpha 撮合器里（`matcher/base.py:207-216`），
本文件只给数值。
"""
from __future__ import annotations

import math

TICK = 0.01
#: 各板块涨跌幅（AGENTS 合规表 #4/#5/#6）。**ST 不降档**：主板 ST 自 2026-07-06
#: 起 5%→10%；创业/科创/北交所 ST 同幅。AGENTS 硬规则 2 明确"不得沿用 ST=5% 旧口径"。
BAND_MAIN = 0.10
BAND_GROWTH = 0.20      # 30xxxx 创业板、688xxx 科创板
BAND_BSE = 0.30         # 北交所：43/83/87/88 与 2024 年起的 920xxx
#: 北交所代码段（前两位）。**不含 900xxx**——那是沪市 B 股，主板 10% 档。
#: 表本身在 `utils/markets.py`（口径唯一），这里只重新导出以保持既有引用。
from paper_trading.utils.markets import BJ_SEGMENTS as BSE_PREFIXES


def band_for(symbol: str, *, is_st: bool = False) -> float:
    """6 位代码 → 涨跌幅档。`is_st` 必须显式表态，但**不改变结果**（新规）。

    2026-11-03 现网实据：池里进来了 `920156`（北交所新代码段），旧写法只看首字符
    `"4"/"8"` ⇒ 920 落到主板 10% 档。band 是我们供的（rqalpha 只读 `limit_up/
    limit_down`，缺字段就 fail-open），档错了就是**撮合价被错判涨跌停**，
    所以这里改成按前两位判市，并且和 `store.to_order_book_id` 用同一张表。
    """
    if is_st:
        # 保留参数是为了让调用点无法忽略 ST 这件事——历史上 ST=5% 是旧口径，
        # 现在与同板块一致；若哪天规则再变，只改这里。
        pass
    code = str(symbol or "").strip().zfill(6)
    if code[:2] in BSE_PREFIXES:
        return BAND_BSE
    if code.startswith(("30", "68")):
        return BAND_GROWTH
    return BAND_MAIN


def round_tick(x: float) -> float:
    """四舍五入到最小变动单位 0.01（AGENTS 合规表 #7）。"""
    return math.floor(float(x) * 100 + 0.5) / 100


def limit_prices(prev_close, symbol: str, *, is_st: bool = False) -> tuple:
    """(limit_up, limit_down)；`prev_close` 非法 → (nan, nan)，由调用方拒供该日。"""
    try:
        pc = float(prev_close)
    except (TypeError, ValueError):
        return math.nan, math.nan
    if not math.isfinite(pc) or pc <= 0:
        return math.nan, math.nan
    band = band_for(symbol, is_st=is_st)
    return round_tick(pc * (1 + band)), round_tick(pc * (1 - band))
