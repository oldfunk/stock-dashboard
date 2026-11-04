"""T51 端到端证据：**改 config.yaml 的费率，回测与面板的费用必须跟着变**。

修前的现网事实（report-t51 §1）：三处"各自 new"读 `load_config(...)["trading"]`，
而 `utils/config.py` 返回的键叫 `trading_config` ⇒ `.get("trading")` 恒为 `None`
⇒ 每次都用代码里抄的兜底数字，`config.yaml` 改成什么都不动。

跑法：`python spikes/e2e_t51.py`（纯本地临时目录，不碰现网任何库）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.data.db_manager import DataDBManager          # noqa: E402
from paper_trading.models import Bar                               # noqa: E402
from paper_trading.utils.config import load_config                 # noqa: E402

SYM = "600519"
RULES = ("engine:\n  enabled: true\n  universe: list\n"
         "  rules:\n    - id: m1\n      type: ma_cross\n"
         "      symbol: 600519\n      params: {short: 3, long: 10}\n"
         "      action: {side: buy, value: 8000}\n")
CFG_A = ("trading:\n  commission_rate: 0.00025\n  commission_min: 5.0\n"
         "  stamp_duty_rate: 0.0005\n  transfer_fee_rate: 0.00001\n")
CFG_B = ("trading:\n  commission_rate: 0.001\n  commission_min: 50.0\n"
         "  stamp_duty_rate: 0.002\n  transfer_fee_rate: 0.00003\n")


def seed_bars(db_path: Path) -> None:
    """造一段先平后涨的日线，保证 ma_cross 至少触发一次买入。"""
    db = DataDBManager(db_path)
    closes = [9.0] * 20 + [9.2, 9.6, 10.4, 11.5, 12.6]
    start = datetime(2026, 8, 3)
    bars = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        bars.append(Bar(symbol=SYM, timestamp=d, open=c, high=c + 0.1, low=c - 0.1,
                        close=c, volume=100_000, amount=c * 100_000, adjust="qfq"))
    db.upsert_bars(bars)


def run_backtest(work: Path, cfg_name: str) -> dict:
    r = subprocess.run([sys.executable, "-m", "paper_trading.cli",
                        "--data-db", str(work / "data.db"),
                        "--account-db", str(work / "acct.db"),
                        "--config", cfg_name,
                        "engine", "backtest", "--symbols", SYM,
                        "--days", "30", "--cash", "100000", "--json"],
                       cwd=str(work), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={"PYTHONPATH": str(ROOT), **dict(__import__("os").environ)})
    if r.returncode != 0:
        raise SystemExit(f"回测没跑起来（exit {r.returncode}）：\n{r.stdout}\n{r.stderr}")
    lines = r.stdout.splitlines()
    # emit 打的是缩进 JSON，日志行在它前面；从第一个独占一行的 "{" 起才是 payload
    start = next(i for i, l in enumerate(lines) if l.strip() == "{")
    return json.loads("\n".join(lines[start:]))["data"]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="e2e-t51-"))
    (work / "engine.local.yaml").write_text(RULES, encoding="utf-8")
    (work / "cfgA.yaml").write_text(CFG_A, encoding="utf-8")
    (work / "cfgB.yaml").write_text(CFG_B, encoding="utf-8")
    seed_bars(work / "data.db")

    # ① 先把"修前为什么静默失效"钉在证据里
    keys = sorted(load_config(work / "cfgA.yaml").keys())
    print(f"load_config 返回的键：{keys}")
    print(f"  其中 'trading' 键存在吗：{'trading' in keys}"
          f"  ← 修前三处读的就是这个不存在的键 ⇒ 永远吃兜底数字")

    a, b = run_backtest(work, "cfgA.yaml"), run_backtest(work, "cfgB.yaml")
    field = "total_fees"
    print(f"取费用字段：{field}")
    print(f"  配置 A（万 2.5 / 地板 5 元）：{a.get(field)}  "
          f"成交 {a.get('trades')} 笔")
    print(f"  配置 B（万 10  / 地板 50 元）：{b.get(field)}  "
          f"成交 {b.get('trades')} 笔")
    fa, fb = float(a.get(field) or 0), float(b.get(field) or 0)
    if fa == fb:
        print("✗ 两份 config 费率不同但回测费用相同 ⇒ 口径还没收敛")
        return 1
    if (fa, fb) == (0.0, 0.0):
        print("✗ 两边都没费用，比对无意义（规则没触发？检查 trades）")
        return 1
    ratio = fb / fa if fa else float("inf")
    print(f"✅ 费率改档后回测费用变化：{fa} → {fb}（×{ratio:.2f}）；"
          f"地板 5→50 元也在里面起作用")

    # ② 面板侧同源
    from paper_trading.dashboard import _fee_of

    fm_a, fm_b = _fee_of(str(work / "cfgA.yaml")), _fee_of(str(work / "cfgB.yaml"))
    assert (fm_a.commission_rate, fm_b.commission_rate) == (0.00025, 0.001)
    assert (fm_a.commission_min, fm_b.commission_min) == (5.0, 50.0)
    print("✅ 面板 _fee_of 同样跟着 config 变（修前恒为兜底值）")
    print(f"\n临时目录：{work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
