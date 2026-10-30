"""no_data 归因体检（批10-c，2026-10-30，只读）。

派工单要求"13 只行情 + 321 只财务 no_data 逐只给证据、100% 归因"。手写清单会随
下一次回填漂移而腐烂，所以这里交的是**能重跑的归因器**：

    python scripts/audit_no_data.py                 # 只用库内证据分类
    python scripts/audit_no_data.py --verify-live   # 对含糊桶补一次新浪末根日线
    python scripts/audit_no_data.py --format md     # 直接产出文档表格

分类规则（每条都给可核查的证据，不靠人记）：

| 类别 | 判据 | 性质 |
|---|---|---|
| `bj_unmapped` | 代码属北交所号段（920/83/87/43 开头）且快照里有行、库内一条行情/财务都没有 | **采集缺陷**：市场后缀按 "6/9→沪、其余→深" 二分，920 被当沪市，上游 `.SH` 返回 0 行而 `.BJ` 有数据（实测见 docs/no-data-inventory.md） |
| `delisted` | 快照名称含 "退" | 事实性无数据：交易所退市整理期命名规则 |
| `ambiguous` | 以上都不满足（行情 0 行但名称不带"退"） | 单靠库内证据分不开"停止交易"与"采集失败" -> 需要 `--verify-live` |
| `stopped_trading` | ambiguous 桶里，新浪末根日线早于 `--fresh-days` | 事实性无数据，末根日期即停止交易日 |
| `live_but_no_data` | ambiguous 桶里，末根日线仍新鲜 | **采集缺陷**（限流/空响应被误记 no_data 后会被进度表永久排除） |
| `unattributed` | 落不进任何一类 | 非空即 exit 1 —— "100% 归因"是机器断言，不是我的口头承诺 |

不写任何表；`--verify-live` 只 GET 新浪（默认不打网络、不碰东财）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src.models.database import db_conn, init_database  # noqa: E402

logger = logging.getLogger(__name__)

BJ_PREFIXES = ("920", "83", "87", "43")
SINA_KLINE_API = ("https://quotes.sina.cn/cn/api/json_v2.php/"
                  "CN_MarketDataService.getKLineData")
SINA_HEADERS = {"User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/"}

CATEGORIES = ("bj_unmapped", "delisted", "stopped_trading", "live_but_no_data",
              "ambiguous", "unattributed")


def sina_sina_symbol(code: str) -> str:
    """新浪行情 symbol：6/9 开头走 sh，其余走 sz（北交所另有 bj 前缀，本函数不调它）。"""
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def classify(fact: dict) -> tuple[str, str]:
    """库内证据 -> (类别, 证据文字)。纯函数，单测直接喂字典。"""
    code = fact["code"]
    name = fact.get("name") or ""
    if code.startswith(BJ_PREFIXES) and fact.get("in_snapshot") \
            and not fact.get("kline_rows") and not fact.get("fin_rows"):
        return ("bj_unmapped",
                f"北交所号段（{code[:3]}）、快照有行（{name}）但行情/财务均为 0 行")
    if "退" in name:
        return ("delisted", f"名称含退市标记：{name}")
    if not fact.get("kline_rows") and fact.get("fin_rows"):
        return ("ambiguous",
                f"行情 0 行却有 {fact['fin_rows']} 条财务（末次覆盖 "
                f"{fact.get('covered_report_date') or '-'}）、名称不带退字")
    return ("unattributed",
            f"kline={fact.get('kline_rows')} fin={fact.get('fin_rows')} name={name!r}"
            " —— 现有判据都盖不住，需要新增规则")


def last_bar_date(code: str, timeout: int = 15) -> tuple[str | None, str | None]:
    """新浪日线末根日期 -> `(末根日期 | None, 核验失败原因 | None)`。

    三个出口必须分得开，否则归因会猜错：
    - `(日期, None)`：末根日期；
    - `(None, None)`：接口正常应答但**空数组/无此 symbol** —— 这**本身就是证据**
      （已停止挂牌/该市场无此码），据此判 stopped_trading；
    - `(None, 原因)`：核验动作失败（超时/断连）**或响应形状变了** ——
      不能据此下任何结论，留在 ambiguous。
    """
    url = SINA_KLINE_API + "?" + urllib.parse.urlencode(
        {"symbol": sina_sina_symbol(code), "scale": "240", "ma": "no",
         "datalen": "8"})
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers=SINA_HEADERS),
            timeout=timeout).read().decode("utf-8", "ignore").strip()
        bars = json.loads(raw) if raw and raw != "null" else []
    except Exception as e:  # noqa: BLE001 - 核验失败要如实报，不能当成"没有日线"
        return None, f"{type(e).__name__}: {str(e)[:80]}"
    if not isinstance(bars, list):
        # 上游改返回形状时不许判成"确属停止挂牌"——那是把解析失败当事实
        return None, f"响应形状异常（{type(bars).__name__}）"
    if not bars:
        return None, None
    return str(bars[-1].get("day"))[:10], None


def gather_facts() -> list[dict]:
    """所有 status='no_data' 的进度记录 + 库内存在感证据。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT p.task AS task, p.code AS code, p.updated_at AS at,"
            " p.covered_report_date AS cov, p.updated_rows AS written,"
            " s.name AS name, s.market AS market,"
            " (SELECT COUNT(*) FROM kline_daily k WHERE k.code = p.code) AS kline_rows,"
            " (SELECT MAX(trade_date) FROM kline_daily k WHERE k.code = p.code)"
            "   AS kline_last,"
            " (SELECT COUNT(*) FROM financial_history f WHERE f.stock_code = p.code)"
            "   AS fin_rows,"
            " (SELECT MAX(report_date) FROM financial_history f"
            "   WHERE f.stock_code = p.code) AS fin_last,"
            " (s.code IS NOT NULL) AS in_snapshot"
            " FROM backfill_progress p LEFT JOIN stock_snapshot s ON s.code = p.code"
            " WHERE p.status = 'no_data' ORDER BY p.task, p.code").fetchall()
    return [{
        "task": r["task"], "code": r["code"], "name": r["name"],
        "market": r["market"], "in_snapshot": bool(r["in_snapshot"]),
        "kline_rows": r["kline_rows"], "kline_last": r["kline_last"],
        "fin_rows": r["fin_rows"], "fin_last": r["fin_last"],
        "covered_report_date": r["cov"], "written_rows": r["written"],
        "recorded_at": str(r["at"])[:10],
    } for r in rows]


def resolve(rows: list[dict], verify_live: bool, fresh_days: int,
            sleep: float) -> list[dict]:
    """分类；ambiguous 在 --verify-live 时升级为 stopped_trading / live_but_no_data。"""
    fresh_floor = (datetime.now() - timedelta(days=fresh_days)).strftime("%Y-%m-%d")
    for row in rows:
        category, evidence = classify(row)
        if category == "ambiguous" and verify_live:
            last, err = last_bar_date(row["code"])
            time.sleep(sleep)
            if err:
                evidence += f"；末根核验失败（{err}）"
            elif last is None:
                category = "stopped_trading"
                evidence += "；新浪已查不到该 symbol 日线 -> 确属停止交易"
            elif last >= fresh_floor:
                category = "live_but_no_data"
                evidence += f"；末根日线 {last} 仍在近 {fresh_days} 日内 -> 采集缺陷"
            else:
                category = "stopped_trading"
                evidence += f"；末根日线 {last} -> 该日起停止交易"
        row["category"] = category
        row["evidence"] = evidence
    return rows


def summarize(rows: list[dict]) -> dict:
    counts: dict[str, int] = {c: 0 for c in CATEGORIES}
    by_task: dict[str, dict[str, int]] = {}
    for row in rows:
        counts[row["category"]] += 1
        by_task.setdefault(row["task"], {}).setdefault(row["category"], 0)
        by_task[row["task"]][row["category"]] += 1
    return {"total": len(rows), "by_category": counts, "by_task": by_task,
            "unattributed_codes": [r["code"] for r in rows
                                   if r["category"] == "unattributed"]}


def to_markdown(rows: list[dict]) -> str:
    lines = ["| 任务 | 代码 | 名称 | 类别 | 行情行数 | 财务行数 | 证据 |",
             "|---|---|---|---|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['task']} | {r['code']} | {r['name'] or '-'} | "
                     f"{r['category']} | {r['kline_rows']} | {r['fin_rows']} | "
                     f"{r['evidence']} |")
    return "\n".join(lines)


def run(verify_live: bool = False, fresh_days: int = 5,
        sleep: float = 0.6) -> dict:
    rows = resolve(gather_facts(), verify_live=verify_live,
                   fresh_days=fresh_days, sleep=sleep)
    return {"summary": summarize(rows), "rows": rows,
             "verify_live": verify_live, "fresh_days": fresh_days}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="no_data 归因体检（只读）")
    ap.add_argument("--verify-live", action="store_true",
                    help="对 ambiguous 桶查一次新浪末根日线（默认不打网络）")
    ap.add_argument("--fresh-days", type=int, default=5,
                    help="末根日线在这几天内即视为仍在交易，默认 5")
    ap.add_argument("--sleep", type=float, default=0.6,
                    help="末根核验的请求间隔秒，默认 0.6")
    ap.add_argument("--format", choices=("json", "md"), default="json")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    init_database()
    result = run(verify_live=args.verify_live, fresh_days=args.fresh_days,
                 sleep=args.sleep)
    if args.format == "md":
        print(to_markdown(result["rows"]))
        print(json.dumps(result["summary"], ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    unattributed = result["summary"]["unattributed_codes"]
    if unattributed:
        logger.error("有 %s 只票归因失败，需要补规则：%s",
                     len(unattributed), unattributed[:20])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
