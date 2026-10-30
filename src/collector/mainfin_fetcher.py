"""东财主要财务指标直连采集（2026-10-24，P0 覆盖扩展）。

``_fetch_eastmoney_roic_fcf`` 走 akshare 模块（会 import akshare/pandas，较重）。
全市场回填只需要"披露日 + fcf + roic + 总股本"这几项，且**一次请求即取回全部
历史年报**（``ps=30``），故独立成轻量模块：只依赖 urllib，可单独跑、可单测。

- 数据源：``datacenter.eastmoney.com`` ``RPT_F10_FINANCE_MAINFINADATA``。
- 只取年报（``REPORT_TYPE == "年报"``）；失败返回 {}（保持 NULL，不阻断）。
"""

import logging

from src.collector.balance_sheet_fetcher import _get_json, _num
from src.utils import em_secucode

logger = logging.getLogger(__name__)

_MAINFIN = "https://datacenter.eastmoney.com/securities/api/data/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://emweb.securities.eastmoney.com/",
    "Accept-Encoding": "identity",
}


def _secucode(code: str) -> str:
    """东财 SECUCODE 格式：600519.SH / 000001.SZ / **920000.BJ**。

    实测（920000 安徽凤凰）：`SECUCODE="920000.BJ"` 给 5 行，
    `.SH` / `.SZ` 各给 0 行——旧二分规则把北交所判成沪市后，
    主要财务指标（含披露日）整只票取不到。
    """
    return em_secucode(code)


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
            # 2026-10-26：同一响应已含基础财务指标，全市场回填不再需要 akshare 摘要
            "roe": _num(r.get("ROEJQ")),
            "eps": _num(r.get("EPSJB")),
            "gross_margin": _num(r.get("XSMLL")),
            "net_margin": _num(r.get("XSJLL")),
            "revenue_growth": _num(r.get("TOTALOPERATEREVETZ")),
            "profit_growth": _num(r.get("PARENTNETPROFITTZ")),
            "net_profit": _num(r.get("PARENTNETPROFIT")),
            "debt_ratio": _num(r.get("ZCFZL")),
            "interest_coverage": _num(r.get("INTSTCOVRATE")),
            "total_assets": _num(r.get("TOTAL_ASSETS_PK")),
            "total_equity": _num(r.get("TOTAL_EQUITY_PK")),
            "ocf_per_share": _num(r.get("MGJYXJJE")),
        }
        if any(v is not None for v in rec.values()):
            out[rpt] = rec
    return out


# ── 季报披露日（批10-a，2026-10-29） ────────────────────────────────────────

# 东财 ``REPORT_TYPE`` 这一列的实际取值（nec 实测 20 只票 1,600 行）：
# **'一季报' / '中报' / '三季报' / '年报'——没有 '季报' 这个值**。
# 照 ``fetch_mainfin_annual`` 的思路加 `(REPORT_TYPE="季报")` 过滤会**稳定返回 0 行**
# （600519 / 000001 各实测一次，均为 0 行）。所以这里不加类型过滤，在代码里筛三类季报。
QUARTERLY_REPORT_TYPES = ("一季报", "中报", "三季报")
MAINFIN_MAX_PAGES = 3         # ps=100 × 3 页 ≈ 75 年季度深度，够全历史回填


def clean_notice_date(report_date: str, notice: object) -> str | None:
    """把上游披露日清洗成 ``YYYY-MM-DD``；不可能/占位的值一律返回 None（保持 NULL）。

    ``notice_date`` 是 PIT 的前视闸门（缺 notice 就不参与、不回退 report_date），
    填错等于把未来数据提前放行，所以这里宁可少填：
    - 截断后必须是 10 位 ``YYYY-MM-DD``（``2026/04/25`` 这种形状一律不收）；
    - 不得早于报告期（季报不可能在期末之前披露）。
    第二条顺带挡住 ``1900-01-01`` 这类占位假值，所以**没有**再单独写一条
    "年份 < 2000"：删掉它做变异，没有任何用例改变——在唯一调用路径上
    （报告期非空才走到这里）它永远不会触发，不留无法被证伪的防御分支。
    """
    text = str(notice or "")[:10]
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    if report_date and text < report_date:
        return None
    return text


def fetch_mainfin_quarterly_notices(code: str, ps: int = 100, timeout: int = 15,
                                    max_pages: int = MAINFIN_MAX_PAGES) -> dict:
    """某股全部**季报**披露日：``{"notices": {报告期: 披露日}, "rejected": int,
    "rows": int, "error": str | None}``。

    - **分页取到"本页不满"为止**（上限 ``max_pages``）。实测依据：``ps=100`` 时
      600519 的 103 行第一页就给满，而 000001 库内有 86 个待填季度、第一页只覆盖
      2002 年起的 75 个（只能填 87%）——不翻页就会把剩下的 11 行永久留在 NULL，
      而这只票还被标成 done。3 页约等于 75 年的季度深度。
    - ``error`` 非 None 表示这次**没取全**（网络/上游异常）。调用方必须按 partial
      处理，不能把它当成"这只票没有季报披露日"——那会让下次续传直接跳过它。
      第一页成功、后续页失败时同样带 error：已有结果仍然可用，但不算取全。
    - ``rejected`` 是被清洗规则挡掉的不可能披露日（20 只票 1,177 条季报实测为 0，
      闸门仍留着：上游哪天开始发占位值，这里要先看得见）。
    """
    notices: dict[str, str] = {}
    rejected = 0
    rows_total = 0
    error: str | None = None
    for page in range(1, max_pages + 1):
        url = (
            f"{_MAINFIN}?type=RPT_F10_FINANCE_MAINFINADATA&sty=ALL"
            f"&filter=(SECUCODE%3D%22{_secucode(code)}%22)"
            f"&p={page}&ps={ps}&sr=-1&st=REPORT_DATE&source=HSF10&client=PC"
        )
        try:
            data = _get_json(url, timeout=timeout, headers=_HEADERS)
        except Exception as e:  # noqa: BLE001 - 网络失败要回传给调用方定状态
            logger.debug(f"[mainfin-季报] {code} 第 {page} 页请求失败: {e}")
            error = f"{type(e).__name__}: {e}"
            break
        reports = (data.get("result") or {}).get("data") or []
        rows_total += len(reports)
        for r in reports:
            if r.get("REPORT_TYPE") not in QUARTERLY_REPORT_TYPES:
                continue
            rpt = str(r.get("REPORT_DATE") or "")[:10]
            if not rpt:
                continue
            notice = clean_notice_date(rpt, r.get("NOTICE_DATE"))
            if notice is None:
                if str(r.get("NOTICE_DATE") or "").strip():
                    rejected += 1
                continue
            notices[rpt] = notice
        if len(reports) < ps:
            break
    return {"notices": notices, "rejected": rejected,
            "rows": rows_total, "error": error}
