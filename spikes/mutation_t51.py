"""T51 变异检查：把"抄第二遍"这件事重新做一遍，确认反射测试真的会响。

T51 的交付物本质上是**一条纪律的代码化**（AGENTS 纪律 7 口径唯一），
所以变异全部是"回退成旧写法"，而不是"改一个数字"：
每条都对应一处历史真相源或一条已经关掉的兜底路径。
"""
import pathlib
import subprocess
import sys

TARGETS = ["tests/test_fee_single_source_t51.py", "tests/test_fees_and_oplog_t38.py",
           "tests/test_m3_verify_t46.py", "tests/test_backtest_pricing.py"]
FILES = {
    "backtest": pathlib.Path("paper_trading/engine/backtest.py"),
    "costs": pathlib.Path("paper_trading/integration/rq_engine/costs.py"),
    "broker": pathlib.Path("paper_trading/broker/paper_broker.py"),
    "verify": pathlib.Path("paper_trading/utils/m3_verify.py"),
    "fees": pathlib.Path("paper_trading/utils/fees.py"),
    "config": pathlib.Path("paper_trading/utils/config.py"),
    "dash": pathlib.Path("paper_trading/dashboard.py"),
}
ORIG = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
py = sys.executable


def patch(key, old, new):
    assert old in ORIG[key], f"锚点丢失：{key}\n{old!r}"
    FILES[key].write_text(ORIG[key].replace(old, new, 1), encoding="utf-8")


def restore(key):
    FILES[key].write_text(ORIG[key], encoding="utf-8")


def run(label):
    r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l
            or "error" in l]
    print("%-44s → %s" % (label, tail[-1] if tail else r.stdout[-200:]))


# ① FeeModel 把数字抄回来（T51 前的旧写法）
patch("backtest", "    commission_rate: float = _TC.commission_rate",
      "    commission_rate: float = 0.00025")
run("① FeeModel 自带费率数字")
restore("backtest")

# ② costs.py 把四个现网常量抄回来（T38 旧写法）
patch("costs", "RQ_STOCK_COMMISSION_RATE = 0.0008    # deciders.py:73",
      "COMMISSION_RATE = 0.00025\nRQ_STOCK_COMMISSION_RATE = 0.0008    # deciders.py:73")
run("② costs.py 重长出现网费率常量")
restore("costs")

# ③ broker 把佣金公式抄回本地（三份公式的第一份）
patch("broker", "        return fees.buy_cost(self.config, amount)",
      "        return (max(amount * self.config.commission_rate,\n"
      "                    self.config.commission_min),\n"
      "                amount * self.config.transfer_fee_rate)")
run("③ broker 内联佣金公式")
restore("broker")

# ④ m3 verify 把公式抄回本地（第三份：检查与记账分尺子）
patch("verify", "            want_commission, want_stamp, want_transfer = fees.sell_cost(cfg, amount)",
      "            want_commission = max(amount * float(cfg.commission_rate),\n"
      "                                    float(cfg.commission_min))\n"
      "            want_stamp, want_transfer = (amount * float(cfg.stamp_duty_rate),\n"
      "                                         amount * float(cfg.transfer_fee_rate))")
run("④ m3 verify 内联佣金公式")
restore("verify")

# ⑤ 面板不吃 config（T51 前的实际行为：读错键 ⇒ 永远兜底）
patch("dash", "    return FeeModel.from_trading_config(load_config(cfg_path)[\"trading_config\"])",
      "    return FeeModel()          # 故意退回\"用默认值\"的旧行为")
run("⑤ 面板忽略 config 费率")
restore("dash")

# ⑥ as_fee_dict 允许半份费率（缺项用 0 补 = 静默混口径）
patch("fees", '        raise ValueError(f"费率来源缺 {missing}，四项缺一不可（现有 {sorted(out)}）")',
      "        out.update({k: 0.0 for k in missing})")
run("⑥ 缺项费率被 0 补齐")
restore("fees")

# ⑦ as_fee_dict 不传时又兜回 TradingConfig（第二真相源复活）
patch("fees", '        raise ValueError("必须显式给出费率来源（TradingConfig 或含四项费率的 dict）；"\n'
              '                         "不给就报错——兜底数字是第二真相源（纪律 7）")',
      "        from paper_trading.models import TradingConfig\n        fee = TradingConfig()")
run("⑦ fee 缺省重新兜底")
restore("fees")

# ⑧ config 加载忽略 yaml 里写的费率
patch("config", "            setattr(cfg, key, float(val))", "            pass  # 故意忽略 yaml 值")
run("⑧ yaml 费率被忽略")
restore("config")

# ⑨ config 加载器把兜底数字抄回来（T51 前的四处之一）
patch("config", "    cfg = TradingConfig()\n    unknown: list[str] = []",
      "    cfg = TradingConfig()\n    COMMISSION_MIN = 5.0     # 故意抄回兜底数字\n"
      "    unknown: list[str] = []")
run("⑨ 加载器重长出费率数字")
restore("config")

# ⑩ 未知键静默丢掉（"改了没生效"的另一种形态）
patch("config", '            unknown.append(str(key))', "            continue")
run("⑩ 未知 trading 键不吭声")
restore("config")

run("还原后（应全绿）")
