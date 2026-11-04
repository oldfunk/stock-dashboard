"""新线落账（T47-G1）+ 收盘 NAV（G2）+ 执行底座留痕（G3 的一部分）。

为什么单独一个模块而不是塞进 `runner.py`：`runner` 只管"跑一个 rqalpha 会话"，
碰账本的动作必须**默认关着**（M3 之前 `rq day` 只是影子/演练），
所以它需要自己的边界与自己的测试。

三条硬规矩：
1. **不重判**：价格/数量/费用一律用框架返回的数值（L1 数值红线），
   本模块只做"写进账本"和"写之前的一致性守卫"；
2. **不猜费用拆分**：佣金/印花税/过户费必须来自成交明细的三分量，
   缺任何一列就**拒绝落账**（猜出来的费用拆分是账本腐烂的经典起点）；
3. **幂等**：同一 `biz_id`（会话指纹 + 日 + 成交序号）不重复落账——
   `rq day` 被重跑、或运维手工补跑时，重复落账等于凭空多出成交。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from paper_trading.broker.paper_broker import LedgerConsistencyError
from paper_trading.utils import get_logger

from .store import to_symbol

logger = get_logger(__name__)

#: 成交落账的幂等标记前缀（写在 fills/order 之外，用 op_log 的 params 承载）
BIZ_PREFIX = "rq-fill"
NAV_BIZ_PREFIX = "rq-nav"


def _already_posted(broker, action: str, biz_id: str) -> bool:
    return broker.has_op_with(action, biz_id)


def trade_fee_split(t: dict) -> Optional[dict]:
    """把 rqalpha 成交明细拆成账本的三列费用；拆不出来就返回 None（调用方拒落）。

    rqalpha 的 `Trade` 有 `commission` 与 `tax`（印花税，卖出单边），
    我们的过户费在 `other_fees` 里（T38 的 overlay）——
    `cost` 只是三者之和，落账必须用分量，不能用和去猜分量。
    """
    if not all(k in t for k in ("commission", "tax", "other_fees")):
        return None
    return {"commission": float(t.get("commission") or 0.0),
            "stamp_duty": float(t.get("tax") or 0.0),
            "transfer_fee": float(t.get("other_fees") or 0.0)}


def post_session(broker, evidence: dict, *, today: str, fp: str = "",
                 dry_run: bool = False) -> dict:
    """把一次 rqalpha 会话的成交落到现网账本（G1）。

    返回 `{posted, skipped, errors, ok}`。两道fail-loud：
    * 任何一笔**缺费用分量** ⇒ 整批零写入（半本账最难归因）；
    * 守卫失败（钱/仓不够）⇒ **停在这一笔**，后面的笔不写。
    """
    trades = (evidence or {}).get("trades") or []
    posted, skipped, errors = [], [], []
    # 先整批验费用拆分：任何一笔缺分量就说明这份 evidence 不是当前格式
    # （旧会话产物/被截断），此时只落"能算清的那几笔"会得到半本账 ⇒ 整批拒绝。
    splits = []
    for i, t in enumerate(trades):
        split = trade_fee_split(t)
        if split is None:
            errors.append({"why": "fee_split_missing", "index": i, "trade": t})
        splits.append(split)
    if errors:
        logger.error("会话成交缺费用分量，整批拒绝落账：%d 笔", len(errors))
        return {"posted": [], "skipped": [], "errors": errors,
                "ok": False, "today": today}
    for i, t in enumerate(trades):
        biz_id = f"{BIZ_PREFIX}:{fp or today}:{i}:{t.get('symbol')}:{t.get('volume')}"
        if _already_posted(broker, "rq:fill", biz_id):
            skipped.append({"biz_id": biz_id, "why": "already_posted"})
            continue
        if dry_run:
            posted.append({"biz_id": biz_id, "dry_run": True, **splits[i]})
            continue
        try:
            order = broker.apply_external_fill(
                symbol=to_symbol(str(t.get("symbol"))),
                direction=1 if str(t.get("side")).upper() == "BUY" else -1,
                volume=int(t.get("volume") or 0), price=float(t.get("price") or 0.0),
                **splits[i])
        except LedgerConsistencyError as exc:
            errors.append({"why": "ledger_inconsistent", "biz_id": biz_id,
                           "error": str(exc)})
            break                      # 守卫失败就停，不继续写后面的笔
        # `side` 必须带出来：`rq tick` 靠 (标的, 方向, 数量) 逐笔认领成交，
        # 少了方向就把"买 100"错配给同标的的"卖 100"意图（M3 接管②）
        posted.append({"biz_id": biz_id, "order_id": order.order_id,
                       "symbol": order.symbol, "volume": order.filled_volume,
                       "price": order.filled_price,
                       "side": "BUY" if order.direction == 1 else "SELL"})
        # `date` 放 result：`has_operation` 的跨日补记判重认的是 result 里的业务日
        # （Issue #2 的教训——只按 timestamp 匹配，补跑昨天的会话永远查不到旧行）
        broker.log_operation("rq:fill", {"biz_id": biz_id, "session": fp[:16]}, True,
                             {"date": today, "order_id": order.order_id},
                             None, None)
    if not dry_run:
        for r in (evidence or {}).get("rejects") or []:
            broker.log_operation("rq:reject", r, False, r, None, None)
    return {"posted": posted, "skipped": skipped, "errors": errors,
            "ok": not errors, "today": today}


def _nav_history_has_today(broker, today: str) -> bool:
    """nav_history 当天是否已有行（RunDaily 等无 op_log 的写入路径的让位依据）。

    查不到按"没有"处理（宁可多记一行，也不让面板当天断供），但**必须吭一声**：
    这处 fail-open 直接决定同日会不会写进行 NAV，静默就等于让位失败没人知道
    （T69 审查点名的一条）。
    """
    try:
        import sqlite3
        db = getattr(broker, "db_path", None)
        if not db:
            return False
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            n = c.execute("SELECT COUNT(*) FROM nav_history WHERE timestamp LIKE ?",
                          (f"{today}%",)).fetchone()[0]
            return n > 0
        finally:
            c.close()
    except Exception as exc:            # noqa: BLE001 - 查不到按"没有"处理（宁可多记不可断供）
        logger.warning("nav_history 当日行检查失败（按\"没有\"处理，可能多记一行 NAV）："
                       "%s: %s", type(exc).__name__, exc)
        return False


def record_close_nav(broker, prices: dict, *, today: str, fp: str = "") -> dict:
    """收盘 NAV（G2）：业务日判重、**当天没有成交也要记**，面板净值不断供。

    金额算法**不在这里重算**：调既有的 `broker.get_nav()`，与旧线 `nav:close`
    共用一份公式（pnl 对 initial_cash、市值按 `total_volume` 加权）——NAV 序列
    在切换日前后必须是同一把尺子，否则净值曲线在切换点凭空跳一格。

    判重认**业务日**而不是写入时刻：双轨期间旧线已经记过当天的 `nav:close`，
    新线再记一次就是同一业务日两行 NAV（面板与 `m3 verify` 的 nav 项都会失真）。
    """
    cash = float(broker.get_cash())
    positions = {p.symbol: p for p in broker.get_all_positions()}
    missing = [s for s in positions if not prices.get(s)]
    if missing:
        return {"recorded": False, "reason": "price_missing",
                "symbols": sorted(missing)[:10]}
    # 让位检查三条：op_log 的 rq-nav / 旧线 nav:close / **nav_history 当天已有行**
    # （2026-11-04 现网双写事故：RunDaily 每日首笔直接写 nav_history 且不留
    # op_log ⇒ 前两条看不见它，rq-nav 让位失败，同日 2 行 NAV 触发 m3 verify 红）
    if broker.has_operation(NAV_BIZ_PREFIX, today) or \
            broker.has_operation("nav:close", today) or \
            _nav_history_has_today(broker, today):
        return {"recorded": False, "reason": "already_recorded", "today": today}
    snap = broker.get_nav(prices)
    broker.record_nav(snap)
    broker.log_operation(NAV_BIZ_PREFIX, {"session": (fp or "")[:16]}, True,
                         {"date": today, "total_value": snap.total_value},
                         cash, snap.total_value)
    return {"recorded": True, "today": today, "cash": snap.cash,
            "market_value": snap.market_value, "total_value": snap.total_value}


def close_prices(db, symbols, today: str) -> dict:
    """从 raw 表取每只标的当日（或最近一根）收盘价，给 NAV 用。

    只认 `adjust='raw'` 的口径——NAV 用复权价算市值是另一类尺度污染的入口。
    """
    out = {}
    for s in symbols or []:
        sym = to_symbol(str(s)) if "." in str(s) else str(s).strip().zfill(6)
        rows = db.get_raw_bars(sym, limit=1, end=f"{today}T23:59:59")
        if rows:
            out[sym] = float(rows[-1].close)
    return out


def executor_from_config(cfg) -> str:
    """读执行底座（G3）。只认 `legacy|rqalpha`，别的值直接抛——静默降级到旧线是事故。"""
    raw = str((cfg.get("executor") if isinstance(cfg, dict)
               else getattr(cfg, "executor", None)) or "legacy").strip().lower()
    if raw not in ("legacy", "rqalpha"):
        raise ValueError(f"engine.executor 只能是 legacy|rqalpha，收到 {raw!r}")
    return raw


def startup_guard(cfg, *, rqalpha_available: bool) -> tuple[str, Optional[str]]:
    """启动时把底座解析成合法值并裁决"能不能启动"（runbook §5.2）。返回 `(executor, error)`。

    两条都不许静默：未知值直接报错；`rqalpha` 底座的可执行性按**分层架构**判——
    engine 进程（交易 venv）在 rqalpha 底座下只做"判定+意图入队"（queue 模块，
    纯 sqlite，不需要 rqalpha），**执行由 `rq tick`（引擎 venv）承担**。所以
    守卫不再要求 engine 进程自己装 rqalpha（T52 后架构如此）；但意图队列必须
    可写，否则"判定完的意图无处去"= 比静默退回旧线更糟的静默丢失。
    """
    try:
        ex = executor_from_config(cfg)
    except ValueError as exc:
        return "legacy", str(exc)
    if ex == "rqalpha":
        try:
            import sqlite3 as _sq
            from paper_trading.integration.rq_engine import queue as _q
            db = cfg.get("account_db_path") or "paper_account.db"
            _q._conn(db).close()               # noqa: SLF001 - 建表即验证可写
        except Exception as exc:                # noqa: BLE001
            return ex, ("engine.executor=rqalpha，但意图队列不可用："
                        f"{type(exc).__name__}: {exc}（拒绝启动——判定完的意图"
                        "无处去比静默退回旧线更糟）")
    return ex, None


def trace_switch(broker, executor: str, today: str, *, reason: str = "") -> bool:
    """底座切换留痕：同一天同一取值只记一行（面板/审计要知道"今天是谁在下单"）。"""
    biz = f"m3:switch:{today}:{executor}"
    if _already_posted(broker, "m3:switch", biz):
        return False
    broker.log_operation("m3:switch", {"biz_id": biz, "executor": executor,
                                        "date": today, "reason": reason}, True,
                         {"executor": executor}, None, None)
    return True


# ---------------------------------------------------------------- G4 归档/新开账
def _table_rows(conn, table: str) -> int:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone():
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def archive_and_reopen(account_db, archive_dir, *, stamp: Optional[str] = None,
                       allow_existing: bool = False,
                       state_root: str = "var/rq_state") -> dict:
    """冷备旧账 → 只读归档 → 原地新开一本并把起点对齐（T47-G4）。

    六条不能含糊的规矩：
    1. **先备份再动**目标文件：用 sqlite backup API（不是 `cp`）——`cp` 可能拷到
       事务中途，产出一份打不开的"归档"；备份按 `chmod 444` 只读落盘；
    2. **有人在写就拒绝**：`<db>-journal`/`-wal` 存在说明事务正进行中，这时删文件
       等于和 cron 抢一本账 ⇒ 直接抛错（宁可运维等一分钟）；
    3. **不合并两本账**：新账只按归档的 cash + 当前持仓重建起点
       （旧线 `avg_cost` 含费、rqalpha `avg_price` 不含费，跨账合并必错，
       见 migration-m3 §5.1 ③）；旧 NAV 序列**不搬**，切换后第一行净值由
       当日收盘路径正常生成；
    4. **T+1 冻结跟着搬**：只搬 `positions.available_volume` 而不搬未解冻的
       `t1_freeze` 行，那笔冻结股就再也没有解冻入口（`unfreeze_t1` 按 t1_freeze 走）
       ——切换日刚买的票会永久卖不掉；
    5. **幂等可重入**：归档已存在不重复备份（同一 `--stamp` 重跑复用同份归档）；
       目标账本已有持仓/成交且没给 `allow_existing` 就直接拒绝。
    6. **重开不等于状态干净**：KV 运行态随文件一起没了，但 `var/rq_state/<日>`
       会话目录不在库里，删账本碰不到它——`rq tick` 的 intraday_resume 会把旧账的
       portfolio 快照接进新账（钱类残留）。所以本命令只把它**报出来**
       （`reset_state_pending`），清理交给显式的 `ledger reset-state`（T62）。

    返回 `{archive, archive_rows, reopened, cash, positions, frozen, total_value,
    reset_state_pending, reset_state_reports, note}`。
    """
    src = Path(str(account_db))
    if not src.exists():
        raise FileNotFoundError(f"账本不存在：{src}")
    for sidecar in (src.with_name(src.name + "-journal"), src.with_name(src.name + "-wal")):
        if sidecar.exists():
            raise RuntimeError(
                f"检测到 {sidecar.name}：账本正被写入（cron/面板？），"
                f"此刻重开会和写入方抢同一本账。请停掉写入方或等事务结束后再跑。")
    adir = Path(str(archive_dir))
    adir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = adir / f"{src.stem}.pre-m3-{stamp}.db"

    if archive.exists():
        archived = {"reused": True}
    else:
        # `closing()` 是必需的：`with sqlite3.connect(...)` 只提交事务、**不关句柄**，
        # Windows 上残留句柄会让下面的 `src.unlink()` 直接 PermissionError（实测）。
        with closing(sqlite3.connect(f"file:{src}?mode=ro", uri=True)) as s, \
                closing(sqlite3.connect(str(archive))) as d:
            s.backup(d)
        os.chmod(archive, 0o444)
        archived = {"reused": False}

    with closing(sqlite3.connect(f"file:{archive}?mode=ro", uri=True)) as a:
        a.row_factory = sqlite3.Row
        def has(name: str) -> bool:
            return bool(a.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,)).fetchone())

        cash_row = (a.execute("SELECT cash FROM account WHERE id=1").fetchone()
                    if has("account") else None)
        cash = float(cash_row["cash"]) if cash_row else None
        last_nav = dict(a.execute(
            "SELECT timestamp, total_value FROM nav_history ORDER BY timestamp DESC LIMIT 1"
        ).fetchone() or {}) if has("nav_history") else {}
        positions = [dict(r) for r in a.execute(
            "SELECT symbol, total_volume, available_volume, avg_cost, entry_day, peak_price "
            "FROM positions WHERE total_volume > 0 ORDER BY symbol")] if has("positions") else []
        frozen = [dict(r) for r in a.execute(
            "SELECT symbol, volume, freeze_date, unfreeze_date FROM t1_freeze "
            "WHERE is_unfrozen = 0 ORDER BY id")] if has("t1_freeze") else []
        archive_rows = {t: _table_rows(a, t) for t in ("orders", "fills", "nav_history",
                                                        "positions", "op_log")}

    if cash is None:
        raise RuntimeError(f"归档账里没有 account 行，无法对齐起点：{archive}")

    with closing(sqlite3.connect(str(src))) as c:
        rows_before = _table_rows(c, "positions") + _table_rows(c, "fills")
    if rows_before and not allow_existing:
        raise RuntimeError(
            f"目标账本 {src} 已有 {rows_before} 行持仓/成交，拒绝原地重开（会抹掉现网账）。"
            f"确认要重开请显式给 --allow-existing；归档副本在 {archive}（只读）。")

    # 重开前再落一份"即将被删"的现场副本：归档是 backup API 的产物，这份是原文件本身
    shutil.copy2(src, src.with_name(src.name + f".pre-reopen-{stamp}"))
    src.unlink()
    from paper_trading.broker.paper_broker import PaperBroker
    from paper_trading.models import TradingConfig

    broker = PaperBroker(db_path=str(src), config=TradingConfig(initial_cash=float(cash)))
    with broker._connect() as conn:                       # noqa: SLF001 - 同库重建起点
        conn.execute("UPDATE account SET cash = ?, initial_cash = ? WHERE id = 1",
                     (float(cash), float(cash)))
        for p in positions:
            conn.execute(
                """INSERT INTO positions (symbol, total_volume, available_volume,
                       avg_cost, last_update, entry_day, peak_price)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (p["symbol"], int(p["total_volume"]), int(p["available_volume"] or 0),
                 float(p["avg_cost"] or 0.0), datetime.now().isoformat(),
                 p.get("entry_day"), float(p.get("peak_price") or 0.0)))
        for f in frozen:
            conn.execute(
                """INSERT INTO t1_freeze (symbol, volume, freeze_date, unfreeze_date,
                       is_unfrozen) VALUES (?, ?, ?, ?, 0)""",
                (f["symbol"], int(f["volume"]), str(f["freeze_date"]),
                 str(f["unfreeze_date"])))
    mv = sum(float(p["total_volume"]) * float(p["avg_cost"]) for p in positions)
    total = float(last_nav.get("total_value") or (float(cash) + mv))
    # T62：重开账本抹不掉的东西只有会话状态目录（KV 态随文件一起没了）。
    # 这里只**报**，不代 operator 决定——`rq tick` 的 intraday_resume 会把旧账的
    # portfolio 快照接进新账，那是钱类残留，必须让下一步（reset-state）显式发生。
    from paper_trading import ledger_state as _state

    residue = _state.plan(src, state_root=state_root)
    broker.log_operation("ledger:archive",
                         {"archive": str(archive), "stamp": stamp,
                          "readonly": True, "reopen": True,
                          "allow_existing": bool(allow_existing)},
                         True,
                         {"cash": float(cash), "positions": len(positions),
                          "frozen_rows": len(frozen), "total_value": round(total, 4),
                          "nav_anchor": str(last_nav.get("timestamp")),
                          "archive_rows": archive_rows,
                          "reset_state_pending": residue["pending"],
                          "reset_state_reports": residue["red_reports"]},
                         float(cash), total)
    return {"archive": str(archive), "archive_reused": archived["reused"],
            "archive_rows": archive_rows, "reopened": True, "cash": float(cash),
            "positions": len(positions), "frozen": len(frozen),
            "total_value": round(total, 4),
            "reset_state_pending": residue["pending"],
            "reset_state_reports": residue["red_reports"],
            "note": "旧账只读归档；新账起点=归档 cash + 持仓 + 未解冻的 T+1；"
                    "NAV 序列不搬，第一行由切换日收盘正常生成"}
