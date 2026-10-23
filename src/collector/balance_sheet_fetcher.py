"""东财 F10 资产负债表采集（2026-10-23 P0-3）。

akshare 的 ``stock_balance_sheet_by_report_em`` 依赖 HTML 抓 ``company_type``，
在部分环境会失败；此处直连东财 F10 的 ``zcfzbDateAjaxNew`` / ``zcfzbAjaxNew``。

- ``company_type`` 缺省 4（一般公司）；日期列表为空时依次尝试 1/2/3（银行/保险/证券）。
- 只取年报（12-31）；缺数据返回 {}（保持 NULL，不阻断）。
- 有息负债 = 短期借款 + 一年内到期的非流动负债 + 长期借款 + 应付债券 + 短期应付债券。
"""

import json
import logging
import math
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_F10 = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def _num(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers=_HEADERS)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return json.loads(raw.decode("utf-8", "ignore"))


def _em_symbol(code: str) -> str:
    """东财 F10 用前缀格式：SH600519 / SZ000001。"""
    return f"SH{code}" if code.startswith(("6", "9")) else f"SZ{code}"


def _interest_bearing_debt(row: dict) -> float | None:
    """有息负债 = 短借 + 一年内到期非流动负债 + 长借 + 应付债券 + 短期应付债券。"""
    parts = [
        _num(row.get("SHORT_LOAN")),
        _num(row.get("NONCURRENT_LIAB_1YEAR")),
        _num(row.get("LONG_LOAN")),
        _num(row.get("BOND_PAYABLE")),
        _num(row.get("SHORT_BOND_PAYABLE")),
    ]
    vals = [p for p in parts if p is not None]
    if not vals:
        return None
    return round(sum(vals), 2)


def fetch_balance_sheet_annual(code: str,
                               company_types=(4, 1, 2, 3)) -> dict:
    """拉取年报资产负债表，返回 {report_date: {cash, interest_bearing_debt, ...}}（单位：元）。

    失败返回 {}（缺数据保持 NULL，不阻断）。
    """
    sym = _em_symbol(code)
    dates: list[str] = []
    ctype = None
    for ct in company_types:
        try:
            url = f"{_F10}/zcfzbDateAjaxNew?" + urllib.parse.urlencode(
                {"companyType": ct, "reportDateType": "0", "code": sym})
            data = _get_json(url)
            items = [x for x in (data.get("data") or [])
                     if str(x.get("REPORT_DATE", ""))[:10].endswith("12-31")]
            if items:
                dates = [str(x["REPORT_DATE"])[:10] for x in items]
                ctype = ct
                break
        except Exception as e:
            logger.debug(f"[P0-3] {code} companyType={ct} 日期列表失败: {e}")
    if not dates or ctype is None:
        return {}

    out: dict[str, dict] = {}
    for i in range(0, len(dates), 5):
        batch = dates[i:i + 5]
        try:
            url = f"{_F10}/zcfzbAjaxNew?" + urllib.parse.urlencode(
                {"companyType": ctype, "reportDateType": "0", "reportType": "1",
                 "dates": ",".join(batch), "code": sym})
            data = _get_json(url)
            for row in (data.get("data") or []):
                rpt = str(row.get("REPORT_DATE", ""))[:10]
                if not rpt:
                    continue
                out[rpt] = {
                    "cash": _num(row.get("MONETARYFUNDS")),
                    "interest_bearing_debt": _interest_bearing_debt(row),
                    "goodwill": _num(row.get("GOODWILL")),
                    "total_assets": _num(row.get("TOTAL_ASSETS")),
                    "total_equity": _num(row.get("TOTAL_EQUITY")),
                }
        except Exception as e:
            logger.debug(f"[P0-3] {code} 批次 {batch} 失败: {e}")
    return out
