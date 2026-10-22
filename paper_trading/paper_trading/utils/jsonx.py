"""JSON 序列化安全网：非有限浮点（NaN / Infinity）必须换成 null。

原因：`json.dumps` 默认会把 float('inf') 写成 `Infinity`，这**不是合法 JSON**——
浏览器 `response.json()` 直接抛 SyntaxError，面板整块功能静默卡住。
真机上就这么踩过：profit_factor 在"没有亏损单"时是 Infinity，
参数网格的结果在面板里显示不出来，页面只剩"搜索中…"。

规则：任何对外输出的 JSON，都过一遍 `safe()`。
"""
from __future__ import annotations

import math
from typing import Any


def safe(obj: Any) -> Any:
    """递归把 NaN / ±Infinity 换成 None，其余结构原样返回。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [safe(v) for v in obj]
    return obj
