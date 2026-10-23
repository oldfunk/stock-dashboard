"""东财 F10 现金流量表采集（2026-10-23 P0-4）。

复用 ``balance_sheet_fetcher`` 的 F10 直连机制（akshare 的现金流 API 依赖 HTML
抓 company_type，部分环境失败）。只取年报。
"""

import logging
import urllib.parse

from src.collector.balance_sheet_fetcher import _F10, _em_symbol, _get_json, _num

logger = logging.getLogger(__name__)


def fetch_cash_flow_annual(code: str, company_types=(4, 1, 2, 3)) -> dict:
    """拉取年报现金流量表，返回 {report_date: {capex, net_operate_cashflow}}（单位：元）。

    - capex = 购建固定资产、无形资产和其他长期资产支付的现金（CONSTRUCT_LONG_ASSET）
    - net_operate_cashflow = 经营活动产生的现金流量净额（NETCASH_OPERATE）
    - 失败返回 {}（保持 NULL，不阻断）
    """
    sym = _em_symbol(code)
    dates: list[str] = []
    ctype = None
    for ct in company_types:
        try:
            url = f"{_F10}/xjllbDateAjaxNew?" + urllib.parse.urlencode(
                {"companyType": ct, "reportDateType": "0", "code": sym})
            data = _get_json(url)
            items = [x for x in (data.get("data") or [])
                     if str(x.get("REPORT_DATE", ""))[:10].endswith("12-31")]
            if items:
                dates = [str(x["REPORT_DATE"])[:10] for x in items]
                ctype = ct
                break
        except Exception as e:
            logger.debug(f"[P0-4] {code} companyType={ct} 现金流日期列表失败: {e}")
    if not dates or ctype is None:
        return {}

    out: dict[str, dict] = {}
    for i in range(0, len(dates), 5):
        batch = dates[i:i + 5]
        try:
            url = f"{_F10}/xjllbAjaxNew?" + urllib.parse.urlencode(
                {"companyType": ctype, "reportDateType": "0", "reportType": "1",
                 "dates": ",".join(batch), "code": sym})
            data = _get_json(url)
            for row in (data.get("data") or []):
                rpt = str(row.get("REPORT_DATE", ""))[:10]
                if not rpt:
                    continue
                out[rpt] = {
                    "capex": _num(row.get("CONSTRUCT_LONG_ASSET")),
                    "net_operate_cashflow": _num(row.get("NETCASH_OPERATE")),
                }
        except Exception as e:
            logger.debug(f"[P0-4] {code} 批次 {batch} 失败: {e}")
    return out
