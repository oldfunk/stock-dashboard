"""sector 行业覆盖扩图（批9-b，"把数据都搞好"专项）。

缺口与收益（nec 只读实测 2026-10-29，命令与原始输出见 Issue #9 交付帖）
------------------------------------------------------------------------
`stock_snapshot` 5,527 行里 sector 为空 **2,933 只（53.1%）**；已覆盖的 2,594 只
全部来自 S8 新浪 49 板块——行业名只有 49 个，其中 114 只挂在"次新股"这种**不是
行业**的桶里。申万一级行业映射实测：31 个行业、5,221 只、`0` 个 code 落在两个行业
（是真划分，不是重叠标签），能补掉那 2,933 只缺口的 **90.5%**，sector 覆盖从
46.9% → **94.9%**；`kline_daily` 的 5,193 只里只剩 1 只（002731）没有行业。

为什么不是派工单点名的那两个接口
--------------------------------
派工单点名 `stock_board_industry_name_em` / `stock_board_industry_cons_em`（东财
行业板块）。立前在产线实测（结论浓缩，取证过程在 `akshare_fetcher.py` S8-b 段）：
东财那个 clist 口对端**直接断连**（akshare 封装 `ConnectionError: RemoteDisconnected`，
重跑仍失败；手拼同样参数直连第一次 200、随后同样断连），而且它对到的"行业板块"是
**496 个且分层**（名字带 Ⅱ/Ⅲ 后缀），逐板块取成分要 496 次 HTTP——正好撞在 N10
修的"东财快速请求被限流"上。换成申万官方一级行业（`sw_index_first_info` +
`index_component_sw`）后是 **31 次请求换一次全市场划分**，行业名也是业界通用口径：
既满足派工单的字面（"申万行业"），也比东财板块更满足目的（全量映射）。
"新浪映射作回退"照办：申万没有的 code 用新浪补，两边都没有的一律不进 map
（`backfill_sectors` 只碰 map 里的 code，未收录的票继续 NULL，不臆造）。

一次运行会改变整列口径（重要，先看清再跑）
------------------------------------------
map 里的票无论原来挂在新浪哪个桶，都会被改写成申万一级行业名——这是"扩图"的本意，
但副作用是列里可能**两套词汇并存**：申万没覆盖、只有新浪覆盖的那批 code 会继续挂着
新浪行业名。数量报在 `sina_only_codes` / `sina_only_name_count` 里。要不要把这批
清成 NULL、或强制"只用同源映射"，是口径决定，归你——我不在脚本里替你删既有值。

成本、节奏与 fail-closed
------------------------
实时拉取 = 1 次行业名单 + 31 次成分，产线实测 **190.6s**（含 0.3s 请求间节奏）。
结果落磁盘缓存，默认 30 天内不再打网络（行业分类变动很慢），所以只有首次或
`--refresh` 才真的打这 31 次。映射规模低于 `SW_SECTOR_MAP_MIN=3000` 判上游异常：
照常返回给本次使用，但**不覆盖缓存**（半截映射不能被下次运行当成"最新全量"）。
两个源都拿不到映射时**不写库**并回非 0——空 map 时 `backfill_sectors` 本来就返回 0，
但那样"跑了"看起来像"跑成了"，所以要多报一个 error。

不直连生产：本脚本只交付代码 + 单测；实跑与是否进排程由母方在 nec 决定。

用法
----
    python scripts/backfill_sectors_sw.py --dry-run
    python scripts/backfill_sectors_sw.py                    # 缓存够新就不打网络
    python scripts/backfill_sectors_sw.py --refresh          # 强制重拉 31 个行业
    python scripts/backfill_sectors_sw.py --no-sina          # 只用申万，不带新浪回退
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src.collector.akshare_fetcher import (  # noqa: E402
    fetch_sector_map, fetch_sw_sector_map, merge_sector_maps,
)
from src.models.database import StockSnapshotDAO  # noqa: E402


def run(refresh: bool = False, dry_run: bool = False,
        use_sina: bool = True) -> dict:
    """建映射 -> 与现网缺口对账 -> （非 dry-run）落库；返回汇总。"""
    res = fetch_sw_sector_map(force=refresh)
    sw = res["map"]
    sina = fetch_sector_map() if use_sina else {}
    merged = merge_sector_maps(sw, sina)

    dao = StockSnapshotDAO()
    missing_before = dao.codes_missing_sector()
    missing_set = set(missing_before)
    sina_only = {c: n for c, n in merged.items() if c not in sw}
    predicted_fill = sum(1 for c in merged if c in missing_set)
    report = {
        "sw_source": res.get("source"), "sw_error": res.get("error"),
        "sw_codes": len(sw), "sina_codes": len(sina), "merged": len(merged),
        "sina_only_codes": len(sina_only),
        "sina_only_name_count": len({n for n in sina_only.values()}),
        "snapshot_total": dao.count(),
        "missing_before": len(missing_before),
        "predicted_fill": predicted_fill,
        "predicted_missing_after": len(missing_before) - predicted_fill,
        "sw_stats": res.get("stats") or {},
        "dry_run": dry_run,
        "updated": 0,
    }
    if not merged:
        report["error"] = "申万与新浪两个源都没有拿到行业映射，不写库"
        return report
    if dry_run:
        return report
    report["updated"] = dao.backfill_sectors(merged)
    report["missing_after"] = len(dao.codes_missing_sector())
    report["fill_actual"] = report["missing_before"] - report["missing_after"]
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="sector 行业扩图：申万一级行业全量映射（新浪作回退）")
    ap.add_argument("--refresh", action="store_true",
                    help="忽略 30 天缓存，强制重拉 31 个行业的成分")
    ap.add_argument("--no-sina", action="store_true",
                    help="不拉新浪映射做回退（申万没覆盖的 code 就继续 NULL）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只建映射并对账缺口，不写 stock_snapshot")
    args = ap.parse_args(argv)
    result = run(refresh=args.refresh, dry_run=args.dry_run,
                 use_sina=not args.no_sina)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
