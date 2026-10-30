"""字段"取到率"体检（批10-b 方案 1 + 批10-c 五字段体检，2026-10-30，只读）。

派工单原文是"历史商誉只有 35% 年报行有值，修采集把覆盖率拉到 ≥85%"。取证（Issue #9
批10-b 报告）证明那 35% **不是漏采**：东财对没有商誉的公司，同一行里给了货币资金
却明确不给商誉（`GOODWILL` 键存在、值为 null），重跑采集一字节都不会变。
母方裁定改走方案 1——**不设百分比指标，只交"取到率"事实**。

两个口径必须分开，否则同一份数据能编出两个相反的故事：

- **有值率** = `goodwill IS NOT NULL` / 全部年报行。把"上游确认没有"与"我没采到"
  混在同一个分母里——原来 85% 目标用的是它，它不可达，也不该达。
- **取到率** = 某字段有值 / **"这份资产负债表确实被取到过"的行**。判据取两个：
  宽判据（五个补充字段任一有值）与严格判据（`total_assets` 有值）；
  两者在本库只差 3 行，说明分母是稳的。剩下的"分母内无值"就是**源确认无此项目**，
  那是事实，不是缺陷。

脚本只做四件事：算这两个口径、点出"整份资产负债表都没落地"的行（这才是采集缺陷）、
按年与按票给分布、输出 JSON 或 markdown。**不写任何表**。

    python scripts/report_field_coverage.py
    python scripts/report_field_coverage.py --format md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src.models.database import db_conn  # noqa: E402

EXTRA_FIELDS = ("cash", "interest_bearing_debt", "goodwill",
                "total_assets", "total_equity")
STRICT_FIELD = "total_assets"


def _any_present(fields=EXTRA_FIELDS) -> str:
    return " OR ".join(f"{f} IS NOT NULL" for f in fields)


def _all_null(fields=EXTRA_FIELDS) -> str:
    return " AND ".join(f"{f} IS NULL" for f in fields)


def coverage(conn) -> dict:
    """一次连接内算完所有口径（全 SELECT）。连接由调用方给，便于单测复用同一 tmp 库。"""
    any_present, all_null = f"({_any_present()})", f"({_all_null()})"
    row = conn.execute(
        "SELECT COUNT(*) AS annual_rows,"
        f"  SUM({any_present}) AS fetched_rows,"
        f"  SUM({STRICT_FIELD} IS NOT NULL) AS strict_fetched_rows,"
        f"  SUM({all_null}) AS never_fetched_rows"
        " FROM financial_history WHERE report_type = 'A'").fetchone()
    annual = row["annual_rows"] or 0
    fetched = row["fetched_rows"] or 0
    strict = row["strict_fetched_rows"] or 0
    never = row["never_fetched_rows"] or 0

    fields: dict[str, dict] = {}
    for f in EXTRA_FIELDS:
        present = conn.execute(
            f"SELECT COUNT(*) AS n FROM financial_history"
            f" WHERE report_type = 'A' AND {any_present} AND {f} IS NOT NULL"
        ).fetchone()["n"] or 0
        fields[f] = {
            "present": present,
            "take_rate": round(100.0 * present / fetched, 1) if fetched else None,
            "has_value_rate_all_rows": round(100.0 * present / annual, 1) if annual else None,
        }

    by_year = [{"year": r["y"], "annual_rows": r["n"],
                "never_fetched": r["nf"], "goodwill_present": r["gw"]}
               for r in conn.execute(
        "SELECT SUBSTR(report_date, 1, 4) AS y, COUNT(*) AS n,"
        f"  SUM({all_null}) AS nf,"
        "  SUM(goodwill IS NOT NULL) AS gw"
        " FROM financial_history WHERE report_type = 'A'"
        " GROUP BY y ORDER BY y DESC LIMIT 10").fetchall()]

    worst = [{"code": r["code"], "row_count": r["row_count"], "name": r["name"]}
             for r in conn.execute(
        "SELECT f.stock_code AS code, COUNT(*) AS row_count, s.name AS name"
        " FROM financial_history f"
        " LEFT JOIN stock_snapshot s ON s.code = f.stock_code"
        f" WHERE f.report_type = 'A' AND {all_null}"
        " GROUP BY f.stock_code, s.name"
        " ORDER BY row_count DESC LIMIT 10").fetchall()]

    return {
        "annual_rows": annual,
        "fetched_rows": fetched,
        "strict_fetched_rows": strict,
        "never_fetched_rows": never,
        "never_fetched_pct": round(100.0 * never / annual, 2) if annual else None,
        "fields": fields,
        "goodwill_take_rate_pct": fields["goodwill"]["take_rate"],
        "goodwill_has_value_pct_all_rows": fields["goodwill"]["has_value_rate_all_rows"],
        "by_year": by_year,
        "worst_never_fetched_codes": worst,
    }


def to_markdown(s: dict) -> str:
    lines = [
        f"- 年报行 **{s['annual_rows']}**；资产负债表确实取到过 **{s['fetched_rows']}** 行"
        f"（严格判据 `{STRICT_FIELD}` 有值 = {s['strict_fetched_rows']} 行）",
        f"- 整份资产负债表都没落地的行：**{s['never_fetched_rows']}**"
        f"（{s['never_fetched_pct']}%）——只有这一类是采集缺陷，重跑有可能救",
        "",
        "| 字段 | 有值行数 | 取到率（取到过的行做分母） | 有值率（全部年报行做分母） |",
        "|---|---:|---:|---:|",
    ]
    for f, v in s["fields"].items():
        lines.append(f"| {f} | {v['present']} | {v['take_rate']}% | "
                     f"{v['has_value_rate_all_rows']}% |")
    lines += ["", "近 10 年分布（未取到 = 五字段全 NULL）：",
              "| 年份 | 年报行 | 未取到 | goodwill 有值 |", "|---|---:|---:|---:|"]
    for y in s["by_year"]:
        lines.append(f"| {y['year']} | {y['annual_rows']} | {y['never_fetched']} |"
                     f" {y['goodwill_present']} |")
    return "\n".join(lines)


def run() -> dict:
    with db_conn() as conn:
        return coverage(conn)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补充字段取到率体检（只读）")
    ap.add_argument("--format", choices=("json", "md"), default="json")
    args = ap.parse_args(argv)
    stats = run()
    print(to_markdown(stats) if args.format == "md"
          else json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
