"""导入 AKShare SH/SZ 退市名单（161+211），落 instrument + universe_membership。

用法：
  python scripts/research/import_delisted.py --dry-run
  python scripts/research/import_delisted.py
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.research.universe import import_delisted


def _fetch(fetch_fn, name):
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"需要 akshare: {exc}")
    frame = getattr(ak, fetch_fn)()
    print(f"[delisted] {name} 名单 {len(frame)} 行")
    return frame.to_dict("records")


def main() -> int:
    parser = argparse.ArgumentParser(description="导入退市名单")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sh = _fetch("stock_info_sh_delist", "SH")
    sz = _fetch("stock_info_sz_delist", "SZ")
    print(json.dumps(import_delisted(sh, sz, dry_run=args.dry_run),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
