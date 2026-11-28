"""R3 策略/部署/归因/漂移 CLI（2026-11-28）。

示例：
    python scripts/research_strategy.py register
    python scripts/research_strategy.py deploy
    python scripts/research_strategy.py attribute --start 2026-11-20 --end 2026-11-27
    python scripts/research_strategy.py drift
    python scripts/research_strategy.py promote S-20261128-001
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.research.attribution import run_attribution  # noqa: E402
from src.research.dao import ResearchDAO  # noqa: E402
from src.research.drift import run_drift  # noqa: E402
from src.research.strategy import (  # noqa: E402
    BASE_STRATEGY_ID, create_version, ensure_base_strategy, ensure_deployment,
    set_status,
)


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _latest_deployment(dao):
    rows = dao.list_deployments(limit=10)
    return rows[0] if rows else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R3 策略/部署/归因/漂移")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("register", help="登记 S-BASE-v0")
    sub.add_parser("list", help="列出策略")
    p = sub.add_parser("new", help="新建 draft 版本")
    p.add_argument("--parent", default=BASE_STRATEGY_ID)
    p.add_argument("--spec", required=True, help="spec JSON 文件")
    p.add_argument("--hypotheses", default="", help="逗号分隔的假设 ID")
    p.add_argument("--provenance", default="{}", help="provenance JSON")
    p = sub.add_parser("promote", help="draft → paper")
    p.add_argument("strategy_id")
    p = sub.add_parser("retire", help="→ retired")
    p.add_argument("strategy_id")
    p = sub.add_parser("deploy", help="登记纸盘部署")
    p.add_argument("--strategy", default=BASE_STRATEGY_ID)
    p.add_argument("--capital", type=float)
    p = sub.add_parser("attribute", help="生成周期归因")
    p.add_argument("--deployment", default="")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--benchmark", default="sh000300")
    p.add_argument("--publish", action="store_true")
    p = sub.add_parser("drift", help="漂移检测")
    p.add_argument("--deployment", default="")
    p.add_argument("--window", type=int, default=4)
    p.add_argument("--publish", action="store_true")

    args = ap.parse_args(argv)
    dao = ResearchDAO()

    if args.cmd == "register":
        _print(ensure_base_strategy())
    elif args.cmd == "list":
        _print(dao.list_strategies(limit=100))
    elif args.cmd == "new":
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        hyps = [x.strip() for x in args.hypotheses.split(",") if x.strip()]
        prov = json.loads(args.provenance or "{}")
        _print(create_version(spec, args.parent, hyps, prov))
    elif args.cmd == "promote":
        _print(set_status(args.strategy_id, "paper"))
    elif args.cmd == "retire":
        _print(set_status(args.strategy_id, "retired"))
    elif args.cmd == "deploy":
        _print(ensure_deployment(args.strategy, args.capital))
    elif args.cmd == "attribute":
        dep = args.deployment or (_latest_deployment(dao) or {}).get("id")
        if not dep:
            print("没有部署，先跑 deploy", file=sys.stderr)
            return 2
        _print(run_attribution(dep, args.start, args.end, args.benchmark,
                               dry_run=not args.publish))
    elif args.cmd == "drift":
        dep = args.deployment or (_latest_deployment(dao) or {}).get("id")
        if not dep:
            print("没有部署，先跑 deploy", file=sys.stderr)
            return 2
        _print(run_drift(dep, args.window, dry_run=not args.publish))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
