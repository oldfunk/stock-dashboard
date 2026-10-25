"""东财主要财务指标直连采集（2026-10-24，P0 覆盖扩展）。

``_fetch_eastmoney_roic_fcf`` 走 akshare 模块（会 import akshare/pandas，较重）。
全市场回填只需要"披露日 + fcf + roic + 总股本"这几项，且**一次请求即取回全部
历史年报**（``ps=30``），故独立成轻量模块：只依赖 urllib，可单独跑、可单测。

- 数据源：``datacenter.eastmoney.com`` ``RPT_F10_FINANCE_MAINFINADATA``。
- 只取年报（``REPORT_TYPE == "年报"``）；失败返回 {}（保持 NULL，不阻断）。
"""

import logging

from src.collector.balance_sheet_fetcher import _get_json, _num

logger = logging.getLogger(__name__)

_MAINFIN = "https://datacenter.eastmoney.com/securities/api/data/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://emweb.securities.eastmoney.com/",
    "Accept-Encoding": "identity",
}


def _secucode(code: str) -> str:
    """东财 SECUCODE 格式：600519.SH / 000001.SZ。"""
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"


def fetch_mainfin_annual(code: str, timeout: int = 15) -> dict:
    """拉取年报主要财务指标，返回 ``{report_date: {notice_date, fcf, roic, total_share}}``。

    - ``notice_date`` 披露日期（防前视的关键字段）。
    - 失败返回 {}（缺数据保持 NULL，不阻断）。
    """
    secucode = _secucode(code)
    # 2026-10-25：显式只要年报。旧 URL 无过滤、ps=30 被季报占满，只回 ~7 年；
    # 加过滤后 600519 可回 28 年（1998–2025），2016–2018 的披露日才能补上。
    url = (
        f"{_MAINFIN}?type=RPT_F10_FINANCE_MAINFINADATA&sty=ALL"
        f"&filter=(SECUCODE%3D%22{secucode}%22)"
        f"(REPORT_TYPE%3D%22%E5%B9%B4%E6%8A%A5%22)"
        f"&p=1&ps=30&sr=-1&st=REPORT_DATE&source=HSF10&client=PC"
    )
    try:
        data = _get_json(url, timeout=timeout, headers=_HEADERS)
    except Exception as e:  # noqa: BLE001 - 网络失败按无数据处理
        logger.debug(f"[mainfin] {code} 请求失败: {e}")
        return {}
    reports = (data.get("result") or {}).get("data") or []
    out: dict[str, dict] = {}
    for r in reports:
        if r.get("REPORT_TYPE") != "年报":
            continue
        rpt = str(r.get("REPORT_DATE", ""))[:10]
        if not rpt:
            continue
        notice = str(r.get("NOTICE_DATE", ""))[:10] or None
        rec = {
            "notice_date": notice,
            "fcf": _num(r.get("FCFF_BACK")),
            "roic": _num(r.get("ROIC")),
            "total_share": _num(r.get("TOTAL_SHARE")),
        }
        if any(v is not None for v in rec.values()):
            out[rpt] = rec
    return out
