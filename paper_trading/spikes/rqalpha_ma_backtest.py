"""M1-A：rqalpha 原生 bundle 端到端回测（我们的 MA5/20 规则语义）。

数据完整性承诺（用户指令"不能缺任何数据"）：
本脚本**不喂任何自定义数据**——价格(原始价)/复权因子/分红/拆股/停牌/ST/
交易日历/合约元数据全部来自 rqalpha bundle（全市场、自洽三件套）。
跑通 + 有成交 + 指标非有限值门禁全过 = "新项目在完整数据上跑起来"。

M1 §5.1-5 门禁：summary 里任何 NaN/Inf → exit 1（rqalpha 越界窗口只吐
NaN 不抛错，见 worker report-t5 §10.3）。
"""
import math
import sys

from rqalpha import run_func

BUNDLE = r"C:\Users\yanzh\.rqalpha\bundle"
SYMBOLS = ["000858", "601318", "600036", "601398", "600900",
           "000333", "600887", "600276", "601888", "000651"]
INIT_CASH = 100000.0
LOT = 100                      # 与 AGENTS 合规表一致：整手 100 股


def obid(sym: str) -> str:
    """6/9 开头 → 上交所 XSHG，0/3 → 深交所 XSHE（本池无北交所）。"""
    return f"{sym}.{'XSHG' if sym[0] in '69' else 'XSHE'}"


def init(context):
    context.symbols = [obid(s) for s in SYMBOLS]
    context.n_signals = 0
    context.n_orders = 0


def handle_bar(context, bar_dict):
    from rqalpha.api import history_bars, order_shares
    from rqalpha.const import POSITION_DIRECTION

    for ob in context.symbols:
        try:
            closes = history_bars(ob, 21, "1d", "close")
        except Exception:
            continue
        if closes is None or len(closes) < 21:
            continue
        c = [float(x) for x in closes]
        ma5, ma20 = sum(c[-5:]) / 5, sum(c[-20:]) / 20
        ma5_p, ma20_p = sum(c[-6:-1]) / 5, sum(c[-21:-1]) / 20
        golden = ma5_p <= ma20_p and ma5 > ma20      # 金叉=买（方向由信号定）
        death = ma5_p >= ma20_p and ma5 < ma20       # 死叉=卖
        # 6.x API：多头持仓（本框架禁做空，只看 LONG）
        pos = context.portfolio.get_position(ob, POSITION_DIRECTION.LONG)
        qty = int(pos.quantity) if pos else 0
        if golden and qty == 0:
            context.n_signals += 1
            order_shares(ob, LOT)                    # 买入
            context.n_orders += 1
        elif death and qty > 0:
            context.n_signals += 1
            order_shares(ob, -min(LOT, qty))         # 卖出（整手）
            context.n_orders += 1


def _walk_finite(obj, path=""):
    """非有限值门禁：递归找 NaN/Inf，返回违规路径列表。"""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            bad += _walk_finite(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad += _walk_finite(v, f"{path}[{i}]")
    elif isinstance(obj, float) and not math.isfinite(obj):
        bad.append(f"{path} = {obj}")
    return bad


def main() -> int:
    config = {
        "base": {
            "start_date": "2026-01-02",
            "end_date": "2026-09-10",       # bundle 价格覆盖到 2026-09/10
            "accounts": {"STOCK": INIT_CASH},   # 6.x：parse_accounts 只 upper()，
                                                # 必须用 DEFAULT_ACCOUNT_TYPE.STOCK
            "frequency": "1d",
            "data_bundle_path": BUNDLE,
            "capital_gain_tax_rate": 0.0,    # 6.x 显式要求，A股无资本利得税
            "locale": "zh_CN",
        },
        "mod": {
            # 6.x：基准移到 sys_analyser（base.benchmark 已弃用）
            "sys_analyser": {"benchmark": "000300.XSHG"},
        },
    }
    result = run_func(init=init, handle_bar=handle_bar, config=config)

    # 结果导航（6.x 返回形态防御：summary 可能在 sys_analyser 下或顶层）
    summary = {}
    if isinstance(result, dict):
        sa = result.get("sys_analyser")
        if isinstance(sa, dict):
            summary = sa.get("summary") or sa
        else:
            summary = result.get("summary") or result

    print("=" * 60)
    print("M1-A rqalpha bundle 端到端回测（MA5/20，10 只 A 股，完整数据）")
    print("=" * 60)
    for k in ("start_date", "end_date", "starting_cash", "total_value",
              "total_return", "annualized_returns", "sharpe", "max_drawdown",
              "total_trades", "alpha", "beta"):
        if isinstance(summary, dict) and k in summary:
            print(f"  {k}: {summary[k]}")

    # 门禁 1：非有限值（NaN/Inf）→ 作废
    bad = _walk_finite(summary, "summary")
    if bad:
        print("NaN/Inf GATE FAILED:", *bad[:10], sep="\n  ")
        return 1

    # 门禁 2：确实有成交（证明数据喂进了信号，不是空转）
    trades = 0
    sa = result.get("sys_analyser") if isinstance(result, dict) else None
    if isinstance(sa, dict):
        tr = sa.get("trades")
        if tr is not None:
            try:
                trades = len(tr)            # trades 是 DataFrame/列表
            except TypeError:
                trades = int(tr or 0)
    if trades <= 0 and isinstance(summary, dict):
        print("summary keys:", sorted(summary.keys()))
        for key in ("total_trades", "trade_count", "num_trades"):
            if key in summary:
                try:
                    trades = int(summary[key])
                except (TypeError, ValueError):
                    trades = 0
                break
    if trades <= 0:
        print("NO-TRADE GATE FAILED: 数据/信号链未产出成交")
        return 1

    print(f"M1-A GATE PASSED: trades={trades}, summary 全有限值")
    return 0


if __name__ == "__main__":
    sys.exit(main())
