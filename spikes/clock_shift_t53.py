"""T53 诊断器：把墙钟拨快 N 天跑 pytest，让"会随日历老化"的测试自己站出来。

grep 只能给候选（哪条断言真的读墙钟，要看产品码里那条路径调不调 `date.today()`）；
这个脚本直接改口径：**导入所有 `paper_trading.*` 模块，把它们的 `date`/`datetime`
换成偏移了 N 天的假类**，再跑一遍测试。红了的就是真正的 flaky 族，
绿的就只是"日期写得不漂亮"但语义与钟无关。

用法：
    python spikes/clock_shift_t53.py 7          # +7 天（跨一个周末）
    python spikes/clock_shift_t53.py 30         # +30 天（跨月）
    python spikes/clock_shift_t53.py 0          # 基线（应全绿）
"""
from __future__ import annotations

import datetime as _dt
import importlib
import pkgutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SHIFT_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
TARGETS = sys.argv[2:] or ["tests"]


class FakeDate(_dt.date):
    @classmethod
    def today(cls):
        return _dt.date.today() + _dt.timedelta(days=SHIFT_DAYS)

    @classmethod
    def fromisoformat(cls, s):
        return _dt.date.fromisoformat(s)      # 归一回基类，避免子类构造签名差异


class FakeDateTime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _dt.datetime.now(tz) + _dt.timedelta(days=SHIFT_DAYS)

    @classmethod
    def today(cls):
        return _dt.datetime.now() + _dt.timedelta(days=SHIFT_DAYS)

    @classmethod
    def fromisoformat(cls, s):
        return _dt.datetime.fromisoformat(s)


def patch_modules() -> list[str]:
    """遍历 paper_trading 全部模块，把 stdlib 的 date/datetime 名字换成假类。"""
    import paper_trading

    mods = [paper_trading]
    for m in pkgutil.walk_packages(paper_trading.__path__, "paper_trading."):
        try:
            mods.append(importlib.import_module(m.name))
        except Exception:                        # noqa: BLE001 - 装不上的模块不参与
            continue
    patched = []
    for mod in mods:
        changed = []
        if getattr(mod, "date", None) is _dt.date:
            mod.date = FakeDate                      # type: ignore[attr-defined]
            changed.append("date")
        if getattr(mod, "datetime", None) is _dt.datetime:
            mod.datetime = FakeDateTime              # type: ignore[attr-defined]
            changed.append("datetime")
        if changed:
            patched.append(f"{mod.__name__}:{'/'.join(changed)}")
    return patched


def main() -> int:
    real_now = _dt.datetime.now()
    patched = patch_modules()
    print(f"墙钟 +{SHIFT_DAYS} 天（真实 {real_now:%Y-%m-%d} ⇒ 假装 "
          f"{real_now + _dt.timedelta(days=SHIFT_DAYS):%Y-%m-%d}），"
          f"已替换 {len(patched)} 个模块的时钟名：")
    print("   " + ", ".join(p.split(".")[1] if p.startswith("paper_trading") else p
                            for p in patched[:26]))
    import pytest
    code = pytest.main(["-q", "--no-header", "-p", "no:cacheprovider", *TARGETS])
    print(f"\n墙钟 +{SHIFT_DAYS} 天跑完：pytest exit={code}")
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
