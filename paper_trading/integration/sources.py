"""数据来源标注（provenance）。

每条富化数据都能追到出处，供排查与 AI 分析：

- ``local:*``     子项目自有管线（akshare / 腾讯 / 新浪），独立可用
- ``mother:*``    母库 Stock Dashboard 只读表
- ``unavailable`` 两边都没有（字段为缺失，**绝不填 0**）

统一用 :func:`block` 产出结构化块，避免各处自造字段名导致 AI 上下文解析分叉。
"""
from __future__ import annotations

from typing import Any, Optional

SRC_LOCAL_AKSHARE = "local:akshare"
SRC_LOCAL_TENCENT = "local:tencent"
SRC_LOCAL_SINA = "local:sina"
SRC_LOCAL_CONFIG = "local:config"
SRC_MOTHER = "mother"
SRC_UNAVAILABLE = "unavailable"


def mother_source(table: str) -> str:
    """母库表来源标签，例如 ``mother:kline_daily``。"""
    return f"{SRC_MOTHER}:{table}"


def block(data: Any, source: str, asof: Optional[str] = None,
          stale: bool = False, note: str = "", max_age_days: Optional[int] = None,
          today: Optional[str] = None) -> dict:
    """把一份数据包装成带来源的块。

    ``available`` 只表示"有没有数据"，不代表新鲜；新鲜度看 ``asof``/``stale``。
    ``max_age_days`` 给定时按 ``asof`` 自动判旧（超过即 ``stale=True``），
    避免把过期数据当新鲜喂给 AI。
    ``today``（T53）：判旧的基准日，缺省取墙钟。**测试必须显式传**——
    新鲜度断言一旦依赖墙钟，就会随真实时间推移自己变红（两次 flaky 教训）。
    """
    from datetime import date

    has = bool(data) if not isinstance(data, (dict, list)) else bool(data)
    if max_age_days is not None and asof and not stale:
        stale = _is_stale(asof, max_age_days, today or date.today().isoformat())
    return {
        "available": has,
        "source": source,
        "asof": asof,
        "stale": bool(stale),
        "note": note,
        "data": data,
    }


def _is_stale(asof: str, max_age_days: int, today: str) -> bool:
    from datetime import date

    try:
        return (date.fromisoformat(today) - date.fromisoformat(asof)).days > max_age_days
    except ValueError:
        return False


def unavailable(note: str = "") -> dict:
    """两边都没有时的标准空块（``data=None``，不造假）。"""
    return block(None, SRC_UNAVAILABLE, asof=None, stale=False, note=note)
