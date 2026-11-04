"""原始价（不复权）日线管道：采集 → 独立源对照 → 落库 → 新鲜度闸。

T31（M2 数据侧，P0）。为什么需要这个模块：`daily_bars` 一直是**前复权**
（`akshare_fetcher.py` 默认 `adjust="qfq"`），而 rqalpha 的 DayBarStore 契约要
**原始价**，它自己会再按 `ex_cum_factor` 复权一次（`base_data_source/adjust.py`）。
把 qfq 当 raw 喂 = 双重计入 = D3 裁决否掉的 route A（实测 2,802.42 元），
量级见 `docs/worker/report-t13.md` §4.1：27,420 个可比标量-日里 84.3% 偏差 >0.5%。

四条硬规矩（AGENTS 纪律 3/4 + G-T13-1/2；§5.5 裁决 2 = A+B 同做）：
1. **口径标签**：写进 `daily_bars_raw` 的行必须 `adjust='raw'`，否则 `upsert_raw_bars` 拒写；
2. **独立对照（历史）**：每行与母库 `close_raw` 同日比对，超容差 → **整只拒收**（不静默降级）；
2b. **独立对照（当日，A）**：母库 `close_raw` 实测在 T 日为 NULL（2026-10-28 已到该日
   的 20 只全 NULL），故收盘后改用**腾讯实时快照**对照当日那根（`intraday_reference`）；
2c. **T+1 必须翻转（B）**：当日允许 `checked=0`，但母库补上后必须在下个交易日翻成
   已验证，否则 `raw_flip_watch()` 报 `stop_line`；
3. **新鲜度**：喂引擎前先问 `raw_freshness()`——缺最新交易日就是缺数据，
   引擎按"缺数据不下单"处理，而不是拿旧价凑数。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Optional

from paper_trading.models import Bar
from paper_trading.utils import get_logger

logger = get_logger(__name__)

#: raw 对 raw 的同日收盘容差。两条独立源实测**逐分一致**
#: （000792 2026-10-26：akshare 22.24 / 母库 close_raw 22.24，volume/amount 也相同），
#: 所以这里可以比 qfq 跨源闸（10%）严得多；0.5% 仍远高于 0.01 元的最小变动位，
#: 而尺度污染事故的那类错误是 6.4×，一律落网。
RAW_CLOSE_TOLERANCE = 0.005
#: 绝对量级闸（沿用 H-7 思路：A 股真实股价 <10 万元/股）
RAW_ABS_MAX = 100_000.0
#: 允许的最大滞后（交易日数）：>此值即视为陈旧
MAX_LAG_TRADING_DAYS = 1
#: 采集窗口的自然日数换算：`days` 是"交易日行数"的口语参数，实际拉取跨度按
#: `days*2 + 10` 自然日算（停牌/长假要留余量）。T41 把它收成一个常量，
#: 因为参照窗口和审计窗口必须用**同一个**算式，否则又是一处"各算各的"。
FETCH_CAL_SPAN_MULT = 2
FETCH_CAL_SPAN_PAD = 10


def fetch_window(days: int, today: Optional[date] = None) -> tuple[date, date]:
    """采集/参照/审计三处共用的日期窗口（含端点）。

    T39 实测的漏验就来自这里：采集是 `days*2+10` **自然日**，
    而母库参照是 `LIMIT days` **交易日** ⇒ 老的一半天生没参照（590 行只验 400）。
    """
    end = _as_date(today or date.today())
    start = end - timedelta(days=int(days) * FETCH_CAL_SPAN_MULT + FETCH_CAL_SPAN_PAD)
    return start, end


def _no_reference(symbols, days: int = 0, *args, **kwargs) -> dict:
    """显式"不读母库"的参照提供者（`--no-mother-ref` 用它，T41 裁决 3）。"""
    return {}


#: 传给 `sync_raw(reference=...)` 表示"这一轮就是单源，别回退去读母库"
NO_REFERENCE = _no_reference


def reference_for(no_mother_ref: bool):
    """CLI 旗子 → 参照提供者的唯一映射（T41 裁决 3）。

    放在这里而不是 cli.py，是因为旧 bug 就长在那段接线里：
    `--no-mother-ref` 时留 `reference=None`，而 `None` 的语义是"用默认（读母库）"，
    旗子等于没拉。现在两边都只认这一个函数。
    """
    return NO_REFERENCE if no_mother_ref else None


def _ref_date_set(refs: dict) -> dict:
    """`{sym: {有 close_raw 的日期}}`——审计时"参照到底存不存在"的唯一依据。

    母库当日的 `close_raw` 实测是 NULL（§5.5 裁决 2 的 A 半边就是为此存在的），
    所以**只有非 NULL 的日期才算有参照**，不然 `no_reference` 会被误判成
    `unflipped`，把母库自己的滞后算成我们的锅。
    """
    out = {}
    for sym, m in (refs or {}).items():
        out[str(sym)] = {str(d)[:10] for d, v in (m or {}).items()
                         if isinstance(v, dict) and v.get("close_raw")}
    return out


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:10]).date()


def check_raw_bars(bars: list[Bar], reference: Optional[dict] = None,
                   tolerance: float = RAW_CLOSE_TOLERANCE, *,
                   intraday: Optional[dict] = None, day: Optional[str] = None,
                   enforce_intraday: bool = False) -> dict:
    """把待写入的 raw bars 与**独立源**同日对照；返回裁决。

    `reference` = 母库 `{date: {"close_raw": x, ...}}`（历史主参照）。
    `intraday`   = `{price, date}` 腾讯实时快照（**当日**唯一可用的独立参照——
    母库 `close_raw` 实测在 T 日为 NULL：2026-10-28 已到该日的 20 只全是 NULL）。
    `enforce_intraday=True`（收盘后同步当日）时，缺参照或偏差 > 容差都算分歧：
    缺参照 → `no_intraday` 计数并判 `ok=False`？——**不**，缺参照不判死，
    由 `raw_flip_watch()` 在 T+1 未翻转时停线（§5.5 裁决 2 的 A+B 组合）。
    """
    ref = reference or {}
    mismatches: list[dict] = []
    verified: set = set()
    no_ref = 0
    no_intraday = 0
    intraday_checked = 0
    absurd: list[dict] = []
    for b in bars:
        if not (0.0 < float(b.close) <= RAW_ABS_MAX):
            absurd.append({"date": _as_date(b.timestamp).isoformat(),
                           "close": b.close, "why": "abs-price"})
            continue
        d = _as_date(b.timestamp).isoformat()
        if enforce_intraday and day and d == day:
            px = float((intraday or {}).get("price") or 0.0)
            if px <= 0 or (intraday or {}).get("date") != day:
                no_intraday += 1
            else:
                ratio_i = float(b.close) / px
                if abs(ratio_i - 1.0) > tolerance:
                    mismatches.append({"date": d, "akshare_close": round(float(b.close), 4),
                                       "intraday_price": round(px, 4),
                                       "ratio": round(ratio_i, 4), "why": "intraday"})
                else:
                    intraday_checked += 1
                    verified.add(d)
            continue
        r = ref.get(d)
        if not r or not r.get("close_raw"):
            no_ref += 1
            continue
        try:
            ratio = float(b.close) / float(r["close_raw"])
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = float("nan")
        if abs(ratio - 1.0) > tolerance:
            mismatches.append({"date": d,
                               "akshare_close": round(float(b.close), 4),
                               "mother_close_raw": round(float(r["close_raw"]), 4),
                               "ratio": round(ratio, 4)})
        else:
            verified.add(d)
    return {"ok": not mismatches and not absurd,
            "bars": len(bars), "no_reference": no_ref,
            "checked": len(verified), "verified_dates": verified,
            "mismatches": mismatches, "absurd": absurd,
            "no_intraday": no_intraday, "intraday_checked": intraday_checked}


def sync_symbol(symbol: str, bars: list[Bar], verdict: dict, db,
                source: str = "akshare:sina:raw") -> dict:
    """按裁决落库：干净才写（对照过的那批 checked=1），有分歧整只拒收。"""
    if not bars:
        return {"symbol": symbol, "written": 0, "status": "empty"}
    if not verdict["ok"]:
        logger.warning("raw 口径分歧，拒收 %s：%s", symbol, verdict["mismatches"][:3])
        return {"symbol": symbol, "written": 0, "status": "rejected",
                "mismatches": verdict["mismatches"], "absurd": verdict["absurd"]}
    n = db.upsert_raw_bars(bars, source=source,
                           checked_dates=verdict["verified_dates"])
    return {"symbol": symbol, "written": n, "status": "ok",
            "checked": verdict["checked"], "no_reference": verdict["no_reference"],
            "intraday_checked": verdict.get("intraday_checked", 0),
            "no_intraday": verdict.get("no_intraday", 0)}


def session_closed(now: Optional[datetime] = None, *, grace_min: int = 5) -> bool:
    """收盘判定：A 股 15:00 收盘 + 缓冲，**按北京时间算**。

    生产机可能是 UTC/其它时区（实测 NEC 跑批时本地钟还没到 15:05，
    用机器本地时间会漏掉当日跨源对照），所以：
    - **不传 `now`** ⇒ 取 UTC 现在再折算北京时间（与时区无关，这是生产路径）；
    - 传**无时区**的 `now` ⇒ 视为北京时间墙钟（测试/手工指定用）；
    - 传**带时区**的 `now` ⇒ 折算到北京时间。
    """
    from datetime import timezone

    CN = timezone(timedelta(hours=8))
    if now is None:
        cn = datetime.now(timezone.utc).astimezone(CN)
    elif now.tzinfo is None:
        cn = now
    else:
        cn = now.astimezone(CN)
    return cn.hour * 60 + cn.minute >= 15 * 60 + grace_min


def intraday_reference(symbols, *, quotes: Optional[dict] = None,
                       get_quotes=None) -> dict:
    """当日独立参照：腾讯实时快照（与 akshare/新浪**不同源**，符合独立基准原则）。

    T50-A：这条路径**就是**给盘后用的（母库 T 日 `close_raw` 是 NULL），所以默认
    入口带 `allow_closing=True`——否则 15:00 以后 `get_quotes` 直接回空，
    "当日跨源对照"永远拿不到参照（report-t42 §4 的实测）。
    注入 `get_quotes`/`quotes` 的调用方（测试、其它口径）保持原契约，不受影响。
    """
    if quotes is None:
        fn = get_quotes
        if fn is None:
            def fn(codes):
                from paper_trading.data.realtime import get_quotes as _gq
                return _gq([str(c) for c in codes], allow_closing=True)
        try:
            quotes = fn([str(s) for s in symbols]) or {}
        except Exception:                   # noqa: BLE001
            return {}
    out = {}
    for code, q in (quotes or {}).items():
        sym = str(code).strip()[-6:].zfill(6)
        try:
            px = float((q or {}).get("price") or 0.0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            out[sym] = {"price": px, "date": str((q or {}).get("date") or "")}
    return out


def raw_flip_watch(db, symbols, today: Optional[date] = None,
                   max_lag: int = MAX_LAG_TRADING_DAYS,
                   ref_asof: Optional[dict] = None,
                   ref_days: Optional[dict] = None,
                   since: Optional[date] = None,
                   days: Optional[int] = None) -> dict:
    """G-T13-1 增补判据（§5.5 裁决 2-B）：**当日允许 checked=0，T+1 必须翻转，否则停线**。

    T41 起改成**逐日台账**：采集窗口里的每一个交易日都必须落到下面四类之一，
    否则就是"没人对它负责"——旧实现只看 `MAX(timestamp) WHERE checked=1` 距今
    几个交易日，中段一批从未验料的行既不动最新日期也不动最后 checked 日期，
    于是 `data raw-coverage --gate` 会对着 190/590 行单源数据报 `violations=[]`
    （report-t39 §6-B 的盲区，实证）。

    四态（停不停线分开，"验料源自己滞后"不许永远停我们的线）：
    - `verified`                  该行 `checked=1`；
    - `today_pending`             就是今天那一根 ⇒ §5.5 明文允许，**不停线**；
    - `waiting_reference`         母库还没出到该日（晚于母库最新日）⇒ 告警不停线；
    - `no_reference_available`    母库出了更晚的日子却没有这一天（停牌/母库自身缺口）
      ⇒ 告警，但**显式可见**，不再是静默的 `checked=0`；
    - `unflipped`                 参照已有而我们没重跑对照 ⇒ **停线**（我们的锅）。
    另有 `raw_stale` / `no_raw_data`（喂料本身断了）与
    `reference_source_unavailable`（参照源整条没回来 + 窗口内有未验行）⇒ 停线。

    `ref_days`（`{sym: {有 close_raw 的日期}}`）缺省时逐日存在性未知，未验料的行
    进 `audit_incomplete` 并**告警**——宁可显式说"这一轮审计不完整"，
    也不靠猜把母库的滞后算成我们的失败，反过来也不给静默放行。
    """
    today_d = _as_date(today or date.today())
    ref_asof = ref_asof or {}
    ref_days = ref_days or {}
    if since is None:
        since = fetch_window(days if days else 30, today_d)[0]
    since_d = _as_date(since)
    out = {}
    for sym in symbols:
        sym = str(sym).strip().zfill(6)
        last_bar = db.raw_latest(sym)
        last_ck = db.raw_latest_checked(sym)
        if not last_bar:
            out[sym] = {"flip_ok": False, "stop": True, "reason": "no_raw_data",
                        "bar_last": None, "checked_last": None, "checked_lag": None,
                        "daily": None}
            continue
        fresh = raw_freshness(db, [sym], today=today_d, max_lag=max_lag)[sym]
        ck_lag = None
        if last_ck:
            from paper_trading.utils.trading_calendar import is_trading_day
            d, n = _as_date(last_ck), 0
            while d < today_d and n < 400:
                d += timedelta(days=1)
                if is_trading_day(d):
                    n += 1
            ck_lag = n
        bar_last = fresh.get("last")
        ref_day = str(ref_asof.get(sym) or "")[:10]
        waiting = bool(ref_day) and bool(bar_last) and bar_last > ref_day

        # ---- 逐日台账 ----
        flags = db.raw_date_flags(sym, since=since_d.isoformat(),
                                  until=today_d.isoformat())
        have = ref_days.get(sym)
        daily = {"audited": len(flags), "verified": 0, "today_pending": [],
                 "waiting_reference": [], "no_reference_available": [],
                 "unflipped": [], "audit_incomplete": []}
        for ds, ck in sorted(flags.items()):
            if ck:
                daily["verified"] += 1
                continue
            if ds == today_d.isoformat():
                daily["today_pending"].append(ds)        # §5.5：当日允许未翻转
            elif have is not None and ds in have:
                daily["unflipped"].append(ds)            # 参照明明有 ⇒ 我们的锅
            elif ref_day and ds > ref_day:
                daily["waiting_reference"].append(ds)    # 母库还没出到该日
            elif have is None:
                # 参照存在性未知（母库没回料 / 调用方没给 ref_days）：
                # 既不能算我们没跑（可能是母库自己的缺口），也不能静默放行
                daily["audit_incomplete"].append(ds)
            else:
                daily["no_reference_available"].append(ds)
        daily["audited"] = len(flags)
        daily["unflipped_days"] = len(daily["unflipped"])
        daily["waiting_days"] = len(daily["waiting_reference"])
        daily["no_reference_days"] = len(daily["no_reference_available"])
        daily["incomplete_days"] = len(daily["audit_incomplete"])
        daily["today_pending_days"] = len(daily["today_pending"])

        if fresh.get("stale"):
            reason, stop = "raw_stale", True
        elif daily["unflipped"]:
            reason, stop = "checked_not_flipped", True      # 逐日台账：参照已有却没重跑
        elif waiting and (ck_lag is None or ck_lag > max_lag):
            reason, stop = "waiting_reference", False       # 等母库，不是我们的锅
        elif not ref_day and daily["verified"] == 0 and daily["audited"] > 0:
            # 参照一条都没回来且窗口里**没有任何一行被验过**（母库不可达 / 单源模式）
            # ⇒ 停线，原因归"参照源不可用"而不是"我们没跑"：钱类数据宁可停也不单源裸奔。
            # 窗口里验到过行（verified>0）而最后 checked 落后的，仍是我们的锅
            # （走下面的 ck_lag 规则）——T34 已验收的语义只加严不放松。
            reason, stop = "reference_source_unavailable", True
        elif ck_lag is None or ck_lag > max_lag:
            reason, stop = "checked_not_flipped", True      # 老规矩一并留着（只加严不放松）
        elif daily["audit_incomplete"]:
            reason, stop = "audit_incomplete", False
        elif daily["waiting_reference"]:
            reason, stop = "waiting_reference", False
        elif daily["no_reference_available"]:
            reason, stop = "no_reference_available", False
        else:
            reason, stop = None, False
        out[sym] = {"flip_ok": not stop, "stop": stop, "reason": reason,
                    "bar_last": bar_last, "checked_last": str(last_ck)[:10] if last_ck else None,
                    "checked_lag": ck_lag, "ref_asof": ref_day or None,
                    "ref_days_known": have is not None, "daily": daily}

    def _sum(key: str) -> int:
        return sum((v["daily"] or {}).get(key, 0) for v in out.values())

    warn_syms = sorted(s for s, v in out.items()
                       if v["reason"] in ("waiting_reference", "no_reference_available",
                                          "audit_incomplete")
                       or (v["daily"] or {}).get("waiting_days")
                       or (v["daily"] or {}).get("no_reference_days")
                       or (v["daily"] or {}).get("incomplete_days"))
    return {"today": today_d.isoformat(), "max_lag_trading_days": max_lag,
            "symbols": len(out),
            "violations": sorted(s for s, v in out.items() if v["stop"]),
            "warnings": warn_syms,
            "stop_line": any(v["stop"] for v in out.values()), "per_symbol": out,
            "audit_since": since_d.isoformat(),
            "audited_days": _sum("audited"), "verified_days": _sum("verified"),
            "unflipped_days": _sum("unflipped_days"),
            "waiting_days": _sum("waiting_days"),
            "no_reference_days": _sum("no_reference_days"),
            "incomplete_days": _sum("incomplete_days"),
            "audit_complete": all((v["daily"] or {}).get("incomplete_days", 0) == 0
                                  for v in out.values() if v["daily"] is not None)}


def flip_watch_with_mother(db, symbols, today=None, days: int = 30,
                           max_lag: int = MAX_LAG_TRADING_DAYS,
                           start: Optional[date] = None,
                           end: Optional[date] = None) -> dict:
    """读母库后跑逐日闸（CLI `raw-coverage --gate` 与 `sync_raw` 共用同一口径）。

    参照窗口 = 采集窗口（`fetch_window`），单位不一致那个坑就是从这里根除的。
    `start/end`（T47 顺手项，裁决 3）指名日期区间时用区间，不给就按 `--days` 换算。
    """
    today_d = _as_date(today or date.today())
    win_start, win_end = fetch_window(days, today_d)
    start_d = _as_date(start) if start else win_start
    end_d = _as_date(end) if end else win_end
    syms = [str(s).strip().zfill(6) for s in (symbols or [])]
    refs: dict = {}
    mother_ok = True
    try:
        from paper_trading.integration import mother as _m

        raw = _m.raw_close_map(syms, since=start_d.isoformat()) or {}
        # 注意形状：raw_close_map 返回 {code: {date: row}}，这里要的是**内层** {date: row}。
        # 早先写成 `refs[s] = raw_close_map([s], ...)`（整包外层），于是
        # `max(v)` 取到的是标的代码而不是日期、`ds in have` 永远为假——
        # 真机实测把 7 只母库滞后的标的误判成 `checked_not_flipped` 停线（T41 修）。
        refs = {s: (raw.get(s) or {}) for s in syms}
    except Exception as exc:              # noqa: BLE001 - 母库不可用时显式标不完整
        logger.warning("raw 逐日闸读母库失败（%s）→ 本轮审计标记为不完整", exc)
        mother_ok = False
    return raw_flip_watch(db, syms, today=end_d, max_lag=max_lag,
                          ref_asof={s: (max(v) if v else "") for s, v in refs.items()},
                          # 母库读失败 ⇒ 参照存在性"未知"（ref_days=None）而不是"没有"，
                          # 未验料的行才会被记成 audit_incomplete 而不是假装审计完整
                          ref_days=_ref_date_set(refs) if mother_ok else None,
                          since=start_d, days=days)


def sync_raw(db, symbols, *, days: int = 30,
             start: Optional[date] = None, end: Optional[date] = None,
             fetch: Optional[Callable[[str, int], list[Bar]]] = None,
             reference: Optional[Callable[[list[str], int], dict]] = None,
             today: Optional[date] = None, now: Optional[datetime] = None,
             intraday: Optional[dict] = None,
             quotes: Optional[dict] = None) -> dict:
    """批量同步。`fetch`/`reference`/`intraday`/`quotes` 可注入（测试零网络）。

    三个窗口**必须同源**（T41 裁决 1）：采集、母库参照、逐日审计都从
    `fetch_window(days, today)` 取同一个 `start`，不再各算各的单位。
    `reference=NO_REFERENCE` 表示显式单源（`--no-mother-ref`），此时**不回退读母库**。
    """
    today_d = _as_date(today or date.today())
    win_start, win_end = fetch_window(days, today_d)
    # T47 顺手项（裁决 3）：`--days` 只是"窗口规模"的含糊参数，回填要能指名日期区间；
    # 给了 start/end 就以它为准，采集/参照/逐日闸三处仍共用这一份窗口（T41 裁决 1）。
    start_d = _as_date(start) if start else win_start
    end_d = _as_date(end) if end else win_end
    if fetch is None:
        from paper_trading.data.akshare_fetcher import AkshareFetcher

        def fetch(sym, d):
            return AkshareFetcher.fetch_daily_raw(sym, start_date=start_d.strftime("%Y%m%d"))
    symbols = [str(s).strip().zfill(6) for s in (symbols or [])]
    single_source = reference is NO_REFERENCE
    refs: dict = {}
    if reference is None:
        try:
            from paper_trading.integration import mother as _m
            refs = {c: _m.raw_close_map([c], days=days, since=start_d.isoformat()).get(c, {})
                    for c in symbols}
        except Exception as exc:              # noqa: BLE001 - 母库不可用则全部走单源
            logger.warning("母库 raw 参照不可用（%s），本轮单源写入 checked=0", exc)
            refs = {c: {} for c in symbols}
    else:
        refs = reference(symbols, days) or {}

    intraday_map: dict = {}
    # 盘中跨源对照只对"真实今天"成立：回填历史区间时没有当天的实时价可比，
    # 硬套会把整批历史行报成 intraday_missing（fail-loud 不能用在错的日子）。
    day_d = end_d if end_d < today_d else today_d
    day = day_d.isoformat()
    enforce = session_closed(now or datetime.now()) and day_d == today_d
    if enforce:
        intraday_map = (intraday if intraday is not None
                        else intraday_reference(symbols, quotes=quotes))

    results = []
    no_today_bar = 0
    for sym in symbols:
        try:
            bars = [b for b in (fetch(sym, days) or [])
                    if start_d <= _as_date(b.timestamp) <= end_d]
        except Exception as exc:              # noqa: BLE001 - 单只失败不影响整批
            results.append({"symbol": sym, "written": 0, "status": "fetch_error",
                            "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        if enforce and not any(_as_date(b.timestamp).isoformat() == day for b in bars):
            # 源还没出今天那根日K（新浪收盘后延迟发布）⇒ 跨源对照"没做"必须可见，
            # 不能报成 0/0 让人以为已经校过
            no_today_bar += 1
        verdict = check_raw_bars(bars, refs.get(sym) or {}, day=day,
                                 intraday=intraday_map.get(sym),
                                 enforce_intraday=enforce)
        results.append(sync_symbol(sym, bars, verdict, db))
    written = sum(r["written"] for r in results)
    ref_asof = {s: (max(v) if v else "") for s, v in (refs or {}).items()}
    flip = raw_flip_watch(db, symbols, today=end_d, ref_asof=ref_asof,
                          ref_days=None if single_source else _ref_date_set(refs),
                          since=start_d, days=days)
    return {"today": day, "requested": len(symbols),
            "written": written, "results": results,
            "ref_window": {"start": start_d.isoformat(), "end": end_d.isoformat(),
                           "single_source": bool(single_source),
                           "symbols_with_ref": sum(1 for v in refs.values() if v)},
            "intraday_enforced": enforce,
            "intraday_missing": sum(r.get("no_intraday") or 0 for r in results),
            "intraday_checked": sum(r.get("intraday_checked") or 0 for r in results),
            "intraday_no_today_bar": no_today_bar,
            "flip_watch": {k: v for k, v in flip.items() if k != "per_symbol"},
            "rejected": [r["symbol"] for r in results if r["status"] == "rejected"]}


def today_reference_state(db, symbol: str,
                          today: Optional[date] = None) -> dict:
    """**今天这根 bar 有没有独立参照**（T50-C 的判据，喂引擎前必须问一次）。

    三态：
    - `checked`     今天这根已同日对照过（母库或盘后收盘快照）⇒ 可以喂引擎；
    - `unchecked`   今天这根在库里但 `checked=0` ⇒ **不许喂**（fail-closed）；
    - `no_today_bar` 今天这根还没采到 ⇒ 不由本判据管（新鲜度闸 `raw_freshness`
      会把它当陈旧处理），所以这里只报状态、不做二次停线。
    """
    today_d = _as_date(today or date.today())
    d = today_d.isoformat()
    flags = db.raw_date_flags(str(symbol), since=d, until=d)
    if not flags:
        return {"state": "no_today_bar", "date": d}
    return {"state": "checked" if flags.get(d) else "unchecked", "date": d}


def raw_freshness(db, symbols, today: Optional[date] = None,
                  max_lag: int = MAX_LAG_TRADING_DAYS) -> dict:
    """每只标的 raw 数据的陈旧度（G-T13-2 的本地半边）。

    落后 > `max_lag` 个交易日即 `stale=True`——引擎侧据此拒单，而不是拿旧 bar 凑。
    """
    from paper_trading.utils.trading_calendar import is_trading_day

    today = _as_date(today or date.today())
    out = {}
    for sym in symbols:
        last = db.raw_latest(sym)
        if not last:
            out[sym] = {"last": None, "lag": None, "stale": True, "reason": "no_raw_data"}
            continue
        last_d = _as_date(last)
        lag, d = 0, last_d
        while d < today:
            d += timedelta(days=1)
            if is_trading_day(d):
                lag += 1
        out[sym] = {"last": last_d.isoformat(), "lag": lag, "stale": lag > max_lag,
                    "last_checked": (db.raw_latest_checked(sym) or "")[:10] or None}
    return out


def factor_freshness_ok(today, factor_last, *, max_lag_days: int = 7) -> dict:
    """复权因子新鲜度裁决（G-T13-2 的回测线半边；因子末日读取由 T32 适配器负责）。

    因子比"今日"旧超过 `max_lag_days` 自然日 ⇒ 拒跑：期间若有除权除息，
    原始价换算就是**系统性错价**，外推不是降级（report-t13 §4.3）。
    """
    if factor_last is None:
        return {"ok": False, "reason": "factor_unavailable", "lag_days": None}
    fl, td = _as_date(factor_last), _as_date(today)
    lag = (td - fl).days
    return {"ok": lag <= max_lag_days, "factor_last": fl.isoformat(),
            "today": td.isoformat(), "lag_days": lag,
            "reason": None if lag <= max_lag_days else "factor_stale"}
