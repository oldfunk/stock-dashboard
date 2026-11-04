"""T52 取证：rqalpha 撮合器能不能**脱离事件循环**逐笔调用（方案④判定）。

主 AI 已在 `spikes/intraday_resume_t52.py` 定案"方案①改良 = 终态"并**否决④**
（理由：手工拼 Environment/validator 上下文 = 另一种绕过框架）。
本报告不重开决策，只把它欠的**文件:行证据**补齐，并回答三个可执行问题：

  Q1 逐笔撮合要哪些在场状态？（依赖清单，机器导出不用手抄）
  Q2 会话内手工逐笔（跳过 api/validator/broker 记账）会**丢哪些语义**？
     —— 涨停拒单 / T+1 不可卖 / 费用三分量 / 撮合价=bar 口径 四项逐一实测
  Q3 跨进程、没有会话时还剩什么？（"每分钟进程直接调撮合器"这句话的真面目）

用法（需要装了 rqalpha 的 venv + bundle + raw 行情 fixture）：
    <spike venv>/python spikes/matcher_call_t52.py
本脚本自己起子进程跑 B/C（G-T13-9：一个进程只允许一个会话），末行输出 JSON 结论。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RQ_DIR = Path(sys.executable).parent.parent / "Lib" / "site-packages" / "rqalpha"
FIXTURE = Path(os.environ.get("T52_FIXTURE",
                              str(Path(os.environ["LOCALAPPDATA"]) / "Temp"
                                  / "qoder_t13" / "e2e_data.db")))
BUNDLE = os.environ.get("RQALPHA_BUNDLE", str(Path.home() / ".rqalpha" / "bundle"))
TODAY = os.environ.get("T52_TODAY", "2026-10-28")
OUT = Path(os.environ.get("T52_OUT", ""))

# ============================================================ A 静态依赖面
TARGETS = ("mod/rqalpha_mod_sys_simulation/simulation_broker.py",
           "mod/rqalpha_mod_sys_simulation/matcher/base.py",
           "mod/rqalpha_mod_sys_simulation/matcher/bar_matcher.py",
           "apis/api_base.py", "environment.py")


def _env_members(path: Path) -> dict[str, list[int]]:
    """收集 `self._env.X` / `env.X` 的访问点（file:line 可核，不靠手抄）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        head = node.value
        name = None
        if isinstance(head, ast.Attribute) and isinstance(head.value, ast.Name) \
                and head.value.id in ("self", "env") and head.attr in ("_env", "env"):
            name = node.attr
        elif isinstance(head, ast.Name) and head.id == "env":
            name = node.attr
        if name:
            out.setdefault(name, []).append(node.lineno)
    return out


def part_a() -> dict:
    members: dict[str, dict] = {}
    for rel in TARGETS:
        p = RQ_DIR / rel
        if not p.exists():
            continue
        members[rel] = _env_members(p)
    env_singleton = []
    for rel in TARGETS:
        p = RQ_DIR / rel
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "Environment.get_instance()" in line or "ExecutionContext.phase" in line:
                env_singleton.append(f"{rel}:{i}")
    return {"env_members": members, "singleton_uses": env_singleton}


# ============================================================ B 会话内三路径
def part_b() -> dict:                     # noqa: C901 - spike，一函数看全现场
    sys.path.insert(0, str(ROOT))
    from paper_trading.integration.rq_engine import runner as rn
    from paper_trading.models import TradingConfig

    probe: dict = {"orders": [], "trades": [], "cash": {}, "positions": {},
                   "errors": {}, "limits": {}}
    ev_snapshot = {"path": Path(tempfile.mkdtemp()) / "ev.json"}

    def run_probe():
        from rqalpha import run_func
        from rqalpha.api import order_shares
        from rqalpha.const import POSITION_EFFECT, SIDE
        from rqalpha.core.events import EVENT
        from rqalpha.environment import Environment
        from rqalpha.model.order import MarketOrder, Order

        ev_path = ev_snapshot["path"]
        cfg = rn.build_config(today=TODAY, data_db=str(FIXTURE), bundle=BUNDLE,
                              symbols=["000792", "000651", "000858", "600036"],
                              init_cash=100_000.0, state_dir="",
                              evidence=str(ev_path),
                              fee=TradingConfig(), require_today_reference=False)
        rn._register_mod()

        def on_trade(event):
            t = getattr(event, "trade", None)
            if t is None:
                return
            tc = getattr(t, "transaction_cost", 0)
            total = float(getattr(tc, "total", tc) or 0)
            probe["trades"].append({
                "symbol": t.order_book_id, "price": round(float(t.last_price), 4),
                "volume": int(t.last_quantity),
                "commission": round(float(getattr(t, "commission", 0) or 0), 6),
                "tax": round(float(getattr(t, "tax", 0) or 0), 6),
                "other_fees": round(total - float(getattr(t, "commission", 0) or 0)
                                     - float(getattr(t, "tax", 0) or 0), 6),
                "total": round(total, 6)})

        def handle_bar(context, bar_dict):        # noqa: ARG001
            env = Environment.get_instance()
            env.event_bus.add_listener(EVENT.TRADE, on_trade)
            acc = env.get_account("000792.XSHE")   # 按标的取账户（STOCK 不是 order_book_id）
            cash0 = acc.cash

            def snap(tag, order, extra=None):
                probe["orders"].append({
                    "tag": tag,
                    "symbol": getattr(order, "order_book_id", None),
                    "status": str(getattr(getattr(order, "status", None), "value",
                                          getattr(order, "status", None))),
                    "quantity": int(getattr(order, "quantity", 0) or 0),
                    "filled": int(getattr(order, "filled_quantity", 0) or 0),
                    "in_open_orders": order in [o for _a, o in env.broker.get_open_orders()],
                    "message": str(getattr(order, "message", "") or "")[:120],
                    "cash_after": round(float(acc.cash), 4), **(extra or {})})

            # P1 正常 api（市价单，走 validator + broker 记账）
            o1 = order_shares("000792.XSHE", 100)
            snap("P1_api", o1)

            # P2 手工建单 + validator + broker.submit_order（跳过 api 层）
            o2 = Order.__from_create__(order_book_id="000651.XSHE", quantity=100,
                                       side=SIDE.BUY, style=MarketOrder(),
                                       position_effect=POSITION_EFFECT.OPEN)
            ok2 = env.can_submit_order(o2)
            if ok2:
                env.broker.submit_order(o2)
            snap("P2_submit_no_api", o2, {"passed_validators": bool(ok2)})

            # P3 裸调 matcher（跳过 validator、跳过 ORDER_* 事件、不进 broker open_orders）
            o3 = Order.__from_create__(order_book_id="000858.XSHE", quantity=100,
                                       side=SIDE.BUY, style=MarketOrder(),
                                       position_effect=POSITION_EFFECT.OPEN)
            try:
                matcher = env.broker._get_matcher("000858.XSHE")
                matcher.match(acc, o3, False)
                note3 = "matched"
            except Exception as exc:                          # noqa: BLE001
                note3 = f"{type(exc).__name__}: {str(exc)[:120]}"
            probe["errors"]["P3"] = note3
            probe["cash"]["P3_delta"] = round(float(acc.cash) - float(cash0), 4)
            snap("P3_raw_matcher", o3)

            # P4 T+1：当天买过的 000792 立刻卖（api 路径与裸 matcher 路径各试一次）
            o4 = order_shares("000792.XSHE", -100)
            snap("P4_t1_api_sell", o4)
            o5 = Order.__from_create__(order_book_id="000792.XSHE", quantity=100,
                                       side=SIDE.SELL, style=MarketOrder(),
                                       position_effect=POSITION_EFFECT.CLOSE)
            try:
                matcher = env.broker._get_matcher("000792.XSHE")
                matcher.match(acc, o5, False)
                probe["errors"]["P5_t1_raw_sell"] = "matched"
            except Exception as exc:                          # noqa: BLE001
                probe["errors"]["P5_t1_raw_sell"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            snap("P5_t1_raw_sell", o5)

            # P6 涨停：人为把 limit_up 钉到当收 ⇒ 限价单留在市场、市价单被拒（语义分工）
            pb = env.price_board
            real_up = pb.get_limit_up
            bar_close = float(env.get_bar("600036.XSHG").close)
            pb.get_limit_up = lambda obid: bar_close if obid == "600036.XSHG" else real_up(obid)
            o6m = order_shares("600036.XSHG", 100)
            snap("P6_limit_market", o6m)
            probe["limits"]["bar_close"] = bar_close
            pb.get_limit_up = real_up

            pos = env.portfolio.get_position_or_none if hasattr(
                env.portfolio, "get_position_or_none") else None
            for sym in ("000792.XSHE", "000651.XSHE", "000858.XSHE"):
                p = acc.get_position(sym)
                if p is not None:
                    probe["positions"][sym] = {
                        "total": int(p.quantity), "closable": int(p.closable),
                        "avg": round(float(p.avg_price), 4)}
            probe["cash"]["end"] = round(float(acc.cash), 4)

        run_func(config=cfg, handle_bar=handle_bar)

    try:
        run_probe()
        if ev_snapshot["path"].exists():
            ev = json.loads(ev_snapshot["path"].read_text(encoding="utf-8"))
            probe["mod_rejects"] = ev.get("rejects")
            probe["mod_event_counts"] = ev.get("event_counts")
    except BaseException as exc:                              # noqa: BLE001
        probe["fatal"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    return probe


# ============================================================ C 跨进程无会话
def part_c() -> dict:
    out: dict = {}
    try:
        from rqalpha.environment import Environment
        Environment.get_instance()
        out["environment"] = "意外可用"
    except Exception as exc:                                  # noqa: BLE001
        out["environment"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    try:
        from rqalpha.const import POSITION_EFFECT, SIDE
        from rqalpha.model.order import MarketOrder, Order
        Order.__from_create__(order_book_id="000792.XSHE", quantity=100, side=SIDE.BUY,
                              style=MarketOrder(), position_effect=POSITION_EFFECT.OPEN)
        out["order_create"] = "意外可用"
    except Exception as exc:                                  # noqa: BLE001
        out["order_create"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    # 裸 matcher：用一个"记录访问顺序"的假 env，看它按什么顺序要哪些在场状态
    class Recorder:
        def __init__(self, trail, label):
            self.trail, self.label = trail, label

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            self.trail.append(f"{self.label}.{name}")
            return Recorder(self.trail, name)

        def __call__(self, *a, **kw):
            self.trail.append(f"{self.label}()")
            return Recorder(self.trail, f"{self.label}()")

    trail: list[str] = []
    try:
        from rqalpha.mod.rqalpha_mod_sys_simulation.matcher.bar_matcher import (
            DefaultBarMatcher)
        import types as pytypes
        from rqalpha.const import MATCHING_TYPE
        mod_cfg = pytypes.SimpleNamespace(slippage_model="LimitPriceSlippage",
                                          slippage=0, price_limit=True,
                                          volume_percent=0.25, inactive_limit=False,
                                          volume_limit=True,
                                          matching_type=MATCHING_TYPE.CURRENT_BAR_CLOSE)
        m = DefaultBarMatcher(Recorder(trail, "env"), mod_cfg)
        m.match(Recorder(trail, "account"), Recorder(trail, "order"), False)
        out["matcher"] = "竟然走通了（不合理，检查）"
    except Exception as exc:                                  # noqa: BLE001
        out["matcher"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    out["env_access_order"] = trail[:14]
    return out


# ============================================================ 编排
def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if which == "d":
        mode, state_dir, executed, plan = sys.argv[2], sys.argv[3], sys.argv[4],             json.loads(sys.argv[5])
        OUT.write_text(json.dumps(part_d(mode, state_dir, executed, plan),
                                  ensure_ascii=False, indent=1, default=str),
                        encoding="utf-8")
        return 0
    if which == "b":
        OUT.write_text(json.dumps(part_b(), ensure_ascii=False, indent=1,
                                  default=str), encoding="utf-8")
        return 0
    if which == "c":
        OUT.write_text(json.dumps(part_c(), ensure_ascii=False, indent=1,
                                  default=str), encoding="utf-8")
        return 0

    py = sys.executable
    tmp = Path(tempfile.mkdtemp(prefix="t52_"))
    res: dict = {"fixture": str(FIXTURE), "bundle": BUNDLE, "today": TODAY,
                 "fixture_exists": FIXTURE.exists()}
    res["A_static"] = part_a()
    for tag in ("b", "c"):
        outp = tmp / f"{tag}.json"
        r = subprocess.run([py, str(Path(__file__).resolve()), tag],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(ROOT),
                           env={**os.environ, "T52_OUT": str(outp)})
        res[f"part_{tag}"] = json.loads(outp.read_text(encoding="utf-8")) if outp.exists() \
            else {"crash": r.stdout[-400:] + r.stderr[-400:]}
        res[f"exit_{tag}"] = r.returncode
    # D：产品路径上的"日内续跑"三连跳（方案①改良的真实语义）
    state = tmp / "state"
    exe = tmp / "executed.json"
    plan1 = [{"symbol": "000792", "action": "buy", "volume": 100}]
    # 金额足够大才分得开佣金口径：18.65 万元 ⇒ 万 2.5=46.63 元，框架万八=149.20 元
    plan2 = plan1 + [{"symbol": "000651", "action": "buy", "volume": 5000}]
    dres = []
    for tag, mode, pl in (("d1", "first", plan1), ("d2", "resume", plan2),
                          ("d3", "idem", plan2)):
        outp = tmp / f"{tag}.json"
        r = subprocess.run([py, str(Path(__file__).resolve()), "d", mode, str(state),
                            str(exe), json.dumps(pl)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(ROOT),
                           env={**os.environ, "T52_OUT": str(outp)})
        dres.append({"probe": tag, "mode": mode,
                     "result": json.loads(outp.read_text(encoding="utf-8"))
                     if outp.exists() else {"crash": (r.stdout + r.stderr)[-500:]}})
    res["part_d"] = dres
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0



# ============================================================ D 产品路径日内续跑
def part_d(mode: str, state_dir: str, executed: str, plan: list) -> dict:
    """方案①改良在产品路径上的实测：续跑恢复是否正确 + 重复成交从哪来。

    mode=first  当天第一跳（正常闸）
    mode=resume 同日续跑，**放宽闸**（主 AI ①改良的做法：同日+有 state+指纹一致）
    mode=idem   同 resume，但执行前按"已成交清单"过滤意图（①改良的第二块）
    """
    sys.path.insert(0, str(ROOT))
    from paper_trading.integration.rq_engine import provider, runner as rn
    from paper_trading.models import TradingConfig

    if mode != "first":
        orig = provider.SessionGate.check

        def relaxed(self, today_d, fp, force=False):   # noqa: ARG001 - 模拟①改良
            m = self.read()
            if m is None:
                raise provider.SessionGateError("GATE: 无 state 不许同日重跑")
            if m.get("fingerprint") != fp:
                raise provider.SessionGateError("GATE: fingerprint 漂移")
            return {"allowed": True, "prior": m.get("last_day"), "resume": True}
        provider.SessionGate.check = relaxed
        del orig

    if mode == "idem" and Path(executed).exists():
        done = set(json.loads(Path(executed).read_text(encoding="utf-8")))
        plan = [p for p in plan if f"{str(p['symbol'])[:6]}:{p['volume']}" not in done]

    res = rn.run_session(today=TODAY, data_db=str(FIXTURE), bundle=BUNDLE,
                         symbols=["000792", "000651"], plan=plan,
                         init_cash=100_000.0, state_dir=state_dir,
                         fee=TradingConfig(), require_today_reference=False)
    ev = res.get("evidence") or {}
    trades = ev.get("trades") or []
    if Path(executed).exists() or mode in ("first", "resume"):
        prev = set(json.loads(Path(executed).read_text(encoding="utf-8"))) \
            if Path(executed).exists() else set()
        prev |= {f"{str(t['symbol'])[:6]}:{t['volume']}" for t in trades}
        Path(executed).write_text(json.dumps(sorted(prev)), encoding="utf-8")
    snaps = res.get("snapshots") or []
    last = snaps[-1] if snaps else None
    return {"mode": mode, "error": res.get("error"), "plan": plan,
            "trades": trades, "cost": ev.get("cost"),
            "event_counts": ev.get("event_counts"),
            "orders": res.get("orders"), "n_handler_calls": len(res.get("handler_dates") or []),
            "cash_end": (last or {}).get("cash"), "total_value_end": (last or {}).get("total_value"),
            "positions_end": (last or {}).get("positions"),
            "gates_ok": (res.get("gates") or {}).get("ok"),
            "gates_errors": (res.get("gates") or {}).get("errors"),
            "marked_done": res.get("session_marked_done")}


if __name__ == "__main__":
    sys.exit(main())
